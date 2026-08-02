"""The registry: one name, one function -- doubled, now, for `harvest` and
its downstream `parse`.

No base class, no Protocol, no entry points, no dynamic import -- there is
nothing for a source to inherit and therefore nothing it can get wrong except
its own functions. Adding a source is one import and two dict entries;
removing it is those lines in reverse, and nothing else in the tree names a
source.

    async def harvest(feed: Feed, net: Net) -> Record
    def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]

`harvest` may import `net` and `records`, nothing else -- no database, no
scheduling, no scoring, no sibling source. `parse` is stricter still: it is
sync and pure, reading only what `records` puts within reach. It never sees
`net` or a database connection; the replay pass (`silver.py`) that calls it
owns those.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from xjobscore.net import Net
from xjobscore.records import Canonical, Feed, Item, Record
from xjobscore.sources import (
    ashby,
    bamboohr,
    breezy,
    comeet,
    greenhouse,
    house_ats,
    icims,
    janestreet,
    jobvite,
    jsonld,
    lever,
    oracle,
    rippling,
    simplify,
    smartrecruiters,
    teamtailor,
    workable,
    workday,
)

__all__ = ["PARSERS", "SOURCES", "Harvest", "Parse", "UnknownSource", "get", "parser"]

Harvest = Callable[[Feed, Net], Awaitable[Record]]
Parse = Callable[[Feed, Item, dict], list[Canonical]]


class UnknownSource(KeyError):
    """An enabled feed named a source with no module.

    An error, never a skip: a feed silently doing nothing looks identical to a
    board with no jobs, and the whole point of this table is to notice
    absence.
    """


# Adding a source: write <name>.py exporting `harvest` and `parse`, import it
# here, add both entries below, and save one real response to
# fixtures/<name>.json.
#
# Only implemented sources appear here. The rest of this package is still stubs
# (`raise NotImplementedError`) and stays unregistered on purpose -- a
# registered stub would raise from inside a poll instead of the clearer
# UnknownSource an unregistered name raises here.
SOURCES: dict[str, Harvest] = {
    "ashby": ashby.harvest,
    "bamboohr": bamboohr.harvest,
    "breezy": breezy.harvest,
    "comeet": comeet.harvest,
    "greenhouse": greenhouse.harvest,
    "house_ats": house_ats.harvest,
    "icims": icims.harvest,
    "janestreet": janestreet.harvest,
    "jobvite": jobvite.harvest,
    "jsonld": jsonld.harvest,
    "lever": lever.harvest,
    "oracle": oracle.harvest,
    "rippling": rippling.harvest,
    "simplify": simplify.harvest,
    "smartrecruiters": smartrecruiters.harvest,
    "teamtailor": teamtailor.harvest,
    "workable": workable.harvest,
    "workday": workday.harvest,
}

# Kept as a second dict, not folded into SOURCES, so a source can register a
# harvest without a parser (silver.py's replay pass simply cannot run for it
# yet) without every existing SOURCES lookup growing a tuple-unpack.
PARSERS: dict[str, Parse] = {
    "ashby": ashby.parse,
    "bamboohr": bamboohr.parse,
    "breezy": breezy.parse,
    "comeet": comeet.parse,
    "greenhouse": greenhouse.parse,
    "house_ats": house_ats.parse,
    "icims": icims.parse,
    "janestreet": janestreet.parse,
    "jobvite": jobvite.parse,
    "jsonld": jsonld.parse,
    "lever": lever.parse,
    "oracle": oracle.parse,
    "rippling": rippling.parse,
    "simplify": simplify.parse,
    "smartrecruiters": smartrecruiters.parse,
    "teamtailor": teamtailor.parse,
    "workable": workable.parse,
    "workday": workday.parse,
}


def get(name: str) -> Harvest:
    """The harvest function for a source name, or raise UnknownSource."""
    try:
        return SOURCES[name]
    except KeyError:
        raise UnknownSource(name) from None


def parser(name: str) -> Parse:
    """The parse function for a source name, or raise UnknownSource."""
    try:
        return PARSERS[name]
    except KeyError:
        raise UnknownSource(name) from None
