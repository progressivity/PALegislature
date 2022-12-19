import bidict
import collections
from metro_db import MetroDB
from enum import IntEnum


class Chamber(IntEnum):
    HOUSE = 1
    SENATE = 2

    @staticmethod
    def from_letter(s):
        if s[0] == 'H':
            return Chamber.HOUSE
        elif s[0] == 'S':
            return Chamber.SENATE
        raise RuntimeError(f'Cannot convert {repr(s)} to a Chamber')

    def __str__(self):
        return self.name.title()

    def __repr__(self):
        return self.__str__()


class Vote(IntEnum):
    YEA = 1
    NAY = 2
    NO_VOTE = 3
    LEAVE = 4


VOTE_CODES = bidict.bidict({
    'Y': Vote.YEA,
    'N': Vote.NAY,
    'X': Vote.NO_VOTE,
    'E': Vote.LEAVE,
})

Vote.from_letter = lambda s: VOTE_CODES[s]
Vote.to_letter = lambda v: VOTE_CODES.inverse[v]


class PALegislatureDB(MetroDB):
    def __init__(self):
        MetroDB.__init__(self, 'pa_legislature', enums_to_register=[
            Chamber,
            Vote,
        ])

    def get_crawl_statuses(self, rolls=None):
        day_total = collections.Counter()
        day_crawled = collections.Counter()
        session_id_to_key = {}

        days = {d['id']: d for d in self.query('SELECT * FROM session_days')}

        for session in self.query('SELECT year, chamber, id FROM sessions ORDER BY year'):
            key = session['year'], session['chamber']
            sid = session['id']
            session_id_to_key[sid] = key

        for day in days.values():
            key = session_id_to_key[day['session_id']]
            day_total[key] += 1
            if day['last_crawl']:
                day_crawled[key] += 1

        roll_total = collections.Counter()
        roll_crawled = collections.Counter()

        if rolls is None:
            rolls = {d['id']: d for d in self.query('SELECT * FROM roll_calls')}

        for roll in rolls.values():
            day = days[roll['day_id']]
            key = session_id_to_key[day['session_id']]
            roll_total[key] += 1
            if roll['last_crawl']:
                roll_crawled[key] += 1

        statuses = {}
        for key in day_total:
            if day_total[key] == 0 or roll_total[key] == 0:
                statuses[key] = None
            elif day_total[key] == day_crawled[key]:
                if roll_total[key] == roll_crawled[key]:
                    statuses[key] = 'complete'
                else:
                    statuses[key] = 'rolls missing'
            else:
                statuses[key] = 'days missing'
        return statuses
