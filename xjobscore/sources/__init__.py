"""Source registration: one name, one function -- doubled, now, for
`harvest` and its downstream `parse`.

Registration is explicit. JSON API
sources bind to the plain config table in `json_api_configs.py`; genuinely
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

import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from functools import partial
from urllib.parse import urlsplit

from xjobscore import net as net_module
from xjobscore.net import Net
from xjobscore.records import Canonical, Feed, Item, Record
from xjobscore.source_policy import require_source_allowed
from xjobscore.sources import (
    avature, dayforce, docxtract, hn, janestreet, join, json_api, taleo,
    workatastartup, zohorecruit,
)

__all__ = [
    "PARSERS", "REVISION_FIELDS", "REVISION_REFRESH_WINDOWS", "SOURCES", "Harvest", "Parse",
    "UnknownSource", "docxtract", "get", "json_api", "lane_filter", "lane_of", "lanes",
    "parser", "partition_index", "partition_lane", "partitions", "rate_budget", "rate_group", "reusing", "width",
]

Harvest = Callable[[Feed, Net], Awaitable[Record]]
Parse = Callable[[Feed, Item, dict], list[Canonical]]


class UnknownSource(KeyError):
    """An enabled feed named a source with no module.

    An error, never a skip: a feed silently doing nothing looks identical to a
    board with no jobs, and the whole point of this table is to notice
    absence.
    """


# JSON API sources are bound from json_api_configs.py. Bespoke formats keep one
# focused module exporting harvest and parse.
#
# Only implemented sources appear here; unknown names raise UnknownSource.
SOURCES: dict[str, Harvest] = {
    **{name: json_api.harvester(name) for name in json_api.CONFIGS},
    "avature": avature.harvest,
    "dayforce": dayforce.harvest,
    "hn": hn.harvest,
    "join": join.harvest,
    "janestreet": janestreet.harvest,
    "taleo": taleo.harvest,
    "workatastartup": workatastartup.harvest,
    "zohorecruit": zohorecruit.harvest,
    **{name: docxtract.harvester(name) for name in docxtract.CONFIGS},
}

# Kept as a second dict, not folded into SOURCES, so a source can register a
# harvest without a parser (silver.py's replay pass simply cannot run for it
# yet) without every existing SOURCES lookup growing a tuple-unpack.
PARSERS: dict[str, Parse] = {
    **{name: json_api.parser(name) for name in json_api.CONFIGS},
    "avature": avature.parse,
    "dayforce": dayforce.parse,
    "hn": hn.parse,
    "join": join.parse,
    "janestreet": janestreet.parse,
    "taleo": taleo.parse,
    "zohorecruit": zohorecruit.parse,
    **{name: docxtract.parser(name) for name in docxtract.CONFIGS},
}


# Sources whose list response omits the fields `parse` needs, so every posting
# costs a second, per-item detail request. The tuple
# is what `json_api.py`'s revision check compares between a live list row and
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
    # Workday's detail payload is merged over its list row (config `merge`),
    # so the stored payload carries these list keys verbatim beside
    # jobPostingInfo/hiringOrganization/similarJobs/userAuthenticated, which
    # never collide with them -- checked over 3,000 stored merged payloads
    # (2026-10-05). `locationsText` is on 97% of them; a row without it is
    # never reusable and simply refetches. `postedOn` is excluded: it is a
    # relative bucket ("Posted 3 Days Ago") that moves daily on its own.
    # Without reuse, every full listing refetched every gated detail -- ~2,800
    # requests per 15-minute round of the fast Workday feeds.
    "workday": ("externalPath", "title", "locationsText"),
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
    # Same bound, and the same window as the config's `count_probe`, so one
    # forced pass a day both re-lists the board and refetches every detail.
    "workday": 86_400,
}


# Host *shape* of a source's list endpoint, for the sources whose own config
# cannot say. Two facts are read off these and nothing else: whether the host
# is per-tenant (a `{placeholder}` in it) and which `net._VENDOR_CAPS` suffix
# binds it. No width is ever written here -- `width()` derives those from each
# adapter's own concurrency constant, so this table cannot drift one.
_HOST_TEMPLATES: dict[str, str] = {
    "eightfold": "{host}.eightfold.ai",  # config's list netloc is the bare `{host}`
    "taleo": "{key}.taleo.net",          # real host comes from feed.config["url"]
    "avature": "{key}.avature.net",
    "join": "join.com",
    "zohorecruit": "{key}.zohorecruit.com",
    "dayforce": "jobs.dayforcehcm.com",
    "hn": "hn.algolia.com",
    "janestreet": "www.janestreet.com",
    "workatastartup": "www.workatastartup.com",
}

# Per-feed request fan-out of the bespoke adapters, read from each module's own
# constant so a retune there is a retune here.
_BESPOKE_FAN_OUT: dict[str, Callable[[], int]] = {
    "taleo": lambda: taleo._CONCURRENCY,
    "avature": lambda: avature.CONCURRENCY,
    "join": lambda: join.CONCURRENCY,
}


def _config(name: str) -> dict:
    return json_api.CONFIGS.get(name) or docxtract.CONFIGS.get(name) or {}


def _list_host_template(name: str) -> str:
    """Netloc of this source's list URL template ("" when unknown)."""
    if name in _HOST_TEMPLATES:
        return _HOST_TEMPLATES[name]
    return urlsplit(_config(name).get("list", {}).get("url", "")).netloc


def _fan_out(name: str) -> int:
    """Most requests one poll of this source keeps in flight at once."""
    if name in _BESPOKE_FAN_OUT:
        return _BESPOKE_FAN_OUT[name]()
    config = _config(name)
    listing = config.get("list", {})
    return max(
        1,
        listing.get("concurrency", 1),
        listing.get("page", {}).get("concurrency", 1),  # `page` nests inside `list`
        config.get("detail", {}).get("concurrency", 1),
        config.get("detail_concurrency", 1),            # docxtract's spelling
    )


def width(name: str) -> int:
    """How many feeds of `name` one polling cycle may have in flight.

    Derived, never declared: the number is whatever this source's own
    concurrency and `net`'s vendor cap already leave room for. A second copy
    of a width is how the inspector once measured 8 while production ran 4
    (see `inspector/service.py`), and here the cost would be worse than a
    wrong number on a page -- workers wider than the cap sit holding feed
    leases while queued on a semaphore.

    - Shared host (one netloc for every tenant): every feed contends on the
      list leg, so that leg's width is the vendor's whole budget. Without one
      the source is on `Net._throttle`'s serialized 1 req/s path, where extra
      workers only queue, so the answer is 1.
    - Per-tenant host: the per-host semaphore does not bind across feeds, and
      `_VENDOR_CAPS` is the only cross-feed ceiling -- so the pool is the cap
      divided by what one poll itself keeps in flight. No cap means
      unmeasured, and unmeasured is 1, not unlimited.
    - An unregistered name is 1; `poll` raising `UnknownSource` is where that
      failure belongs, not here.
    """
    source = name.partition(LANE_SEP)[0]
    host = _list_host_template(source)
    if host and "{" not in host:
        return max(1, _config(source).get("list", {}).get("concurrency", 1))
    cap = net_module.vendor_cap(_lane_host(name)) if host else None
    if cap is None:
        return 1
    return max(1, cap // _fan_out(source))


def _lane_host(name: str) -> str:
    """A concrete hostname standing for every host of a source or lane.

    A lane's own partition is filled in, so the caps and budgets it resolves
    to are that partition's; every other placeholder becomes `x`, which no
    caps-table suffix names.
    """
    source, _, partition = name.partition(LANE_SEP)
    host = _list_host_template(source)
    if partition and partition != OTHER_PARTITION:
        host = host.replace("{" + _PARTITION_PARTS[source] + "}", partition)
    return re.sub(r"\{[^}]*\}", "x", host)


def rate_group(name: str) -> str | None:
    """The `net._VENDOR_RATES` budget a source or lane spends, if any."""
    host = _list_host_template(name.partition(LANE_SEP)[0])
    return net_module.rate_group(_lane_host(name)) if host else None


def rate_budget(name: str) -> float | None:
    """Sustained req/s of the budget `rate_group(name)` names, if any."""
    group = rate_group(name)
    return None if group is None else net_module._VENDOR_RATES[group][0]


# LANES -----------------------------------------------------------------------
#
# A lane is one scheduler queue with its own worker pool. Most sources are one
# lane, named after the source. A source whose limiter counts per part of the
# feed key rather than per vendor -- Workday, per data center (`wd_host`) --
# gets one lane per partition that has its own `net._VENDOR_RATES` budget,
# named `source:partition`, plus `source:other` for every partition sharing
# the vendor's fallback budget. Separate lanes are what let wd3 feeds use
# wd3's budget while wd1's queue is still draining; one pool would park every
# worker on whichever budget its oldest feeds happen to need.
#
# The partitions are read off `_VENDOR_RATES`, never listed here, so adding a
# measured data center there adds its lane.

LANE_SEP = ":"
OTHER_PARTITION = "other"

# Source -> the `key_parts` name its limiter partitions on.
_PARTITION_PARTS: dict[str, str] = {"workday": "wd_host"}


def partitions(source: str) -> tuple[str, ...]:
    """Partitions of `source` with their own rate budget; () if unpartitioned."""
    part = _PARTITION_PARTS.get(source)
    if part is None:
        return ()
    _, _, suffix = _list_host_template(source).partition("{" + part + "}.")
    found = []
    for key in net_module._VENDOR_RATES:
        label = key.removesuffix("." + suffix)
        if label != key and "." not in label:
            found.append(label)
    return tuple(sorted(found))


def partition_index(source: str) -> int:
    """1-based position of `source`'s partition in its '/'-separated key."""
    return _config(source)["key_parts"].index(_PARTITION_PARTS[source]) + 1


def lanes(source: str) -> list[str]:
    """Every lane of `source`, in a stable order."""
    named = partitions(source)
    if not named:
        return [source]
    return [f"{source}{LANE_SEP}{part}" for part in (*named, OTHER_PARTITION)]


def lane_of(source: str, key: str) -> str:
    """The lane a feed of `source` with this key is polled from."""
    if not partitions(source):
        return source
    parts = key.split("/")
    index = partition_index(source) - 1
    return partition_lane(source, parts[index] if index < len(parts) else "")


def partition_lane(source: str, value: str) -> str:
    """The lane of `source` that partition `value` is polled from."""
    value = value.lower()
    named = partitions(source)
    return f"{source}{LANE_SEP}{value if value in named else OTHER_PARTITION}"


def lane_filter(lane: str) -> tuple[str, tuple[int, tuple[str, ...], bool] | None]:
    """`(source, part)` for `store.claim_due_feeds`; part is None for a whole source."""
    source, _, partition = lane.partition(LANE_SEP)
    if not partition:
        return source, None
    index = partition_index(source)
    if partition == OTHER_PARTITION:
        return source, (index, partitions(source), True)
    return source, (index, (partition,), False)


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
    require_source_allowed(name)
    fields = REVISION_FIELDS.get(name)
    if not fields or name not in json_api.CONFIGS:
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
    require_source_allowed(name)
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
