"""Unit tests for tokens.py's validation, shaping, and SQL assembly -- no DB.

The SQL itself is exercised by running the
page against a real database. What is tested here is what is easy to get
silently wrong -- filter validation, the predicate each filter selects, the
arguments handed to the pool, the clamps, and the shaping the page depends on.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from xjobscore.console import tokens

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

COUNTS = {
    "total": 1433,
    "live": 1405,
    "dead": 28,
    "unknown": 0,
    "muted": 0,
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
        "muted": False,
        "tags": None,
        "note": None,
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


@pytest.mark.parametrize("value", ["bogus", "alive-active", "live", "none"])
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


async def test_default_view_is_inactive_live_tokens():
    pool = pool_with([])
    await tokens.query(pool)

    sql, _ = pool.calls[-1]
    assert "WHERE t.status = 'live' AND (f.id IS NULL OR NOT f.polling)" in sql


@pytest.mark.parametrize(
    "filter,fragment",
    [
        ("inactive", "t.status = 'live' AND (f.id IS NULL OR NOT f.polling)"),
        ("muted", "t.muted"),
        ("dead", "t.status = 'dead'"),
        ("all", "TRUE"),
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
    await tokens.query(pool, filter="inactive", limit=10, offset=20)

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


async def test_set_note_updates_only_the_named_token():
    pool = FakePool(row={"ats": "workable", "token": "snap", "note": "follow up"})

    saved = await tokens.set_note(
        pool, ats="workable", token="snap", note="follow up"
    )

    sql, args = pool.calls[-1]
    assert "UPDATE token" in sql
    assert "SET note = $1" in sql
    assert args == ("follow up", "workable", "snap")
    assert saved == {"ats": "workable", "token": "snap", "note": "follow up"}


async def test_set_tags_updates_only_the_named_token():
    pool = FakePool(row={"ats": "workable", "token": "snap", "tags": ["bio"]})

    saved = await tokens.set_tags(
        pool, ats="workable", token="snap", tags=["bio"]
    )

    sql, args = pool.calls[-1]
    assert "UPDATE token" in sql
    assert "SET tags = $1" in sql
    assert args == (["bio"], "workable", "snap")
    assert saved == {"ats": "workable", "token": "snap", "tags": ["bio"]}


async def test_muting_is_atomic_and_drops_the_feed_only_when_true():
    pool = FakePool(
        row={
            "ats": "workable",
            "token": "snap",
            "muted": True,
            "feed_dropped": True,
        }
    )

    saved = await tokens.set_muted(
        pool,
        ats="workable",
        token="snap",
        muted=True,
    )

    sql, args = pool.calls[-1]
    assert "WITH changed AS" in sql
    assert "SET muted = $1" in sql
    assert "DELETE FROM feed" in sql
    assert "WHERE $1" in sql
    assert args == (True, "workable", "snap")
    assert saved["feed_dropped"] is True


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


async def test_promotion_candidates_are_dead_unmuted_and_absent_from_feed():
    """Dead, not live: the heartbeat looks for resurrections, not confirmations."""
    pool = FakePool(
        [
            {"ats": "workable", "token": "snap", "total": 2},
            {"ats": "greenhouse", "token": "stripe", "total": 2},
        ]
    )

    identities, total = await tokens.promotion_candidates(pool, limit=10)

    sql, args = pool.calls[-1]
    assert "t.status = 'dead'" in sql
    assert "NOT t.muted" in sql
    assert "NOT EXISTS" in sql
    assert "FROM feed AS f" in sql
    assert args == (10,)
    assert identities == [("workable", "snap"), ("greenhouse", "stripe")]
    assert total == 2


async def test_promote_verified_rechecks_eligibility_during_insert():
    pool = FakePool([{"source": "workable", "key": "snap"}])

    promoted = await tokens.promote_verified(
        pool,
        [("workable", "snap"), ("workable", "snap")],
    )

    sql, args = pool.calls[-1]
    assert "INSERT INTO feed" in sql
    assert "t.status = 'live'" in sql
    assert "NOT t.muted" in sql
    assert "NOT EXISTS" in sql
    assert "ON CONFLICT (source, key) DO NOTHING" in sql
    assert args == (["workable"], ["snap"], None)
    assert promoted == 1


async def test_vendor_is_a_bind_value_and_groups_share_the_filter():
    pool = FakePool([{"ats": "ashby", "count": 7}], [])
    result = await tokens.query(pool, filter="unknown", limit=10, ats="ashby", groups=True)

    groups_sql, _ = pool.calls[1]
    page_sql, page_args = pool.calls[-1]
    assert "WHERE t.status = 'unknown'" in groups_sql and "GROUP BY t.ats" in groups_sql
    assert "AND t.ats = $3" in page_sql and "ashby" not in page_sql
    assert page_args == (10, 0, "ashby")
    assert result["groups"] == [{"ats": "ashby", "count": 7}]
    assert result["total"] == 7


async def test_search_and_vendor_bind_in_order_and_groups_follow_search():
    pool = FakePool([], [])
    await tokens.query(pool, filter="all", ats="ashby", groups=True, q=" 50%_off ", order="checked")

    groups_sql, groups_args = pool.calls[1]
    page_sql, page_args = pool.calls[-1]
    assert groups_args == ("%50\\%\\_off%",)
    assert "t.token ILIKE $1 OR c.name ILIKE $1" in groups_sql
    assert page_args == (50, 0, "%50\\%\\_off%", "ashby")
    assert "ILIKE $3" in page_sql and "t.ats = $4" in page_sql
    assert "ORDER BY t.checked_at DESC NULLS LAST" in page_sql


async def test_unknown_order_rejected():
    with pytest.raises(ValueError):
        await tokens.query(pool_with([]), order="random()")


async def test_add_vendors_to_feed_resolves_rows_on_the_server():
    pool = FakePool([{"added": 42}])
    added = await tokens.add_vendors_to_feed(
        pool, ["greenhouse", "greenhouse", "lever"], filter="inactive", q="ai", interval_s=86_400,
    )

    sql, args = pool.calls[-1]
    assert added == 42
    assert args == (["greenhouse", "lever"], 86_400, "%ai%")
    # Same guard as the per-token path, plus the page's own filter and search.
    assert "t.status = 'live' AND NOT t.muted AND f.id IS NULL" in sql
    assert "t.status = 'live' AND (f.id IS NULL OR NOT f.polling)" in sql
    assert "t.ats = ANY($1::text[])" in sql and "ILIKE $3" in sql
    assert "'feed_entered'" in sql and "ON CONFLICT (source, key) DO NOTHING" in sql


async def test_add_vendors_to_feed_with_nothing_ticked_touches_nothing():
    pool = FakePool()
    assert await tokens.add_vendors_to_feed(pool, []) == 0
    assert pool.calls == []
