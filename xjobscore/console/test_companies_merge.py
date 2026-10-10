"""Merge is five tables and a delete, so it is tested against a real schema.

A fake pool would only assert the SQL strings this module already contains. The
value here is the foreign keys: `job_posting.company_id` is NO ACTION and
`company_alias`/`company_logo` are ON DELETE CASCADE, so an ordering mistake
either refuses to commit or silently destroys the aliases that let `silver`
keep resolving the old spelling.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from xjobscore.console import companies

pytestmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"),
    reason="set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests",
)


async def _company(conn, name, domain=None, **columns):
    company_id = await conn.fetchval(
        "INSERT INTO company (name, domain, tags) VALUES ($1, $2, $3::jsonb)"
        " RETURNING id",
        name,
        domain,
        columns.get("tags", {}),
    )
    return int(company_id)


async def _posting(conn, company_id, source, key, title="SWE Intern"):
    raw_id = await conn.fetchval(
        "INSERT INTO raw_posting (source, source_key, data, data_hash)"
        " VALUES ($1, $2, '{}'::jsonb, $3) RETURNING id",
        source,
        key,
        uuid4().hex,
    )
    return await conn.fetchval(
        "INSERT INTO job_posting (source, source_key, company_id, title, raw_id,"
        " content_hash) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
        source,
        key,
        company_id,
        title,
        raw_id,
        uuid4().hex,
    )


@pytest.mark.asyncio
async def test_merge_moves_tokens_postings_and_aliases(core_schema):
    pool = core_schema.pool
    async with pool.acquire() as conn:
        source = await _company(conn, "Tradeweb Europe Limited")
        target = await _company(conn, "Tradeweb", "tradeweb.com")
        await conn.execute(
            "INSERT INTO token (ats, token, company_id, status, source)"
            " VALUES ('oracle', 'ecnf.fa.us2/cx', $1, 'live', 'keyscan')",
            source,
        )
        await conn.execute(
            "INSERT INTO company_alias (alias_norm, company_id, alias)"
            " VALUES ('ecnf.fa.us2', $1, 'ecnf.fa.us2')",
            source,
        )
        await _posting(conn, source, "oracle:ecnf.fa.us2/cx", "j1")

    row = await companies.merge(pool, source, target)

    assert row is not None and row["id"] == target
    # The target keeps its own identity: a merge is not a rename.
    assert row["name"] == "Tradeweb"
    assert row["domain"] == "tradeweb.com"

    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT company_id FROM token WHERE token = 'ecnf.fa.us2/cx'"
        ) == target
        assert await conn.fetchval(
            "SELECT company_id FROM job_posting WHERE source_key = 'j1'"
        ) == target
        aliases = {
            r["alias_norm"]
            for r in await conn.fetch(
                "SELECT alias_norm FROM company_alias WHERE company_id = $1", target
            )
        }
        assert await conn.fetchval(
            "SELECT count(*) FROM company WHERE id = $1", source
        ) == 0
    # The token slug survives, and so does the source's own name: silver keeps
    # seeing both on the wire long after this row is gone.
    assert "ecnf.fa.us2" in aliases
    assert "tradeweb europe limited" in aliases


@pytest.mark.asyncio
async def test_a_spelling_the_target_already_owns_is_not_stolen(core_schema):
    pool = core_schema.pool
    async with pool.acquire() as conn:
        source = await _company(conn, f"Dup A {uuid4().hex[:6]}")
        target = await _company(conn, f"Dup B {uuid4().hex[:6]}")
        shared = f"shared-{uuid4().hex[:8]}"
        await conn.execute(
            "INSERT INTO company_alias (alias_norm, company_id, alias)"
            " VALUES ($1, $2, $1), ($1 || '-src', $3, $1)",
            shared,
            target,
            source,
        )

    await companies.merge(pool, source, target)

    async with pool.acquire() as conn:
        owner = await conn.fetchval(
            "SELECT company_id FROM company_alias WHERE alias_norm = $1", shared
        )
    # alias_norm is unique; the duplicate is dropped rather than colliding.
    assert owner == target


@pytest.mark.asyncio
async def test_merge_refuses_a_self_merge_and_a_missing_row(core_schema):
    pool = core_schema.pool
    async with pool.acquire() as conn:
        only = await _company(conn, f"Solo {uuid4().hex[:6]}")

    with pytest.raises(ValueError, match="itself"):
        await companies.merge(pool, only, only)
    assert await companies.merge(pool, only, 2_000_000_000) is None


@pytest.mark.asyncio
async def test_owner_of_domain_names_the_blocking_row(core_schema):
    pool = core_schema.pool
    domain = f"{uuid4().hex[:8]}.test"
    async with pool.acquire() as conn:
        owner = await _company(conn, "Domain Owner", domain)

    found = await companies.owner_of_domain(pool, f"  {domain} ")
    assert found is not None and found["id"] == owner
    assert found["name"] == "Domain Owner"
    # A blank box is "no domain", not a lookup for the empty string.
    assert await companies.owner_of_domain(pool, "   ") is None
    assert await companies.owner_of_domain(pool, None) is None
