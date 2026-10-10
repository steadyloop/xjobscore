"""Observability for the Activity pane must not count a posting per step."""
import asyncio
from datetime import datetime, timezone

import pytest

from xjobscore import gold
from xjobscore.pipeline import GoldCoordinator
from xjobscore.runtime import GoldWorker
from xjobscore.silver import ChangeSet


class Context:
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return False


class Pool:
    def acquire(self):
        return Context(self)

    def transaction(self):
        return Context()


class Runs:
    async def begin(self, *args):
        return 1

    async def finish(self, *args, **kwargs):
        self.finished = kwargs


@pytest.mark.asyncio
async def test_changed_postings_are_counted_once_across_five_steps(monkeypatch):
    async def apply(conn, name, ids, companies=()):
        return gold.Counts(postings=len(ids))

    monkeypatch.setattr(gold, "apply_incremental", apply)
    changes = ChangeSet()
    changes.add(1, 10)
    changes.add(2, 10)
    result = await GoldCoordinator().enrich_changed(Pool(), changes)
    assert sum(count.postings for count in result.counts.values()) == 10
    assert result.detail()["postings"] == 2
    assert len(result.stage_ms) == 5
    assert result.duration_ms >= sum(result.stage_ms.values()) - 0.01


@pytest.mark.asyncio
async def test_catchup_counts_distinct_successful_candidates_and_times_failures(monkeypatch):
    batches = {"skills": [1, 2], "degrees": [2, 3], "summaries": [2, 4]}

    async def stale(conn, name, *, limit):
        return batches.get(name, [])

    async def apply(conn, name, ids):
        if name == "summaries":
            raise TimeoutError("database timeout")
        return gold.Counts(postings=len(ids))

    monkeypatch.setattr(gold, "stale_posting_ids", stale)
    monkeypatch.setattr(gold, "apply_incremental", apply)
    runs = Runs()
    result = await GoldCoordinator(runs).catch_up_once(Pool())
    assert result.postings == 2  # 1 and 3; 2 and 4 still need summaries.
    assert set(result.stage_ms) == set(batches)
    assert result.stage_ms["summaries"] >= 0
    assert runs.finished["state"] == "degraded"
    assert runs.finished["detail"]["gold"]["postings"] == 2
    assert "TimeoutError" in result.errors["summaries"]


@pytest.mark.asyncio
async def test_interrupted_steps_do_not_report_candidates_as_completed(monkeypatch):
    interrupted = False

    async def stale(conn, name, *, limit):
        return {"skills": [1, 2], "degrees": [2, 3]}.get(name, [])

    async def apply(conn, name, ids):
        nonlocal interrupted
        interrupted = True
        return gold.Counts(postings=len(ids))

    monkeypatch.setattr(gold, "stale_posting_ids", stale)
    monkeypatch.setattr(gold, "apply_incremental", apply)
    runs = Runs()
    result = await GoldCoordinator(runs).catch_up_once(Pool(), accepting=lambda: not interrupted)
    assert result.postings == 1
    assert runs.finished["state"] == "interrupted"


@pytest.mark.asyncio
async def test_worker_reports_checking_and_the_real_next_deadline(monkeypatch):
    worker = GoldWorker(object(), object(), tick_s=60, shutdown_grace_s=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def tick():
        entered.set()
        await release.wait()

    monkeypatch.setattr(worker, "tick", tick)
    assert worker.status()["next_check_at"] is None
    await worker.start()
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert worker.status()["checking"] is True
        release.set()

        async def scheduled():
            while worker.status()["next_check_at"] is None:
                await asyncio.sleep(0)

        await asyncio.wait_for(scheduled(), timeout=1)
        status = worker.status()
        assert status["checking"] is False
        deadline = datetime.fromisoformat(status["next_check_at"])
        assert 59 <= (deadline - datetime.now(timezone.utc)).total_seconds() <= 60
        worker.nudge()
        assert datetime.fromisoformat(worker.status()["next_check_at"]) < deadline
    finally:
        await worker.stop()
    assert worker.status()["next_check_at"] is None
    assert worker.status()["alive"] is False
