"""The registry: one name, one function -- doubled, now, for `harvest` and
its downstream `parse`.

No entry points or dynamic import. JSON ATS names bind to the plain config
table in `ats_configs.py`; genuinely bespoke transports bind to focused
modules here. Both paths expose the same two call signatures, and nothing
else in the tree names a source.

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
from xjobscore.sources.catalog import CatalogEntry, entries as catalog_entries, feeds as catalog_feeds
from xjobscore.sources import (
    ats,
    inhouse,
    webboard,
)
from xjobscore.sources.planned import janestreet, workatastartup

__all__ = [
    "PARSERS", "SOURCES", "CatalogEntry", "Harvest", "Parse", "UnknownSource",
    "catalog_entries", "catalog_feeds", "get", "parser",
]

Harvest = Callable[[Feed, Net], Awaitable[Record]]
Parse = Callable[[Feed, Item, dict], list[Canonical]]


class UnknownSource(KeyError):
    """An enabled feed named a source with no module.

    An error, never a skip: a feed silently doing nothing looks identical to a
    board with no jobs, and the whole point of this table is to notice
    absence.
    """


# JSON ATS sources are bound from ats_configs.py. Bespoke formats keep one
# focused module exporting harvest and parse.
#
# Only implemented sources appear here. The rest of this package is still stubs
# (`raise NotImplementedError`) and stays unregistered on purpose -- a
# registered stub would raise from inside a poll instead of the clearer
# UnknownSource an unregistered name raises here.
SOURCES: dict[str, Harvest] = {
    **{name: ats.harvester(name) for name in ats.SPECS},
    "inhouse": inhouse.harvest,
    # Compatibility alias for feeds stored before the adapter rename.
    "house_ats": inhouse.harvest,
    "janestreet": janestreet.harvest,
    "workatastartup": workatastartup.harvest,
    **{name: webboard.harvester(name) for name in webboard.SPECS},
}

# Kept as a second dict, not folded into SOURCES, so a source can register a
# harvest without a parser (silver.py's replay pass simply cannot run for it
# yet) without every existing SOURCES lookup growing a tuple-unpack.
PARSERS: dict[str, Parse] = {
    **{name: ats.parser(name) for name in ats.SPECS},
    "inhouse": inhouse.parse,
    "house_ats": inhouse.parse,
    "janestreet": janestreet.parse,
    **{name: webboard.parser(name) for name in webboard.SPECS},
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
