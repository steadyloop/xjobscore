from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from xjobscore import gold, publish, router, run, runtime, silver, store, workable_policy
from xjobscore.net import NetError
from xjobscore.pipeline import GoldCoordinator, GoldResult
from xjobscore.records import Feed, Item, Record
from xjobscore.runtime import (
    GoldWorker,
    NeonPublisher,
    RuntimeSettings,
    SystemScheduler,
    UnifiedRuntime,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Connection:
    def transaction(self):
        return _Context(self)


class _Pool:
    def __init__(self):
        self.connection = _Connection()

    def acquire(self):
        return _Context(self.connection)


class _Coordinator:
    def __init__(self, *, hold_gold: bool = False):
        self.marked: list[tuple[set[int], set[int]]] = []
        self.finished: list[dict] = []
        self.notifications = 0
        # When set, deferred Gold waits here instead of running at once, so a
        # test can observe the poll returning before its Gold has run.
        self.hold_gold = hold_gold
        self.deferred: list = []

    async def defer(self, job):
        if self.hold_gold:
            self.deferred.append(job)
        else:
            await job()

    async def begin_feed(self, pool, feed):
        return 17

    async def mark_changed(self, conn, changes, *, current):
        self.marked.append((set(changes.posting_ids), set(changes.company_ids)))

    async def enrich_changed(self, pool, changes):
        return GoldResult(errors={"skills": "RuntimeError: broken taxonomy"})

    async def finish_feed(self, pool, run_id, **kwargs):
        self.finished.append({"run_id": run_id, **kwargs})

    def notify_changed(self):
        self.notifications += 1


async def test_gold_failure_degrades_pipeline_without_failing_source(monkeypatch):
    pool = _Pool()
    coordinator = _Coordinator()
    feed = Feed(
        id=1,
        source="fixture",
        key="acme",
        company_id=42,
        interval_s=900,
        hold_until=NOW + timedelta(hours=1),
    )
    record = Record(items=[Item("job-1", {"title": "Intern"})])
    schedule = []

    async def harvest(feed, net):
        return record

    async def persist(conn, feed, prepared):
        return [router.StoredRaw(prepared[0].item, 7, prepared[0].data_hash, True)]

    async def process(conn, feed, stored, envelope, **kwargs):
        kwargs["changes"].add(101, 42)
        return silver.Counts(postings=1, created=1)

    async def success(conn, feed, **kwargs):
        schedule.append("success")
        return True

    async def failure(*args, **kwargs):
        schedule.append("failure")

    async def observe(*args, **kwargs):
        return None

    monkeypatch.setattr(run.sources, "get", lambda name: harvest)
    monkeypatch.setattr(router, "persist", persist)
    monkeypatch.setattr(silver, "process", process)
    monkeypatch.setattr(store, "finish_schedule_success", success)
    monkeypatch.setattr(store, "finish_schedule_failure", failure)
    monkeypatch.setattr(store, "append_poll_log", observe)

    async def mark_missing(conn, source, polled_at, **kwargs):
        return 0

    monkeypatch.setattr(store, "mark_missing_postings", mark_missing)

    outcome = await run.poll(
        pool, object(), feed, NOW, coordinator=coordinator
    )

    assert outcome == "ok"
    assert schedule == ["success"]
    assert coordinator.marked == [({101}, {42})]
    assert coordinator.notifications == 1
    assert coordinator.finished[0]["gold_result"].degraded is True
    assert coordinator.finished[0]["error"] is None
    assert set(coordinator.finished[0]["stages"]) == {
        "fetch", "bronze", "silver", "gold"
    }
    assert coordinator.finished[0]["counts"] == {
        "fetch": {
            "items": 1,
            "http_status": None,
            "truncated": False,
            "reused_items": 0,
            "detail_fetched": 0,
        },
        "bronze": {"rows": 1, "inserted": 1},
        "silver": {
            "postings": 1,
            "created": 1,
            "unchanged": 0,
            "unresolved": 0,
            "unsupported": 0,
            "secondhand": 0,
            "unparseable": 0,
            "minted": 0,
        },
    }


async def test_poll_returns_before_its_gold_runs(monkeypatch):
    """Gold is handed to the coordinator, not awaited by the poll worker: on
    2026-10-05 awaiting it was ~27s of a ~48s Workday poll whose fetch took
    2.5s, and the worker could claim nothing else meanwhile."""
    coordinator = _Coordinator(hold_gold=True)
    feed = Feed(
        id=1, source="fixture", key="acme", company_id=42, interval_s=900,
        hold_until=NOW + timedelta(hours=1),
    )

    async def harvest(feed, net):
        return Record(items=[Item("job-1", {"title": "Intern"})])

    async def persist(conn, feed, prepared):
        return [router.StoredRaw(prepared[0].item, 7, prepared[0].data_hash, True)]

    async def process(conn, feed, stored, envelope, **kwargs):
        kwargs["changes"].add(101, 42)
        return silver.Counts(postings=1, created=1)

    async def success(conn, feed, **kwargs):
        return True

    async def nothing(*args, **kwargs):
        return 0

    monkeypatch.setattr(run.sources, "get", lambda name: harvest)
    monkeypatch.setattr(router, "persist", persist)
    monkeypatch.setattr(silver, "process", process)
    monkeypatch.setattr(store, "finish_schedule_success", success)
    monkeypatch.setattr(store, "append_poll_log", nothing)
    monkeypatch.setattr(store, "mark_missing_postings", nothing)

    outcome = await run.poll(_Pool(), object(), feed, NOW, coordinator=coordinator)

    assert outcome == "ok"
    assert coordinator.finished == []  # the run stays open until its Gold runs
    assert len(coordinator.deferred) == 1

    await coordinator.deferred[0]()

    assert coordinator.notifications == 1
    assert coordinator.finished[0]["outcome"] == "ok"
    assert "gold" in coordinator.finished[0]["stages"]


async def test_deferred_gold_is_bounded_and_drained():
    from xjobscore import pipeline

    coordinator = GoldCoordinator(_Runs())
    running = peak = 0
    done = []

    def job(index):
        async def run_job():
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
            done.append(index)
        return run_job

    for index in range(10):
        await coordinator.defer(job(index))
    await coordinator.drain()

    assert peak == pipeline.DEFERRED_GOLD_CONCURRENCY
    assert sorted(done) == list(range(10))


async def test_defer_waits_once_the_gold_queue_is_full(monkeypatch):
    from xjobscore import pipeline

    monkeypatch.setattr(pipeline, "DEFERRED_GOLD_CONCURRENCY", 1)
    monkeypatch.setattr(pipeline, "DEFERRED_GOLD_QUEUE", 2)
    coordinator = GoldCoordinator(_Runs())
    gate = asyncio.Event()

    async def blocked():
        await gate.wait()

    await coordinator.defer(blocked)
    await coordinator.defer(blocked)
    third = asyncio.create_task(coordinator.defer(blocked))
    await asyncio.sleep(0.02)
    assert not third.done()  # backpressure: the poll worker waits here

    gate.set()
    await asyncio.wait_for(third, timeout=1)
    await coordinator.drain()


async def test_drain_cancels_gold_that_outlives_the_grace():
    coordinator = GoldCoordinator(_Runs())
    cancelled = asyncio.Event()

    async def endless():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    await coordinator.defer(endless)
    await asyncio.sleep(0)
    await asyncio.wait_for(coordinator.drain(0.05), timeout=1)

    assert cancelled.is_set()


async def test_a_failed_deferred_gold_job_is_logged_not_raised():
    coordinator = GoldCoordinator(_Runs())
    ran = []

    async def broken():
        raise RuntimeError("taxonomy unavailable")

    async def fine():
        ran.append(True)

    await coordinator.defer(broken)
    await coordinator.defer(fine)
    await coordinator.drain()

    assert ran == [True]


class _Policy:
    def __init__(self, running: bool, pool=None):
        self.running = running
        self.pool = object() if pool is None else pool

    async def pool_or_none(self):
        return self.pool

    async def scheduler_control(self):
        return {"running": self.running}


class _GoldCoordinator:
    def __init__(self):
        self.runs = self
        self.catchups = 0
        self.dedup: list[bool] = []

    async def prune(self, pool):
        return None

    async def catch_up_once(self, pool, *, accepting=None):
        self.catchups += 1
        return None

    async def dedup_once(self, pool, *, queue_drained):
        self.dedup.append(queue_drained)

    async def next_dedup_delay(self, pool, *, queue_drained):
        return 30.0

    def catchup_may_remain(self):
        return False


class _GoldPool:
    async def fetchval(self, query):
        assert "NOT EXISTS" in query
        return True


async def test_paused_scheduler_still_repairs_stored_gold():
    gold = _GoldCoordinator()
    policy = _Policy(False, _GoldPool())
    scheduler = SystemScheduler(
        policy,
        gold,
        user_agent="test",
        tick_s=60,
        cycle_budget_s=1,
        lease_seconds=10,
        shutdown_grace_s=1,
    )
    scheduler._net = object()
    worker = GoldWorker(
        policy,
        gold,
        tick_s=60,
        shutdown_grace_s=1,
    )

    await scheduler.tick()
    await worker.tick()

    assert gold.catchups == 1
    assert gold.dedup == []
    assert worker._next_delay_s is None


async def test_enabled_dedup_runs_from_the_gold_worker(monkeypatch):
    monkeypatch.setattr(runtime, "DEDUP_ENABLED", True)
    gold = _GoldCoordinator()
    worker = GoldWorker(
        _Policy(False, _GoldPool()),
        gold,
        tick_s=60,
        shutdown_grace_s=1,
    )

    await worker.tick()

    assert gold.dedup == [True]
    assert worker._next_delay_s == 30.0


class _AcquirePool:
    """Just enough pool for the tick's `due_sources` read."""

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _scheduler(policy, gold):
    scheduler = SystemScheduler(
        policy,
        gold,
        user_agent="test",
        tick_s=60,
        cycle_budget_s=1,
        lease_seconds=10,
        shutdown_grace_s=1,
    )
    scheduler._net = object()
    return scheduler


async def test_running_scheduler_passes_coordinator_to_pipeline(monkeypatch):
    gold = _GoldCoordinator()
    scheduler = _scheduler(_Policy(True, _AcquirePool()), gold)
    seen = []

    async def due_sources(conn, now):
        return ["greenhouse", "lever"]

    async def drain_source(pool, net, cfg, source, *, coordinator, accepting):
        seen.append((source, coordinator))
        assert accepting() is True
        return 1

    monkeypatch.setattr(store, "due_sources", due_sources)
    monkeypatch.setattr(run, "drain_source", drain_source)

    await scheduler.tick()
    await asyncio.gather(*scheduler._lanes.values())
    await scheduler.tick()

    assert sorted(seen[:2]) == [("greenhouse", gold), ("lever", gold)]
    assert scheduler.status()["last_polled"] == 2
    assert gold.dedup == []


async def test_scheduler_skips_saved_workable_cooldown_and_resumes_when_it_expires(monkeypatch, caplog):
    await workable_policy.policy().hold(86400)
    deadline = await workable_policy.cooldown_until()
    scheduler = _scheduler(_Policy(True, _AcquirePool()), _GoldCoordinator())
    lanes = []

    async def due_sources(conn, now):
        return ["workable", "lever"]

    async def drain_source(pool, net, cfg, source, **kwargs):
        lanes.append(source)
        return 0

    monkeypatch.setattr(store, "due_sources", due_sources)
    monkeypatch.setattr(run, "drain_source", drain_source)

    for _ in range(2):
        await scheduler.tick()
        await asyncio.gather(*scheduler._lanes.values())

    assert lanes == ["lever", "lever"]
    notices = [record for record in caplog.records if "Workable rate-limited" in record.message]
    assert len(notices) == 1
    assert notices[0].exc_info is None

    monkeypatch.setattr(run.time, "time", lambda: deadline + 1)
    monkeypatch.setattr(runtime, "now_utc", lambda: datetime.fromtimestamp(deadline + 1, timezone.utc))
    await scheduler.tick()
    await asyncio.gather(*scheduler._lanes.values())

    assert lanes == ["lever", "lever", "workable", "lever"]


async def test_poll_logs_rate_limit_without_traceback(monkeypatch, caplog):
    failures = []

    async def harvest(feed, net):
        raise NetError("Workable cooldown: 56779s remaining", url="https://apply.workable.com", status=429)

    async def finish_failure(pool, feed, now, outcome, **kwargs):
        failures.append((outcome, kwargs["status_code"]))

    monkeypatch.setattr(run.sources, "get", lambda name: harvest)
    monkeypatch.setattr(run, "_finish_failure", finish_failure)
    feed = Feed(id=1, source="workable", key="acme")

    assert await run.poll(_Pool(), object(), feed, NOW) == "error"
    assert failures == [("error", 429)]
    notices = [record for record in caplog.records if "rate limited (429)" in record.message]
    assert len(notices) == 1
    assert notices[0].exc_info is None


async def test_a_slow_vendor_never_holds_another_vendors_lane(monkeypatch):
    """The barrier this replaced: one long Avature poll idled Lever for minutes.

    The tick starts a lane per due vendor and awaits none of them, so a vendor
    whose lane finished is restarted on the next tick while another vendor's
    lane is still mid-poll.
    """
    scheduler = _scheduler(_Policy(True, _AcquirePool()), _GoldCoordinator())
    release_avature = asyncio.Event()
    runs: list[str] = []

    async def due_sources(conn, now):
        return ["avature", "lever"]

    async def drain_source(pool, net, cfg, source, *, coordinator, accepting):
        runs.append(source)
        if source == "avature":
            await release_avature.wait()
        return 1

    monkeypatch.setattr(store, "due_sources", due_sources)
    monkeypatch.setattr(run, "drain_source", drain_source)

    await scheduler.tick()
    await asyncio.sleep(0)
    await scheduler.tick()
    await asyncio.sleep(0)

    # Lever ran twice; Avature's first lane is still open and was not doubled.
    assert runs.count("lever") == 2
    assert runs.count("avature") == 1
    assert scheduler.status()["lanes"] == ["avature"]

    release_avature.set()
    await asyncio.gather(*scheduler._lanes.values())


async def test_pausing_the_scheduler_stops_lanes_claiming(monkeypatch):
    policy = _Policy(True, _AcquirePool())
    scheduler = _scheduler(policy, _GoldCoordinator())
    accepting_seen: list[bool] = []
    paused = asyncio.Event()

    async def due_sources(conn, now):
        return ["lever"]

    async def drain_source(pool, net, cfg, source, *, coordinator, accepting):
        accepting_seen.append(accepting())
        await paused.wait()
        accepting_seen.append(accepting())
        return 0

    monkeypatch.setattr(store, "due_sources", due_sources)
    monkeypatch.setattr(run, "drain_source", drain_source)

    await scheduler.tick()
    await asyncio.sleep(0)
    policy.running = False
    await scheduler.tick()
    paused.set()
    await asyncio.gather(*scheduler._lanes.values())

    assert accepting_seen == [True, False]


async def test_drain_source_polls_until_its_queue_is_empty(monkeypatch):
    queue = [Feed(id=i, source="lever", key=f"k{i}") for i in range(5)]
    polled: list[int] = []

    async def claim_due_feeds(conn, now, limit, *, lease_seconds, source):
        assert source == "lever"
        return [queue.pop(0)] if queue else []

    async def poll(pool, net, feed, claimed_at, **kwargs):
        polled.append(feed.id)

    monkeypatch.setattr(store, "claim_due_feeds", claim_due_feeds)
    monkeypatch.setattr(run, "poll", poll)
    monkeypatch.setattr(run.sources, "width", lambda source: 3)

    count = await run.drain_source(_AcquirePool(), object(), run.Config(dsn="", user_agent="test"), "lever")

    assert count == 5
    assert sorted(polled) == [0, 1, 2, 3, 4]


async def test_drain_source_claims_only_its_lanes_partition(monkeypatch):
    seen = []

    async def claim_due_feeds(conn, now, limit, *, lease_seconds, source, part=None):
        seen.append((source, part))
        return []

    monkeypatch.setattr(store, "claim_due_feeds", claim_due_feeds)

    await run.drain_source(
        _AcquirePool(), object(), run.Config(dsn="", user_agent="test"), "workday:wd3"
    )

    assert set(seen) == {("workday", (2, ("wd3",), False))}
    assert len(seen) == run.sources.width("workday:wd3")


async def test_due_lanes_splits_partitioned_sources(monkeypatch):
    async def due_sources(conn, now):
        return ["greenhouse", "workday"]

    async def due_key_parts(conn, now, source, index):
        assert (source, index) == ("workday", 2)
        return ["wd1", "wd103", "wd12", "wd5"]

    monkeypatch.setattr(store, "due_sources", due_sources)
    monkeypatch.setattr(store, "due_key_parts", due_key_parts)

    assert await run.due_lanes(object(), NOW) == [
        "greenhouse", "workday:other", "workday:wd1", "workday:wd5",
    ]


class _LockConnection:
    def __init__(self, owned: bool):
        self.owned = owned
        self.closed = False

    async def fetchval(self, query, value):
        if "to_regclass" in query:
            return value
        if "pg_try_advisory_lock" in query:
            return self.owned
        raise AssertionError(query)

    async def close(self):
        self.closed = True


def _settings() -> RuntimeSettings:
    return RuntimeSettings(
        dsn="postgresql://test/runtime",
        console_store_dsn="postgresql://test/runtime",
        console_schema="console",
    )


async def test_runtime_claims_the_single_system_lock(monkeypatch):
    conn = _LockConnection(True)

    async def connect(dsn):
        return conn

    monkeypatch.setattr("xjobscore.runtime.asyncpg.connect", connect)
    runtime = UnifiedRuntime(_settings())

    await runtime._claim_process_lock()

    assert runtime._lock_conn is conn


async def test_second_runtime_is_rejected_and_connection_is_closed(monkeypatch):
    conn = _LockConnection(False)

    async def connect(dsn):
        return conn

    monkeypatch.setattr("xjobscore.runtime.asyncpg.connect", connect)
    runtime = UnifiedRuntime(_settings())

    with pytest.raises(RuntimeError, match="already owns"):
        await runtime._claim_process_lock()

    assert conn.closed is True


async def test_runtime_settings_use_unified_names(
    monkeypatch,
):
    monkeypatch.setattr("xjobscore.runtime.config.dsn", lambda: "postgresql://test/db")
    monkeypatch.setattr(
        "xjobscore.runtime.config.persistence_dsn", lambda: "postgresql://test/db"
    )
    monkeypatch.setattr("xjobscore.runtime.config.persistence_schema", lambda: "console")
    monkeypatch.setenv("XJOBSCORE_PORT", "9000")
    monkeypatch.setenv("XJOBSCORE_CONSOLE_PORT", "8999")
    monkeypatch.setenv("XJOBSCORE_TICK_S", "12")
    monkeypatch.setenv("XJOBSCORE_CONSOLE_RUNNER_TICK_S", "12")

    settings = RuntimeSettings.from_env()

    assert settings.port == 9000
    assert settings.tick_s == 12


class _IncrementalGoldConnection:
    def __init__(self):
        self.updates = []

    async def fetch(self, query, *args):
        if "SELECT DISTINCT company_id" in query:
            return [{"company_id": 42}]
        if "company_postings AS MATERIALIZED" in query:
            return [{"id": 42}]
        if "FROM job_posting" in query and "job_domain_version" in query:
            return [
                {
                    "id": 1,
                    "title": "Quantitative Developer",
                    "company_id": 42,
                    "job_domain": None,
                    "job_domain_version": None,
                },
                {
                    "id": 2,
                    "title": "Software Engineer",
                    "company_id": 42,
                    "job_domain": None,
                    "job_domain_version": None,
                },
            ]
        raise AssertionError(query)

    async def executemany(self, query, values):
        self.updates.extend(values)


async def test_incremental_job_domain_expands_to_the_affected_company():
    conn = _IncrementalGoldConnection()

    counts = await gold.apply_incremental(conn, "job_type", [1])

    assert counts == gold.Counts(postings=2)
    assert {row[0] for row in conn.updates} == {1, 2}


class _Runs:
    def __init__(self):
        self.finished = []

    async def begin(self, pool, job_type, identity=None):
        return 8

    async def finish(self, pool, run_id, **kwargs):
        self.finished.append((run_id, kwargs))


async def test_gold_catchup_retries_stale_rows_without_fetching(monkeypatch):
    pool = _Pool()
    runs = _Runs()
    coordinator = GoldCoordinator(runs, batch_size=10)
    applied = []

    async def stale(conn, name, *, limit):
        return [101] if name == "skills" else []

    async def apply(conn, name, posting_ids, company_ids=(), *, dry_run=False):
        applied.append((name, tuple(posting_ids)))
        return gold.Counts(postings=len(posting_ids))

    monkeypatch.setattr(gold, "stale_posting_ids", stale)
    monkeypatch.setattr(gold, "apply_incremental", apply)

    result = await coordinator.catch_up_once(pool)

    assert result == GoldResult(counts={"skills": gold.Counts(postings=1)})
    assert applied == [("skills", (101,))]
    assert runs.finished[0][1]["state"] == "complete"


class _DedupConnection:
    def __init__(self):
        self.executed = []

    def transaction(self):
        return _Context(self)

    async def fetchrow(self, query, *args):
        self.executed.append((query, args))
        return {"generation": 5}

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "UPDATE 1"


class _DedupPool:
    def __init__(self):
        self.connection = _DedupConnection()

    def acquire(self):
        return _Context(self.connection)


async def test_dedup_checkpoint_records_only_the_claimed_generation(monkeypatch):
    pool = _DedupPool()
    runs = _Runs()
    coordinator = GoldCoordinator(runs)

    async def rebuild(conn):
        return gold.Counts(groups=2, postings=4)

    monkeypatch.setattr(
        gold,
        "steps",
        lambda only=None: (gold.Step("dedup", 1, rebuild, atomic=True),),
    )

    counts = await coordinator.dedup_once(pool, queue_drained=True, current=NOW)

    assert counts == gold.Counts(groups=2, postings=4)
    finish_sql = next(
        query for query, _args in pool.connection.executed
        if "processed_generation = GREATEST" in query
    )
    assert "CASE WHEN generation <= $2" in finish_sql
    assert runs.finished[0][1]["detail"]["generation"] == 5


async def test_dedup_retries_when_postgres_picks_it_as_the_deadlock_victim(monkeypatch):
    from asyncpg.exceptions import DeadlockDetectedError

    pool = _DedupPool()
    runs = _Runs()
    coordinator = GoldCoordinator(runs)
    attempts = []

    async def rebuild(conn):
        attempts.append(conn)
        if len(attempts) == 1:
            raise DeadlockDetectedError("deadlock detected")
        return gold.Counts(groups=2, postings=4)

    monkeypatch.setattr(gold, "steps", lambda only=None: _dedup_step(rebuild))

    counts = await coordinator.dedup_once(pool, queue_drained=True, current=NOW)

    assert len(attempts) == 2
    assert counts == gold.Counts(groups=2, postings=4)
    assert runs.finished[0][1]["state"] == "complete"


async def test_dedup_gives_up_after_repeated_deadlocks(monkeypatch):
    from asyncpg.exceptions import DeadlockDetectedError

    from xjobscore import pipeline

    pool = _DedupPool()
    runs = _Runs()
    coordinator = GoldCoordinator(runs)
    attempts = []

    async def rebuild(conn):
        attempts.append(conn)
        raise DeadlockDetectedError("deadlock detected")

    monkeypatch.setattr(gold, "steps", lambda only=None: _dedup_step(rebuild))

    assert await coordinator.dedup_once(pool, queue_drained=True, current=NOW) is None
    assert len(attempts) == pipeline.DEDUP_ATTEMPTS
    assert runs.finished[0][1]["state"] == "failed"


class _StatefulDedupConnection:
    def __init__(
        self,
        *,
        generation=1,
        processed_generation=0,
        dirty_at=NOW,
        not_before=NOW,
        hold_until=None,
    ):
        self.generation = generation
        self.processed_generation = processed_generation
        self.dirty_at = dirty_at
        self.not_before = not_before
        self.hold_until = hold_until

    def transaction(self):
        return _Context(self)

    async def fetchrow(self, query, current, new_hold, queue_drained, max_delay):
        due = self.generation > self.processed_generation
        lease_free = self.hold_until is None or self.hold_until <= current
        quiet = bool(
            queue_drained
            and self.not_before is not None
            and self.not_before <= current
        )
        overdue = bool(
            self.dirty_at is not None
            and self.dirty_at <= current - timedelta(seconds=max_delay)
        )
        if not (due and lease_free and (quiet or overdue)):
            return None
        self.hold_until = new_hold
        return {"generation": self.generation}

    async def execute(self, query, *args):
        if "processed_generation = GREATEST" not in query:
            raise AssertionError(query)
        _finished, claimed_generation, claimed_hold = args
        if self.hold_until != claimed_hold:
            return "UPDATE 0"
        self.processed_generation = max(
            self.processed_generation, claimed_generation
        )
        if self.generation <= claimed_generation:
            self.dirty_at = None
            self.not_before = None
        self.hold_until = None
        return "UPDATE 1"


class _StatefulDedupPool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Context(self.connection)


def _dedup_step(rebuild):
    return (gold.Step("dedup", 1, rebuild, atomic=True),)


async def test_dedup_waits_for_thirty_quiet_seconds(monkeypatch):
    conn = _StatefulDedupConnection(not_before=NOW + timedelta(seconds=30))
    coordinator = GoldCoordinator(_Runs())

    async def rebuild(_conn):
        return gold.Counts(groups=1)

    monkeypatch.setattr(gold, "steps", lambda only=None: _dedup_step(rebuild))

    assert await coordinator.dedup_once(
        _StatefulDedupPool(conn), queue_drained=True, current=NOW + timedelta(seconds=29)
    ) is None
    assert await coordinator.dedup_once(
        _StatefulDedupPool(conn), queue_drained=True, current=NOW + timedelta(seconds=30)
    ) == gold.Counts(groups=1)


async def test_dedup_max_delay_overrides_a_busy_queue(monkeypatch):
    conn = _StatefulDedupConnection(not_before=NOW + timedelta(hours=1))
    coordinator = GoldCoordinator(_Runs())

    async def rebuild(_conn):
        return gold.Counts(postings=3)

    monkeypatch.setattr(gold, "steps", lambda only=None: _dedup_step(rebuild))

    assert await coordinator.dedup_once(
        _StatefulDedupPool(conn),
        queue_drained=False,
        current=NOW + timedelta(minutes=4, seconds=59),
    ) is None
    assert await coordinator.dedup_once(
        _StatefulDedupPool(conn),
        queue_drained=False,
        current=NOW + timedelta(minutes=5),
    ) == gold.Counts(postings=3)


async def test_dedup_recovers_an_expired_lease(monkeypatch):
    conn = _StatefulDedupConnection(hold_until=NOW + timedelta(seconds=10))
    coordinator = GoldCoordinator(_Runs())

    async def rebuild(_conn):
        return gold.Counts(groups=2)

    monkeypatch.setattr(gold, "steps", lambda only=None: _dedup_step(rebuild))

    assert await coordinator.dedup_once(
        _StatefulDedupPool(conn), queue_drained=True, current=NOW
    ) is None
    assert await coordinator.dedup_once(
        _StatefulDedupPool(conn),
        queue_drained=True,
        current=NOW + timedelta(seconds=11),
    ) == gold.Counts(groups=2)


async def test_change_during_dedup_remains_dirty_for_another_generation(monkeypatch):
    conn = _StatefulDedupConnection(generation=5, not_before=NOW)
    coordinator = GoldCoordinator(_Runs())

    async def rebuild(_conn):
        conn.generation = 6
        conn.not_before = NOW + timedelta(seconds=30)
        return gold.Counts(groups=1)

    monkeypatch.setattr(gold, "steps", lambda only=None: _dedup_step(rebuild))

    await coordinator.dedup_once(
        _StatefulDedupPool(conn), queue_drained=True, current=NOW
    )

    assert conn.processed_generation == 5
    assert conn.generation == 6
    assert conn.dirty_at == NOW
    assert conn.not_before == NOW + timedelta(seconds=30)


class _DedupControlPool:
    def __init__(self, **overrides):
        self.row = {
            "generation": 1,
            "processed_generation": 0,
            "dirty_at": NOW,
            "not_before": NOW + timedelta(seconds=30),
            "hold_until": None,
            "last_completed_at": None,
            "last_outcome": "never",
            **overrides,
        }

    async def fetchrow(self, query):
        assert "FROM gold_control" in query
        return self.row


async def test_supervisor_wakes_at_quiet_or_max_dedup_deadline():
    coordinator = GoldCoordinator(_Runs())
    pool = _DedupControlPool()

    assert await coordinator.next_dedup_delay(
        pool, queue_drained=True, current=NOW
    ) == 30
    assert await coordinator.next_dedup_delay(
        pool, queue_drained=False, current=NOW
    ) == 300


async def test_supervisor_waits_for_dedup_lease_or_error_retry():
    coordinator = GoldCoordinator(_Runs())
    leased = _DedupControlPool(hold_until=NOW + timedelta(seconds=45))
    failed = _DedupControlPool(
        not_before=NOW,
        last_outcome="error",
        last_completed_at=NOW,
    )

    assert await coordinator.next_dedup_delay(
        leased, queue_drained=True, current=NOW
    ) == 45
    assert await coordinator.next_dedup_delay(
        failed, queue_drained=True, current=NOW
    ) == 60


class _AcquirePool:
    def __init__(self):
        self.conn = object()

    def acquire(self):
        return _Context(self.conn)


class _PublishPolicy:
    def __init__(self, pool):
        self.pool = pool

    async def pool_or_none(self):
        return self.pool


async def test_neon_publisher_is_off_without_a_target():
    publisher = NeonPublisher(
        _PublishPolicy(_AcquirePool()),
        target_dsn=None,
        interval_s=3600,
        seen_drift_s=60,
        shutdown_grace_s=1,
    )

    await publisher.start()

    assert publisher.status()["enabled"] is False
    assert publisher.status()["alive"] is False
    assert UnifiedRuntime(_settings()).publisher.enabled is False


async def test_neon_publisher_tick_publishes_from_a_pooled_connection(monkeypatch):
    pool = _AcquirePool()
    checks = []
    seen = []

    async def ownership_check():
        checks.append(True)

    async def publish_to(source, target_dsn, *, seen_drift_s):
        seen.append((source, target_dsn, seen_drift_s))
        return publish.PublishCounts(10, 2, 1, 0, 3, False)

    monkeypatch.setattr(publish, "publish_to", publish_to)
    publisher = NeonPublisher(
        _PublishPolicy(pool),
        target_dsn="postgresql://neon/thin",
        interval_s=3600,
        seen_drift_s=60,
        shutdown_grace_s=1,
        ownership_check=ownership_check,
    )

    await publisher.tick()

    assert checks == [True]
    assert seen == [(pool.conn, "postgresql://neon/thin", 60)]
    status = publisher.status()
    assert status["last_counts"]["postings"] == 2
    assert status["last_counts"]["closed"] == 3
    assert status["last_published_at"] is not None


async def test_runtime_settings_read_the_publish_target(monkeypatch):
    monkeypatch.setattr("xjobscore.runtime.config.dsn", lambda: "postgresql://test/db")
    monkeypatch.setattr(
        "xjobscore.runtime.config.persistence_dsn", lambda: "postgresql://test/db"
    )
    monkeypatch.setattr("xjobscore.runtime.config.persistence_schema", lambda: "console")
    monkeypatch.setenv("PUBLISH_DATABASE_URL", "postgresql://neon/thin")
    monkeypatch.setenv("XJOBSCORE_PUBLISH_INTERVAL_S", "900")
    monkeypatch.delenv("XJOBSCORE_PUBLISH_SEEN_DRIFT_S", raising=False)

    settings = RuntimeSettings.from_env()

    assert settings.publish_dsn == "postgresql://neon/thin"
    assert settings.publish_interval_s == 900
    assert settings.publish_seen_drift_s == publish.DEFAULT_SEEN_DRIFT_S
