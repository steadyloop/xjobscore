"""Reads over ``public.poll_log`` -- what the production poller did.

This is the second read-only coupling to the pipeline's own schema, alongside
``policy.py``. It borrows that module's pool rather than opening its own (see
``PolicyStore.pool_or_none``), and like it, writes nothing: every column here
belongs to ``run.py``.

Two absences are normal and are reported differently, because they mean
different things to an operator:

* ``poll_log`` does not exist -- the DDL in ``schema.sql`` has not been
  applied to this database. Every function returns ``None``.
* The table exists but is empty -- ``run.py`` appends a row per completed poll,
  and it has not completed one yet. Functions return empty collections.

Both render as "no telemetry yet" rather than as zeros. A zero would claim the
poller ran and found nothing, which is a different and much more alarming
statement than "nobody is recording."
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

# Width of the per-feed new-postings chart.
DEFAULT_WINDOW_DAYS = 14
# "Recent activity" window for the New/7d column.
ROLLUP_DAYS = 7
# Discovery latency is a claim about current behavior, not all history.
# Per-feed needs the longer window: one feed rarely has enough postings in a
# week for a 95th percentile to mean anything. The header tile uses the short
# one so it actually moves when something changes.
LATENCY_WINDOW_DAYS = 30
TILE_LATENCY_WINDOW_DAYS = 7


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _day_start(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


async def _fetch(pool: Any, sql: str, *args: Any) -> list[Any] | None:
    """Run a read; ``None`` means the table has not been created yet."""
    try:
        async with pool.acquire() as conn:
            return await conn.fetch(sql, *args)
    except asyncpg.UndefinedTableError:
        return None


async def feed_daily_counts(
    pool: Any,
    *,
    now: datetime,
    days: int = DEFAULT_WINDOW_DAYS,
) -> dict[int, list[int]] | None:
    """New postings per feed per UTC day, oldest first and densely zero-filled.

    Dense output keeps every day aligned with its chart label. A feed absent
    from the result has no observations; a zero inside a returned series means
    it was observed but found no new postings that day.
    """
    start = _day_start(now) - timedelta(days=days - 1)
    rows = await _fetch(
        pool,
        """
        SELECT feed_id,
               (polled_at AT TIME ZONE 'UTC')::date AS day,
               sum(new_postings)::bigint AS new_postings
        FROM poll_log
        WHERE polled_at >= $1
        GROUP BY feed_id, day
        """,
        start,
    )
    if rows is None:
        return None

    start_date = start.date()
    counts: dict[int, list[int]] = {}
    for row in rows:
        index = (row["day"] - start_date).days
        if not 0 <= index < days:
            continue
        counts.setdefault(row["feed_id"], [0] * days)[index] = int(
            row["new_postings"] or 0
        )
    return counts


async def feed_rollups(
    pool: Any,
    *,
    now: datetime,
) -> dict[int, dict[str, Any]] | None:
    """Per-feed counters over seven days, plus the latest outcome."""
    rollup_start = now - timedelta(days=ROLLUP_DAYS)
    rows = await _fetch(
        pool,
        """
        SELECT feed_id,
               count(*)::bigint AS poll_count_7d,
               coalesce(sum(new_postings), 0)::bigint
                   AS new_7d,
               count(*) FILTER (WHERE status = 'error')::bigint
                   AS error_count_7d,
               max(polled_at) AS last_polled_at
        FROM poll_log
        WHERE polled_at >= $1
        GROUP BY feed_id
        """,
        rollup_start,
    )
    if rows is None:
        return None

    # One indexed seek per feed rather than DISTINCT ON over the whole table.
    # DISTINCT ON cannot skip ahead in poll_log_feed_idx, so it walks every
    # poll ever recorded to return one row per feed -- a cost that grows with
    # poll_log forever. The lateral drives off `feed` instead, so the work
    # tracks the fleet size, which is what the answer is actually about.
    latest = await _fetch(
        pool,
        """
        SELECT f.id AS feed_id, o.status
        FROM feed f
        CROSS JOIN LATERAL (
            SELECT status
            FROM poll_log p
            WHERE p.feed_id = f.id
            ORDER BY p.polled_at DESC
            LIMIT 1
        ) o
        """,
    )
    last_status = {row["feed_id"]: row["status"] for row in (latest or [])}

    return {
        row["feed_id"]: {
            "poll_count_7d": int(row["poll_count_7d"]),
            "new_7d": int(row["new_7d"]),
            "error_count_7d": int(row["error_count_7d"]),
            "last_polled_at": _iso(row["last_polled_at"]),
            "last_status": last_status.get(row["feed_id"]),
        }
        for row in rows
    }


async def global_tiles(pool: Any, *, now: datetime) -> dict[str, Any] | None:
    """The page-header numbers. ``None`` when poll_log does not exist."""
    today = _day_start(now)
    day_ago = now - timedelta(hours=24)
    rows = await _fetch(
        pool,
        """
        SELECT count(*)::bigint AS observations_total,
               count(*) FILTER (WHERE polled_at >= $1)::bigint AS requests_today,
               coalesce(sum(new_postings) FILTER (WHERE polled_at >= $1), 0)::bigint
                   AS new_postings_today,
               count(*) FILTER (WHERE polled_at >= $2)::bigint AS polls_24h,
               count(*) FILTER (WHERE polled_at >= $2 AND status = 'error')::bigint
                   AS errors_24h
        FROM poll_log
        """,
        today,
        day_ago,
    )
    # An ungrouped aggregate always returns one row, so an empty result means
    # the read did not happen at all -- same story as a missing table.
    if not rows:
        return None
    row = rows[0]

    current = await discovery_latency_p95_s(pool, now=now)
    previous = await discovery_latency_p95_s(
        pool, now=now, offset_days=TILE_LATENCY_WINDOW_DAYS
    )
    return {
        "observations_total": int(row["observations_total"]),
        "requests_today": int(row["requests_today"]),
        "new_postings_today": int(row["new_postings_today"]),
        "polls_24h": int(row["polls_24h"]),
        "errors_24h": int(row["errors_24h"]),
        "discovery_latency_p95_s": current,
        "discovery_latency_p95_prev_s": previous,
        "latency_window_days": TILE_LATENCY_WINDOW_DAYS,
        "polls_by_hour": await polls_by_hour(pool, now=now),
        "error_sources": await error_sources(pool, now=now),
    }


async def discovery_latency_p95_s(
    pool: Any,
    *,
    now: datetime,
    window_days: int = TILE_LATENCY_WINDOW_DAYS,
    offset_days: int = 0,
) -> float | None:
    """95th percentile of (first seen) − (source posted_at), in seconds.

    ``offset_days`` shifts the window back so the same query yields the previous
    period for a period-over-period delta.

    ``posted_at`` is supplied by the source and is regularly ahead of our clock
    (timezone-less dates normalized to midnight, clock skew, backdated reposts),
    which would otherwise contribute negative latencies. Those rows are excluded
    rather than clamped: a posting we saw "before" it existed says nothing
    truthful about how fast we discover things.
    """
    end = now - timedelta(days=offset_days)
    start = end - timedelta(days=window_days)
    rows = await _fetch(
        pool,
        """
        SELECT percentile_cont(0.95) WITHIN GROUP (
                   ORDER BY EXTRACT(EPOCH FROM (first_seen_at - posted_at))
               ) AS p95
        FROM job_posting
        WHERE posted_at IS NOT NULL
          AND first_seen_at >= $1
          AND first_seen_at < $2
          AND first_seen_at >= posted_at
        """,
        start,
        end,
    )
    if not rows or rows[0]["p95"] is None:
        return None
    return float(rows[0]["p95"])


async def polls_by_hour(
    pool: Any,
    *,
    now: datetime,
    hours: int = 24,
) -> list[int] | None:
    """Poll counts per hour over the trailing window, oldest first and dense."""
    start = now.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    ) - timedelta(hours=hours - 1)
    rows = await _fetch(
        pool,
        """
        SELECT date_trunc('hour', polled_at AT TIME ZONE 'UTC') AS hour,
               count(*)::bigint AS polls
        FROM poll_log
        WHERE polled_at >= $1
        GROUP BY hour
        """,
        start,
    )
    if rows is None:
        return None
    counts = [0] * hours
    naive_start = start.replace(tzinfo=None)
    for row in rows:
        index = int((row["hour"] - naive_start).total_seconds() // 3600)
        if 0 <= index < hours:
            counts[index] = int(row["polls"])
    return counts


async def error_sources(pool: Any, *, now: datetime) -> list[dict[str, Any]] | None:
    """Errored polls in the last 24 h, worst source first.

    Feeds the "N of M from <source>" line under the error tile: an error count
    with no attribution tells an operator nothing about where to look.
    """
    rows = await _fetch(
        pool,
        """
        SELECT f.source, count(*)::bigint AS errors
        FROM poll_log o
        JOIN feed f ON f.id = o.feed_id
        WHERE o.polled_at >= $1 AND o.status = 'error'
        GROUP BY f.source
        ORDER BY errors DESC
        """,
        now - timedelta(hours=24),
    )
    if rows is None:
        return None
    return [{"source": row["source"], "errors": int(row["errors"])} for row in rows]


async def posting_stats_by_source(
    pool: Any,
    *,
    now: datetime,
) -> dict[str, dict[str, Any]] | None:
    """Open-posting counts and discovery-latency p95, keyed by ``source:key``.

    ``job_posting`` carries no ``feed_id`` -- only the ``source`` namespace
    ``store.source_instance`` builds as ``f"{feed.source}:{feed.key}"``. Callers
    rebuild that string to join. It is a string join with no foreign key behind
    it, which is fine for a read-only ops view and not something to build on.

    Two queries rather than one: FILTER on an ordered-set aggregate
    (``percentile_cont ... WITHIN GROUP``) is not portable, and folding the
    latency predicate into a shared WHERE would silently drop feeds whose
    postings all lack ``posted_at`` from the open-count as well.
    """
    open_rows = await _fetch(
        pool,
        """
        SELECT source, count(*)::bigint AS open_postings
        FROM job_posting
        WHERE status = 'active'
        GROUP BY source
        """,
    )
    if open_rows is None:
        return None

    latency_rows = await _fetch(
        pool,
        """
        SELECT source,
               percentile_cont(0.95) WITHIN GROUP (
                   ORDER BY EXTRACT(EPOCH FROM (first_seen_at - posted_at))
               ) AS p95
        FROM job_posting
        WHERE posted_at IS NOT NULL
          AND first_seen_at >= $1
          AND first_seen_at >= posted_at
        GROUP BY source
        """,
        now - timedelta(days=LATENCY_WINDOW_DAYS),
    )

    stats: dict[str, dict[str, Any]] = {
        row["source"]: {"open_postings": int(row["open_postings"]), "p95_latency_s": None}
        for row in open_rows
    }
    for row in latency_rows or []:
        entry = stats.setdefault(
            row["source"], {"open_postings": 0, "p95_latency_s": None}
        )
        entry["p95_latency_s"] = None if row["p95"] is None else float(row["p95"])
    return stats


async def feed_events(
    pool: Any,
    feed_id: int,
    *,
    limit: int = 20,
) -> list[dict[str, Any]] | None:
    """Recent polls for one feed, newest first -- the detail pane's event list."""
    rows = await _fetch(
        pool,
        """
        SELECT polled_at, status, new_postings, status_code
        FROM poll_log
        WHERE feed_id = $1
        ORDER BY polled_at DESC
        LIMIT $2
        """,
        feed_id,
        limit,
    )
    if rows is None:
        return None
    return [
        {
            "polled_at": _iso(row["polled_at"]),
            "status": row["status"],
            "new_postings": int(row["new_postings"]),
            "status_code": row["status_code"],
        }
        for row in rows
    ]


async def recent_events(
    pool: Any,
    *,
    limit: int = 50,
) -> list[dict[str, Any]] | None:
    """Latest polls across every feed, newest first -- the live fetch log."""
    rows = await _fetch(
        pool,
        """
        SELECT o.polled_at, o.status, o.new_postings, o.status_code, f.source, f.key
        FROM poll_log o
        JOIN feed f ON f.id = o.feed_id
        ORDER BY o.polled_at DESC
        LIMIT $1
        """,
        limit,
    )
    if rows is None:
        return None
    return [
        {
            "polled_at": _iso(row["polled_at"]),
            "status": row["status"],
            "new_postings": int(row["new_postings"]),
            "status_code": row["status_code"],
            "source": row["source"],
            "key": row["key"],
        }
        for row in rows
    ]
