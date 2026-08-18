"""Unit tests for runner.py -- no real database, no real network.

``SchedulerRunner._tick`` is tested directly rather than through the full
``_loop``/``asyncio.sleep`` cycle wherever possible: the draining and
pause-checking logic all lives in ``_tick``, so exercising it in isolation
keeps these tests fast and deterministic. A couple of tests do drive the
full ``_loop`` (with the tick interval set to ~0) to cover the parts that
only exist at that level: the try/except that keeps one bad tick from
killing the task, and ``stop()``'s cancellation.
"""

from __future__ import annotations

import asyncio

import pytest

from xjobscore import gold
from xjobscore.inspector import runner as runner_module
from xjobscore.inspector.runner import GoldJob, SchedulerRunner
from xjobscore.net import Net


class FakePolicy:
    """Records scheduler_control()/pool_or_none() calls with scripted answers."""

    def __init__(self, *, running: bool | None = True, pool: object = "pool"):
        self.running = running
        self.pool = pool
        self.control_calls = 0

    async def scheduler_control(self):
        self.control_calls += 1
        if self.running is None:
            return None
        return {"running": self.running, "changed_at": None, "note": None}

    async def pool_or_none(self):
        return self.pool


def make_runner(policy, *, batch=20, tick_s=1000.0, enabled=True):
    runner = SchedulerRunner(
        policy,
        enabled=enabled,
        user_agent="runner-test",
        tick_s=tick_s,
        batch=batch,
        lease_seconds=60,
    )
    # _tick calls _refresh_registry(), which parses the whole real registry off
    # a thread. These tests care about pause/drain/error behavior, not registry
    # parsing, so stand in trivial already-built values.
    runner._registry_index = {}
    runner._registry_types = {}
    runner._registry_built_at = float("inf")
    return runner


# --------------------------------------------------------------------- tick


async def test_tick_skips_a_cycle_when_paused(monkeypatch):
    calls = []
    monkeypatch.setattr(runner_module.run, "cycle", _record(calls))
    policy = FakePolicy(running=False)
    runner = make_runner(policy)

    await runner._tick(net=object())

    assert calls == []
    assert runner.status()["last_cycle_at"] is None


async def test_tick_skips_when_control_unavailable(monkeypatch):
    """Degraded PostgreSQL: scheduler_control() returns None, same as paused."""
    calls = []
    monkeypatch.setattr(runner_module.run, "cycle", _record(calls))
    runner = make_runner(FakePolicy(running=None))

    await runner._tick(net=object())

    assert calls == []


async def test_tick_polls_once_when_below_a_full_batch(monkeypatch):
    calls = []
    monkeypatch.setattr(runner_module.run, "cycle", _record(calls, returns=[5]))
    runner = make_runner(FakePolicy(running=True), batch=20)

    await runner._tick(net=object())

    assert len(calls) == 1
    status = runner.status()
    assert status["last_polled"] == 5
    assert status["last_cycle_at"] is not None


async def test_tick_drains_consecutive_full_batches(monkeypatch):
    """A full batch means more feeds were due than one cycle claimed."""
    calls = []
    monkeypatch.setattr(runner_module.run, "cycle", _record(calls, returns=[3, 3, 1]))
    runner = make_runner(FakePolicy(running=True), batch=3)

    await runner._tick(net=object())

    assert len(calls) == 3
    assert runner.status()["last_polled"] == 1


async def test_tick_stops_draining_if_paused_mid_loop(monkeypatch):
    """Every full-batch iteration re-reads the control flag before looping again."""
    calls = []
    policy = FakePolicy(running=True)

    async def cycle(pool, net, cfg, now, *, index=None, types=None):
        calls.append(1)
        policy.running = False  # paused from another tab, mid-drain
        return 3  # always a full batch: only the control re-check should stop it

    monkeypatch.setattr(runner_module.run, "cycle", cycle)
    runner = make_runner(policy, batch=3)

    await runner._tick(net=object())

    assert len(calls) == 1


def _record(calls: list, returns: list[int] | None = None):
    returns = list(returns or [0])

    async def cycle(pool, net, cfg, now, *, index=None, types=None):
        calls.append((pool, cfg.batch))
        return returns[min(len(calls) - 1, len(returns) - 1)]

    return cycle


# --------------------------------------------------------------------- loop


async def test_start_is_a_no_op_when_disabled():
    runner = make_runner(FakePolicy(running=True), enabled=False)
    await runner.start()
    assert runner.status()["alive"] is False
    await runner.stop()  # must not raise on a runner that never started


async def test_loop_survives_a_raising_tick_and_recovers(monkeypatch):
    calls: list[int] = []

    async def flaky_tick(self, net):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(SchedulerRunner, "_tick", flaky_tick)
    runner = make_runner(FakePolicy(running=True), tick_s=0.01)

    await runner.start()
    try:
        for _ in range(200):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("runner never recovered from the raising tick")
        assert runner.status()["consecutive_errors"] == 0
    finally:
        await runner.stop()

    assert runner.status()["alive"] is False


async def test_stop_cancels_a_running_loop(monkeypatch):
    async def slow_tick(self, net):
        await asyncio.sleep(10)

    monkeypatch.setattr(SchedulerRunner, "_tick", slow_tick)
    runner = make_runner(FakePolicy(running=True), tick_s=0.01)

    await runner.start()
    assert runner.status()["alive"] is True
    await asyncio.wait_for(runner.stop(), timeout=2.0)
    assert runner.status()["alive"] is False


async def test_nudge_ticks_without_waiting_out_the_interval(monkeypatch):
    """"Poll now" must not mean "poll within a minute".

    tick_s is set far above the test's own timeout, so a second tick can only
    happen because nudge() cut the wait short.
    """
    calls: list[int] = []

    async def counting_tick(self, net):
        calls.append(1)

    monkeypatch.setattr(SchedulerRunner, "_tick", counting_tick)
    runner = make_runner(FakePolicy(running=True), tick_s=600.0)

    await runner.start()
    try:
        for _ in range(200):  # let the first tick land and the loop settle
            if calls:
                break
            await asyncio.sleep(0.01)
        runner.nudge()
        for _ in range(200):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("nudge() did not wake the loop")
    finally:
        await runner.stop()


async def test_nudge_overrides_the_error_backoff(monkeypatch):
    """An operator asking for a poll outranks the last tick's exception."""
    calls: list[int] = []

    async def failing_tick(self, net):
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(SchedulerRunner, "_tick", failing_tick)
    # tick_s * 2**errors would be 300s of backoff after the first failure.
    runner = make_runner(FakePolicy(running=True), tick_s=150.0)

    await runner.start()
    try:
        for _ in range(200):
            if calls:
                break
            await asyncio.sleep(0.01)
        assert runner.status()["consecutive_errors"] == 1
        runner.nudge()
        for _ in range(200):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("nudge() did not cut the backoff short")
    finally:
        await runner.stop()


async def test_nudge_on_a_disabled_runner_is_inert():
    """The API calls nudge() unconditionally; a process with no loop must not care."""
    runner = make_runner(FakePolicy(running=True), enabled=False)
    await runner.start()
    runner.nudge()
    assert runner.status()["alive"] is False
    await runner.stop()


async def test_net_opens_and_closes_cleanly_around_the_loop():
    """The loop borrows the shared Net; stop() is what closes it. That alone
    must not touch the network or fail without one."""
    runner = make_runner(FakePolicy(running=False), tick_s=0.01)
    await runner.start()
    await asyncio.sleep(0.05)
    await runner.stop()
    assert runner.status()["alive"] is False
    assert runner._net is None


# ------------------------------------------------------------ poll_feed_now


class PollConn:
    """Scripted fetchrow (refusal diagnostics) and fetchval (new_postings)."""

    def __init__(self, *, diagnostic_row=None, new_postings=None):
        self.diagnostic_row = diagnostic_row
        self.new_postings = new_postings
        self.fetchval_args = None

    async def fetchrow(self, sql, *args):
        return self.diagnostic_row

    async def fetchval(self, sql, *args):
        self.fetchval_args = args
        return self.new_postings


class PollPool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _poll_feed(**overrides):
    from xjobscore.records import Feed

    defaults = dict(id=7, source="route-test", key="demo")
    return Feed(**{**defaults, **overrides})


async def test_poll_feed_now_claims_hydrates_and_polls(monkeypatch):
    """One call: claim the lease, hydrate, run.poll, read back new_postings --
    all against the same `now`, with no gate on control or aliveness."""
    conn = PollConn(new_postings=3)
    runner = make_runner(FakePolicy(running=False), enabled=False)
    runner._policy.pool = PollPool(conn)

    claims = []

    async def claim_feed(c, *, source, key, now, lease_seconds):
        claims.append((source, key, now, lease_seconds))
        return _poll_feed()

    polls = []

    async def poll(pool, net, feed, now):
        polls.append((feed, now))
        return "ok"

    monkeypatch.setattr(runner_module.store, "claim_feed", claim_feed)
    monkeypatch.setattr(runner_module.run, "poll", poll)
    # poll_feed_now must not need a real Net either.
    monkeypatch.setattr(SchedulerRunner, "_ensure_net", _fake_ensure_net)

    result = await runner.poll_feed_now("route-test", "demo")

    assert result == {"status": "ok", "feed_id": 7, "new_postings": 3}
    assert claims[0][:2] == ("route-test", "demo")
    assert claims[0][3] == 60  # make_runner's lease_seconds, same as the loop
    (feed, poll_now) = polls[0]
    assert feed.source_type == "Other"  # _hydrate ran (empty test index)
    assert poll_now == claims[0][2]  # claim and poll share one `now`
    assert conn.fetchval_args == (7, poll_now)  # read back by that same stamp


async def _fake_ensure_net(self):
    return object()


async def test_poll_feed_now_degraded_postgres(monkeypatch):
    runner = make_runner(FakePolicy(running=True, pool=None))

    with pytest.raises(runner_module.PolicyUnavailable):
        await runner.poll_feed_now("route-test", "demo")


@pytest.mark.parametrize(
    ("diagnostic_row", "expected_reason"),
    [
        ({"enabled": False, "paused_at": None, "held": False}, "feed_disabled"),
        ({"enabled": True, "paused_at": "x", "held": False}, "feed_paused"),
        ({"enabled": True, "paused_at": None, "held": True}, "already_polling"),
        # A pure race: eligible on re-read, but the claim itself lost.
        ({"enabled": True, "paused_at": None, "held": False}, "already_polling"),
    ],
)
async def test_poll_feed_now_refuses_unclaimable(monkeypatch, diagnostic_row, expected_reason):
    conn = PollConn(diagnostic_row=diagnostic_row)
    runner = make_runner(FakePolicy(running=True, pool=PollPool(conn)))

    async def claim_feed(c, **kwargs):
        return None

    monkeypatch.setattr(runner_module.store, "claim_feed", claim_feed)
    monkeypatch.setattr(SchedulerRunner, "_ensure_net", _fake_ensure_net)

    with pytest.raises(runner_module.PollNowRefused) as excinfo:
        await runner.poll_feed_now("route-test", "demo")

    assert excinfo.value.reason == expected_reason


@pytest.mark.parametrize("diagnostic_row", [None])  # no feed row at all
async def test_poll_feed_now_unknown_feed(monkeypatch, diagnostic_row):
    conn = PollConn(diagnostic_row=diagnostic_row)
    runner = make_runner(FakePolicy(running=True, pool=PollPool(conn)))

    async def claim_feed(c, **kwargs):
        return None

    monkeypatch.setattr(runner_module.store, "claim_feed", claim_feed)
    monkeypatch.setattr(SchedulerRunner, "_ensure_net", _fake_ensure_net)

    with pytest.raises(runner_module.FeedNotInPostgres):
        await runner.poll_feed_now("route-test", "demo")


async def test_stop_closes_a_net_opened_without_the_loop():
    """poll_feed_now on a never-started runner opens the shared Net; the
    lifespan's unconditional stop() must be what closes it."""
    runner = make_runner(FakePolicy(running=True), enabled=False)
    net = await runner._ensure_net()
    assert isinstance(net, Net)
    assert runner._net is net

    await runner.stop()
    assert runner._net is None


# -------------------------------------------------------------------- gold


class GoldPolicy:
    def __init__(self, pool):
        self._pool = pool

    async def pool_or_none(self):
        return self._pool


class _NullCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    def transaction(self):
        return _NullCtx()


class FakePool:
    def acquire(self):
        return _AcquireCtx()


class _AcquireCtx:
    async def __aenter__(self):
        return FakeConn()

    async def __aexit__(self, *exc):
        return False


async def test_gold_job_single_flight(monkeypatch):
    async def slow_rebuild(conn, *, dry_run=False):
        await asyncio.sleep(0.05)
        return gold.Counts(groups=1, postings=2)

    monkeypatch.setattr(
        gold, "steps", lambda only=None: (gold.Step("dedup", 1, slow_rebuild),)
    )
    job = GoldJob(GoldPolicy(FakePool()))

    job.start()
    assert job.state == "running"
    with pytest.raises(RuntimeError):
        job.start()

    await job._task
    assert job.state == "done"
    assert job.counts == {"groups": 1, "postings": 2, "skipped_keys": 0, "skipped_postings": 0}
    assert job.error is None


async def test_gold_job_captures_failure(monkeypatch):
    async def failing_rebuild(conn, *, dry_run=False):
        raise ValueError("bad plan")

    monkeypatch.setattr(
        gold, "steps", lambda only=None: (gold.Step("dedup", 1, failing_rebuild),)
    )
    job = GoldJob(GoldPolicy(FakePool()))

    job.start()
    await job._task

    assert job.state == "failed"
    assert "bad plan" in job.error


async def test_gold_job_degraded_postgres():
    job = GoldJob(GoldPolicy(None))
    job.start()
    await job._task
    assert job.state == "failed"
    assert "PostgreSQL unavailable" in job.error
