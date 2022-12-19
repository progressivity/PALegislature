#!/usr/bin/python3
import click
import yaml

from pa_legislature import PALegislatureDB, Chamber
from names import dict_to_name

if __name__ == '__main__':
    edits = yaml.safe_load(open('edits.yaml'))
    with PALegislatureDB() as db:
        for key in edits.keys():
            if isinstance(key, int):
                # Year Edits
                for chamber_s in edits[key]:
                    chamber = Chamber[chamber_s.upper()]
                    for last, edit in edits[key][chamber_s].items():
                        clause_d = {'chamber': chamber, 'year': key}
                        if ' ' in last:
                            clause_d['first'], clause_d['last'] = last.split(' ')
                        else:
                            clause_d['last'] = last
                        existing_query = 'SELECT * FROM service LEFT JOIN members ON service.member_id == members.id '
                        existing_query += db.generate_clause(clause_d)
                        existing_matches = list(db.query(existing_query))
                        if edit is None:
                            if len(existing_matches) == 1:
                                mid = existing_matches[0]['id']
                                click.secho(f'Removing service for {last} ({chamber_s} {key})', fg='blue')
                                clause = db.generate_clause({'member_id': mid, 'year': key, 'chamber': chamber})
                                db.execute('DELETE FROM service ' + clause)
                            elif existing_matches:
                                click.secho(f'Too many matches for {last} ({chamber_s} {key})', fg='yellow')
                        # Add in
                        elif not existing_matches:
                            # Need to add one in, find in year before or after
                            query = 'SELECT * FROM service LEFT JOIN members ON service.member_id == members.id '
                            query += f'WHERE last == "{last}" AND chamber={chamber.value} '
                            query += f'AND (year == {key - 1} OR year == {key + 1})'
                            matches = list(db.query(query))
                            if len(matches) == 1:
                                match = matches[0]
                                mid = match['id']
                                click.secho(f'Adding service for {last} ({chamber_s} {key}) (id={mid})', fg='blue')
                                service = {'member_id': mid,
                                           'year': key,
                                           'chamber': chamber,
                                           'district': match['district'],
                                           'party': match['party']}
                                db.insert('service', service)
                            elif len(matches) > 1:
                                click.secho(f'Ambiguous match for {last} ({chamber_s} {key}) (id={mid})', fg='yellow')
                            else:
                                click.secho(f'No match for {last} ({chamber_s} {key}) (id={mid})', fg='yellow')
            elif key == 'Votes':
                for before, v in edits[key].items():
                    if isinstance(v, str):
                        after = v
                        votes = list(db.lookup_all('roll_id', 'votes', {'name': before}))
                        if votes:
                            click.secho(f'Replacing {len(votes):4d} votes by "{before}" with "{after}"', fg='blue')
                            cmd = f'UPDATE votes SET name=? WHERE name="{before}"'
                            db.execute(cmd, [after])
                    else:
                        after = v['name']
                        table = 'votes LEFT JOIN roll_calls ON votes.roll_id=roll_calls.id'
                        clause = f'WHERE votes.name == "{before}" AND stamp > "{v["start"]}" AND stamp < "{v["stop"]}"'
                        roll_ids = list(db.lookup_all('id', table, clause))
                        if roll_ids:
                            click.secho(f'Replacing {len(roll_ids):4d} votes by "{before}" with "{after}"', fg='blue')
                            for roll_id in roll_ids:
                                print(roll_id)
                                cmd = f'UPDATE votes SET name=? WHERE name="{before}" AND roll_id={roll_id}'
                                db.execute(cmd, [after])
            elif key == 'Rename':
                for d in edits[key]:
                    member = db.query_one('SELECT * FROM members ' + db.generate_clause(d['from']))
                    if member is None:
                        click.secho(f'Could not find member: {d["from"]}', fg='yellow')
                        continue
                    updates = {}
                    for k, v in d['to'].items():
                        if v == member[k]:
                            continue
                        updates[k] = v
                    if updates:
                        updates['id'] = member['id']
                        click.secho(f'Renaming {dict_to_name(member)}...', fg='blue')
                        db.update('members', updates)
