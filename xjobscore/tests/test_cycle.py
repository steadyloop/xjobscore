"""`run.cycle` -- per-vendor worker pools under a time budget.

No database and no network: `store.due_sources` / `store.claim_due_feeds` /
`run.poll` are replaced with scripted queues, so what is under test is the
pooling itself -- how many workers a vendor gets, that they refill without
waiting on each other, and when the budget stops them.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from xjobscore import coverage_snapshot, run, store
from xjobscore.records import Feed

pytestmark = pytest.mark.asyncio


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Pool:
    def acquire(self):
        return _AsyncContext(object())


def _feed(source: str, index: int) -> Feed:
    return Feed(id=index, source=source, key=f"{source}-{index}")


def _cfg(**kwargs) -> run.Config:
    return run.Config(dsn="postgres://test", user_agent="test", **kwargs)


class _Harness:
    """Scripted queues plus per-source in-flight tracking."""

    def __init__(self, monkeypatch, queues, widths, *, infinite=()):
        self.queues = {source: list(feeds) for source, feeds in queues.items()}
        self.infinite = set(infinite)
        self.claims: list[tuple[str, object]] = []
        self.polls: list[tuple[Feed, object]] = []
        self.order: list[str] = []
        self.active: dict[str, int] = {}
        self.peak: dict[str, int] = {}
        self.refreshed: list[object] = []
        self._next_id = 1000

        async def due_sources(conn, now):
            return sorted(self.queues)

        async def claim_due_feeds(conn, now, limit, *, lease_seconds, source=None):
            self.claims.append((source, now))
            if source in self.infinite:
                self._next_id += 1
                return [_feed(source, self._next_id)]
            queue = self.queues.get(source) or []
            return [queue.pop(0)] if queue else []

        async def due_feeds(conn, now, limit=None, *, source=None):
            self.claims.append((source, now))
            return list(self.queues.get(source) or [])

        async def refresh(pool):
            self.refreshed.append(pool)

        monkeypatch.setattr(store, "due_sources", due_sources)
        monkeypatch.setattr(store, "claim_due_feeds", claim_due_feeds)
        monkeypatch.setattr(store, "due_feeds", due_feeds)
        monkeypatch.setattr(coverage_snapshot, "refresh", refresh)
        monkeypatch.setattr(run.sources, "width", lambda name: widths.get(name, 1))

    def poll(self, sleeps: dict[str, float] | None = None):
        sleeps = sleeps or {}

        async def poll(pool, net, feed, now):
            source = feed.source
            self.polls.append((feed, now))
            self.active[source] = self.active.get(source, 0) + 1
            self.peak[source] = max(self.peak.get(source, 0), self.active[source])
            await asyncio.sleep(sleeps.get(source, 0))
            self.active[source] -= 1
            self.order.append(source)
            return "ok"

        return poll


async def test_each_vendor_runs_exactly_its_own_width(monkeypatch):
    harness = _Harness(
        monkeypatch,
        {"greenhouse": [_feed("greenhouse", i) for i in range(6)],
         "workday": [_feed("workday", i) for i in range(3)]},
        {"greenhouse": 3, "workday": 1},
    )
    monkeypatch.setattr(run, "poll", harness.poll({"greenhouse": 0.01, "workday": 0.01}))

    result = await run.cycle(_Pool(), object(), _cfg(), run.datetime.now(run.timezone.utc))

    assert harness.peak == {"greenhouse": 3, "workday": 1}
    assert result.polled == 9
    assert result.by_source == {"greenhouse": 6, "workday": 3}
    assert result.drained is True
    assert harness.refreshed  # exactly one read-model refresh per cycle
    assert len(harness.refreshed) == 1


async def test_a_slow_vendor_does_not_hold_up_a_fast_one(monkeypatch):
    """The convoy this replaces: one 11s Workday poll used to idle 19 slots."""
    harness = _Harness(
        monkeypatch,
        {"greenhouse": [_feed("greenhouse", i) for i in range(3)],
         "workday": [_feed("workday", 0)]},
        {"greenhouse": 3, "workday": 1},
    )
    monkeypatch.setattr(run, "poll", harness.poll({"greenhouse": 0.01, "workday": 0.2}))

    started = time.monotonic()
    result = await run.cycle(_Pool(), object(), _cfg(), run.datetime.now(run.timezone.utc))
    elapsed = time.monotonic() - started

    assert harness.order[:3] == ["greenhouse"] * 3   # all three finished first
    assert harness.order[-1] == "workday"
    assert elapsed < 0.4                              # not 3 x 0.2 serialised
    assert result.drained is True


async def test_the_budget_stops_claiming_and_reports_undrained(monkeypatch):
    harness = _Harness(
        monkeypatch, {"greenhouse": []}, {"greenhouse": 2}, infinite={"greenhouse"}
    )
    monkeypatch.setattr(run, "poll", harness.poll({"greenhouse": 0.02}))

    result = await run.cycle(
        _Pool(), object(), _cfg(cycle_budget_s=0.05), run.datetime.now(run.timezone.utc)
    )

    assert result.drained is False       # feeds were still due when time ran out
    assert 0 < result.polled < 40        # bounded, not the infinite supply
    # Every claimed feed was polled to completion: the budget stops claiming,
    # it does not abandon a poll already in flight.
    assert len(harness.polls) == result.polled
    assert harness.active == {"greenhouse": 0}


async def test_shutdown_signal_stops_claiming_new_feeds(monkeypatch):
    harness = _Harness(
        monkeypatch, {"greenhouse": []}, {"greenhouse": 2}, infinite={"greenhouse"}
    )
    monkeypatch.setattr(run, "poll", harness.poll())

    result = await run.cycle(
        _Pool(),
        object(),
        _cfg(),
        run.datetime.now(run.timezone.utc),
        accepting=lambda: False,
    )

    assert result.polled == 0
    assert result.drained is False
    assert harness.claims == []


async def test_drained_needs_every_source_to_run_dry(monkeypatch):
    harness = _Harness(
        monkeypatch,
        {"greenhouse": [_feed("greenhouse", 0)], "workday": []},
        {"greenhouse": 1, "workday": 1},
        infinite={"workday"},
    )
    monkeypatch.setattr(run, "poll", harness.poll({"workday": 0.01}))

    result = await run.cycle(
        _Pool(), object(), _cfg(cycle_budget_s=0.05), run.datetime.now(run.timezone.utc)
    )

    assert result.drained is False       # greenhouse ran dry, workday did not


async def test_each_poll_is_stamped_with_its_own_claim_time(monkeypatch):
    """A 30s cycle must not backdate every poll_log row to its own start."""
    harness = _Harness(
        monkeypatch, {"greenhouse": [_feed("greenhouse", i) for i in range(4)]}, {"greenhouse": 1}
    )
    monkeypatch.setattr(run, "poll", harness.poll({"greenhouse": 0.01}))
    cycle_now = run.datetime.now(run.timezone.utc)

    await run.cycle(_Pool(), object(), _cfg(), cycle_now)

    stamps = [now for _, now in harness.polls]
    claim_stamps = [now for _, now in harness.claims]
    assert all(stamp >= cycle_now for stamp in stamps)
    assert stamps == sorted(stamps)              # monotonic across the cycle
    assert len(set(stamps)) == len(stamps)       # not all the cycle's single `now`
    # Each poll carries the timestamp of the claim that produced it.
    assert stamps == [stamp for stamp in claim_stamps if stamp in set(stamps)]


async def test_nothing_due_is_drained_and_still_refreshes_once(monkeypatch):
    harness = _Harness(monkeypatch, {}, {})
    monkeypatch.setattr(run, "poll", harness.poll())

    result = await run.cycle(_Pool(), object(), _cfg(), run.datetime.now(run.timezone.utc))

    assert result == run.CycleResult(polled=0, drained=True, by_source={})
    assert len(harness.refreshed) == 1
    assert harness.claims == []


async def test_dry_run_snapshots_each_queue_and_never_claims(monkeypatch):
    harness = _Harness(
        monkeypatch, {"greenhouse": [_feed("greenhouse", i) for i in range(3)]}, {"greenhouse": 2}
    )
    fetched: list[str] = []

    def get(name):
        async def harvest(feed, net):
            fetched.append(feed.key)
            if feed.key == "greenhouse-0":
                raise RuntimeError("dead board")
            return None
        return harvest

    monkeypatch.setattr(run.sources, "get", get)
    monkeypatch.setattr(
        run, "poll", lambda *a, **k: pytest.fail("dry run must never poll")
    )

    result = await run.cycle(
        _Pool(), object(), _cfg(dry_run=True), run.datetime.now(run.timezone.utc)
    )

    assert sorted(fetched) == ["greenhouse-0", "greenhouse-1", "greenhouse-2"]
    assert result.polled == 3 and result.drained is True
    assert harness.refreshed == []               # dry run writes nothing, not even the read model
    # One snapshot read for the source, and no per-feed claim after it: a dry
    # run takes no lease, so a refilling claim would reselect the same rows.
    assert [source for source, _ in harness.claims] == ["greenhouse"]
