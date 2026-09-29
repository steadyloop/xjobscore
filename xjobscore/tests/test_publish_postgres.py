"""Publish against real PostgreSQL: a store pool on the home side, the thin
schema on the target side. The fakes in test_publish.py cannot see how asyncpg
encodes jsonb, which is exactly where the two connections must agree."""

from __future__ import annotations

import os
import pathlib
from datetime import datetime, timezone

import asyncpg
import pytest

from xjobscore import publish


pytestmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"),
    reason="set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests",
)

THIN_SQL = pathlib.Path(__file__).resolve().parents[2] / "schema-thin.sql"
NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


@pytest.fixture
async def thin_target(core_schema):
    schema = f"{core_schema.schema}_thin"
    setup = await asyncpg.connect(core_schema.dsn)
    try:
        await setup.execute(f"CREATE SCHEMA {schema}")
        await setup.execute(f"SET search_path TO {schema}")
        await setup.execute(THIN_SQL.read_text())
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
