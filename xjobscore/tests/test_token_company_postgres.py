"""Silver mints the company for an ATS token that has none.

Bulk-promoted tokens reach ``feed`` with ``token.company_id`` NULL, and every
posting they fetched was dropped as unresolved. These pin the mint: one company
per employer even under concurrent polls, existing companies reused, and the
paths that must never write (Board feeds, dry runs, alias snapshots) left alone.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from xjobscore import router, silver, store
from xjobscore.records import Feed, Item, Posting

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


async def _token(conn, ats: str, token: str) -> None:
    await conn.execute(
        "INSERT INTO token (ats, token, status) VALUES ($1, $2, 'live')", ats, token
    )


async def _company_of(conn, ats: str, token: str) -> int | None:
    return await conn.fetchval(
        "SELECT company_id FROM token WHERE ats = $1 AND token = $2", ats, token
    )


def _patch_silver(monkeypatch, company_raw: str | None, count: int = 1) -> list[dict]:
    postings = [
        Posting(
            external_id=f"job-{index}",
            title="Software Engineer",
            url_posting=f"https://example.test/jobs/{index}",
            company_raw=company_raw,
        )
        for index in range(count)
    ]
    by_id = {posting.external_id: posting for posting in postings}
    monkeypatch.setattr(
        silver.sources,
        "parser",
        lambda name: lambda feed, item, envelope: [by_id[item.external_id]],
    )
    writes: list[dict] = []

    async def write(conn, **kwargs):
        writes.append(kwargs)
        return len(writes), True, True

    monkeypatch.setattr(store, "upsert_job_posting", write)
    return writes


def _stored(count: int = 1) -> list[router.StoredRaw]:
    return [
        router.StoredRaw(Item(f"job-{index}", {}), index + 1, f"raw-{index}", True)
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_mints_names_aliases_and_links_the_token(core_schema):
    token = f"centria-{uuid4().hex[:8]}"
    async with core_schema.pool.acquire() as conn:
        await _token(conn, "greenhouse", token)
        company_id, minted = await store.ensure_token_company(
            conn, ats="greenhouse", token=token, name="Centria Autism"
        )
        company = await conn.fetchrow("SELECT * FROM company WHERE id = $1", company_id)
        aliases = {
            row["alias_norm"]
            for row in await conn.fetch(
                "SELECT alias_norm FROM company_alias WHERE company_id = $1", company_id
            )
        }
        linked = await _company_of(conn, "greenhouse", token)

    assert minted is True
    assert company["name"] == "Centria Autism"
    assert company["reviewed"] == "pending"
    assert company["discovered_from"] == "feed"
    assert company["domain"] is None
    assert company["tags"] == {"name_machine": "Centria Autism"}
    assert {"centria autism", token} <= aliases
    assert linked == company_id


@pytest.mark.asyncio
async def test_a_nameless_token_is_minted_under_its_slug(core_schema):
    token = f"tenant{uuid4().hex[:6]}/wd5/external"
    async with core_schema.pool.acquire() as conn:
        await _token(conn, "workday", token)
        company_id, _ = await store.ensure_token_company(
            conn, ats="workday", token=token, name=None
        )
        name = await conn.fetchval("SELECT name FROM company WHERE id = $1", company_id)

    # The tenant, not the whole `tenant/host/site` path.
    assert name == token.split("/")[0]


@pytest.mark.asyncio
async def test_an_existing_company_is_linked_not_duplicated(core_schema):
    token = f"acme-{uuid4().hex[:8]}"
    async with core_schema.pool.acquire() as conn:
        existing = await store.mint_company(conn, "Acme Robotics", discovered_from="manual")
        await store.write_company_aliases(conn, existing, "Acme Robotics")
        await _token(conn, "lever", token)
        company_id, minted = await store.ensure_token_company(
            conn, ats="lever", token=token, name="Acme Robotics, Inc."
        )
        companies = await conn.fetchval(
            "SELECT count(*) FROM company WHERE name LIKE 'Acme Robotics%'"
        )
        slug_owner = await conn.fetchval(
            "SELECT company_id FROM company_alias WHERE alias_norm = $1", token
        )

    assert (company_id, minted) == (existing, False)
    assert companies == 1
    # The slug alias is added, so the next poll resolves without the lock.
    assert slug_owner == existing


@pytest.mark.asyncio
async def test_concurrent_feeds_for_one_employer_mint_one_company(core_schema):
    """Without the advisory lock both transactions miss and both mint.

    The first holds its transaction open after minting, so the second's alias
    lookup runs while the first's aliases are still uncommitted -- exactly the
    window two Greenhouse workers polling sibling boards would hit.
    """
    suffix = uuid4().hex[:8]
    name = f"Twin Labs {suffix}"
    tokens = [f"twinlabs-{suffix}", f"twinlabs-careers-{suffix}"]
    async with core_schema.pool.acquire() as conn:
        for token in tokens:
            await _token(conn, "greenhouse", token)

    first_minted = asyncio.Event()

    async def poll(token: str, *, hold: bool) -> int:
        async with core_schema.pool.acquire() as conn, conn.transaction():
            if not hold:
                await first_minted.wait()
            company_id, _ = await store.ensure_token_company(
                conn, ats="greenhouse", token=token, name=name
            )
            if hold:
                first_minted.set()
                await asyncio.sleep(0.3)
            return company_id

    ids = await asyncio.gather(poll(tokens[0], hold=True), poll(tokens[1], hold=False))

    async with core_schema.pool.acquire() as conn:
        companies = await conn.fetchval("SELECT count(*) FROM company WHERE name = $1", name)
    assert companies == 1
    assert ids[0] == ids[1]


@pytest.mark.asyncio
async def test_silver_mints_once_and_writes_every_posting(core_schema, monkeypatch):
    token = f"quaise-{uuid4().hex[:8]}"
    writes = _patch_silver(monkeypatch, company_raw="Quaise Energy", count=3)
    feed = Feed(id=1, source="greenhouse", key=token, company_id=None, type="ATS")
    async with core_schema.pool.acquire() as conn:
        await _token(conn, "greenhouse", token)
        async with conn.transaction():
            counts = await silver.process(conn, feed, _stored(3), {}, seen_at=NOW)
        linked = await _company_of(conn, "greenhouse", token)
        companies = await conn.fetchval(
            "SELECT count(*) FROM company WHERE name = 'Quaise Energy'"
        )

    assert (counts.postings, counts.unresolved, counts.minted) == (3, 0, 1)
    assert companies == 1
    assert {write["company_id"] for write in writes} == {linked}


@pytest.mark.asyncio
async def test_silver_without_a_company_name_mints_from_the_slug(core_schema, monkeypatch):
    token = f"tenant{uuid4().hex[:6]}/wd1/careers"
    writes = _patch_silver(monkeypatch, company_raw=None, count=2)
    feed = Feed(id=1, source="workday", key=token, company_id=None, type="ATS")
    async with core_schema.pool.acquire() as conn:
        await _token(conn, "workday", token)
        async with conn.transaction():
            counts = await silver.process(conn, feed, _stored(2), {}, seen_at=NOW)
        linked = await _company_of(conn, "workday", token)

    assert (counts.postings, counts.minted) == (2, 1)
    assert linked is not None
    assert {write["company_id"] for write in writes} == {linked}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("feed_type", "kwargs"),
    [
        # An aggregator's token is not the employer.
        ("Board", {}),
        # A dry run writes nothing.
        ("ATS", {"dry_run": True}),
        # An alias snapshot promises nothing writes aliases mid-pass.
        ("ATS", {"aliases": {}}),
    ],
)
async def test_paths_that_must_not_mint_still_skip(core_schema, monkeypatch, feed_type, kwargs):
    token = f"nomint-{uuid4().hex[:8]}"
    writes = _patch_silver(monkeypatch, company_raw=f"Nobody {token}")
    feed = Feed(id=1, source="greenhouse", key=token, company_id=None, type=feed_type)
    async with core_schema.pool.acquire() as conn:
        await _token(conn, "greenhouse", token)
        async with conn.transaction():
            counts = await silver.process(conn, feed, _stored(), {}, seen_at=NOW, **kwargs)
        linked = await _company_of(conn, "greenhouse", token)
        companies = await conn.fetchval(
            "SELECT count(*) FROM company WHERE name = $1", f"Nobody {token}"
        )

    assert (counts.unresolved, counts.minted, counts.postings) == (1, 0, 0)
    assert (linked, companies, writes) == (None, 0, [])
