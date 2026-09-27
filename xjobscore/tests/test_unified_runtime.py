from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from xjobscore import gold, router, run, silver, store
from xjobscore.pipeline import GoldCoordinator, GoldResult
from xjobscore.records import Feed, Item, Record
from xjobscore.runtime import GoldWorker, RuntimeSettings, SystemScheduler, UnifiedRuntime

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
    def __init__(self):
        self.marked: list[tuple[set[int], set[int]]] = []
        self.finished: list[dict] = []
        self.notifications = 0

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
        },
    }


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
    assert gold.dedup == [True]
    assert worker._next_delay_s == 30.0


async def test_running_scheduler_passes_coordinator_to_pipeline(monkeypatch):
    gold = _GoldCoordinator()
    policy = _Policy(True)
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
    seen = []

    async def cycle(pool, net, cfg, now, *, coordinator, accepting):
        seen.append(coordinator)
        assert accepting() is True
        return run.CycleResult(polled=2, drained=True)

    monkeypatch.setattr(run, "cycle", cycle)

    await scheduler.tick()

    assert seen == [gold]
    assert scheduler.status()["last_polled"] == 2
    assert gold.dedup == []


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
        inspector_store_dsn="postgresql://test/runtime",
        inspector_schema="inspector",
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


async def test_runtime_settings_prefer_unified_names_and_accept_legacy_aliases(
    monkeypatch,
):
    monkeypatch.setattr("xjobscore.runtime.config.dsn", lambda: "postgresql://test/db")
    monkeypatch.setattr(
        "xjobscore.runtime.config.persistence_dsn", lambda: "postgresql://test/db"
    )
    monkeypatch.setattr("xjobscore.runtime.config.persistence_schema", lambda: "inspector")
    monkeypatch.setenv("XJOBSCORE_PORT", "9000")
    monkeypatch.setenv("XJOBSCORE_INSPECTOR_PORT", "8999")
    monkeypatch.delenv("XJOBSCORE_TICK_S", raising=False)
    monkeypatch.setenv("XJOBSCORE_INSPECTOR_RUNNER_TICK_S", "12")

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
