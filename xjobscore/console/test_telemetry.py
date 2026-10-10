"""Unit tests for telemetry.py's degradation and densification -- no database.

The SQL itself is exercised by running the page against a real database. What is
tested here is what is easy to get silently wrong: a table that does not exist
yet must read as "unknown" rather than as zero activity, and daily chart counts
must come back dense so their positions remain aligned with UTC dates.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import asyncpg

from xjobscore.console import telemetry

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


class FakePool:
    """asyncpg.Pool stand-in. Each positional arg is one fetch's result, in order."""

    def __init__(self, *results, raises=None):
        self.results = list(results)
        self.raises = raises
        self.queries: list[str] = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        if self.raises is not None:
            raise self.raises
        return self.results.pop(0) if self.results else []


def missing_table():
    return FakePool(raises=asyncpg.UndefinedTableError("relation does not exist"))


# ----------------------------------------------------- table not created yet

async def test_daily_counts_none_when_table_absent():
    assert await telemetry.feed_daily_counts(missing_table(), now=NOW) is None


async def test_rollups_none_when_table_absent():
    assert await telemetry.feed_rollups(missing_table(), now=NOW) is None


async def test_tiles_none_when_table_absent():
    assert await telemetry.global_tiles(missing_table(), now=NOW) is None


async def test_events_none_when_table_absent():
    assert await telemetry.feed_events(missing_table(), 1) is None


# ------------------------------------------------- empty table is not absent

async def test_empty_table_reads_as_nothing_recorded_not_as_no_table():
    assert await telemetry.feed_daily_counts(FakePool([]), now=NOW) == {}
    assert await telemetry.feed_rollups(FakePool([], []), now=NOW) == {}
    assert await telemetry.feed_events(FakePool([]), 1) == []


# ---------------------------------------------------------- densification

async def test_daily_counts_are_dense_and_positioned_by_date():
    days = 14
    oldest = (NOW - timedelta(days=days - 1)).date()
    pool = FakePool(
        [
            {"feed_id": 7, "day": oldest, "new_postings": 3},
            {"feed_id": 7, "day": NOW.date(), "new_postings": 5},
        ]
    )
    counts = await telemetry.feed_daily_counts(pool, now=NOW, days=days)

    assert len(counts[7]) == days
    assert counts[7][0] == 3, "oldest day first"
    assert counts[7][-1] == 5, "today last"
    assert sum(counts[7]) == 8, "quiet days are zero-filled, not omitted"


async def test_rows_outside_the_window_are_dropped_not_wrapped():
    """A stale row must not fold back into the window at a bogus index."""
    pool = FakePool(
        [{"feed_id": 7, "day": (NOW - timedelta(days=90)).date(), "new_postings": 99}]
    )
    assert await telemetry.feed_daily_counts(pool, now=NOW, days=14) == {}


# ----------------------------------------------------------------- rollups

async def test_rollups_carry_counters_and_the_latest_outcome():
    pool = FakePool(
        [
            {
                "feed_id": 7,
                "poll_count_7d": 96,
                "new_7d": 4,
                "error_count_7d": 1,
                "last_polled_at": NOW,
            }
        ],
        [{"feed_id": 7, "status": "unchanged"}],
    )
    rollups = await telemetry.feed_rollups(pool, now=NOW)
    assert rollups[7]["new_7d"] == 4
    assert "observed_days" not in rollups[7]
    assert rollups[7]["last_status"] == "unchanged"
    assert rollups[7]["last_polled_at"] == NOW.isoformat()


# ------------------------------------------------------------------- tiles

def tiles_pool(*, p95=None, prev_p95=None, hours=None, errors=None, **counters):
    """global_tiles issues five reads, in this order."""
    row = {
        "observations_total": 0,
        "requests_today": 0,
        "new_postings_today": 0,
        "polls_24h": 0,
        "errors_24h": 0,
    }
    row.update(counters)
    return FakePool(
        [row],
        [{"p95": p95}],
        [{"p95": prev_p95}],
        hours if hours is not None else [],
        errors if errors is not None else [],
    )


async def test_tiles_report_zero_counts_but_unknown_latency():
    """Counts of zero are real; a p95 with nothing to measure stays None."""
    tiles = await telemetry.global_tiles(tiles_pool(), now=NOW)
    assert tiles["requests_today"] == 0
    assert tiles["errors_24h"] == 0
    assert tiles["discovery_latency_p95_s"] is None
    assert tiles["discovery_latency_p95_prev_s"] is None


async def test_tiles_carry_a_previous_period_for_the_delta():
    tiles = await telemetry.global_tiles(
        tiles_pool(p95=1200.0, prev_p95=1800.0), now=NOW
    )
    assert tiles["discovery_latency_p95_s"] == 1200.0
    assert tiles["discovery_latency_p95_prev_s"] == 1800.0


async def test_tiles_attribute_errors_to_their_worst_source():
    tiles = await telemetry.global_tiles(
        tiles_pool(
            errors_24h=15,
            errors=[{"source": "workday", "errors": 14}, {"source": "lever", "errors": 1}],
        ),
        now=NOW,
    )
    assert tiles["error_sources"][0] == {"source": "workday", "errors": 14}


# ------------------------------------------------------- hourly densification

async def test_polls_by_hour_is_dense_and_positioned():
    top_of_hour = NOW.replace(minute=0, second=0, microsecond=0)
    pool = FakePool(
        [
            {"hour": (top_of_hour - timedelta(hours=23)).replace(tzinfo=None), "polls": 4},
            {"hour": top_of_hour.replace(tzinfo=None), "polls": 9},
        ]
    )
    counts = await telemetry.polls_by_hour(pool, now=NOW, hours=24)
    assert len(counts) == 24
    assert counts[0] == 4, "oldest hour first"
    assert counts[-1] == 9, "current hour last"
    assert sum(counts) == 13


async def test_polls_by_hour_none_when_table_absent():
    assert await telemetry.polls_by_hour(missing_table(), now=NOW) is None


# ------------------------------------------------------- posting stats join

async def test_posting_stats_keep_feeds_with_no_measurable_latency():
    """A feed whose postings all lack posted_at still reports its open count."""
    pool = FakePool(
        [{"source": "ashby:acme", "open_postings": 12}],
        [],  # no latency rows at all
    )
    stats = await telemetry.posting_stats_by_source(pool, now=NOW)
    assert stats["ashby:acme"]["open_postings"] == 12
    assert stats["ashby:acme"]["p95_latency_s"] is None


async def test_posting_stats_merge_latency_onto_the_open_counts():
    pool = FakePool(
        [{"source": "ashby:acme", "open_postings": 12}],
        [{"source": "ashby:acme", "p95": 900.0}],
    )
    stats = await telemetry.posting_stats_by_source(pool, now=NOW)
    assert stats["ashby:acme"] == {"open_postings": 12, "p95_latency_s": 900.0}
