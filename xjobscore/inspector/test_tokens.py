"""Unit tests for tokens.py's validation, shaping, and SQL assembly -- no DB.

Same precedent as test_postings.py: the SQL itself is exercised by running the
page against a real database. What is tested here is what is easy to get
silently wrong -- filter validation, the predicate each filter selects, the
arguments handed to the pool, the clamps, and the shaping the page depends on.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from xjobscore.inspector import tokens

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

COUNTS = {
    "total": 1433,
    "live": 1405,
    "dead": 28,
    "unknown": 0,
    "unpromoted": 257,
    "promotable": 257,
}


def token_row(**overrides):
    row = {
        "ats": "workable",
        "token": "snap",
        "company_id": 12,
        "company": "Snap",
        "status": "live",
        "source": "slug",
        "discovered_at": NOW,
        "checked_at": NOW,
        "feed_id": None,
        "feed_state": "none",
        "total": 1,
    }
    row.update(overrides)
    return row


class FakePool:
    """asyncpg.Pool stand-in recording every query and its arguments."""

    def __init__(self, *results, row=None):
        self.results = list(results)
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self.results.pop(0) if self.results else []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self.row


def pool_with(rows, *, counts=COUNTS):
    # query() runs one fetchrow (the counts header), then one fetch (the page),
    # so the recorded calls are [counts, page] and calls[-1] is the SELECT.
    return FakePool(rows, row=counts)


# ------------------------------------------------------------- validation


@pytest.mark.parametrize("value", ["bogus", "inactive", "live", "none"])
async def test_unknown_filter_rejected(value):
    # Only the four FILTERS keys are legal; the stored status vocabulary
    # ("live") and the feed-state words ("none") are not filter values.
    with pytest.raises(ValueError):
        await tokens.query(pool_with([]), filter=value)


async def test_every_declared_filter_is_accepted():
    """Each filter must have a predicate, or the .format() would KeyError."""
    for filter in tokens.FILTERS:
        await tokens.query(pool_with([]), filter=filter)


# ------------------------------------------------------------- filtering


async def test_default_view_is_the_whole_inventory():
    pool = pool_with([])
    await tokens.query(pool)

    sql, _ = pool.calls[-1]
    assert "WHERE TRUE" in sql


@pytest.mark.parametrize(
    "filter,fragment",
    [
        ("all", "TRUE"),
        ("alive-active", "t.status = 'live' AND f.enabled"),
        ("alive-inactive", "t.status = 'live' AND (f.id IS NULL OR NOT f.enabled)"),
        ("dead", "t.status = 'dead'"),
    ],
)
async def test_filter_selects_its_predicate(filter, fragment):
    pool = pool_with([])
    await tokens.query(pool, filter=filter)

    sql, _ = pool.calls[-1]
    # Anchored on the WHERE keyword: the derived feed_state CASE mentions these
    # same columns, so a bare substring check would pass either way.
    assert f"WHERE {fragment}" in sql


async def test_filter_is_not_interpolated_as_a_bind_value():
    # The filter maps to a fixed predicate; the only bound arguments are the
    # page window, never the filter itself.
    pool = pool_with([])
    await tokens.query(pool, filter="dead", limit=10, offset=20)

    _, args = pool.calls[-1]
    assert args == (10, 20)


# ------------------------------------------------------------- bounds


@pytest.mark.parametrize(
    "limit,offset,expected",
    [(0, 0, (1, 0)), (9000, 0, (5000, 0)), (50, -10, (50, 0))],
)
async def test_limit_and_offset_are_clamped(limit, offset, expected):
    pool = pool_with([])
    body = await tokens.query(pool, limit=limit, offset=offset)

    _, args = pool.calls[-1]
    assert args == expected
    assert (body["limit"], body["offset"]) == expected


# ------------------------------------------------------------- shaping


async def test_rows_are_shaped_for_the_page():
    pool = pool_with([token_row(total=257)])
    body = await tokens.query(pool)

    assert body["total"] == 257
    (row,) = body["rows"]
    assert "total" not in row
    assert row["discovered_at"] == NOW.isoformat()
    assert row["checked_at"] == NOW.isoformat()
    assert row["feed_state"] == "none"
    assert row["company"] == "Snap"


async def test_registry_rows_carry_no_discovery_timestamps():
    pool = pool_with(
        [token_row(source=None, discovered_at=None, checked_at=None, feed_state="enabled")]
    )
    (row,) = (await tokens.query(pool))["rows"]

    assert row["source"] is None
    assert row["discovered_at"] is None
    assert row["checked_at"] is None


async def test_empty_page_reports_zero_total():
    body = await tokens.query(pool_with([]))

    assert body["total"] == 0
    assert body["rows"] == []


async def test_counts_come_back_whole():
    body = await tokens.query(pool_with([]))

    assert body["counts"] == COUNTS


async def test_counts_survive_an_empty_inventory():
    body = await tokens.query(pool_with([], counts=None))

    assert body["counts"] == {}
