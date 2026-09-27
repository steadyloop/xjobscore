from __future__ import annotations

import pytest

from xjobscore import publish


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


class Target:
    def __init__(
        self,
        *,
        thin: bool = True,
        coverage_uses_id: bool = True,
        legacy_coverage_rows: int = 0,
    ) -> None:
        self.thin = thin
        self.coverage_uses_id = coverage_uses_id
        self.legacy_coverage_rows = legacy_coverage_rows
        self.many: list[tuple[str, list[tuple[object, ...]]]] = []
        self.commands: list[tuple[str, tuple[object, ...]]] = []
        self.tx = Transaction()

    async def fetchval(self, query: str) -> bool:
        if "information_schema.columns" in query:
            return self.coverage_uses_id
        if "COUNT(*) FROM coverage_snapshot" in query:
            return self.legacy_coverage_rows
        return self.thin

    def transaction(self) -> Transaction:
        return self.tx

    async def executemany(self, query: str, rows: list[tuple[object, ...]]) -> None:
        self.many.append((query, rows))

    async def execute(self, query: str, *args: object) -> str:
        self.commands.append((query, args))
        return "UPDATE 2" if query == publish.SOFT_CLOSE_SQL else "INSERT 0 1"


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
        "last_seen_at": None,
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


@pytest.mark.asyncio
async def test_write_snapshot_upserts_and_soft_closes_without_delete():
    snapshot = publish.Snapshot(
        jobs=(_job(),),
        companies=(
            {
                "id": 3,
                "name": "Example",
                "domain": "example.com",
                "description": None,
                "tags": "{}",
            },
        ),
        logos=({"company_id": 3, "bytes": b"logo"},),
        coverage={"id": True, "payload": "{}", "computed_at": None},
    )
    target = Target()

    counts = await publish.write_snapshot(target, snapshot)

    assert counts == publish.PublishCounts(1, 1, 1, 2)
    assert target.tx.started and target.tx.committed
    assert not target.tx.rolled_back
    assert [len(rows) for _query, rows in target.many] == [1, 1, 1]
    assert "OVERRIDING SYSTEM VALUE" in target.many[0][0]
    assert any(query == publish.SOFT_CLOSE_SQL for query, _args in target.commands)
    assert "DELETE" not in "\n".join(
        [query for query, _rows in target.many]
        + [query for query, _args in target.commands]
    ).upper()


@pytest.mark.asyncio
async def test_dry_run_rolls_back():
    snapshot = publish.Snapshot((_job(),), (), (), None)
    target = Target()

    await publish.write_snapshot(target, snapshot, dry_run=True)

    assert target.tx.rolled_back
    assert not target.tx.committed


@pytest.mark.asyncio
async def test_empty_legacy_coverage_snapshot_is_replaced_safely():
    snapshot = publish.Snapshot(
        (_job(),),
        (),
        (),
        {"id": True, "payload": "{}", "computed_at": None},
    )
    target = Target(coverage_uses_id=False)

    await publish.write_snapshot(target, snapshot)

    assert any(
        "RENAME TO coverage_snapshot_legacy" in query
        for query, _args in target.commands
    )
    assert any(query == publish.UPSERT_COVERAGE_SQL for query, _args in target.commands)


@pytest.mark.asyncio
async def test_nonempty_legacy_coverage_snapshot_requires_manual_migration():
    snapshot = publish.Snapshot((_job(),), (), (), None)
    target = Target(coverage_uses_id=False, legacy_coverage_rows=2)

    with pytest.raises(RuntimeError, match="legacy coverage_snapshot contains data"):
        await publish.write_snapshot(target, snapshot)

    assert target.tx.rolled_back


@pytest.mark.asyncio
async def test_refuses_a_full_schema_target():
    snapshot = publish.Snapshot((_job(),), (), (), None)

    with pytest.raises(RuntimeError, match="not the thin OneShot schema"):
        await publish.write_snapshot(Target(thin=False), snapshot)


class EmptySource:
    async def fetch(self, _query: str, *_args: object) -> list[object]:
        return []


@pytest.mark.asyncio
async def test_refuses_to_close_feed_when_selection_is_empty():
    with pytest.raises(RuntimeError, match="selected zero postings"):
        await publish.read_snapshot(EmptySource())


def test_selection_is_the_broad_ui_cohort_and_deduplicates():
    sql = publish.OPPORTUNITY_SQL
    assert "j.status = 'active'" in sql
    assert "'intern', 'coop', 'student', 'new_grad', 'research'" in sql
    assert 'j.locations @> \'[{"country": "US"}]\'::jsonb' in sql
    assert "j.id = g.primary_posting_id" in sql
    assert "software" not in sql.lower()
