from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from xjobscore import publish


NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


class Transaction:
    def __init__(self) -> None:
        self.started = False
        self.committed = False
        self.rolled_back = False

    async def start(self) -> None:
        self.started = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def __aenter__(self) -> "Transaction":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.commit()


def _job(job_id: int = 7) -> dict[str, object]:
    return {
        "id": job_id,
        "source": "test",
        "source_key": f"job-{job_id}",
        "company_id": 3,
        "title": "Software Intern",
        "description": "Build software",
        "location_text": "Seattle, WA",
        "source_url": "https://example.com/job",
        "apply_url": "https://example.com/apply",
        "posted_at": None,
        "content_hash": "abc",
        "remote": "unknown",
        "part_time": "unknown",
        "job_type": "intern",
        "locations": '[{"country":"US"}]',
        "status": "active",
        "first_seen_at": None,
        "last_seen_at": NOW,
        "vendor_closed_at": None,
        "link_marked_at": None,
        "link_checked_at": None,
        "link_closed_at": None,
        "attributes": '{"version":1}',
        "degree_version": None,
        "job_domain": "software",
        "job_domain_version": 1,
        "raw_id": 19,
        "removal_marked_at": None,
    }


def _state(job_id: int, fingerprint: str, seen: datetime | None = NOW) -> dict:
    return {"id": job_id, "company_id": 3, "fingerprint": fingerprint, "last_seen_at": seen}


class Source:
    def __init__(
        self,
        cohort: list[dict],
        *,
        company_fp: str = "c1",
        logo_fp: str = "l1",
        coverage_at: datetime | None = NOW,
    ) -> None:
        self.cohort = cohort
        self.company_fp = company_fp
        self.logo_fp = logo_fp
        self.coverage_at = coverage_at
        self.fetched_jobs: list[list[int]] = []

    def transaction(self) -> Transaction:
        return Transaction()

    async def execute(self, _query: str, *_args: object) -> str:
        return "SET"

    async def fetch(self, query: str, *args: object) -> list[dict]:
        if query == publish.COHORT_SQL:
            return self.cohort
        if query == publish.JOB_ROWS_SQL:
            self.fetched_jobs.append(list(args[0]))
            return [_job(job_id) for job_id in args[0]]
        if query == publish.COMPANY_FINGERPRINT_SQL:
            return [{"id": 3, "fingerprint": self.company_fp}]
        if query == publish.COMPANY_ROWS_SQL:
            return [
                {"id": 3, "name": "Example", "domain": "example.com",
                 "description": None, "tags": "{}"}
            ]
        if query == publish.LOGO_FINGERPRINT_SQL:
            return [{"id": 3, "fingerprint": self.logo_fp}]
        if query == publish.LOGO_ROWS_SQL:
            return [{"company_id": 3, "bytes": b"logo"}]
        raise AssertionError(query)

    async def fetchval(self, query: str) -> object:
        assert query == publish.COVERAGE_STAMP_SQL
        return self.coverage_at

    async def fetchrow(self, query: str) -> dict:
        assert query == publish.COVERAGE_ROW_SQL
        return {"id": True, "payload": "{}", "computed_at": self.coverage_at}


class Target:
    def __init__(
        self,
        published: list[dict] | None = None,
        *,
        thin: bool = True,
        coverage_uses_id: bool = True,
        legacy_coverage_rows: int = 0,
        coverage_at: datetime | None = NOW,
    ) -> None:
        self.published = published or []
        self.thin = thin
        self.coverage_uses_id = coverage_uses_id
        self.legacy_coverage_rows = legacy_coverage_rows
        self.coverage_at = coverage_at
        self.many: list[tuple[str, list[tuple[object, ...]]]] = []
        self.commands: list[tuple[str, tuple[object, ...]]] = []
        self.tx = Transaction()

    async def fetchval(self, query: str) -> object:
        if "information_schema.columns" in query:
            return self.coverage_uses_id
        if "COUNT(*) FROM coverage_snapshot" in query:
            return self.legacy_coverage_rows
        if query == publish.COVERAGE_STAMP_SQL:
            return self.coverage_at
        return self.thin

    async def fetch(self, query: str, *_args: object) -> list[dict]:
        if query == publish.PUBLISHED_JOBS_SQL:
            return self.published
        if query == publish.COMPANY_FINGERPRINT_SQL:
            return [{"id": 3, "fingerprint": "c1"}]
        if query == publish.LOGO_FINGERPRINT_SQL:
            return [{"id": 3, "fingerprint": "l1"}]
        raise AssertionError(query)

    def transaction(self) -> Transaction:
        return self.tx

    async def executemany(self, query: str, rows: list[tuple[object, ...]]) -> None:
        self.many.append((query, rows))

    async def execute(self, query: str, *args: object) -> str:
        self.commands.append((query, args))
        return "UPDATE 2" if query == publish.SOFT_CLOSE_SQL else "INSERT 0 1"

    def sent(self, query: str) -> list[tuple[object, ...]]:
        return [row for sql, rows in self.many if sql == query for row in rows]


def test_changed_job_ids_skips_unchanged_and_tolerates_seen_drift():
    cohort = [
        _state(1, "same"),
        _state(2, "new-content"),
        _state(3, "missing"),
        _state(4, "same", NOW),
        _state(5, "same", NOW),
        _state(6, "same", NOW),
    ]
    published = [
        _state(1, "same"),
        _state(2, "old-content"),
        _state(4, "same", NOW - timedelta(hours=1)),
        _state(5, "same", NOW - timedelta(hours=7)),
        _state(6, "same", None),
    ]

    assert publish.changed_job_ids(cohort, published, seen_drift_s=6 * 3600) == [
        2, 3, 5, 6,
    ]


@pytest.mark.asyncio
async def test_publish_sends_only_rows_that_differ():
    source = Source(
        [_state(1, "same"), _state(2, "changed"), _state(3, "new")],
        logo_fp="l2",
    )
    target = Target([_state(1, "same"), _state(2, "stale")])

    counts = await publish.publish(source, target)

    assert counts == publish.PublishCounts(
        cohort=3, postings=2, companies=0, logos=1, closed=2, coverage=False
    )
    assert source.fetched_jobs == [[2, 3]]
    assert [row[0] for row in target.sent(publish.UPSERT_JOB_SQL)] == [2, 3]
    assert target.sent(publish.UPSERT_COMPANY_SQL) == []
    assert target.sent(publish.UPSERT_LOGO_SQL) == [(3, b"logo")]
    assert not any(q == publish.UPSERT_COVERAGE_SQL for q, _ in target.commands)
    assert (publish.SOFT_CLOSE_SQL, ([1, 2, 3],)) in target.commands
    assert target.tx.started and target.tx.committed
    assert not target.tx.rolled_back


@pytest.mark.asyncio
async def test_unchanged_cohort_writes_nothing_but_the_soft_close():
    source = Source([_state(1, "same")])
    target = Target([_state(1, "same")])

    counts = await publish.publish(source, target)

    assert counts.postings == counts.companies == counts.logos == 0
    assert target.many == []
    assert source.fetched_jobs == []
    assert [q for q, _ in target.commands if q != "SET LOCAL TIME ZONE 'UTC'"] == [
        publish.SOFT_CLOSE_SQL
    ]


@pytest.mark.asyncio
async def test_recomputed_coverage_is_sent():
    source = Source([_state(1, "same")], coverage_at=NOW)
    target = Target([_state(1, "same")], coverage_at=NOW - timedelta(hours=1))

    counts = await publish.publish(source, target)

    assert counts.coverage is True
    assert any(q == publish.UPSERT_COVERAGE_SQL for q, _ in target.commands)


@pytest.mark.asyncio
async def test_dry_run_rolls_back():
    target = Target()

    await publish.publish(Source([_state(7, "new")]), target, dry_run=True)

    assert target.tx.rolled_back
    assert not target.tx.committed


@pytest.mark.asyncio
async def test_empty_legacy_coverage_snapshot_is_replaced_safely():
    source = Source([_state(7, "new")])
    target = Target(coverage_uses_id=False, coverage_at=None)

    await publish.publish(source, target)

    assert any(
        "RENAME TO coverage_snapshot_legacy" in query
        for query, _args in target.commands
    )
    assert any(query == publish.UPSERT_COVERAGE_SQL for query, _args in target.commands)


@pytest.mark.asyncio
async def test_nonempty_legacy_coverage_snapshot_requires_manual_migration():
    target = Target(coverage_uses_id=False, legacy_coverage_rows=2)

    with pytest.raises(RuntimeError, match="legacy coverage_snapshot contains data"):
        await publish.publish(Source([_state(7, "new")]), target)

    assert target.tx.rolled_back


@pytest.mark.asyncio
async def test_refuses_a_full_schema_target():
    with pytest.raises(RuntimeError, match="not the thin OneShot schema"):
        await publish.publish(Source([_state(7, "new")]), Target(thin=False))


@pytest.mark.asyncio
async def test_refuses_to_close_feed_when_selection_is_empty():
    target = Target()

    with pytest.raises(RuntimeError, match="selected zero postings"):
        await publish.publish(Source([]), target)

    assert not target.tx.started


def test_selection_is_the_broad_ui_cohort_and_deduplicates():
    sql = publish.COHORT_SQL
    assert "j.status = 'active'" in sql
    assert "'intern', 'coop', 'student', 'new_grad', 'research'" in sql
    assert 'j.locations @> \'[{"country": "US"}]\'::jsonb' in sql
    assert "j.id = g.primary_posting_id" in sql
    assert "software" not in sql.lower()


def test_fingerprint_ignores_last_seen_at_only():
    assert set(publish.JOB_COLUMNS) - set(publish.FINGERPRINT_COLUMNS) == {
        "last_seen_at"
    }
    assert "last_seen_at" not in publish.COHORT_SQL.split("AS fingerprint")[0].split(
        "md5("
    )[1]


def test_upserts_skip_identical_rows():
    for sql in (
        publish.UPSERT_JOB_SQL,
        publish.UPSERT_COMPANY_SQL,
        publish.UPSERT_LOGO_SQL,
    ):
        assert "IS DISTINCT FROM" in sql
    assert "DELETE" not in publish.SOFT_CLOSE_SQL.upper()
