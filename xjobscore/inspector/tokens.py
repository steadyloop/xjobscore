"""Queries and operator-note edits over ``public.token`` for the Tokens page.

``token`` is the full inventory of every ATS board key ever seen -- live or
dead -- while ``feed`` is the curated set the scheduler actually polls. The
two are joined loosely on ``(ats, token) = (feed.source, feed.key)``; there is
no foreign key between them, which is exactly why a token can exist with no
feed row. Those rows are the promotion backlog this page exists to work.

Telemetry-style, same contract as ``postings``: every function borrows the
pipeline pool (see ``PolicyStore.pool_or_none``) and opens nothing of its own.
Untrusted text only ever arrives as a bind parameter; the only values
interpolated into SQL are pre-validated enum keys.
"""

from __future__ import annotations

from typing import Any

import asyncpg

# These are the operator-facing views for the token table.  Keep the tuple in
# the same order as the status filter in the page.
FILTERS = ("inactive", "muted", "dead", "all")
PROMOTION_BATCH = 20

_INACTIVE = "t.status = 'live' AND (f.id IS NULL OR NOT f.polling)"

_FILTER_PREDICATES = {
    "inactive": _INACTIVE,
    "muted": "t.muted",
    "dead": "t.status = 'dead'",
    # Unlike the focused views above, this is the complete inventory,
    # including live tokens that are actively polled.
    "all": "TRUE",
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
ORDER BY t.ats, t.token
LIMIT $1 OFFSET $2
"""

# Deliberately unfiltered: the header reports the shape of the whole inventory,
# so the backlog number does not move when someone narrows the table.
_COUNTS = """
SELECT count(*)::int AS total,
       count(*) FILTER (WHERE t.status = 'live')::int AS live,
       count(*) FILTER (WHERE t.status = 'dead')::int AS dead,
       count(*) FILTER (WHERE t.status = 'unknown')::int AS unknown,
       count(*) FILTER (WHERE t.muted)::int AS muted,
       count(*) FILTER (WHERE f.id IS NULL)::int AS unpromoted,
       count(*) FILTER (
           WHERE t.status = 'live' AND NOT t.muted AND f.id IS NULL
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
"""


_RECENT_CHECKS = """
SELECT l.id, l.checked_at, l.ats, l.token, l.status
  FROM token_log AS l
 ORDER BY l.id DESC
 LIMIT $1
"""



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
    return row


async def query(
    pool,
    *,
    filter: str = "inactive",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """One page of token rows with each row's feed state derived."""
    _validate("filter", filter, FILTERS)
    limit = max(1, min(limit, 5000))
    offset = max(0, offset)

    where_clause = _FILTER_PREDICATES.get(filter, "TRUE")
    select_query = _SELECT.format(where_clause=where_clause)

    async with pool.acquire() as conn:
        counts = await conn.fetchrow(_COUNTS)
        records = await conn.fetch(
            select_query, limit, offset
        )
    return {
        "counts": dict(counts) if counts else {},
        "total": records[0]["total"] if records else 0,
        "offset": offset,
        "limit": limit,
        "rows": [_shape(record) for record in records],
    }


async def recent_checks(pool, *, limit: int = 50) -> list[dict[str, Any]] | None:
    """The newest liveness probes, newest first -- the page's tail log.

    Ordered by ``id``, not ``checked_at``: a batch probes three boards at once
    and their timestamps can tie or land out of order, which would make the
    tail jitter. ``None`` means ``token_log`` does not exist yet -- the DDL in
    ``schema.sql`` has not been applied -- which the page reports as "log
    unavailable" rather than as an empty log. An empty log is a claim that
    nothing has been checked; those are different statements.
    """
    limit = max(1, min(limit, 200))
    try:
        async with pool.acquire() as conn:
            records = await conn.fetch(_RECENT_CHECKS, limit)
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
             RETURNING f.id
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
) -> int:
    identities = list(dict.fromkeys(identities))
    if not identities:
        return 0
    async with pool.acquire() as conn:
        records = await conn.fetch(
            """
            WITH incoming(ats, token) AS (
                SELECT * FROM unnest($1::text[], $2::text[])
            )
            INSERT INTO feed(source, key, polling)
            SELECT t.ats, t.token, true
              FROM incoming AS i
              JOIN token AS t USING (ats, token)
             WHERE t.status = 'live'
               AND NOT t.muted
               AND NOT EXISTS (
                   SELECT 1
                     FROM feed AS f
                    WHERE f.source = t.ats AND f.key = t.token
               )
            ON CONFLICT (source, key) DO NOTHING
            RETURNING source, key
            """,
            [ats for ats, _ in identities],
            [token for _, token in identities],
        )
    return len(records)
