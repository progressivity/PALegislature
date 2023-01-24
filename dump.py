#!/usr/bin/python3
import click
import collections
import csv
import pathlib

from pa_legislature import PALegislatureDB
from names import dict_to_name

if __name__ == '__main__':
    with PALegislatureDB() as db:
        session_days_by_year_and_chamber = collections.defaultdict(lambda: collections.defaultdict(list))
        incomplete = set()

        for session in db.query('SELECT * FROM sessions ORDER BY year'):
            key = session['year'], session['chamber']
            if key in incomplete:
                continue
            sid = session['id']
            all_days = list(db.query(f'SELECT * FROM session_days WHERE session_id={sid}'))

            # Skip if none found
            if not all_days:
                continue

            missing = len([d for d in all_days if d['last_crawl'] is None])
            if missing > 0:
                incomplete.add(key)
                session_days_by_year_and_chamber[session['year']].pop(session['chamber'], None)
            else:
                session_days_by_year_and_chamber[session['year']][session['chamber']] += all_days

        vote_lookup = collections.defaultdict(dict)
        for d in db.query('SELECT roll_id, member_id, vote FROM votes WHERE member_id IS NOT NULL'):
            vote_lookup[d['roll_id']][d['member_id']] = d['vote']

        member_lookup = {d['id']: d for d in db.query('SELECT * FROM members')}
        roll_lookup = {d['id']: d for d in db.query('SELECT * FROM roll_calls')}

        root_folder = pathlib.Path('vote_data')
        root_folder.mkdir(exist_ok=True)

        for year in sorted(session_days_by_year_and_chamber):
            for chamber in sorted(session_days_by_year_and_chamber[year]):
                days = session_days_by_year_and_chamber[year][chamber]

                rolls = []
                for day in sorted(days, key=lambda d: d['date']):
                    roll_subset = [d for d in roll_lookup.values() if d['day_id'] == day['id']]

                    if any(not roll['stamp'] for roll in roll_subset):
                        # Some stamps missing, order by id
                        for roll in sorted(roll_subset, key=lambda d: d['id']):
                            # Fill in stamp with date
                            if not roll['stamp']:
                                roll = dict(roll)
                                roll['stamp'] = day['date']
                            rolls.append(roll)
                    else:
                        rolls += sorted(roll_subset, key=lambda d: d['stamp'])
                if not rolls:
                    continue

                year_folder = root_folder / str(year)
                year_folder.mkdir(exist_ok=True)
                fn = year_folder / (chamber.name.title() + '.csv')

                headers = ['Name', 'Number', 'Date']
                member_ids = []
                districts = []
                parties = []
                for service in db.query(f'SELECT * FROM service WHERE year={year} AND chamber={chamber.value} '
                                        f'ORDER BY district'):
                    member_ids.append(service['member_id'])
                    member = member_lookup[service['member_id']]
                    headers.append(dict_to_name(member))
                    districts.append(service['district'])
                    parties.append(service['party'])

                click.secho(f'Writing {str(fn):30} {len(rolls):4d} rows, {len(headers):3d} columns',
                            fg='bright_green')
                with open(fn, 'w') as f:
                    csv_writer = csv.writer(f)
                    csv_writer.writerow(headers)
                    if any(districts):
                        csv_writer.writerow(['District', '', ''] + districts)
                    if any(parties):
                        csv_writer.writerow(['Party', '', ''] + parties)

                    for roll in rolls:
                        row = []
                        row.append(roll['name'])
                        row.append(roll['number'])
                        row.append(str(roll['stamp']))
                        for mid in member_ids:
                            if mid in vote_lookup[roll['id']]:
                                row.append(vote_lookup[roll['id']][mid].to_letter())
                            else:
                                row.append('')

                        csv_writer.writerow(row)
