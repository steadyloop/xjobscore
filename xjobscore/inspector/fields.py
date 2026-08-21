"""Read-only field recon over ``public.raw_posting`` for the Fields page.

Telemetry-style: every function borrows the pipeline pool (see
``PolicyStore.pool_or_none``) and opens nothing of its own.

The shape of this module is dictated by one measurement. ``raw_posting``'s
heap is small (~69 MB) but ``data`` is over a gigabyte of TOAST, so the cost
of every question here is detoasting payloads, not finding rows -- no index
changes that, which is why none is proposed. On the live database a full
depth-3 path walk of the largest adapter took **108 s**, while the same walk
over a 2,000-payload sample took **2 s** and an exact histogram of one chosen
path took **4 s**. Hence the split:

* ``survey`` is **sampled** and says so. It answers "what fields exist".
* ``values`` is **exact** and full-scan. It answers "what values does this
  one field hold", which is the question worth waiting for.

Both start from ``picked``: a heap-only ``DISTINCT ON`` that resolves the
latest revision per posting while touching no jsonb at all, so sampling
chooses *before* anything is detoasted. The sample is drawn by hashed id
rather than by ``LIMIT`` on the ``DISTINCT ON`` -- a bare limit would take
whichever tenants sort first alphabetically and report one company's schema
as the adapter's.
"""

from __future__ import annotations

import re
import time
from typing import Any

from xjobscore.inspector.query import _cell

SAMPLE_SIZES = (500, 2_000, 5_000, 20_000)
DEFAULT_SAMPLE = 2_000
MAX_DEPTH = 4
DEFAULT_DEPTH = 3
VALUE_LIMIT = 200
MAX_VALUE_LIMIT = 1_000
EXAMPLE_LIMIT = 5
# Longest path the compiler will accept, counting keys and array hops. The
# survey never emits more than DEPTH segments; the cap is a guard on the
# string arriving from the browser.
MAX_SEGMENTS = 12
# Sampled work is bounded by construction; the exact histogram is not, and
# the slowest measured real path took 4.4 s. Both are well under these.
SURVEY_TIMEOUT_MS = 20_000
VALUES_TIMEOUT_MS = 60_000
ADAPTERS_TTL_S = 300.0

_ADAPTER_RE = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
_FEED_RE = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}:[^\s]{1,256}\Z")
# One dot-separated part: a key, then any number of "[]" array hops.
_SEGMENT_RE = re.compile(r"\A([^.\[\]]+)((?:\[\])*)\Z")
# Advisory, not the safety boundary -- keys reach SQL as bind parameters, so
# this exists to turn a typo into a 400 rather than an empty result set.
_KEY_RE = re.compile(r"\A[A-Za-z0-9_ @:/+.À-￿-]{1,128}\Z")


def _validate_adapter(adapter: str) -> str:
    if not _ADAPTER_RE.match(adapter or ""):
        raise ValueError(f"Not an adapter name: {adapter!r}")
    return adapter


def _validate_feed(feed: str | None, adapter: str) -> str | None:
    if feed in (None, ""):
        return None
    if not _FEED_RE.match(feed) or feed.split(":", 1)[0] != adapter:
        raise ValueError(f"Not a feed of {adapter}: {feed!r}")
    return feed


def _validate_sample(sample: int) -> int:
    if sample not in SAMPLE_SIZES:
        raise ValueError(f"sample must be one of {SAMPLE_SIZES}, got {sample}")
    return sample


def _validate_depth(depth: int) -> int:
    if not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be 1..{MAX_DEPTH}, got {depth}")
    return depth


def parse_path(path: str) -> list[list[str]]:
    """Split survey notation into the key groups an array hop separates.

    ``departments[].name`` -> ``[["departments"], ["name"]]``: dig to
    ``departments``, expand it, then read ``name`` on each element. A path
    with no ``[]`` is one group; ``a[]`` is ``[["a"], []]``, the elements
    themselves. Always one more group than there are array hops, so the
    compiler below can emit exactly ``len(groups) - 1`` laterals.
    """
    if not path or not path.strip():
        raise ValueError("Path is empty.")
    groups: list[list[str]] = [[]]
    segments = 0
    for part in path.split("."):
        match = _SEGMENT_RE.match(part)
        if match is None:
            raise ValueError(f"Bad path segment: {part!r}")
        key, arrays = match.group(1), match.group(2)
        if not _KEY_RE.match(key):
            raise ValueError(f"Bad path key: {key!r}")
        groups[-1].append(key)
        segments += 1 + len(arrays) // 2
        groups.extend([] for _ in range(len(arrays) // 2))
    if segments > MAX_SEGMENTS:
        raise ValueError(f"Path is too deep (max {MAX_SEGMENTS} segments).")
    return groups


def _compile_path(groups: list[list[str]], params: list[Any]) -> tuple[str, str]:
    """Emit the LATERAL chain and leaf expression for one parsed path.

    Appends each key group to ``params`` and refers to it by ordinal, so no
    part of the caller's path is ever interpolated into SQL -- only the
    *number* of laterals, which comes from the parse, not from the text.
    ``LEFT JOIN ... ON TRUE`` rather than ``CROSS JOIN``: a posting whose
    array is empty or absent must still be counted, as the absence of a value
    is itself an answer during recon.
    """
    laterals: list[str] = []
    node = "latest.p"
    for index, keys in enumerate(groups[:-1]):
        params.append(keys)
        ordinal = len(params)
        dug = f"{node} #> ${ordinal}::text[]"
        alias = f"e{index}"
        laterals.append(
            f"LEFT JOIN LATERAL jsonb_array_elements("
            f"CASE WHEN jsonb_typeof({dug}) = 'array' THEN {dug} "
            f"ELSE '[]'::jsonb END) AS {alias}(value) ON TRUE"
        )
        node = f"{alias}.value"
    params.append(groups[-1])
    return "\n".join(laterals), f"{node} #>> ${len(params)}::text[]"


# Latest revision per posting, resolved without touching `data`: the columns
# in play are all inline, so this stays a heap scan and never detoasts.
_PICKED = """
picked AS (
    SELECT DISTINCT ON (source, source_key) id
    FROM raw_posting
    WHERE source LIKE $1 || ':%' AND source = COALESCE($2, source)
    ORDER BY source, source_key, fetched_at DESC
)
"""

# Deliberately free of any `data->>` read: dereferencing the jsonb detoasts
# every payload, which measured 13.7 s here against 1.9 s without it.
# pg_column_size is safe -- it reads the TOAST pointer, not the value.
_ADAPTERS_SQL = """
SELECT split_part(source, ':', 1) AS adapter,
       count(*) AS revisions,
       count(DISTINCT source || '/' || source_key) AS postings,
       count(DISTINCT source) AS feeds,
       sum(pg_column_size(data))::bigint AS payload_bytes,
       max(fetched_at) AS last_fetched_at
FROM raw_posting
GROUP BY 1
ORDER BY 3 DESC
"""

# The payload kind, sampled one row per adapter. Same shape as the survey's
# sampling: resolve ids while the jsonb is untouched, then fetch those few by
# primary key. Reading `kind` inline in the aggregate above costs 6.3 s;
# this costs 0.3 s.
_KINDS_SQL = """
WITH first_ids AS (
    SELECT DISTINCT ON (split_part(source, ':', 1))
           split_part(source, ':', 1) AS adapter, id
    FROM raw_posting
    ORDER BY 1, id
)
SELECT f.adapter, r.data->>'kind' AS kind
FROM first_ids AS f JOIN raw_posting AS r ON r.id = f.id
"""

_FEEDS_SQL = """
SELECT source, count(DISTINCT source_key) AS postings, max(fetched_at) AS last_fetched_at
FROM raw_posting
WHERE source LIKE $1 || ':%'
GROUP BY 1
ORDER BY 2 DESC, 1
LIMIT 500
"""

_POSTINGS_SQL = """
SELECT count(*) FROM (
    SELECT DISTINCT source, source_key FROM raw_posting
    WHERE source LIKE $1 || ':%' AND source = COALESCE($2, source)
) AS s
"""

# The recursive walk. PostgreSQL allows exactly one reference to the
# recursive term, so the object and array branches are folded into a single
# CROSS JOIN LATERAL rather than two UNION arms. The CASE guards are load
# bearing: a WHERE clause does not stop jsonb_each from being applied to a
# non-object, it only discards the error's row afterwards -- too late.
_SURVEY_SQL = """
WITH RECURSIVE {picked},
sampled AS (
    SELECT id FROM picked ORDER BY md5($3 || id::text) LIMIT $4
),
latest AS (
    SELECT r.id, r.data->'payload' AS p
    FROM raw_posting AS r JOIN sampled AS s ON s.id = r.id
),
walk AS (
    SELECT id, ''::text AS path, p AS node, 0 AS depth FROM latest
    UNION ALL
    SELECT w.id, w.path || c.seg, c.val, w.depth + 1
    FROM walk AS w
    CROSS JOIN LATERAL (
        SELECT '.' || e.key AS seg, e.value AS val
        FROM jsonb_each(CASE WHEN jsonb_typeof(w.node) = 'object'
                             THEN w.node ELSE '{{}}'::jsonb END) AS e
        UNION ALL
        SELECT '[]', a.value
        FROM jsonb_array_elements(CASE WHEN jsonb_typeof(w.node) = 'array'
                                       THEN w.node ELSE '[]'::jsonb END) AS a
    ) AS c
    WHERE w.depth < $5
)
SELECT ltrim(path, '.') AS path,
       count(*) AS occurrences,
       count(DISTINCT id) AS postings,
       count(*) FILTER (
           WHERE jsonb_typeof(node) <> 'null'
             AND node::text NOT IN ('""', '[]', '{{}}')
       ) AS filled,
       count(DISTINCT node) AS distinct_values,
       array_agg(DISTINCT jsonb_typeof(node)) AS kinds,
       (array_agg(node) FILTER (WHERE jsonb_typeof(node) NOT IN ('object', 'array', 'null')))[1]
           AS example
FROM walk
WHERE depth > 0
GROUP BY 1
ORDER BY 3 DESC, 2 DESC, 1
"""

_SAMPLE_SIZE_SQL = f"WITH {_PICKED}, sampled AS (SELECT id FROM picked ORDER BY md5($3 || id::text) LIMIT $4) SELECT count(*) FROM sampled"


def _shape_survey(records: list[Any], sampled: int) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        occurrences = record["occurrences"]
        rows.append(
            {
                "path": record["path"],
                "occurrences": occurrences,
                "postings": record["postings"],
                "filled": record["filled"],
                "fill": (record["filled"] / occurrences) if occurrences else 0.0,
                "coverage": (record["postings"] / sampled) if sampled else 0.0,
                "distinct_values": record["distinct_values"],
                "kinds": sorted(record["kinds"] or []),
                "example": _cell(record["example"]),
                "leaf": not ({"object", "array"} & set(record["kinds"] or [])),
            }
        )
    return rows


_cache: dict[str, tuple[float, Any]] = {}


async def adapters(pool: Any, *, force: bool = False) -> dict[str, Any]:
    """Every adapter present in bronze, with size and payload kind.

    Cached for five minutes: it is a full aggregate over the table (~0.9 s
    measured) and its answer changes only when a poll lands.
    """
    cached = _cache.get("adapters")
    if cached is not None and not force and time.monotonic() - cached[0] < ADAPTERS_TTL_S:
        return cached[1]
    async with pool.acquire() as conn:
        records = await conn.fetch(_ADAPTERS_SQL)
        kinds = {r["adapter"]: r["kind"] for r in await conn.fetch(_KINDS_SQL)}
    rows = [
        {
            "adapter": record["adapter"],
            "revisions": record["revisions"],
            "postings": record["postings"],
            "feeds": record["feeds"],
            "payload_bytes": record["payload_bytes"],
            # Sampled from one row, so it names the adapter's transport, not
            # a guarantee about every row. Every adapter in bronze today is
            # uniform in kind.
            "kind": kinds.get(record["adapter"]),
            # Only a JSON payload has a field tree to walk; xml/html payloads
            # are a document in a string, and the page must say so instead of
            # rendering an empty survey.
            "surveyable": kinds.get(record["adapter"]) == "json",
            "last_fetched_at": _cell(record["last_fetched_at"]),
        }
        for record in records
    ]
    payload = {"adapters": rows}
    _cache["adapters"] = (time.monotonic(), payload)
    return payload


async def feeds(pool: Any, adapter: str) -> dict[str, Any]:
    """The feed instances (``adapter:tenant``) bronze holds for one adapter."""
    adapter = _validate_adapter(adapter)
    async with pool.acquire() as conn:
        records = await conn.fetch(_FEEDS_SQL, adapter)
    return {
        "adapter": adapter,
        "feeds": [
            {
                "feed": record["source"],
                "postings": record["postings"],
                "last_fetched_at": _cell(record["last_fetched_at"]),
            }
            for record in records
        ],
    }


async def survey(
    pool: Any,
    adapter: str,
    *,
    feed: str | None = None,
    sample: int = DEFAULT_SAMPLE,
    depth: int = DEFAULT_DEPTH,
    seed: str = "",
) -> dict[str, Any]:
    """Approximate path census for one adapter, from a payload sample.

    Every number here is of the sample, never of the adapter, and the
    response says so in ``exact: False`` and ``sample``. The caller is
    expected to surface that -- a sampled distinct count presented as a fact
    is the one way this page could mislead.
    """
    adapter = _validate_adapter(adapter)
    feed = _validate_feed(feed, adapter)
    sample = _validate_sample(sample)
    depth = _validate_depth(depth)

    sql = _SURVEY_SQL.format(picked=_PICKED.strip())
    params = [adapter, feed, seed, sample, depth]
    started = time.perf_counter()
    async with pool.acquire() as conn:
        transaction = conn.transaction(readonly=True)
        await transaction.start()
        try:
            await conn.execute(f"SET LOCAL statement_timeout = {SURVEY_TIMEOUT_MS}")
            sampled = await conn.fetchval(_SAMPLE_SIZE_SQL, *params[:4])
            records = await conn.fetch(sql, *params)
        finally:
            await transaction.rollback()
    elapsed_ms = (time.perf_counter() - started) * 1000

    return {
        "adapter": adapter,
        "feed": feed,
        "exact": False,
        "sample": sample,
        "sampled": sampled or 0,
        "depth": depth,
        "seed": seed,
        "elapsed_ms": elapsed_ms,
        "paths": _shape_survey(list(records), sampled or 0),
    }


async def values(
    pool: Any,
    adapter: str,
    path: str,
    *,
    feed: str | None = None,
    limit: int = VALUE_LIMIT,
) -> dict[str, Any]:
    """Exact value histogram for one path across every current posting.

    No sampling: this is the answer the survey exists to point at, and at
    4 s on the largest adapter it is affordable to be right. ``total`` and
    ``distinct_values`` come from window aggregates so they stay exact even
    when ``limit`` truncates the list.
    """
    adapter = _validate_adapter(adapter)
    feed = _validate_feed(feed, adapter)
    if not 1 <= limit <= MAX_VALUE_LIMIT:
        raise ValueError(f"limit must be 1..{MAX_VALUE_LIMIT}, got {limit}")
    groups = parse_path(path)

    params: list[Any] = [adapter, feed]
    laterals, leaf = _compile_path(groups, params)
    params.append(limit)
    sql = f"""
WITH {_PICKED.strip()},
latest AS (
    SELECT r.data->'payload' AS p
    FROM raw_posting AS r JOIN picked AS s ON s.id = r.id
)
SELECT {leaf} AS value,
       count(*) AS n,
       sum(count(*)) OVER () AS total,
       count(*) OVER () AS distinct_values
FROM latest
{laterals}
GROUP BY 1
ORDER BY 2 DESC, 1
LIMIT ${len(params)}
"""
    started = time.perf_counter()
    async with pool.acquire() as conn:
        transaction = conn.transaction(readonly=True)
        await transaction.start()
        try:
            await conn.execute(f"SET LOCAL statement_timeout = {VALUES_TIMEOUT_MS}")
            postings = await conn.fetchval(_POSTINGS_SQL, adapter, feed)
            records = list(await conn.fetch(sql, *params))
        finally:
            await transaction.rollback()
    elapsed_ms = (time.perf_counter() - started) * 1000

    total = int(records[0]["total"]) if records else 0
    return {
        "adapter": adapter,
        "feed": feed,
        "path": path,
        "exact": True,
        "postings": postings or 0,
        "total": total,
        "distinct_values": records[0]["distinct_values"] if records else 0,
        "truncated": bool(records) and records[0]["distinct_values"] > limit,
        "limit": limit,
        "elapsed_ms": elapsed_ms,
        "values": [
            {
                "value": _cell(record["value"]),
                "count": record["n"],
                "share": (record["n"] / total) if total else 0.0,
            }
            for record in records
        ],
    }


async def examples(
    pool: Any,
    adapter: str,
    path: str,
    value: str | None,
    *,
    feed: str | None = None,
    limit: int = EXAMPLE_LIMIT,
) -> dict[str, Any]:
    """A few whole raw payloads whose ``path`` holds ``value``.

    ``value`` of ``None`` means the postings where the path is absent or
    null, which is why the predicate is ``IS NOT DISTINCT FROM`` rather than
    ``=``: "show me the ones missing this" is a normal recon question.
    """
    adapter = _validate_adapter(adapter)
    feed = _validate_feed(feed, adapter)
    if not 1 <= limit <= EXAMPLE_LIMIT:
        raise ValueError(f"limit must be 1..{EXAMPLE_LIMIT}, got {limit}")
    groups = parse_path(path)

    params: list[Any] = [adapter, feed]
    laterals, leaf = _compile_path(groups, params)
    params.append(value)
    predicate_ordinal = len(params)
    params.append(limit)
    sql = f"""
WITH {_PICKED.strip()},
latest AS (
    SELECT r.id, r.source, r.source_key, r.fetched_at, r.data->'payload' AS p
    FROM raw_posting AS r JOIN picked AS s ON s.id = r.id
)
SELECT DISTINCT ON (latest.id)
       latest.id, latest.source, latest.source_key, latest.fetched_at, latest.p
FROM latest
{laterals}
WHERE {leaf} IS NOT DISTINCT FROM ${predicate_ordinal}::text
ORDER BY latest.id
LIMIT ${len(params)}
"""
    async with pool.acquire() as conn:
        transaction = conn.transaction(readonly=True)
        await transaction.start()
        try:
            await conn.execute(f"SET LOCAL statement_timeout = {VALUES_TIMEOUT_MS}")
            records = list(await conn.fetch(sql, *params))
        finally:
            await transaction.rollback()
    return {
        "adapter": adapter,
        "path": path,
        "value": value,
        "examples": [
            {
                "id": str(record["id"]),
                "source": record["source"],
                "source_key": record["source_key"],
                "fetched_at": _cell(record["fetched_at"]),
                "payload": _cell(record["p"]),
            }
            for record in records
        ],
    }
