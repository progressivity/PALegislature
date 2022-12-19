import bidict
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
