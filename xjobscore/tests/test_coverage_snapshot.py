from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from xjobscore import coverage_snapshot, run, store


NOW = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)


def _row(**overrides):
    row = {
        "source": "greenhouse",
        "key": "acme",
        "polling": True,
        "strikes": 0,
        "last_poll_at": NOW - timedelta(seconds=30),
        "paused_at": None,
        "interval_s": 60,
        "due_at": NOW + timedelta(seconds=30),
        "hold_until": None,
        "company_name": "Acme",
        "coverage": 3,
        "added_today": 2,
        "global_added_today": 7,
    }
    row.update(overrides)
    return row


def test_build_snapshot_preserves_both_coverage_api_shapes():
    snapshot = coverage_snapshot.build_snapshot(
        [
            _row(),
            _row(
                key="beta",
                company_name=None,
                strikes=1,
                last_poll_at=None,
                coverage=0,
                added_today=0,
            ),
            _row(
                source="lever",
                key="quiet",
                polling=False,
                coverage=5,
                added_today=1,
            ),
        ],
        computed_at=NOW,
    )

    source_coverage = snapshot["source_coverage"]
    assert source_coverage["source_count"] == 2
    assert source_coverage["total_coverage"] == 8
    assert source_coverage["total_added_today"] == 3
    assert source_coverage["sources"][0]["source"] == "lever"
    assert source_coverage["sources"][0]["health"] == "Disabled"
    assert source_coverage["sources"][1]["health"] == "Needs attention"

    board_map = snapshot["board_map"]
    assert board_map["totals"] == {
        "boards": 2,
        "providers": 1,
        "ok": 1,
        "erroring": 1,
        "stale": 0,
        "paused": 0,
        "never": 0,
        "added_today": 7,
    }
    assert board_map["providers"][0]["boards"][1]["company"] == "beta"


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Connection:
    def __init__(self):
        self.written = None

    async def fetch(self, query, computed_at):
        assert "FROM global_stats g" in query
        return [_row()]

    async def execute(self, query, payload, computed_at):
        assert "ON CONFLICT (id) DO UPDATE" in query
        self.written = (payload, computed_at)


class _Pool:
    def __init__(self):
        self.connection = _Connection()

    def acquire(self):
        return _Context(self.connection)


@pytest.mark.asyncio
async def test_refresh_replaces_the_single_snapshot_row():
    pool = _Pool()

    payload = await coverage_snapshot.refresh(pool, computed_at=NOW)

    assert pool.connection.written == (payload, NOW)
    assert payload["board_map"]["totals"]["boards"] == 1


@pytest.mark.asyncio
async def test_poll_cycle_refreshes_snapshot_once(monkeypatch):
    """Once per cycle even with nothing due -- it is the read model, not a
    per-feed write."""
    refreshed = []

    async def due_sources(conn, now):
        return []

    async def claim(conn, now, limit, *, lease_seconds, source=None):
        return []

    async def refresh(pool):
        refreshed.append(pool)

    monkeypatch.setattr(store, "due_sources", due_sources)
    monkeypatch.setattr(store, "claim_due_feeds", claim)
    monkeypatch.setattr(coverage_snapshot, "refresh", refresh)
    pool = _Pool()
    cfg = run.Config(dsn="postgresql://example/test", user_agent="test")

    result = await run.cycle(pool, object(), cfg, NOW)
    assert result.polled == 0 and result.drained is True
    assert refreshed == [pool]


def test_board_state_ranks_deliberate_holds_above_derived_signals():
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(minutes=5)
    ancient = now - timedelta(days=2)
    board_state = coverage_snapshot.board_state
    # An operator hold outranks strikes; repeated failures outrank staleness.
    assert board_state(5, ancient, now, 900, now) == "paused"
    assert board_state(3, ancient, None, 900, now) == "failing"
    assert board_state(1, fresh, None, 900, now) == "degraded"
    assert board_state(0, None, None, 900, now) == "never"
    assert board_state(0, ancient, None, 900, now) == "stale"
    assert board_state(0, fresh, None, 900, now) == "ok"
    # No cadence configured: staleness cannot be judged, so a polled feed is ok.
    assert board_state(0, ancient, None, 0, now) == "ok"


def test_both_schemas_define_the_singleton_snapshot():
    root = Path(__file__).parent.parent.parent

    for path in (root / "schema.sql", root / "schema-thin.sql"):
        sql = path.read_text()
        assert "CREATE TABLE" in sql
        assert "coverage_snapshot (" in sql
        assert "id          boolean PRIMARY KEY DEFAULT true CHECK (id)" in sql
        assert "payload     jsonb NOT NULL" in sql


def test_live_database_migration_defines_and_seeds_snapshot():
    root = Path(__file__).parent.parent.parent
    sql = (
        root / "xjobscore" / "migrations" / "20260902_coverage_snapshot.sql"
    ).read_text()

    assert "CREATE TABLE IF NOT EXISTS coverage_snapshot" in sql
    assert "ON CONFLICT (id) DO NOTHING" in sql
