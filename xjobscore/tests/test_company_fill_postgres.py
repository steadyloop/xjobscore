"""Feeds fill what they know about a company, and only into empty columns.

A machine guess must never overwrite a person's answer or another company's
domain, and a company that gains a domain must keep the serving ID OneShot
already shows -- its identity key switches from `source_id` to `domain`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from xjobscore import router, silver, store
from xjobscore.records import Feed, Item, Posting

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def _host() -> str:
    return f"acme-{uuid4().hex[:8]}.com"


async def _company(conn, name: str = "Acme", **columns) -> int:
    company_id = await store.mint_company(conn, name, discovered_from="feed")
    for column, value in columns.items():
        await conn.execute(f"UPDATE company SET {column} = $2 WHERE id = $1", company_id, value)
    return company_id


@pytest.mark.asyncio
async def test_fills_empty_columns_and_records_the_machine_guess(core_schema):
    host = _host()
    async with core_schema.pool.acquire() as conn:
        company_id = await _company(conn)
        changed = await store.fill_company(
            conn, company_id, domain=f"https://www.{host.upper()}/careers?x=1",
            description="  Makes rockets.  ",
        )
        row = await conn.fetchrow("SELECT * FROM company WHERE id = $1", company_id)

    assert changed is True
    assert (row["domain"], row["description"]) == (host, "Makes rockets.")
    assert row["tags"]["domain_machine"] == host
    assert row["tags"]["description_machine"] == "Makes rockets."
    assert row["reviewed"] == "pending"


@pytest.mark.asyncio
async def test_never_overwrites_a_set_column(core_schema):
    kept = _host()
    async with core_schema.pool.acquire() as conn:
        company_id = await _company(conn, domain=kept, description="Theirs.")
        changed = await store.fill_company(
            conn, company_id, domain=_host(), description="Ours."
        )
        row = await conn.fetchrow("SELECT domain, description FROM company WHERE id = $1", company_id)

    assert changed is False
    assert (row["domain"], row["description"]) == (kept, "Theirs.")


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["verified", "rejected"])
async def test_leaves_reviewed_rows_alone(core_schema, verdict):
    async with core_schema.pool.acquire() as conn:
        company_id = await _company(conn, reviewed=verdict)
        changed = await store.fill_company(conn, company_id, domain=_host(), description="x")
        row = await conn.fetchrow("SELECT domain, description FROM company WHERE id = $1", company_id)

    assert changed is False
    assert (row["domain"], row["description"]) == (None, None)


@pytest.mark.asyncio
async def test_skips_a_domain_another_company_holds(core_schema):
    host = _host()
    async with core_schema.pool.acquire() as conn:
        await _company(conn, "Owner", domain=host)
        company_id = await _company(conn, "Claimant")
        changed = await store.fill_company(
            conn, company_id, domain=host, description="Still filled."
        )
        row = await conn.fetchrow("SELECT domain, description FROM company WHERE id = $1", company_id)

    assert changed is True
    assert (row["domain"], row["description"]) == (None, "Still filled.")


@pytest.mark.asyncio
async def test_refuses_a_job_board_host(core_schema):
    async with core_schema.pool.acquire() as conn:
        company_id = await _company(conn)
        changed = await store.fill_company(
            conn, company_id, domain="https://boards.greenhouse.io/acme/jobs/1"
        )

    assert changed is False


@pytest.mark.asyncio
async def test_a_new_domain_keeps_the_serving_id(core_schema):
    host = _host()
    async with core_schema.pool.acquire() as conn:
        company_id = await _company(conn)
        old_key = await store.company_identity_key(conn, company_id)
        serving_id = await conn.fetchval(
            "INSERT INTO frontend_identity (kind, key) VALUES ('company', $1) RETURNING id",
            old_key,
        )
        await store.fill_company(conn, company_id, domain=host)
        mapping = await conn.fetchrow(
            "SELECT key, previous_keys FROM frontend_identity WHERE kind = 'company' AND id = $1",
            serving_id,
        )

    assert mapping["key"] == ["domain", host]
    assert mapping["previous_keys"] == [old_key]


@pytest.mark.asyncio
async def test_silver_fills_once_per_batch_and_never_on_a_dry_run(core_schema, monkeypatch):
    host = _host()
    postings = {
        f"job-{index}": Posting(
            external_id=f"job-{index}",
            title="Intern",
            url_posting=f"https://{host}/jobs/{index}",
            company_domain=f"https://{host}/jobs/{index}",
            company_description="Makes rockets.",
        )
        for index in range(3)
    }
    monkeypatch.setattr(
        silver.sources, "parser",
        lambda name: lambda feed, item, envelope: [postings[item.external_id]],
    )

    async def write(conn, **kwargs):
        return 1, True, True

    monkeypatch.setattr(store, "upsert_job_posting", write)
    fills: list[int] = []
    real_fill = store.fill_company

    async def counting_fill(conn, company_id, **kwargs):
        fills.append(company_id)
        return await real_fill(conn, company_id, **kwargs)

    monkeypatch.setattr(store, "fill_company", counting_fill)
    stored = [
        router.StoredRaw(Item(external_id, {}), index + 1, f"raw-{index}", True)
        for index, external_id in enumerate(postings)
    ]
    async with core_schema.pool.acquire() as conn:
        company_id = await _company(conn)
        feed = Feed(id=1, source="greenhouse", key="acme", company_id=company_id, type="ATS")
        async with conn.transaction():
            await silver.process(conn, feed, stored, {}, seen_at=NOW, dry_run=True)
        assert fills == []
        async with conn.transaction():
            await silver.process(conn, feed, stored, {}, seen_at=NOW)
        domain = await conn.fetchval("SELECT domain FROM company WHERE id = $1", company_id)

    assert fills == [company_id]
    assert domain == host


@pytest.mark.asyncio
async def test_resolve_or_mint_prefers_earlier_names(core_schema):
    suffix = uuid4().hex[:8]
    async with core_schema.pool.acquire() as conn:
        async with conn.transaction():
            by_slug = await _company(conn, f"slug-{suffix}")
            await store.write_company_aliases(conn, by_slug, f"slug-{suffix}")
            company_id, minted = await store.resolve_or_mint(
                conn, f"Named {suffix}", f"slug-{suffix}", discovered_from="feed"
            )
            fresh_id, fresh = await store.resolve_or_mint(
                conn, None, f"Fresh {suffix}", discovered_from="simplify"
            )
            fresh_name = await conn.fetchval("SELECT name FROM company WHERE id = $1", fresh_id)
            named_owner = await store.resolve_company(conn, f"Named {suffix}")

    assert (company_id, minted) == (by_slug, False)
    # The unmatched name becomes an alias of the company the slug found.
    assert named_owner == by_slug
    assert fresh is True
    assert fresh_name == f"Fresh {suffix}"
