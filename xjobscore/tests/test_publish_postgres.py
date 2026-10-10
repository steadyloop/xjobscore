"""Publish against real PostgreSQL: a store pool on the home side, the thin
schema on the target side. The fakes in test_publish.py cannot see how asyncpg
encodes jsonb, which is exactly where the two connections must agree."""

from __future__ import annotations

import asyncio
import os
import pathlib
from datetime import datetime, timezone

import asyncpg
import pytest

from xjobscore import coverage_snapshot, publish, store


pytestmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"),
    reason="set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests",
)

FRONTEND_SQL = pathlib.Path(__file__).resolve().parents[2] / "frontend.sql"
NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


@pytest.fixture
async def thin_target(core_schema):
    schema = f"{core_schema.schema}_thin"
    setup = await asyncpg.connect(core_schema.dsn)
    try:
        await setup.execute(f"CREATE SCHEMA {schema}")
        await setup.execute(f"SET search_path TO {schema}")
        await setup.execute(FRONTEND_SQL.read_text())
    finally:
        await setup.close()

    target = await publish.connect_target(
        core_schema.dsn, server_settings={"search_path": schema}
    )
    try:
        yield target
    finally:
        await target.close()
        cleanup = await asyncpg.connect(core_schema.dsn)
        try:
            await cleanup.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            await cleanup.close()


async def _seed(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        company_id = await conn.fetchval(
            "INSERT INTO company(name, domain, tags) VALUES($1, $2, $3) RETURNING id",
            "Example",
            "example.com",
            {"ck_list": "swe:target"},
        )
        await conn.execute(
            "INSERT INTO company_logo(company_id, bytes) VALUES($1, $2)",
            company_id,
            b"logo",
        )
        raw_id = await conn.fetchval(
            """
            INSERT INTO raw_posting(source, source_key, data, data_hash)
            VALUES('test', 'job-1', $1, 'h') RETURNING id
            """,
            {"title": "Software Intern"},
        )
        await conn.execute(
            """
            INSERT INTO job_posting(
                source, source_key, company_id, title, content_hash, raw_id,
                job_type, status, locations, attributes, last_seen_at
            )
            VALUES('test', 'job-1', $1, 'Software Intern', 'c1', $2,
                   'intern', 'active', $3, $4, $5)
            """,
            company_id,
            raw_id,
            [{"country": "US"}],
            {"version": 1},
            NOW,
        )
        await conn.execute(
            """
            INSERT INTO coverage_snapshot(id, payload, computed_at)
            VALUES(true, $1, $2)
            ON CONFLICT (id) DO UPDATE
            SET payload = EXCLUDED.payload, computed_at = EXCLUDED.computed_at
            """,
            {"sources": 1},
            NOW,
        )
    return company_id


async def test_pool_rows_publish_through_the_target_codec(core_schema, thin_target):
    company_id = await _seed(core_schema.pool)

    async with core_schema.pool.acquire() as source:
        first = await publish.publish(source, thin_target)
        second = await publish.publish(source, thin_target)

    assert (first.cohort, first.postings, first.companies, first.logos) == (1, 1, 1, 1)
    assert first.coverage is True
    assert await thin_target.fetchval(
        "SELECT tags FROM company WHERE id = $1", company_id
    ) == {"ck_list": "swe:target"}
    assert await thin_target.fetchval("SELECT locations FROM job_posting") == [
        {"country": "US"}
    ]
    # Fingerprints match across the two schemas, so a rerun sends nothing.
    assert (second.postings, second.companies, second.logos) == (0, 0, 0)
    assert second.coverage is False


async def test_slow_source_read_does_not_idle_the_target_transaction(core_schema, thin_target):
    await _seed(core_schema.pool)
    await thin_target.execute("SET idle_in_transaction_session_timeout = '250ms'")

    class SlowSource:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        async def fetch(self, query, *args):
            if query == publish.JOB_ROWS_SQL:
                await asyncio.sleep(1)
            return await self.connection.fetch(query, *args)

    async with core_schema.pool.acquire() as source:
        result = await publish.publish(SlowSource(source), thin_target)

    assert result.postings == 1
    assert await thin_target.fetchval('SELECT count(*) FROM job_posting') == 1
    assert not thin_target.is_closed()


async def test_published_snapshot_retains_full_pipeline_active_count(core_schema, thin_target):
    await _seed(core_schema.pool)
    await core_schema.pool.execute("""
        INSERT INTO job_posting(
            source, source_key, company_id, title, content_hash, raw_id,
            job_type, status, locations
        )
        SELECT j.source, v.key, j.company_id, j.title, j.content_hash,
               j.raw_id, v.job_type, v.status, v.locations::jsonb
        FROM job_posting j
        CROSS JOIN (VALUES
            ('unclassified', NULL, 'active', '[]'),
            ('foreign', 'intern', 'active', '[{"country":"CA"}]'),
            ('closed', 'intern', 'closed', '[]')
        ) AS v(key, job_type, status, locations)
        WHERE j.source_key = 'job-1'
    """)
    await coverage_snapshot.refresh(core_schema.pool)
    async with core_schema.pool.acquire() as source:
        await publish.publish(source, thin_target)
    payload = await thin_target.fetchval("SELECT payload FROM coverage_snapshot WHERE id")
    assert payload["board_map"]["totals"]["active_postings"] == 3
    assert await thin_target.fetchval("SELECT COUNT(*) FROM job_posting WHERE status='active'") == 1


async def test_page_closure_signals_reach_a_previously_published_job(core_schema, thin_target):
    await _seed(core_schema.pool)
    async with core_schema.pool.acquire() as source:
        # Keep another active job in the cohort so the empty-selection guard
        # does not prevent this publish.
        await source.execute("""
            INSERT INTO job_posting(source,source_key,company_id,title,content_hash,raw_id,job_type,locations)
            SELECT source,'job-2',company_id,title,content_hash,raw_id,job_type,locations
            FROM job_posting WHERE source_key='job-1'
        """)
        await publish.publish(source,thin_target)
        # Personal history keeps this job after it leaves the serving cohort.
        await thin_target.execute("""
            INSERT INTO job_application(account_id,job_posting_id,company_snapshot,title_snapshot)
            SELECT '00000000-0000-0000-0000-000000000001',id,'Example',title
            FROM job_posting WHERE source_key='job-1'
        """)
        await source.execute("UPDATE job_posting SET missing_since=$1 WHERE source_key='job-1'",NOW)
        await store.close_posting_by_key(source,source='test',source_key='job-1',closed_at=NOW,closure_kind='page')
        result=await publish.publish(source,thin_target)
    row=await thin_target.fetchrow("SELECT * FROM job_posting WHERE source_key='job-1'")
    assert result.closed==1
    assert row['status']=='closed'
    assert row['missing_since']==row['paged_closed_at']==NOW
    assert row['linked_dead_at'] is None
    assert row['ats_closed_at'] is None
    assert row['title']=='Software Intern'


@pytest.mark.parametrize("dry_run", [False, True])
async def test_closed_cleanup_preserves_personal_history_and_rolls_back(
    core_schema, thin_target, dry_run
):
    await _seed(core_schema.pool)
    async with core_schema.pool.acquire() as source:
        await publish.publish(source, thin_target)
        active_id = await thin_target.fetchval("SELECT id FROM job_posting")
        await thin_target.execute("""
            INSERT INTO job_posting(
                id,source,source_key,company_id,title,content_hash,status
            )
            SELECT v.id,'cleanup',v.id::text,j.company_id,'Old posting','old',v.status
            FROM job_posting j
            CROSS JOIN (VALUES
                (101,'active'),(102,'closed'),(103,'active'),(104,'closed')
            ) AS v(id,status)
        """)
        application_id = await thin_target.fetchval("""
            INSERT INTO job_application(
                account_id,job_posting_id,company_snapshot,title_snapshot,note
            ) VALUES (
                '00000000-0000-0000-0000-000000000001',103,'Example','Old posting','Keep note'
            ) RETURNING id
        """)
        await thin_target.execute("""
            INSERT INTO job_application_event(job_application_id,event_type,note)
            VALUES($1,'note','Keep history')
        """, application_id)
        await thin_target.execute("""
            INSERT INTO job_feedback(account_id,job_posting_id,kind)
            VALUES('00000000-0000-0000-0000-000000000001',104,'skip')
        """)
        personal_tables = ("job_application", "job_application_event", "job_feedback")
        personal_before = {
            table: await thin_target.fetch(f"SELECT * FROM {table}")
            for table in personal_tables
        }
        jobs_before = await thin_target.fetch("SELECT * FROM job_posting ORDER BY id")

        await publish.publish(source, thin_target, dry_run=dry_run)

    for table in personal_tables:
        assert await thin_target.fetch(f"SELECT * FROM {table}") == personal_before[table]
    if dry_run:
        assert await thin_target.fetch("SELECT * FROM job_posting ORDER BY id") == jobs_before
    else:
        rows = await thin_target.fetch("SELECT id,status FROM job_posting ORDER BY id")
        assert [(row["id"], row["status"]) for row in rows] == [
            (active_id, "active"), (103, "closed"), (104, "closed")
        ]
    # Cleanup changes only the serving target; pipeline data remains intact.
    assert await core_schema.pool.fetchval("SELECT count(*) FROM job_posting") == 1
