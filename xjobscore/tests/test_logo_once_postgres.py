"""A company's logo is fetched once per domain, success or not.

The publisher runs `harvest` every interval, so anything it reconsiders it
re-pays on every run: a same-domain answer must stand, and only a corrected
domain earns a new attempt.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from xjobscore import logo_harvest, store

PNG = b"\x89PNG fake"


@pytest.fixture
def fetched(monkeypatch):
    """Domains `_fetch_logo` was asked for; `failing` domains answer nothing."""
    asked: list[str] = []
    failing: set[str] = set()

    async def fetch(client, domain):
        asked.append(domain)
        return None if domain in failing else PNG

    monkeypatch.setattr(logo_harvest, "_fetch_logo", fetch)
    return asked, failing


async def _company(conn, domain: str | None) -> int:
    company_id = await store.mint_company(conn, f"Co {uuid4().hex[:6]}", discovered_from="feed")
    await conn.execute("UPDATE company SET domain = $2 WHERE id = $1", company_id, domain)
    return company_id


async def _logo(conn, company_id: int):
    return await conn.fetchrow("SELECT bytes, domain FROM company_logo WHERE company_id = $1", company_id)


@pytest.mark.asyncio
async def test_each_domain_is_asked_once_whatever_it_answered(core_schema, fetched):
    asked, failing = fetched
    good, bad = f"{uuid4().hex[:8]}.com", f"{uuid4().hex[:8]}.com"
    failing.add(bad)
    async with core_schema.pool.acquire() as conn:
        good_id, bad_id = await _company(conn, good), await _company(conn, bad)

    await logo_harvest.harvest(core_schema.pool)
    await logo_harvest.harvest(core_schema.pool)

    async with core_schema.pool.acquire() as conn:
        assert dict(await _logo(conn, good_id)) == {"bytes": PNG, "domain": good}
        assert dict(await _logo(conn, bad_id)) == {"bytes": None, "domain": bad}
    assert sorted(asked) == sorted([good, bad])


@pytest.mark.asyncio
async def test_a_corrected_domain_is_asked_again(core_schema, fetched):
    asked, _ = fetched
    wrong, right = f"{uuid4().hex[:8]}.com", f"{uuid4().hex[:8]}.com"
    async with core_schema.pool.acquire() as conn:
        company_id = await _company(conn, wrong)
    await logo_harvest.harvest(core_schema.pool)
    async with core_schema.pool.acquire() as conn:
        await conn.execute("UPDATE company SET domain = $2 WHERE id = $1", company_id, right)

    await logo_harvest.harvest(core_schema.pool)

    async with core_schema.pool.acquire() as conn:
        assert (await _logo(conn, company_id))["domain"] == right
    assert asked == [wrong, right]


@pytest.mark.asyncio
async def test_a_cleared_domain_keeps_no_logo(core_schema, fetched):
    domain = f"{uuid4().hex[:8]}.com"
    async with core_schema.pool.acquire() as conn:
        company_id = await _company(conn, domain)
    await logo_harvest.harvest(core_schema.pool)
    async with core_schema.pool.acquire() as conn:
        await conn.execute("UPDATE company SET domain = NULL WHERE id = $1", company_id)

    await logo_harvest.harvest(core_schema.pool)

    async with core_schema.pool.acquire() as conn:
        # Emptied, not deleted: publishing ships rows, never deletes.
        assert dict(await _logo(conn, company_id)) == {"bytes": None, "domain": None}


@pytest.mark.asyncio
async def test_harvest_one_records_the_domain_it_asked(core_schema, fetched):
    domain = f"{uuid4().hex[:8]}.com"
    async with core_schema.pool.acquire() as conn:
        company_id = await _company(conn, domain)

    await logo_harvest.harvest_one(core_schema.pool, company_id)

    async with core_schema.pool.acquire() as conn:
        assert dict(await _logo(conn, company_id)) == {"bytes": PNG, "domain": domain}


@pytest.mark.asyncio
async def test_migration_stamps_existing_logos_with_todays_domain(core_schema, live_migration):
    sql = live_migration("20261011_company_logo_domain.sql")
    domain = f"{uuid4().hex[:8]}.com"
    async with core_schema.pool.acquire() as conn:
        company_id = await _company(conn, domain)
        await conn.execute(
            "INSERT INTO company_logo (company_id, bytes) VALUES ($1, $2)", company_id, PNG
        )
        await conn.execute(sql)
        assert dict(await _logo(conn, company_id)) == {"bytes": PNG, "domain": domain}
