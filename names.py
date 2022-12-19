import click
from nameparser import HumanName
from nicknames import NickNamer

NAME_FIELDS = ['first', 'middle', 'last', 'suffix']
LONG_NAMES = ['Michael', 'Timothy', 'Christopher', 'Robert', 'Thomas']

nn = NickNamer()


def dict_to_name(d):
    hn = HumanName()
    for field in NAME_FIELDS:
        if d[field]:
            setattr(hn, field, d[field])
    return hn


def name_tuple(obj):
    if isinstance(obj, dict):
        return tuple(obj[k] for k in NAME_FIELDS)
    elif isinstance(obj, str):
        obj = HumanName(obj)
    return tuple(getattr(obj, k) for k in NAME_FIELDS)


def from_tuple(t):
    hn = HumanName()
    for key, value in zip(NAME_FIELDS, t):
        if value:
            setattr(hn, key, value)
    return hn


def is_nickname_of(first1, first2):
    if (first1, first2) in [('Tommy', 'Thomas'), ('Stan', 'Stanley')]:
        return True
    if first1.lower() in nn.nicknames_of(first2):
        if first2.lower() in nn.nicknames_of(first1):
            if first2 in LONG_NAMES:
                return True
            elif first1 in LONG_NAMES:
                return False
            else:
                click.secho(f'Ambiguous Nicknames: {first1} vs. {first2}', fg='red')
                exit(-1)
        else:
            return True
    return False


def resolve_initial(name1, name2):
    if not name1 or not name2:
        return
    if name1[0] != name2[0]:
        return
    elif len(name1) < len(name2):
        short = name1
        long = name2
    else:
        short = name2
        long = name1

    if len(short) == 2 and short[1] == '.' and len(long) > 2:
        return long
    elif len(short) == 1 and len(long) > 1:
        return long


def match_middle(middle1, middle2):
    if middle1 and not middle2:
        return middle1
    elif middle2 and not middle1:
        return middle2
    elif not middle1 and not middle2:
        return middle1
    elif middle1 == middle2:
        return middle1

    middle0 = resolve_initial(middle1, middle2)
    if middle0:
        return middle0
    elif middle1[0] == middle2[0]:
        click.secho(f'Unable to match middle names: {middle1} vs. {middle2}', fg='red')


def resolve_first_middle(name1, name2, recurse=True):
    if len(name1.first) == 2 and name1.first[1] == '.' and name1.middle and not name2.middle:
        if resolve_initial(name2.first, name1.middle):
            return name1

    if recurse:
        return resolve_first_middle(name2, name1, False)


def is_same_name(name1, name2, require_suffix=True):
    hn = HumanName()
    if name1.last == name2.last:
        hn.last = name1.last
    elif name1.last.title() == name2.last.title():
        if name1.last.title() == name1.last:
            hn.last = name2.last
        elif name2.last.title() == name2.last:
            hn.last = name1.last
        else:
            return
    else:
        return

    if not name1.first and not name1.middle and not name1.suffix:
        return name2

    if name1.first == name2.first:
        hn.first = name1.first
    elif is_nickname_of(name1.first, name2.first):
        hn.first = name2.first
    elif is_nickname_of(name2.first, name1.first):
        hn.first = name1.first
    else:
        first0 = resolve_initial(name1.first, name2.first)
        if first0:
            hn.first = first0
        else:
            fscott = resolve_first_middle(name1, name2)
            if fscott:
                hn.first = fscott.first
                hn.middle = fscott.middle
            else:
                return

    if not hn.middle and (name1.middle or name2.middle):
        middle = match_middle(name1.middle, name2.middle)
        if middle is None:
            return
        hn.middle = middle

    if name1.suffix == name2.suffix:
        hn.suffix = name1.suffix
        return hn
    elif require_suffix:
        return
    elif name1.suffix and not name2.suffix:
        hn.suffix = name1.suffix
        return hn
    elif name2.suffix and not name1.suffix:
        hn.suffix = name2.suffix
        return hn

    click.secho(f'Unable to resolve suffixes: {name1.suffix} vs {name2.suffix}', fg='yellow')
