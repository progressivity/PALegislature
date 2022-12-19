#!/usr/bin/python3
import argparse
import bs4
import click
import datetime
import dateutil.parser
import pathlib
import requests
import urllib.parse

from pa_legislature import PALegislatureDB, Chamber, Vote

CACHE_FOLDER = pathlib.Path('.cached_html')
USER_AGENT = {
    'User-Agent': 'PALegislature Bot',
    'From': 'davidvlu@gmail.com'
}


def get_page(url, name, use_cached=False):
    CACHE_FOLDER.mkdir(exist_ok=True)
    cache_path = CACHE_FOLDER / (name + '.html')
    if use_cached and cache_path.exists():
        contents = open(cache_path).read()
    else:
        req = requests.get(url, headers=USER_AGENT)
        contents = req.text

        if use_cached:
            with open(cache_path, 'w') as f:
                f.write(contents)

    return bs4.BeautifulSoup(contents, 'html.parser')


def chamber_arg(chamber):
    return chamber.name[0]


def update_session_years(db, chamber, year=None, index=None):
    url = 'https://www.legis.state.pa.us/SessionDays.cfm?'
    params = {'Chamber': chamber_arg(chamber)}
    if year is not None:
        params['SessionYear'] = year
    if index is not None:
        params['SessionInd'] = index

    full_url = url + urllib.parse.urlencode(params)
    if year is None:
        click.secho(f'Updating {chamber} Session List', fg='blue', nl=False)
    else:
        click.secho(f'Updating {chamber} Session: {year}/{index}', fg='blue', nl=False)

    click.secho(f' ({full_url})', fg='bright_black')

    soup = get_page(full_url, 'session')

    # Update Sessions
    dropdown = soup.find('select', {'id': 'SessID'})
    for option in dropdown.find_all('option'):
        code = option['value']
        row = {'chamber': chamber, 'year': int(code[:4]), 'session_index': int(code[4]), 'name': option.text}
        db.update('sessions', row, ['chamber', 'year', 'session_index'])
        if option.get('selected') is not None:
            if year is None:
                year = row['year']
            if index is None:
                index = row['session_index']

    # Find Proper Session for this page
    session_id = db.lookup('id', 'sessions',
                           f'WHERE chamber={chamber.value} AND year={year} and session_index={index}')

    # Update Days
    found = 0
    for column in soup.find_all('div', class_='Column-OneHalf'):
        header = column.find('h3')
        if not header:
            if found == 0:
                raise RuntimeError('Cannot find h3 in session')
            continue

        if 'Scheduled' in header.text:
            continue
        for row in column.find_all('div', class_='CalendarDisplay-List-Row'):
            month_s = row.find('div', class_='CalendarDisplay-List-Month').text.strip()
            month = datetime.datetime.strptime(month_s, '%B').month
            for link in row.find_all('a'):
                day_s = link.text
                if '\xa0' in day_s:
                    day_s = day_s.split('\xa0')[0]
                day = int(day_s)

                date = datetime.date(year, month, day)
                row = {'session_id': session_id, 'date': date}
                db.update('session_days', row, row)
                found += 1
    click.secho(f'\t{found:3d} days found', fg='blue')
    db.update('sessions', {'id': session_id, 'last_crawl': datetime.datetime.now()})


def update_day(db, day_d):
    url = 'https://www.legis.state.pa.us/cfdocs/legis/home/sessionPriorDays.cfm?'

    session_id = day_d['session_id']
    session = db.query_one(f'SELECT chamber, session_index FROM sessions WHERE id={session_id}')
    if not session:
        raise RuntimeError(f'Cannot find session {session_id}')

    chamber = session['chamber']
    params = {}
    params['SessionInd'] = session['session_index']
    params['Chamber'] = chamber_arg(chamber)
    params['SessionDate'] = day_d['date'].strftime('%m/%d/%Y')

    full_url = url + urllib.parse.urlencode(params)

    click.secho(f'Updating {chamber} on {params["SessionDate"]}', fg='cyan', nl=False)
    click.secho(f' ({full_url})', fg='bright_black')

    soup = get_page(full_url, 'day')

    link = soup.find('a', text='Floor Roll Call Votes')
    if not link:
        click.secho('\tNo votes found', fg='cyan')
        db.update('session_days', {'id': day_d['id'], 'last_crawl': datetime.datetime.now()})
        return
    floor_url = 'https://www.legis.state.pa.us' + link['href']

    click.secho('\tGetting floor votes', fg='cyan', nl=False)
    click.secho(f' ({floor_url})', fg='bright_black')

    soup = get_page(floor_url, 'votes')

    table = soup.find('table', class_='DataTable')
    found = 0
    for row in table.find('tbody').find_all('tr'):
        links = row.find_all('a')
        assert links[0]['id'].startswith('RCLink')
        roll_url = urllib.parse.urlparse(links[0]['href'])
        roll_query = urllib.parse.parse_qs(roll_url.query)
        query = {k: v[0] if len(v) == 1 else v for k, v in roll_query.items()}

        roll_d = {'day_id': day_d['id'],
                  'number': int(query['rc_nbr']),
                  'session_year': int(query['sess_yr']),
                  'session_index': int(query['sess_ind']),
                  'chamber': Chamber.from_letter(query['rc_body']),
                  'name': links[0].text.strip()}
        db.update('roll_calls', roll_d, roll_d)
        found += 1

    click.secho(f'\t{found} rolls found', fg='cyan')
    db.update('session_days', {'id': day_d['id'], 'last_crawl': datetime.datetime.now()})


def update_roll(db, roll):
    url = 'https://www.legis.state.pa.us/cfdocs/legis/RC/PUBLIC/rc_view_action2.cfm?'

    chamber = roll['chamber']
    params = {}
    params['sess_yr'] = roll['session_year']
    params['sess_ind'] = roll['session_index']
    params['rc_body'] = chamber_arg(chamber)
    params['rc_nbr'] = roll['number']

    full_url = url + urllib.parse.urlencode(params)
    click.secho(f'Getting {chamber} vote #{roll["number"]}', fg='cyan', nl=False)
    click.secho(f' ({full_url})', fg='bright_black')

    soup = get_page(full_url, 'roll')

    # Get the votes
    container = soup.find('div', class_='RollCalls-ListContainer')
    for div in container.find_all('div'):
        if div['class'][0].startswith('Column'):
            # Formatting div
            continue
        kids = list(div.children)
        vote = kids[1].text
        name = kids[2].strip()

        vote_d = {
            'roll_id': roll['id'],
            'name': name,
            'vote': Vote.from_letter(vote),
        }
        db.update('votes', vote_d, vote_d)

    # Get the time stamp
    side_div = soup.find('div', class_='Column-OneFourth')
    sections = list(side_div.findChildren('div', recursive=False))
    info_sections = list(sections[1].findChildren('div', recursive=False))
    if len(info_sections) == 3:
        date_s = info_sections[0].text
        time_s = info_sections[1].text
        stamp = dateutil.parser.parse(f'{date_s} {time_s}')
    else:
        click.secho('\tCould not find time stamp', fg='yellow')
        stamp = None

    db.update('roll_calls', {'id': roll['id'], 'stamp': stamp, 'last_crawl': datetime.datetime.now()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-y', '--min-year', type=int)
    parser.add_argument('-s', '--update-sessions', dest='session_limit', type=int, default=0, nargs='?')
    parser.add_argument('-c', '--update-days', dest='day_limit', type=int, default=0, nargs='?')
    parser.add_argument('-r', '--update-rolls', dest='roll_limit', type=int, default=0, nargs='?')
    parser.add_argument('-a', '--update-all', type=int, default=0, nargs='?')
    args = parser.parse_args()

    if args.update_all != 0:
        args.session_limit = args.update_all
        args.day_limit = args.update_all
        args.roll_limit = args.update_all

    # TODO: Update for recency
    crawl_clause = 'last_crawl IS NULL'
    year_clause = '' if args.min_year is None else f'AND year >= {args.min_year}'

    with PALegislatureDB() as db:
        for table in ['sessions', 'session_days', 'roll_calls']:
            n = db.count(table)
            c = db.count(table, {'last_crawl': None})
            r = n - c
            click.secho(f'{table:15s} ', fg='bright_white', nl=False)
            if n == 0:
                click.secho('     ', fg='bright_white', nl=False)
            elif c == 0:
                click.secho('100% ', fg='bright_white', nl=False)
            else:
                click.secho(f'{100 * r // n:3}% ', nl=False)
            click.secho(f'{r:5}/{n:5}')

        if args.session_limit is None or args.session_limit > 0:
            most_recent = db.lookup('last_crawl', 'sessions', 'WHERE last_crawl IS NOT NULL ORDER BY last_crawl DESC')

            if not most_recent or (datetime.datetime.now() - most_recent) > datetime.timedelta(days=1):
                update_session_years(db, Chamber.HOUSE)
                update_session_years(db, Chamber.SENATE)

            limit_clause = '' if args.session_limit is None else f'LIMIT {args.session_limit}'

            for session in db.query(f'SELECT * FROM sessions WHERE {crawl_clause} {year_clause}'
                                    f'ORDER BY year, chamber, session_index {limit_clause}'):
                update_session_years(db, session['chamber'], session['year'], session['session_index'])

        limit_clause = '' if args.day_limit is None else f'LIMIT {args.day_limit}'
        date_clause = '' if args.min_year is None else f'AND DATE(date) >= "{args.min_year}-01-01"'
        for day in db.query(f'SELECT id, session_id, date FROM session_days WHERE {crawl_clause} {date_clause} '
                            f'ORDER BY date DESC {limit_clause}'):
            update_day(db, day)

        limit_clause = '' if args.roll_limit is None else f'LIMIT {args.roll_limit}'
        for roll in db.query(f'SELECT * FROM roll_calls WHERE {crawl_clause} '
                             f'ORDER BY session_year, number {limit_clause}'):
            update_roll(db, roll)
