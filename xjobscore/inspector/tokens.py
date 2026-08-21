"""Read-only queries over ``public.token`` for the Tokens page.

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

from xjobscore.sources import source_type

# Matches schema.sql's CHECK on token.status. "all" is a filter value, not a
# stored one, so it is listed here but never compared against a column.
FILTERS = ("all", "alive-active", "alive-inactive", "dead")

_FILTER_PREDICATES = {
    "all": "TRUE",
    "alive-active": "t.status = 'live' AND f.enabled",
    "alive-inactive": "t.status = 'live' AND (f.id IS NULL OR NOT f.enabled)",
    "dead": "t.status = 'dead'",
}

_SELECT = """
SELECT t.ats, t.token, t.company_id, c.name AS company, t.status,
       t.source, t.discovered_at, t.checked_at,
       f.id AS feed_id,
       CASE
           WHEN f.id IS NULL THEN 'none'
           WHEN f.enabled THEN 'enabled'
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
       count(*) FILTER (WHERE f.id IS NULL)::int AS unpromoted,
       count(*) FILTER (WHERE t.status = 'live' AND f.id IS NULL)::int
           AS promotable
FROM token AS t
LEFT JOIN feed AS f ON f.source = t.ats AND f.key = t.token
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
    # Enable is ATS-only; the frontend gates on this the same way the Feeds
    # page already gates its own controls on category.
    row["category"] = source_type(row["ats"])
    return row


async def query(
    pool,
    *,
    filter: str = "all",
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
