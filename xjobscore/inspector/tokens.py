"""Queries and operator-note edits over ``public.token`` for the Tokens page.

``token`` is the full inventory of every ATS board key ever seen -- live or
dead -- while ``feed`` is the curated set the scheduler actually polls. The
two are joined loosely on ``(ats, token) = (feed.source, feed.key)``; there is
no foreign key between them, which is exactly why a token can exist with no
feed row. Those rows are the promotion backlog this page exists to work.

Telemetry-style: every function borrows the
pipeline pool (see ``PolicyStore.pool_or_none``) and opens nothing of its own.
Untrusted text only ever arrives as a bind parameter; the only values
interpolated into SQL are pre-validated enum keys.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from xjobscore import sources
from xjobscore.source_policy import require_source_allowed, source_disabled, visible_source_sql

# These are the operator-facing views for the token table.  Keep the tuple in
# the same order as the status filter in the page.
FILTERS = ("inactive", "unknown", "muted", "dead", "all")
PROMOTION_BATCH = 20

_INACTIVE = "t.status = 'live' AND (f.id IS NULL OR NOT f.polling)"

_FILTER_PREDICATES = {
    "inactive": _INACTIVE,
    "muted": "t.muted",
    # Never checked, or checked without a verdict -- the sweep's territory.
    "unknown": "t.status = 'unknown'",
    "dead": "t.status = 'dead'",
    # Unlike the focused views above, this is the complete inventory,
    # including live tokens that are actively polled.
    "all": "TRUE",
}
_FILTER_PREDICATES = {
    name: f"{predicate} AND {visible_source_sql('t.ats')}"
    for name, predicate in _FILTER_PREDICATES.items()
}

_SELECT = """
SELECT t.ats, t.token, t.company_id, c.name AS company, t.status, t.muted, t.tags, t.note,
       t.source, t.discovered_at, t.checked_at,
       f.id AS feed_id,
       CASE
           WHEN f.id IS NULL THEN 'none'
           WHEN f.polling THEN 'enabled'
           ELSE 'disabled'
       END AS feed_state,
       count(*) OVER () AS total
FROM token AS t
LEFT JOIN company AS c ON c.id = t.company_id
LEFT JOIN feed AS f ON f.source = t.ats AND f.key = t.token
WHERE {where_clause}
ORDER BY {order}
LIMIT $1 OFFSET $2
"""

# Row order within a vendor group. Keys are the page's sort menu; values are
# fixed SQL, so the menu can never inject. `t.token` last keeps paging stable.
ORDERS = {
    "token": "t.ats, t.token",
    "discovered": "t.discovered_at DESC NULLS LAST, t.ats, t.token",
    "checked": "t.checked_at DESC NULLS LAST, t.ats, t.token",
    "company": "c.name NULLS LAST, t.ats, t.token",
}

# The board a token polls. Comeet's key is `slug/board-id`, and the same
# board arrives under several slugs (renames: tabtale/crazylabs) and both
# board-id cases (arbe/C6.001, arbe/c6.001) -- measured 2026-10-05: 97 boards
# held by 200 feeds, every pair an identical payload. The API ignores board-id
# case, so the upper-cased board id is the identity. Registry rows key on the
# bare board id. Every other vendor's board is its token.
_BOARD = (
    "CASE WHEN {t}.ats = 'comeet' "
    "THEN upper(coalesce({t}.config->>'company', split_part({t}.token, '/', 2), {t}.token)) "
    "ELSE {t}.token END"
)

# Comeet polls only with the per-board API token that validation writes to
# `token.config`; without it the adapter reads the slug as the board and every
# poll is an HTTP 400 (2026-10-05: 392 of them). A slugged key also needs the
# resolved board id. And a board already in the feed under another spelling
# is not addable again.
_COMEET_READY = f"""(t.ats <> 'comeet' OR (
    t.config ? 'token'
    AND (t.config ? 'company' OR strpos(t.token, '/') = 0)
    AND NOT EXISTS (
        SELECT 1
          FROM feed AS fb
          JOIN token AS tb ON tb.ats = fb.source AND tb.token = fb.key
         WHERE fb.source = 'comeet'
           AND {_BOARD.format(t="tb")} = {_BOARD.format(t="t")}
    )
))"""

# A live board is not necessarily implemented by the polling engine. These
# names come only from the trusted source registry, never from a request.
_POLLING_SUPPORTED = "t.ats IN ({})".format(
    ", ".join(
        "'" + name.replace("'", "''") + "'"
        for name in sorted(sources.SOURCES) if not source_disabled(name)
    )
)

# What "Add to feed" will accept: the same guard both insert paths use.
_ADDABLE = (
    f"t.status = 'live' AND NOT t.muted AND f.id IS NULL"
    f" AND {_POLLING_SUPPORTED} AND {_COMEET_READY}"
)

# One row per board within a single insert: two spellings of one Comeet board
# arriving together would both pass the NOT EXISTS above, which only sees
# feeds that already existed when the statement began.
_FIRST_PER_BOARD = (
    f"row_number() OVER (PARTITION BY t.ats, {_BOARD.format(t='t')} ORDER BY t.token) = 1"
)

# Per-vendor sizes for the current filter, so the page can draw every vendor
# group without shipping every row -- rows load when a group is opened.
_GROUPS = """
SELECT t.ats, count(*)::int AS count,
       count(*) FILTER (WHERE {addable})::int AS addable
FROM token AS t
LEFT JOIN company AS c ON c.id = t.company_id
LEFT JOIN feed AS f ON f.source = t.ats AND f.key = t.token
WHERE {where_clause}
GROUP BY t.ats
ORDER BY count DESC, t.ats
"""

# Deliberately unfiltered: the header reports the shape of the whole inventory,
# so the backlog number does not move when someone narrows the table.
_COUNTS = f"""
SELECT count(*)::int AS total,
       count(*) FILTER (WHERE t.status = 'live')::int AS live,
       count(*) FILTER (WHERE t.status = 'dead')::int AS dead,
       count(*) FILTER (WHERE t.status = 'unknown')::int AS unknown,
       count(*) FILTER (WHERE t.muted)::int AS muted,
       count(*) FILTER (WHERE f.id IS NULL)::int AS unpromoted,
       count(*) FILTER (
           WHERE t.status = 'live' AND NOT t.muted AND f.id IS NULL
             AND {_POLLING_SUPPORTED}
       )::int
           AS promotable,
       count(DISTINCT t.ats)::int AS vendors,
       -- Freshness, the half of the inventory's shape that status alone does
       -- not show: a page of "live" means nothing without when it was asked.
       count(*) FILTER (WHERE t.checked_at IS NULL)::int AS never_checked,
       count(*) FILTER (WHERE t.checked_at >= now() - interval '24 hours')::int
           AS checked_24h
FROM token AS t
LEFT JOIN feed AS f ON f.source = t.ats AND f.key = t.token
WHERE {visible_source_sql('t.ats')}
"""


# token_log carries two kinds of verb in one column. Every tail read names the
# kinds it wants, so a new verb can never leak into a panel that predates it.
PROBE_STATUSES = ("unknown", "live", "dead")
FEED_EVENTS = ("feed_entered", "feed_exited")

_RECENT = f"""
SELECT l.id, l.checked_at, l.ats, l.token, l.status
  FROM token_log AS l
 WHERE l.status = ANY($2::text[])
   AND {visible_source_sql('l.ats')}
 ORDER BY l.id DESC
 LIMIT $1
"""



def _like(q: str) -> str:
    """`q` as a literal ILIKE substring: its own % and _ match themselves."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _validate(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ValueError(f"unknown {name}: {value!r} (expected one of {allowed})")


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _shape(record: Any) -> dict[str, Any]:
    row = dict(record)
    row.pop("total", None)
    for field in ("discovered_at", "checked_at"):
        row[field] = _iso(row.get(field))
    # token is exclusively the ATS discovery inventory. Direct and board
    # feeds are created directly in `feed`, never through this page.
    row["category"] = "ATS"
    row["polling_supported"] = row["ats"] in sources.SOURCES and not source_disabled(row["ats"])
    return row


async def query(
    pool,
    *,
    filter: str = "inactive",
    limit: int = 50,
    offset: int = 0,
    ats: str | None = None,
    groups: bool = False,
    q: str | None = None,
    order: str = "token",
) -> dict[str, Any]:
    """One page of token rows with each row's feed state derived.

    `ats` narrows the page to one vendor and `q` to tokens or companies
    containing it -- both bind values, never interpolated. `groups` adds every
    vendor's row count under the same filter and search -- the page's group
    headers -- without fetching those rows.
    """
    _validate("filter", filter, FILTERS)
    _validate("order", order, tuple(ORDERS))
    limit = max(1, min(limit, 5000))
    offset = max(0, offset)
    q = (q or "").strip() or None

    where_clause = _FILTER_PREDICATES.get(filter, "TRUE")
    # Search binds after the page window ($1, $2) and before the vendor.
    page_args: list[Any] = [limit, offset]
    page_where = where_clause
    group_where = where_clause
    group_args: list[Any] = []
    if q:
        pattern = _like(q)
        page_args.append(pattern)
        page_where = f"({page_where}) AND (t.token ILIKE ${len(page_args)} OR c.name ILIKE ${len(page_args)})"
        group_args.append(pattern)
        group_where = f"({group_where}) AND (t.token ILIKE $1 OR c.name ILIKE $1)"
    if ats:
        page_args.append(ats)
        page_where = f"({page_where}) AND t.ats = ${len(page_args)}"
    select_query = _SELECT.format(where_clause=page_where, order=ORDERS[order])

    async with pool.acquire() as conn:
        counts = await conn.fetchrow(_COUNTS)
        group_rows = (
            await conn.fetch(
                _GROUPS.format(where_clause=group_where, addable=_ADDABLE), *group_args
            )
            if groups else None
        )
        records = await conn.fetch(select_query, *page_args)
    result = {
        "counts": dict(counts) if counts else {},
        "total": records[0]["total"] if records else 0,
        "offset": offset,
        "limit": limit,
        "rows": [_shape(record) for record in records],
    }
    if group_rows is not None:
        result["groups"] = [dict(row) for row in group_rows]
        result["total"] = sum(row["count"] for row in result["groups"])
    return result


async def _recent(
    pool, statuses: tuple[str, ...], *, limit: int
) -> list[dict[str, Any]] | None:
    """One tail of ``token_log``, newest first, restricted to ``statuses``.

    Ordered by ``id``, not ``checked_at``: a batch probes three boards at once
    and their timestamps can tie or land out of order, which would make the
    tail jitter. A promote is worse -- it writes ``live`` and ``feed_entered``
    from the same statement, so ``now()`` is identical by construction.

    ``None`` means ``token_log`` does not exist yet -- the DDL in ``schema.sql``
    has not been applied -- which the page reports as "log unavailable" rather
    than as an empty log. An empty log is a claim that nothing happened; those
    are different statements.
    """
    limit = max(1, min(limit, 200))
    try:
        async with pool.acquire() as conn:
            records = await conn.fetch(_RECENT, limit, list(statuses))
    except asyncpg.UndefinedTableError:
        return None
    return [
        {
            "id": record["id"],
            "checked_at": _iso(record["checked_at"]),
            "ats": record["ats"],
            "token": record["token"],
            "status": record["status"],
        }
        for record in records
    ]


async def recent_checks(pool, *, limit: int = 50) -> list[dict[str, Any]] | None:
    """The newest liveness probes -- the check log's tail."""
    return await _recent(pool, PROBE_STATUSES, limit=limit)


async def recent_feed_events(pool, *, limit: int = 50) -> list[dict[str, Any]] | None:
    """The newest ``feed`` arrivals and departures -- the feed log's tail.

    ``feed`` records neither: a row carries no birth timestamp and a drop is a
    hard delete, so without these rows an identity that came and went leaves
    nothing behind at all.
    """
    return await _recent(pool, FEED_EVENTS, limit=limit)


async def set_note(
    pool,
    *,
    ats: str,
    token: str,
    note: str | None,
) -> dict[str, Any] | None:
    """Store one operator note, returning the saved row when it exists."""
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            """
            UPDATE token
               SET note = $1
             WHERE ats = $2 AND token = $3
         RETURNING ats, token, note
            """,
            note,
            ats,
            token,
        )
    return dict(record) if record else None


async def set_tags(
    pool,
    *,
    ats: str,
    token: str,
    tags: list[str],
) -> dict[str, Any] | None:
    """Store compact operator tags for an existing token."""
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            """
            UPDATE token
               SET tags = $1
             WHERE ats = $2 AND token = $3
         RETURNING ats, token, tags
            """,
            tags,
            ats,
            token,
        )
    return dict(record) if record else None


async def set_muted(
    pool,
    *,
    ats: str,
    token: str,
    muted: bool,
) -> dict[str, Any] | None:
    if not muted:
        require_source_allowed(ats)
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            """
            WITH changed AS (
                UPDATE token
                   SET muted = $1
                 WHERE ats = $2 AND token = $3
             RETURNING ats, token, muted
            ), dropped AS (
                DELETE FROM feed AS f
                 USING changed AS c
                 WHERE $1
                   AND f.source = c.ats
                   AND f.key = c.token
             RETURNING f.source, f.key
            ), logged AS (
                INSERT INTO token_log (checked_at, ats, token, status)
                SELECT now(), source, key, 'feed_exited' FROM dropped
            )
            SELECT c.ats, c.token, c.muted,
                   EXISTS (SELECT 1 FROM dropped) AS feed_dropped
              FROM changed AS c
            """,
            muted,
            ats,
            token,
        )
    return dict(record) if record else None


async def promotion_candidates(
    pool,
    *,
    limit: int = PROMOTION_BATCH,
) -> tuple[list[tuple[str, str]], int]:
    """Dead, unmuted, feed-absent tokens -- the heartbeat's re-probe queue.

    Dead rather than live: a board that has gone quiet can come back, and
    nothing else in the system would ever ask again. Live tokens absent from
    ``feed`` are the operator's promotion backlog, promoted deliberately from
    this page; re-probing them would spend the batch confirming what is
    already known.

    Oldest check first, so a resurrection is found in bounded time rather than
    whenever a token happens to come up. ``promote_verified`` still requires
    ``status = 'live'``, which by then means the probe flipped it back.
    """
    limit = max(1, min(limit, PROMOTION_BATCH))
    async with pool.acquire() as conn:
        records = await conn.fetch(
            """
            SELECT t.ats, t.token, count(*) OVER ()::int AS total
              FROM token AS t
             WHERE t.status = 'dead'
               AND NOT t.muted
               AND NOT EXISTS (
                   SELECT 1
                     FROM feed AS f
                    WHERE f.source = t.ats AND f.key = t.token
               )
             ORDER BY t.checked_at NULLS FIRST, t.ats, t.token
             LIMIT $1
            """,
            limit,
        )
    total = records[0]["total"] if records else 0
    return ([(record["ats"], record["token"]) for record in records], total)


async def promote_verified(
    pool,
    identities: list[tuple[str, str]],
    *,
    interval_s: int | None = None,
) -> int:
    """Add these exact tokens to ``feed`` -- the live, unmuted, absent ones.

    `interval_s` sets the new feeds' poll interval; None keeps the column
    default. Returns how many were added.
    """
    identities = list(dict.fromkeys(identities))
    if not identities:
        return 0
    async with pool.acquire() as conn:
        records = await conn.fetch(
            f"""
            WITH incoming(ats, token) AS (
                SELECT * FROM unnest($1::text[], $2::text[])
            ), candidates AS (
                SELECT t.ats, t.token, {_FIRST_PER_BOARD} AS first_of_board
                  FROM incoming AS i
                  JOIN token AS t USING (ats, token)
                  LEFT JOIN feed AS f ON f.source = t.ats AND f.key = t.token
                 WHERE {_ADDABLE}
            ), promoted AS (
                INSERT INTO feed(source, key, polling, interval_s)
                SELECT ats, token, true, coalesce($3::int, 900)
                  FROM candidates
                 WHERE first_of_board
                ON CONFLICT (source, key) DO NOTHING
                RETURNING source, key
            ), logged AS (
                INSERT INTO token_log (checked_at, ats, token, status)
                SELECT now(), source, key, 'feed_entered' FROM promoted
            )
            SELECT source, key FROM promoted
            """,
            [ats for ats, _ in identities],
            [token for _, token in identities],
            interval_s,
        )
    return len(records)


async def add_vendors_to_feed(
    pool,
    vendors: list[str],
    *,
    filter: str = "inactive",
    q: str | None = None,
    interval_s: int | None = None,
) -> int:
    """Add every addable token of `vendors` under the page's filter and search.

    The bulk twin of `promote_verified`: the page sends vendor names, not
    thousands of identities, and the server resolves the same rows the page
    is showing. The insert guard is identical, so a vendor tick can never add
    a dead, muted, or already-present token.
    """
    _validate("filter", filter, FILTERS)
    vendors = list(dict.fromkeys(vendors))
    if not vendors:
        return 0
    q = (q or "").strip() or None
    where = f"({_FILTER_PREDICATES[filter]}) AND t.ats = ANY($1::text[])"
    args: list[Any] = [vendors, interval_s]
    if q:
        args.append(_like(q))
        where += f" AND (t.token ILIKE ${len(args)} OR c.name ILIKE ${len(args)})"
    async with pool.acquire() as conn:
        records = await conn.fetch(
            f"""
            WITH candidates AS (
                SELECT t.ats, t.token, {_FIRST_PER_BOARD} AS first_of_board
                  FROM token AS t
                  LEFT JOIN company AS c ON c.id = t.company_id
                  LEFT JOIN feed AS f ON f.source = t.ats AND f.key = t.token
                 WHERE {where} AND {_ADDABLE}
            ), promoted AS (
                INSERT INTO feed(source, key, polling, interval_s)
                SELECT ats, token, true, coalesce($2::int, 900)
                  FROM candidates
                 WHERE first_of_board
                ON CONFLICT (source, key) DO NOTHING
                RETURNING source, key
            ), logged AS (
                INSERT INTO token_log (checked_at, ats, token, status)
                SELECT now(), source, key, 'feed_entered' FROM promoted
            )
            SELECT count(*)::int AS added FROM promoted
            """,
            *args,
        )
    return records[0]["added"] if records else 0
