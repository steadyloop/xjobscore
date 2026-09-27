from __future__ import annotations

import asyncpg
import pytest

from xjobscore import gold, run


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *exc):
        return False


class _Connection:
    def __init__(self) -> None:
        self.transactions = 0

    def transaction(self):
        self.transactions += 1
        return _Context(None)


class _Pool:
    def __init__(self) -> None:
        self.connection = _Connection()

    def acquire(self):
        return _Context(self.connection)


def _config(enrich: str | None) -> run.Config:
    return run.Config(dsn="postgresql://unused", user_agent="test", enrich=enrich)


@pytest.mark.asyncio
async def test_bare_enrich_excludes_dedup(monkeypatch) -> None:
    calls = []

    async def rebuild(conn, *, dry_run=False):
        calls.append("rebuilt")
        return gold.Counts(postings=1)

    registered = (
        gold.Step("job_type", 1, rebuild),
        gold.Step("skills", 1, rebuild),
        gold.Step("degrees", 1, rebuild),
        gold.Step("summaries", 1, rebuild),
        gold.Step("dedup", 1, rebuild, atomic=True),
    )
    monkeypatch.setattr(
        gold,
        "steps",
        lambda only=None: registered
        if only is None
        else tuple(step for step in registered if step.name == only),
    )

    result = await run.run_enrichments(_Pool(), _config("all"))

    assert list(result) == ["job_type", "skills", "degrees", "summaries"]
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_internal_full_gold_still_includes_dedup(monkeypatch) -> None:
    async def rebuild(conn, *, dry_run=False):
        return gold.Counts(postings=1)

    registered = (
        gold.Step("skills", 1, rebuild),
        gold.Step("dedup", 1, rebuild, atomic=True),
    )
    monkeypatch.setattr(gold, "steps", lambda only=None: registered)

    result = await run.run_enrichments(_Pool(), _config(None))

    assert list(result) == ["skills", "dedup"]


@pytest.mark.asyncio
async def test_incremental_enrichment_commits_each_batch(monkeypatch) -> None:
    async def rebuild(conn, *, dry_run=False):
        return gold.Counts(postings=2)

    pool = _Pool()
    monkeypatch.setattr(
        gold, "steps", lambda only=None: (gold.Step("degrees", 1, rebuild),)
    )

    result = await run.run_enrichments(pool, _config("degrees"))

    assert result == {"degrees": gold.Counts(postings=2)}
    assert pool.connection.transactions == 0


@pytest.mark.asyncio
async def test_atomic_enrichment_retries_a_deadlock(monkeypatch) -> None:
    calls = 0
    delays = []

    async def rebuild(conn, *, dry_run=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncpg.DeadlockDetectedError("deadlock detected")
        return gold.Counts(groups=1)

    async def sleep(delay):
        delays.append(delay)

    pool = _Pool()
    monkeypatch.setattr(
        gold,
        "steps",
        lambda only=None: (gold.Step("dedup", 1, rebuild, atomic=True),),
    )
    monkeypatch.setattr(run.asyncio, "sleep", sleep)

    result = await run.run_enrichments(pool, _config("dedup"))

    assert result == {"dedup": gold.Counts(groups=1)}
    assert calls == 2
    assert pool.connection.transactions == 2
    assert delays == [0.05]


@pytest.mark.asyncio
async def test_degree_batch_retries_a_deadlock(monkeypatch) -> None:
    class Connection:
        def __init__(self) -> None:
            self.rows = [
                {
                    "id": 1,
                    "title": "Software Engineer",
                    "description": "BS in computer science required.",
                }
            ]
            self.calls = 0

        async def fetch(self, query, last_id, limit, version):
            return self.rows if last_id == 0 else []

        async def executemany(self, query, updates):
            self.calls += 1
            if self.calls == 1:
                raise asyncpg.DeadlockDetectedError("deadlock detected")

    delays = []

    async def sleep(delay):
        delays.append(delay)

    connection = Connection()
    monkeypatch.setattr(gold.asyncio, "sleep", sleep)

    result = await gold._degrees_rebuild(connection)

    assert result == gold.Counts(postings=1)
    assert connection.calls == 2
    assert delays == [0.05]
