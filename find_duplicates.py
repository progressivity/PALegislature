#!/usr/bin/python3
import argparse
import click
import collections
from pa_legislature import PALegislatureDB
from names import dict_to_name, is_same_name

id_fields = ['house_archive_id', 'house_current_id', 'senate_archive_id', 'senate_current_id']


def are_mergable(member1, member2):
    for key in id_fields:
        if member1[key] is not None and member2[key] is not None:
            return False
    return True


def add_candidate_matches(member_ids, require_suffix=True):
    if len(member_ids) == 1:
        return

    member_ids = sorted(member_ids)

    for i, member_id1 in enumerate(member_ids):
        name1 = dict_to_name(members[member_id1])
        for member_id2 in member_ids[i + 1:]:
            name2 = dict_to_name(members[member_id2])
            name0 = is_same_name(name1, name2, require_suffix)
            if name0 and are_mergable(members[member_id1], members[member_id2]):
                merge_groups[member_id1].add(member_id2)
                merge_names[member_id1] = name0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-w', '--write', action='store_true')
    args = parser.parse_args()

    with PALegislatureDB() as db:
        members = {d['id']: d for d in db.query('SELECT * FROM members')}
        service = collections.defaultdict(list)
        for row in db.query('SELECT * FROM service'):
            service[row['member_id']].append(row)

        merge_groups = collections.defaultdict(set)
        merge_names = {}

        # Merges by overlapping service
        by_year_and_last_name = collections.defaultdict(lambda: collections.defaultdict(set))

        for member_id in service:
            last = members[member_id]['last'].lower()
            for row in service[member_id]:
                key = row['year'], row['chamber']
                by_year_and_last_name[key][last].add(member_id)

        for key, last_dict in sorted(by_year_and_last_name.items()):
            for last, member_ids in last_dict.items():
                add_candidate_matches(member_ids)

        # Merges by matching dob
        for row in db.query('SELECT *, COUNT(id) FROM members WHERE dob IS NOT NULL GROUP BY dob ORDER BY dob'):
            if row['COUNT(id)'] == 1:
                continue
            member_ids = set(db.lookup_all('id', 'members', {'dob': row['dob']}))
            add_candidate_matches(member_ids, False)

        # Do the merging
        for member_id1 in merge_groups:
            click.secho(str(merge_names[member_id1]), fg='bright_white')
            updates = {}
            member1 = members[member_id1]
            for key in ['first', 'middle', 'last', 'suffix']:
                name_bit = getattr(merge_names[member_id1], key)
                if name_bit != member1[key] and name_bit:
                    updates[key] = name_bit

            for member_id in [member_id1] + list(merge_groups[member_id1]):
                member = members[member_id]
                ids = ' '.join('{:5}'.format(member[key] or '.....') for key in id_fields)
                print(f'\t{member_id} {ids} {dict_to_name(member)}')
                if member_id != member_id1:
                    for key in id_fields:
                        if member[key] is not None:
                            updates[key] = member[key]
                for row in db.query(f'SELECT * FROM service WHERE member_id={member_id} ORDER BY year'):
                    print('\t\t{chamber} {year}: {party} {district}'.format(**row))
            if updates and args.write:
                updates['id'] = member_id1
                service_set = set()
                skeys = ['chamber', 'year', 'district', 'party']
                for row in service[member_id1]:
                    key = tuple(row[k] for k in skeys)
                    service_set.add(key)

                print(updates)

                for member_id in merge_groups[member_id1]:
                    for row in service[member_id]:
                        key = tuple(row[k] for k in skeys)
                        if key not in service_set:
                            service_set.add(key)
                            new_row = dict(row)
                            new_row['member_id'] = member_id1
                            clause = db.generate_clause(dict(row))

                            db.execute(f'UPDATE service SET member_id=? {clause}', [member_id1])
                    db.execute(f'DELETE FROM members WHERE id={member_id}')
                    db.execute(f'DELETE FROM service WHERE member_id={member_id}')
                db.update('members', updates)
