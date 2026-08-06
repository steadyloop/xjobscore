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
from pathlib import Path
import tomllib

from xjobscore.net import Net
from xjobscore.records import Canonical, Feed, Item, Record
from xjobscore.sources.catalog import CatalogEntry, entries as catalog_entries, feeds as catalog_feeds
from xjobscore.sources.planned import janestreet, workatastartup

# A hyphenated filename cannot be imported with Python's ``import`` syntax.
# Keep the public adapter labels literal while exposing ordinary local aliases.
json_api = import_module(".json-api", __name__)
docxtract = import_module(".docxtract", __name__)

__all__ = [
    "PARSERS", "SOURCES", "CatalogEntry", "Harvest", "Parse", "UnknownSource",
    "catalog_entries", "catalog_feeds", "docxtract", "get", "json_api", "parser",
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
    "janestreet": janestreet.harvest,
    "workatastartup": workatastartup.harvest,
    **{name: docxtract.harvester(name) for name in docxtract.SPECS},
}

# Kept as a second dict, not folded into SOURCES, so a source can register a
# harvest without a parser (silver.py's replay pass simply cannot run for it
# yet) without every existing SOURCES lookup growing a tuple-unpack.
PARSERS: dict[str, Parse] = {
    **{name: json_api.parser(name) for name in json_api.SPECS},
    "janestreet": janestreet.parse,
    **{name: docxtract.parser(name) for name in docxtract.SPECS},
}


def _source_types() -> dict[str, str]:
    """Read source taxonomy from the existing adapter manifests."""
    types: dict[str, str] = {}
    manifest_dir = Path(__file__).with_name("manifest")
    for path in sorted(manifest_dir.glob("*.toml")):
        with path.open("rb") as handle:
            manifest = tomllib.load(handle)
        default_type = manifest.get("type")
        runtime_source = manifest.get("runtime_source")
        legacy_source = manifest.get("legacy_runtime_source")
        if isinstance(default_type, str) and isinstance(runtime_source, str):
            types[runtime_source] = default_type
            if isinstance(legacy_source, str):
                types[legacy_source] = default_type
        for entry in manifest.get("source", []):
            if not isinstance(entry, dict):
                continue
            source = entry.get("runtime_source", runtime_source)
            source_type = entry.get("type", default_type)
            if isinstance(source, str) and isinstance(source_type, str):
                types[source] = source_type
    return types


SOURCE_TYPES = _source_types()


def source_type(source: str) -> str:
    """Stable source taxonomy declared in ``sources/manifest/*.toml``."""
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
