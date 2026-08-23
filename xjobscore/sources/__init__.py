"""Source registration: one name, one function -- doubled, now, for
`harvest` and its downstream `parse`.

Registration is explicit. The two shared adapter modules use literal
hyphenated filenames, so this module loads them once by name. JSON API
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

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from functools import partial
from importlib import import_module

from xjobscore.net import Net
from xjobscore.records import Canonical, Feed, Item, Record
from xjobscore.sources import avature, dayforce, hn, join, taleo, zohorecruit
from xjobscore.sources.planned import janestreet, workatastartup

# A hyphenated filename cannot be imported with Python's ``import`` syntax.
# Keep the public adapter labels literal while exposing ordinary local aliases.
json_api = import_module(".json-api", __name__)
docxtract = import_module(".docxtract", __name__)

__all__ = [
    "PARSERS", "REVISION_FIELDS", "REVISION_REFRESH_WINDOWS", "SOURCES", "Harvest", "Parse",
    "UnknownSource", "docxtract", "get", "json_api", "parser", "reusing",
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


# Sources whose list response omits the fields `parse` needs, so every posting
# costs a second, per-item detail request. The tuple
# is what `json-api.py`'s revision check compares between a live list row and
# a stored detail payload: an unchanged value on both sides skips the detail
# request entirely. Opt-in and per-source on purpose -- these fields are
# measured against one vendor's real payloads (see the config's `detail`
# comment) and are never portable by assumption; a source not listed here
# gets today's unconditional-fetch behaviour from `reusing`, identical to
# calling `get` directly.
REVISION_FIELDS: dict[str, tuple[str, ...]] = {
    # Gem's list and detail operations share these complete metadata fields.
    # Description HTML exists only in detail, so the reuse is time-bounded
    # below: a forced refresh catches description-only edits.
    "gem": (
        "id",
        "title",
        "locations",
        "job.id",
        "job.department",
        "job.locationType",
        "job.employmentType",
    ),
    "smartrecruiters": ("jobAdId", "releasedDate"),
    # BambooHR's list row and its `result.jobOpening` detail payload
    # (config's `detail.payload`) share these scalar paths verbatim --
    # checked against a live tenant (prentkeromich, 13 postings). `location`
    # and `atsLocation` are deliberately excluded even though both sides have
    # them: the detail payload's dicts carry extra keys (postalCode,
    # addressCountry, countryId) the list row doesn't, so the two are never
    # `==`, which would silently disable reuse for every row. `employmentType`
    # is excluded too -- null on all 13 rows of the tenant probed, and
    # `_revision` treats any None field as "never reusable" for the whole
    # tuple, which would have disabled reuse tenant-wide. `location.city` /
    # `location.state` use dotted `_dig` paths to get the stable leaves
    # without the unstable dict wrapper.
    "bamboohr": (
        "jobOpeningName",
        "departmentId",
        "departmentLabel",
        "employmentStatusLabel",
        "locationType",
        "location.city",
        "location.state",
    ),
    # Rippling's list row is thin (uuid/name/department/url/workLocation) and
    # its detail response (config's `detail` has no `payload` key, so the
    # whole body is the stored payload) uses different shapes for the two
    # richer fields: list `department` is `{id, label}`, detail `department`
    # is `{name, base_department, department_tree}` -- never `==`. List
    # `workLocation` doesn't even exist at the detail root (`workLocations`,
    # plural, instead). Checked live (rippling/rippling, 734 postings): only
    # uuid/name/url are identical scalars on both sides, and never null. As
    # with Gem, this can't see a description-only edit, hence the bound below.
    "rippling": ("uuid", "name", "url"),
}

# Maximum age of a successful all-detail reconciliation. Sources absent from
# this table retain revision reuse indefinitely, matching the original
# SmartRecruiters behavior. Gem gets the same bounded-staleness contract as
# Workday's count probe: cheap polls within the window and a forced full poll
# when it expires.
REVISION_REFRESH_WINDOWS: dict[str, int] = {
    "gem": 86_400,
    # Neither vendor's revision fields see a description/compensation-only
    # edit (see the comments on REVISION_FIELDS above); this is the same
    # 24h reconciliation bound Gem uses to bound that blind spot.
    "bamboohr": 86_400,
    "rippling": 86_400,
    # jobAdId/releasedDate don't change on a description-only edit either;
    # was unbounded (the "original SmartRecruiters behavior" noted above),
    # which left such edits permanently stale.
    "smartrecruiters": 86_400,
}


def reusing(
    name: str,
    prior_items: Mapping[str, Item] | None,
    *,
    now: datetime | None = None,
) -> Harvest:
    """`get(name)`, plus revision-checked detail reuse when both are available.

    `prior_items` is the caller's newest stored payload per posting (see
    `router.prior_items`) -- deliberately a parameter, not a lookup this
    function performs itself, since only `run.poll` has the database
    connection `harvest` is barred from touching. Falls back to a plain
    `get(name)` -- byte-identical to production before this existed -- when
    the source hasn't opted in or the config isn't a JSON API adapter (the
    only engine that implements reuse). A time-bounded source with no prior
    payload performs and records a cold full reconciliation.
    """
    fields = REVISION_FIELDS.get(name)
    if not fields or name not in json_api.SPECS:
        return get(name)
    refresh_window = REVISION_REFRESH_WINDOWS.get(name)
    if refresh_window is not None:
        async def harvest(feed: Feed, net: Net) -> Record:
            poll_now = now or datetime.now(timezone.utc)
            last_full = feed.last_full_poll_at
            reuse_is_fresh = (
                prior_items is not None
                and last_full is not None
                and (poll_now - last_full).total_seconds() < refresh_window
            )
            return await json_api.harvest(
                name,
                feed,
                net,
                options=json_api.JsonApiHarvestOptions(
                    revision_fields=fields,
                    prior_items=prior_items if reuse_is_fresh else None,
                    force_full=not reuse_is_fresh,
                    now=poll_now,
                ),
            )

        return harvest
    if prior_items is None:
        return get(name)
    return partial(
        json_api.harvest,
        name,
        options=json_api.JsonApiHarvestOptions(revision_fields=fields, prior_items=prior_items),
    )


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
