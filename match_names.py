#!/usr/bin/python3
import argparse
import click
import collections
from nameparser import HumanName
from tqdm import tqdm

from pa_legislature import PALegislatureDB
from names import dict_to_name, name_tuple, is_same_name, from_tuple
from crawl import SENATE_BIO_TEMPLATE, HOUSE_BIO_TEMPLATE


def get_match(member_lookup, name):
    if ' ' not in name:
        hn = HumanName()
        hn.last = name.title()
    else:
        hn = HumanName(name.title())

    last = hn.last.lower()
    if last not in member_lookup:
        return

    last_dict = member_lookup[last]
    if len(last_dict) == 1:
        key = list(last_dict.keys())[0]
        hn2 = from_tuple(key)
        if is_same_name(hn, hn2, require_suffix=False):
            return last_dict[key]
        click.secho(f'Unable to match vote name {name} to existing name {hn2}', fg='yellow')
        return

    if not hn.first:
        return

    if len(hn.first) == 2 and hn.first[-1] == '.':
        hn.first = hn.first[0]

    matching_keys = []
    for key in last_dict:
        hn2 = from_tuple(key)
        if is_same_name(hn, hn2, require_suffix=False):
            matching_keys.append(key)

    if len(matching_keys) == 1:
        key = matching_keys[0]
        return last_dict[key]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-q', '--quick', action='store_true')
    parser.add_argument('-a', '--all', action='store_true')
    parser.add_argument('-w', '--write', action='store_true')
    parser.add_argument('-u', '--display-urls', action='store_true')
    args = parser.parse_args()

    with PALegislatureDB() as db:
        rolls = {d['id']: d for d in db.query('SELECT * FROM roll_calls')}
        statuses = db.get_crawl_statuses(rolls=rolls)

        fully_crawled = set()
        for key, status in sorted(statuses.items()):
            if status == 'complete':
                fully_crawled.add(key)

        matches = 0
        left_a = 0
        left_b = 0

        votes_by_year_and_chamber = collections.defaultdict(lambda: collections.defaultdict(list))

        if args.quick:
            table = 'votes LEFT JOIN roll_calls ON roll_id=id'
            clause = 'WHERE session_year >= 2017'
        else:
            table = 'votes'
            clause = ''

        votes = collections.defaultdict(list)

        for d in db.query('SELECT * FROM votes'):
            votes[d['roll_id']].append(d)

        for roll_id in tqdm(list(db.lookup_all('roll_id', table, clause, distinct=True))):
            roll = rolls[roll_id]
            roll = db.query_one(f'SELECT * FROM roll_calls WHERE id={roll_id}')
            if roll['stamp']:
                year = roll['stamp'].year
            else:
                date = db.lookup('date', 'session_days', f'WHERE id={roll["day_id"]}')
                year = date.year
            if (year, roll['chamber']) not in fully_crawled:
                continue
            votes_by_year_and_chamber[year][roll['chamber']].append(votes[roll_id])

        for year in sorted(votes_by_year_and_chamber):
            for chamber in votes_by_year_and_chamber[year]:
                votes = votes_by_year_and_chamber[year][chamber]
                member_lookup = collections.defaultdict(dict)
                member_id_info = {}
                c = 0
                for member_id in db.lookup_all('member_id', 'service',
                                               f'WHERE year={year} AND chamber={chamber.value}'):
                    member = dict(db.query_one(f'SELECT * FROM members WHERE id={member_id}'))
                    last = member['last']
                    member_lookup[last.lower()][name_tuple(member)] = member
                    member_id_info[member['id']] = member
                    c += 1

                all_voter_names = set()
                all_members_set = True
                for roll_votes in votes:
                    for vote in roll_votes:
                        all_voter_names.add(vote['name'])
                        if all_members_set and (vote['member_id'] is None):
                            all_members_set = False

                if all_members_set:
                    continue

                click.secho(f'{year} {chamber.name}: {c} members vs. {len(all_voter_names)} voter names '
                            f'({len(votes)} rolls)', bg='blue')

                vote_name_to_id = {}
                id_hits = collections.Counter()
                for name in all_voter_names:
                    match = get_match(member_lookup, name)
                    if match is not None:
                        vote_name_to_id[name] = match['id']
                        id_hits[match['id']] += 1
                missing_vote_names = all_voter_names - set(vote_name_to_id.keys())
                unmatched_ids = set(member_id_info.keys()) - set(id_hits.keys())
                unmatched_names = collections.defaultdict(list)
                for unmatched_id in unmatched_ids:
                    member = member_id_info[unmatched_id]
                    unmatched_names[member['last'].upper()].append(unmatched_id)

                # Subset pass
                changed = True
                while changed:
                    changed = False
                    for name in list(missing_vote_names):
                        matching_ids = []
                        for k, v in unmatched_names.items():
                            if name in k:
                                matching_ids += v
                        if len(matching_ids) == 1:
                            matching_id = matching_ids[0]
                            member = member_id_info[matching_id]
                            click.secho(f'Substring match: {name} => {dict_to_name(member)}', fg='bright_green')
                            vote_name_to_id[name] = matching_id
                            missing_vote_names.remove(name)
                            unmatched_ids.remove(matching_id)
                            del unmatched_names[member['last'].upper()]
                            changed = True

                matches += len(vote_name_to_id)
                left_a += len(missing_vote_names)
                left_b += len(unmatched_ids)
                click.secho(f'{len(missing_vote_names):3}  {len(vote_name_to_id):3}  {len(unmatched_ids):3}',
                            fg='bright_blue')
                click.secho(f'{100 * len(missing_vote_names)//len(all_voter_names):3}% '
                            f'{100 * len(vote_name_to_id)//len(all_voter_names):3}% '
                            f'{100 * len(unmatched_ids)//len(member_id_info):3}%',
                            fg='blue')

                if len(missing_vote_names) == 0 and len(unmatched_ids) == 0:
                    # Write values as needed
                    if args.write:
                        session_ids_to_write = collections.defaultdict(set)
                        roll_ids_count = collections.Counter()
                        for roll_votes in votes:
                            for vote in roll_votes:
                                if vote['member_id'] is not None or vote['name'] not in vote_name_to_id:
                                    continue
                                member_id = vote_name_to_id[vote['name']]
                                key = vote['name'], member_id
                                roll_ids_count[key] += 1
                                session_ids_to_write[vote['name'], member_id].add(vote['session_id'])

                        bar = tqdm(sorted(session_ids_to_write.keys()))
                        for vote_name, member_id in bar:
                            session_ids = session_ids_to_write[vote_name, member_id]
                            bar.set_description(f'Writing member_id for {vote_name} to '
                                                f'{roll_ids_count[vote_name, member_id]} votes')
                            cmd = f'UPDATE votes SET member_id={member_id} WHERE name="{vote_name}" AND session_id=?'
                            db.execute_many(cmd, [[session_id] for session_id in session_ids])

                    continue

                for name in sorted(missing_vote_names.union(unmatched_names.keys()).union(vote_name_to_id.keys())):
                    if name in unmatched_names:
                        if name in missing_vote_names:
                            click.secho(f'\tVote name {name} is ambiguous', fg='red')
                            for unmatched_id in unmatched_names[name]:
                                member = member_id_info[unmatched_id]
                                full_name = dict_to_name(member)
                                click.secho(f'\t\t{full_name} ({unmatched_id})', fg='bright_red')
                        else:
                            for unmatched_id in unmatched_names[name]:
                                member = member_id_info[unmatched_id]
                                full_name = dict_to_name(member)
                                click.secho(f'\tUnmatched member {full_name}', fg='yellow')
                                if args.display_urls:
                                    for key in ['house_archive_id', 'senate_archive_id']:
                                        if not member.get(key):
                                            continue
                                        if 'sen' in key:
                                            url = SENATE_BIO_TEMPLATE.format(number=member[key])
                                        else:
                                            url = HOUSE_BIO_TEMPLATE.format(number=member[key])

                                    click.secho(f'\t{url}', fg='yellow')
                    elif name in missing_vote_names:
                        click.secho(f'\t{name} vote name unmatched', fg='bright_yellow')
                    elif args.all:
                        mid = vote_name_to_id[name]
                        member = member_id_info[mid]
                        full_name = dict_to_name(member)
                        click.secho(f'\t{name} matched with {full_name}', fg='blue')

        # Final Stat Display
        click.secho()
        click.secho(f'{left_a:3}  {matches:3}  {left_b:3}',
                    fg='bright_blue')
        n = left_a + matches
        if n == 0:
            n = 1
        click.secho(f'{100 * left_a//n:3}% '
                    f'{100 * matches//n:3}% '
                    f'{100 * left_b//n:3}%',
                    fg='blue')
