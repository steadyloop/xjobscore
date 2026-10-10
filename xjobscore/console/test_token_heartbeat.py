from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from xjobscore.console import token_heartbeat as heartbeat

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


class FakePool:
    def __init__(self, *, rows=(), values=(), execute_results=()):
        self.rows = list(rows)
        self.values = list(values)
        self.execute_results = list(execute_results)
        self.calls = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.rows.pop(0) if self.rows else None

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self.values.pop(0) if self.values else None

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return self.execute_results.pop(0) if self.execute_results else "UPDATE 1"


def heartbeat_row(**overrides):
    row = {
        "id": True,
        "paused": False,
        "paused_changed_at": None,
        "due_at": NOW + timedelta(hours=4),
        "hold_until": None,
        "runner_seen_at": NOW - timedelta(seconds=30),
        "last_started_at": NOW - timedelta(hours=2, minutes=1),
        "last_completed_at": NOW - timedelta(hours=2),
        "last_outcome": "success",
        "last_candidates": 20,
        "last_validated": 20,
        "last_promoted": 18,
        "last_rejected": 2,
        "last_error": None,
        "eligible": 37,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    "overrides,state",
    [
        ({}, "healthy"),
        ({"hold_until": NOW + timedelta(minutes=5)}, "running"),
        ({"runner_seen_at": NOW - timedelta(minutes=10)}, "offline"),
        ({"last_outcome": "error", "last_error": "boom"}, "attention"),
        ({"due_at": NOW - timedelta(seconds=1)}, "queued"),
        ({"last_outcome": "never"}, "never"),
        ({"paused": True}, "paused"),
        # The switch outranks a stale runner and a due queue alike.
        (
            {"paused": True, "runner_seen_at": NOW - timedelta(minutes=10)},
            "paused",
        ),
        ({"paused": True, "due_at": NOW - timedelta(seconds=1)}, "paused"),
        # ...but not a cycle already in flight, which is a fact, not a plan.
        ({"paused": True, "hold_until": NOW + timedelta(minutes=5)}, "running"),
    ],
)
async def test_snapshot_derives_system_state(overrides, state):
    pool = FakePool(rows=[heartbeat_row(**overrides)])

    result = await heartbeat.heartbeat_snapshot(
        pool,
        current=NOW,
        configured=True,
        offline_after_s=180,
    )

    assert result["state"] == state
    assert result["eligible"] == 37
    assert result["last_promoted"] == 18
    assert result["due_at"] == heartbeat_row(**overrides)["due_at"].isoformat()


async def test_claim_is_a_single_atomic_leased_update():
    hold_until = NOW + timedelta(minutes=15)
    pool = FakePool(rows=[{"hold_until": hold_until}, None])

    first = await heartbeat.claim_due(pool, current=NOW, lease_seconds=900)
    second = await heartbeat.claim_due(pool, current=NOW, lease_seconds=900)

    assert first == heartbeat.HeartbeatLease(hold_until)
    assert second is None
    _, sql, args = pool.calls[0]
    assert "due_at <= $1" in sql
    assert "hold_until IS NULL OR hold_until <= $1" in sql
    assert args == (NOW, hold_until)


async def test_success_and_failure_schedule_the_expected_delays():
    lease = heartbeat.HeartbeatLease(NOW + timedelta(minutes=15))
    pool = FakePool(execute_results=["UPDATE 1", "UPDATE 1"])

    succeeded = await heartbeat.finish_success(
        pool,
        lease,
        current=NOW,
        interval_seconds=21600,
        candidates=20,
        validated=20,
        promoted=18,
        rejected=2,
    )
    failed = await heartbeat.finish_failure(
        pool,
        lease,
        current=NOW,
        retry_seconds=900,
        error="RuntimeError: boom",
    )

    assert succeeded is True
    assert failed is True
    assert pool.calls[0][2][1] == NOW + timedelta(hours=6)
    assert pool.calls[1][2][1] == NOW + timedelta(minutes=15)


async def test_run_cycle_revalidates_and_only_promotes_live(monkeypatch):
    promoted = []
    finished = []

    async def candidates(pool, *, limit):
        return ([("workable", "live"), ("workable", "gone")], 2)

    async def validate(identities, **kwargs):
        return [
            {"ats": "workable", "token": "live", "status": "live"},
            {"ats": "workable", "token": "gone", "status": "dead"},
        ]

    async def promote(pool, identities):
        promoted.extend(identities)
        return len(identities)

    async def finish(pool, lease, **kwargs):
        finished.append(kwargs)
        return True

    monkeypatch.setattr(heartbeat, "promotion_candidates", candidates)
    monkeypatch.setattr(heartbeat.validator, "validate_tokens", validate)
    monkeypatch.setattr(heartbeat, "promote_verified", promote)
    monkeypatch.setattr(heartbeat, "finish_success", finish)

    result = await heartbeat.run_claimed_cycle(
        object(),
        heartbeat.HeartbeatLease(NOW),
        batch=20,
        interval_seconds=21600,
        retry_seconds=900,
    )

    assert promoted == [("workable", "live")]
    assert result == {"candidates": 2, "validated": 2, "promoted": 1, "rejected": 1}
    assert finished[0]["interval_seconds"] == 21600


async def test_system_failure_is_persisted_with_retry(monkeypatch):
    failures = []

    async def candidates(pool, *, limit):
        raise RuntimeError("database interrupted")

    async def finish(pool, lease, **kwargs):
        failures.append(kwargs)
        return True

    monkeypatch.setattr(heartbeat, "promotion_candidates", candidates)
    monkeypatch.setattr(heartbeat, "finish_failure", finish)

    with pytest.raises(RuntimeError, match="database interrupted"):
        await heartbeat.run_claimed_cycle(
            object(),
            heartbeat.HeartbeatLease(NOW),
            batch=20,
            interval_seconds=21600,
            retry_seconds=900,
        )

    assert failures[0]["retry_seconds"] == 900
    assert failures[0]["error"] == "RuntimeError: database interrupted"


async def test_enqueue_refuses_an_active_lease():
    pool = FakePool(rows=[None, {"paused": False}])

    with pytest.raises(heartbeat.HeartbeatAlreadyRunning):
        await heartbeat.enqueue_now(pool, NOW)


async def test_enqueue_refuses_a_paused_heartbeat():
    pool = FakePool(rows=[None, {"paused": True}])

    with pytest.raises(heartbeat.HeartbeatPaused):
        await heartbeat.enqueue_now(pool, NOW)

    _, sql, _ = pool.calls[0]
    assert "NOT paused" in sql


async def test_set_paused_stamps_the_switch():
    pool = FakePool()

    await heartbeat.set_paused(pool, True, current=NOW)

    _, sql, args = pool.calls[0]
    assert "SET paused = $1, paused_changed_at = $2" in sql
    assert args == (True, NOW)


async def test_touch_runner_reports_the_switch():
    pool = FakePool(rows=[{"paused": True}])

    paused = await heartbeat.touch_runner(pool, NOW)

    _, sql, args = pool.calls[0]
    assert paused is True
    assert "RETURNING paused" in sql
    assert args == (NOW,)


class FakePolicy:
    def __init__(self, pool=None):
        self.pool = pool

    async def pool_or_none(self):
        return self.pool


async def test_tick_stamps_liveness_but_claims_nothing_while_paused(monkeypatch):
    claims = []

    async def touch(pool, current):
        return True

    async def claim(pool, **kwargs):
        claims.append(kwargs)
        return heartbeat.HeartbeatLease(NOW)

    monkeypatch.setattr(heartbeat, "touch_runner", touch)
    monkeypatch.setattr(heartbeat, "claim_due", claim)
    runner = heartbeat.TokenHeartbeatRunner(FakePolicy(FakePool()), enabled=True)

    assert await runner.tick() is None
    assert claims == []


async def test_runner_is_inert_until_explicitly_enabled():
    runner = heartbeat.TokenHeartbeatRunner(FakePolicy(), enabled=False)
    await runner.start()
    assert runner.alive is False
    runner.nudge()
    await runner.stop()


async def test_runner_nudge_wakes_the_loop(monkeypatch):
    calls = []

    async def tick(self):
        calls.append(1)

    monkeypatch.setattr(heartbeat.TokenHeartbeatRunner, "tick", tick)
    runner = heartbeat.TokenHeartbeatRunner(FakePolicy(), enabled=True, tick_s=600)
    await runner.start()
    try:
        for _ in range(100):
            if calls:
                break
            await asyncio.sleep(0.01)
        runner.nudge()
        for _ in range(100):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(calls) >= 2
    finally:
        await runner.stop()
