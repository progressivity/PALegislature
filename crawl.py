#!/usr/bin/python3
import argparse
import bs4
import click
import datetime
import dateutil.parser
from nameparser import HumanName
from nameparser.config import CONSTANTS
import pathlib
import re
import requests
import urllib.parse
import yaml

from pa_legislature import PALegislatureDB, Chamber, Vote
from names import dict_to_name

CACHE_FOLDER = pathlib.Path('.cached_html')
USER_AGENT = {
    'User-Agent': 'PALegislature Bot',
    'From': 'davidvlu@gmail.com'
}

# Customize NameParser
CONSTANTS.titles.remove('pope')
CONSTANTS.titles.remove('merchant')
CONSTANTS.titles.remove('bishop')
CONSTANTS.titles.remove('st.')

SENATE_BIO_TEMPLATE = 'https://www.legis.state.pa.us/cfdocs/legis/BiosHistory/MemBio.cfm?ID={number}&body=S'
HOUSE_BIO_TEMPLATE = 'https://archives.house.state.pa.us/people/member-biography?ID={number}'


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
                if datetime.datetime.now().year >= year:
                    click.secho('Cannot find h3 on session page. Skipping for now...', fg='yellow')
                return
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

    link = soup.find('a', string='Floor Roll Call Votes')
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
    session_id = db.lookup('session_id', 'session_days', {'id': roll['day_id']})

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
            'session_id': session_id,
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


ALL_CAPS = re.compile(r'^[^a-z]+$')
TWO_CAPS = re.compile(r'[A-Z]{2}')


def advanced_decapitalization(s):
    """Equivalent to s.title() if no lower case letters. Otherwise converts BRIAN McRAE to Brian McRae"""
    if ALL_CAPS.match(s):
        return s.title()

    capitalize = True
    new_s = ''
    for c in s:
        if c.islower() or c == ' ':
            new_s += c
            capitalize = True
        elif capitalize:
            new_s += c.upper()
            capitalize = False
        else:
            new_s += c.lower()
    return new_s


def get_name_dict(s):
    if TWO_CAPS.search(s):
        s = advanced_decapitalization(s)

    hn = HumanName(s)

    if hn.title:
        click.secho(f'Extra fields in name: {s}', fg='red')
        click.secho(f'\t{hn.as_dict()}', fg='red')
        exit(0)

    return {'first': hn.first,
            'middle': hn.middle or None,
            'last': hn.last,
            'suffix': hn.suffix or None,
            }


NICKS = {
    ('Tom', 'Thomas'),
    ('Mike', 'Michael'),
    ('Bernie', 'Bernard'),
}


def assert_names_equal(name_dict1, name_dict2, fatal=False):
    if name_dict1['first'] == name_dict2['first']:
        l1 = name_dict1['last']
        l2 = name_dict2['last']
        if l1.lower() == l2.lower():
            return True
        if f'{l2}-' in l1 or f'-{l2}' in l1:
            return True

    elif name_dict1['last'] == name_dict2['last']:
        f1 = name_dict1['first']
        f2 = name_dict2['first']
        if f1 in f2 or f2 in f1 or f1.lower() == f1.lower():
            return True

        if f1[1] == '.' and name_dict1['middle'] == f2:
            return True

        nick = tuple(sorted([f1, f2], key=lambda s: len(s)))
        if nick in NICKS:
            return True

    click.secho('Crawl Name Error', fg='red')
    click.secho(f'\t{name_dict1}', fg='red')
    click.secho(f'\t{name_dict2}', fg='red')
    if fatal:
        exit(-1)
    else:
        return False


def get_member_list(db, url, name, wrapper_spec, chamber):
    click.secho(f'Updating {name} List', fg='bright_yellow', nl=False)
    click.secho(f' ({url})', fg='bright_black')
    soup = get_page(url, name)

    content = soup.find('div', wrapper_spec)

    for link in content.find_all('a'):
        bio_url = urllib.parse.urlparse(link['href'])
        bio_query = urllib.parse.parse_qs(bio_url.query)
        archive_id = int(bio_query['ID'][0])
        full_name = link.text.strip()

        prefix = 'house_' if chamber == Chamber.HOUSE else 'senate_'
        key = f'{prefix}archive_id'
        member = {key: archive_id}

        member.update(get_name_dict(full_name))
        db.update('members', member, key)


def update_senator_list(db):
    url = 'https://www.legis.state.pa.us/cfdocs/legis/BiosHistory/ViewAll.cfm?body=S'
    get_member_list(db, url, 'Senator Member', {'class': 'Column-Full'}, Chamber.SENATE)


def update_representative_list(db, letter):
    url = 'https://archives.house.state.pa.us/people/view-all?letter=' + letter
    get_member_list(db, url, f'House Member {letter}', {'id': 'portfolioPaginationWrapper'}, Chamber.HOUSE)


def parse_year_range(s):
    year_range = []
    if '-' not in s:
        year_range.append(int(s))
    else:
        start_s, _, end_s = s.partition('-')
        start = int(start_s)
        end = int(end_s)
        year_range += list(range(start, end + 1))
    return year_range


RESOLUTIONS = yaml.safe_load(open('resolutions.yaml'))


def get_resolved_url(url):
    if url in RESOLUTIONS:
        return RESOLUTIONS[url]
    resolved = url
    while True:
        r = requests.head(resolved, headers=USER_AGENT)

        if r.status_code not in [301, 302] or 'Location' not in r.headers:
            break
        resolved = urllib.parse.urljoin(resolved, r.headers['Location'])

    ret = None if resolved == url else resolved
    RESOLUTIONS[url] = ret
    yaml.safe_dump(RESOLUTIONS, open('resolutions.yaml', 'w'))

    if resolved == url:
        return
    else:
        return resolved


PARTY_PATTERN = re.compile(r'\((.)\)')
DISTRICT_PATTERN = re.compile(r'District (\d+)')
PARTY_CODES = {
    'D': 'Democrat',
    'R': 'Republican',
    'I': 'Independent',
}


def update_current_roll(db, chamber, year=None):
    base_url = 'https://www.legis.state.pa.us/cfdocs/legis/home/member_information/'
    params = {'body': chamber_arg(chamber)}
    if year is not None:
        params['SessYear'] = year

    full_url = base_url + 'mbrList.cfm?' + urllib.parse.urlencode(params)
    if year is None:
        click.secho(f'Updating {chamber} Member List', fg='bright_yellow', nl=False)
    else:
        click.secho(f'Updating {year} {chamber} Member List', fg='bright_yellow', nl=False)
    click.secho(f' ({full_url})', fg='bright_black')

    soup = get_page(full_url, 'CurrentRoll')

    # Update Session Years
    dropdown = soup.find('select', {'id': 'SessYear'})
    year_range = None
    for option in dropdown.find_all('option'):
        update_name = option['value'] + ' ' + chamber.name
        db.update('member_crawl', {'name': update_name}, ['name'])

        if option.get('selected') is not None:
            name = option.text.replace('\xa0', ' ').strip()
            year_range = parse_year_range(name)

    if not year_range:
        click.secho('Could not find year range from option box!', fg='red')
        exit(-1)

    found = 0
    for info in soup.find_all('div', class_='MemberInfoList-MemberWrapper'):
        bio = info.find('div', class_='MemberInfoList-MemberBio')
        link = bio.find('a')
        member_name = link.text.strip()
        name_dict = get_name_dict(member_name)
        bio_url = urllib.parse.urlparse(link['href'])
        bio_query = urllib.parse.parse_qs(bio_url.query)

        current_id = int(bio_query['id'][0])

        resolved_url = get_resolved_url(base_url + link['href'])
        if resolved_url and 'archives' in resolved_url:
            if '?ID=' not in resolved_url and 'search-results' in resolved_url:
                resolved_url += '&fnme=' + name_dict['first']
                resolved_url = get_resolved_url(resolved_url)
                click.secho('\tBonus search', fg='bright_cyan')
            resolved_bio_url = urllib.parse.urlparse(resolved_url)
            resolved_bio_query = urllib.parse.parse_qs(resolved_bio_url.query)
            if 'ID' not in resolved_bio_query:
                print(name_dict)
                print(base_url + link['href'])
                print(resolved_url)
            archive_id = int(resolved_bio_query['ID'][0])
            if archive_id == current_id:
                click.secho(f'\tMatching numbers #{archive_id}', fg='cyan')
            else:
                click.secho(f'\tResolved to archive #{archive_id}', fg='cyan')
        else:
            click.secho(f'\tNo archive id #{current_id}', fg='cyan')
            archive_id = None

        prefix = 'house_' if chamber == Chamber.HOUSE else 'senate_'
        row = {f'{prefix}archive_id': archive_id,
               f'{prefix}current_id': current_id
               }
        row.update(name_dict)

        base_query = 'SELECT * FROM members WHERE '
        if archive_id is not None:
            existing_matches = list(db.query(f'{base_query} {prefix}archive_id == {archive_id}'))
        else:
            existing_matches = list(db.query(f'{base_query} {prefix}current_id == {current_id}'))

        if len(existing_matches) == 1:
            match = existing_matches[0]
            assert_names_equal(match, row, fatal=True)

            member_id = match['id']

            if match[f'{prefix}current_id'] is None:
                db.update('members', {'id': member_id, f'{prefix}current_id': current_id})
        elif len(existing_matches) == 0:
            member_id = db.insert('members', row)
        else:
            click.secho('Multiple matches for found member', fg='red')
            click.secho('\t{base_url}{link["href"]}', fg='bright_black')
            click.secho('\t{resolved_url}', fg='bright_black')
            for match in existing_matches:
                click.secho(str(match), fg='cyan')
            exit(-1)

        party = None
        district = None

        for child in bio.children:
            if isinstance(child, bs4.element.Tag):
                continue
            text = child.text.strip()
            if not text:
                continue

            m1 = PARTY_PATTERN.match(text)
            m2 = DISTRICT_PATTERN.match(text)
            if m1:
                if party:
                    raise RuntimeError('Already have party')
                party_s = m1.group(1)
                if party_s not in PARTY_CODES:
                    raise RuntimeError(f'Unknown party code {party_s}')
                party = PARTY_CODES[party_s]
            elif m2:
                if district is not None:
                    raise RuntimeError('Already have district')
                district = int(m2.group(1))
            else:
                raise RuntimeError('Cannot parse group member info: ' + repr(text))

        if not party:
            raise RuntimeError('Cannot find party')
        if district is None:
            raise RuntimeError('Cannot find district')

        for year in year_range:
            row = {'member_id': member_id, 'year': year, 'chamber': chamber, 'district': district, 'party': party}
            db.update('service', row, ['member_id', 'year', 'chamber'])

        found += 1
    click.secho(f'\t{found} members found', fg='bright_yellow')


def condense(year_list):
    start = None
    end = None
    bits = []
    for year in year_list:
        if start is None:
            start = year
            end = year
        elif year == end + 1:
            end = year
        else:
            if start == end:
                bits.append(str(start))
            else:
                bits.append(f'{start}-{end}')
            start = year
            end = year
    if start == end:
        bits.append(str(start))
    else:
        bits.append(f'{start}-{end}')
    return ', '.join(bits)


def update_member(db, member):
    mid = member['id']

    if member['house_archive_id'] is not None:
        chamber = Chamber.HOUSE
        number = member['house_archive_id']
        url = HOUSE_BIO_TEMPLATE.format(number=number)
    elif member['senate_archive_id']:
        chamber = Chamber.SENATE
        number = member['senate_archive_id']
        url = SENATE_BIO_TEMPLATE.format(number=number)
    else:
        click.secho(f'Cannot find archive_id for {dict_to_name(member)}', fg='yellow')
        return

    click.secho(f'Updating Bio for {chamber} Member #{number}: '
                f'{member["first"]} {member["last"]} {member["suffix"] or ""}', fg='bright_yellow', nl=False)
    click.secho(f' ({url})', fg='bright_black')
    soup = get_page(url, f'{chamber}Bio')

    err = soup.find('div', class_='Message-Error')
    if err:
        click.secho(f'\t{err.text.strip()}', fg='red')
        return

    dob = None
    if chamber == Chamber.HOUSE:
        div = soup.find('div', class_='bio-table')
        table = div.find('table')

        life_e = soup.find('h4')

    else:
        table = soup.find('table', class_='DataTable-Grid')
        life_e = soup.find('h3')

    if life_e:
        life_s = life_e.text.strip()
    else:
        life_s = ''

    name_s = soup.find('h1').text.strip()
    name_dict = get_name_dict(name_s)

    if '-' in life_s:
        dob_s = life_s.split('-')[0].strip()
        if '/' in dob_s:
            stamp = dateutil.parser.parse(dob_s)
            dob = stamp.date()
    prev = None
    years = []

    if not assert_names_equal(member, name_dict):
        return

    for row in table.find_all('tr'):
        if row.find('th'):
            continue
        cells = [td.text.strip() for td in row.find_all('td')]
        if len(cells) != 5:
            continue
        # Sessions / Office / Position / District / Party
        office = cells[1] or None
        if office == 'Representative' and chamber == Chamber.HOUSE:
            pass
        elif office == 'Chief Clerk':
            continue
        elif office:
            click.secho(f'Weird office: {office}', bg='yellow')
            exit(0)
        # position = cells[2]
        if cells[3] in ['N/A', '']:
            district = None
        else:
            try:
                district = int(cells[3])
            except ValueError:
                click.secho(f'Could not parse district number: {cells[3]}', fg='yellow')
                district = None
        party = cells[4] or None

        key = district or '?', party
        if prev and key != prev:
            click.secho(f'\t#{prev[0]} {prev[1]} {condense(years)}')
            years = []
        prev = key

        session_years = parse_year_range(cells[0])

        for year in session_years:
            years.append(year)

            service = {'member_id': mid,
                       'chamber': chamber,
                       'year': year,
                       'district': district,
                       'party': party}
            db.update('service', service, ['member_id', 'year', 'chamber'])
    if prev:
        click.secho(f'\t#{prev[0]} {prev[1]} {condense(years)}')
    else:
        click.secho('\tWarning: No Service Found!', fg='yellow')

    db.update('members', {'id': mid, 'dob': dob, 'last_crawl': datetime.datetime.now()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-y', '--min-year', type=int)
    parser.add_argument('-s', '--update-sessions', dest='session_limit', type=int, default=0, nargs='?')
    parser.add_argument('-c', '--update-days', dest='day_limit', type=int, default=0, nargs='?')
    parser.add_argument('-r', '--update-rolls', dest='roll_limit', type=int, default=0, nargs='?')
    parser.add_argument('-m', '--update-members', dest='member_limit', type=int, default=0, nargs='?')
    parser.add_argument('-b', '--update-bios', dest='bio_limit', type=int, default=0, nargs='?')
    parser.add_argument('-a', '--update-all', type=int, default=0, nargs='?')
    args = parser.parse_args()

    if args.update_all != 0:
        args.session_limit = args.update_all
        args.day_limit = args.update_all
        args.roll_limit = args.update_all
        args.member_limit = args.update_all
        args.bio_limit = args.update_all

    # TODO: Update for recency
    crawl_clause = 'last_crawl IS NULL'
    year_clause = '' if args.min_year is None else f'AND year >= {args.min_year}'

    with PALegislatureDB() as db:
        for table in ['sessions', 'session_days', 'roll_calls', 'member_crawl', 'members']:
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

            for session in db.query(f'SELECT * FROM sessions WHERE {crawl_clause} {year_clause} '
                                    f'ORDER BY year, chamber, session_index {limit_clause}'):
                update_session_years(db, session['chamber'], session['year'], session['session_index'])

        limit_clause = '' if args.day_limit is None else f'LIMIT {args.day_limit}'
        date_clause = '' if args.min_year is None else f'AND DATE(date) >= "{args.min_year}-01-01"'
        for day in db.query(f'SELECT id, session_id, date FROM session_days WHERE {crawl_clause} {date_clause} '
                            f'ORDER BY date DESC {limit_clause}'):
            update_day(db, day)

        limit_clause = '' if args.roll_limit is None else f'LIMIT {args.roll_limit}'
        for roll in db.query(f'SELECT * FROM roll_calls WHERE {crawl_clause} '
                             f'ORDER BY -session_year, number {limit_clause}'):
            update_roll(db, roll)

        if args.member_limit is None or args.member_limit > 0:
            potential_work = []
            # Update Historical Senate List
            potential_work.append(('*', update_senator_list, []))

            # Update Historical Representative List
            for letter in range(ord('A'), ord('Z') + 1):
                potential_work.append((chr(letter), update_representative_list, [chr(letter)]))

            # Add Current Lists
            for chamber in Chamber:
                potential_work.append(('Current ' + chamber.name, update_current_roll, [chamber]))

            # Add Past Recent Lists
            for update_name in db.lookup_all('name', 'member_crawl', 'WHERE name LIKE "2%" ORDER BY name'):
                year_s, chamber_s = update_name.split(' ')
                year = int(year_s)
                chamber = Chamber[chamber_s]
                potential_work.append((update_name, update_current_roll, [chamber, year]))

            completed = 0
            for crawl_name, update_method, arg_list in potential_work:
                last_crawl = db.lookup('last_crawl', 'member_crawl', {'name': crawl_name})

                # TODO: Update to recrawl
                if last_crawl:
                    if 'Current' not in crawl_name or datetime.datetime.now() - last_crawl < datetime.timedelta(days=7):
                        continue

                update_method(db, *arg_list)

                db.update('member_crawl', {'name': crawl_name, 'last_crawl': datetime.datetime.now()}, 'name')

                completed += 1
                if args.member_limit is not None and completed >= args.member_limit:
                    break

        limit_clause = '' if args.bio_limit is None else f'LIMIT {args.bio_limit}'
        for member in db.query(f'SELECT * FROM members WHERE {crawl_clause} ORDER BY last, first {limit_clause}'):
            update_member(db, member)
