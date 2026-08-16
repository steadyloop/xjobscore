"""The registry: one name, one function -- doubled, now, for `harvest` and
its downstream `parse`.

Registration is explicit. The two shared adapter modules use literal
hyphenated filenames, so this registry loads them once by name. JSON API
sources bind to the plain config table in `json-api-configs.py`; genuinely
bespoke transports bind to focused modules here. Both paths expose the same
two call signatures, and nothing else in the tree names a source.

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
from importlib import import_module

from xjobscore.net import Net
from xjobscore.records import Canonical, Feed, Item, Record
from xjobscore.sources import avature, dayforce, hn, join, taleo, zohorecruit
from xjobscore.sources.planned import janestreet, workatastartup
from xjobscore.sources.registry.loader import source_types

# A hyphenated filename cannot be imported with Python's ``import`` syntax.
# Keep the public adapter labels literal while exposing ordinary local aliases.
json_api = import_module(".json-api", __name__)
docxtract = import_module(".docxtract", __name__)

__all__ = [
    "PARSERS", "SOURCES", "Harvest", "Parse", "UnknownSource", "docxtract", "get", "json_api", "parser",
    "source_type",
]

Harvest = Callable[[Feed, Net], Awaitable[Record]]
Parse = Callable[[Feed, Item, dict], list[Canonical]]


class UnknownSource(KeyError):
    """An enabled feed named a source with no module.

    An error, never a skip: a feed silently doing nothing looks identical to a
    board with no jobs, and the whole point of this table is to notice
    absence.
    """


# JSON API sources are bound from json-api-configs.py. Bespoke formats keep one
# focused module exporting harvest and parse.
#
# Only implemented sources appear here. The rest of this package is still stubs
# (`raise NotImplementedError`) and stays unregistered on purpose -- a
# registered stub would raise from inside a poll instead of the clearer
# UnknownSource an unregistered name raises here.
SOURCES: dict[str, Harvest] = {
    **{name: json_api.harvester(name) for name in json_api.SPECS},
    "avature": avature.harvest,
    "dayforce": dayforce.harvest,
    "hn": hn.harvest,
    "join": join.harvest,
    "janestreet": janestreet.harvest,
    "taleo": taleo.harvest,
    "workatastartup": workatastartup.harvest,
    "zohorecruit": zohorecruit.harvest,
    **{name: docxtract.harvester(name) for name in docxtract.SPECS},
}

# Kept as a second dict, not folded into SOURCES, so a source can register a
# harvest without a parser (silver.py's replay pass simply cannot run for it
# yet) without every existing SOURCES lookup growing a tuple-unpack.
PARSERS: dict[str, Parse] = {
    **{name: json_api.parser(name) for name in json_api.SPECS},
    "avature": avature.parse,
    "dayforce": dayforce.parse,
    "hn": hn.parse,
    "join": join.parse,
    "janestreet": janestreet.parse,
    "taleo": taleo.parse,
    "zohorecruit": zohorecruit.parse,
    **{name: docxtract.parser(name) for name in docxtract.SPECS},
}


SOURCE_TYPES = source_types()


def source_type(source: str) -> str:
    """Stable source taxonomy declared in ``sources/registry/feeds.toml``."""
    return SOURCE_TYPES.get(source, "Other")


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
