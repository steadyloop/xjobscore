"""One-time domain attempts through real database claims and poll completion."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from xjobscore import company_domain, router, run, sources, store
from xjobscore.records import Feed, Item, Posting, Record

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


async def _feed(pool, *, source="lever", name="Acme Inc", domain=None, reviewed="pending", tags=None):
    key = f"acme-{uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        company_id = None
        if name is not None:
            company_id = await conn.fetchval(
                "INSERT INTO company(name,domain,reviewed,tags) VALUES ($1,$2,$3,$4) RETURNING id",
                name, domain, reviewed, tags or {},
            )
        await conn.execute(
            "INSERT INTO token(ats,token,company_id,status) VALUES ($1,$2,$3,'live')",
            source, key, company_id,
        )
        await conn.execute(
            "INSERT INTO feed(source,key,polling,due_at) VALUES ($1,$2,true,$3)", source, key, NOW
        )
        feed = await store.claim_feed(conn, source=source, key=key, now=NOW)
    return feed, company_id


async def _row(pool, company_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM company WHERE id=$1", company_id)


@pytest.fixture
def probe(monkeypatch):
    calls = []

    async def reachable(candidate):
        calls.append(candidate)
        return True

    monkeypatch.setattr(company_domain, "_reachable", reachable)
    return calls


@pytest.mark.parametrize("original", [{}, {"domain_machine": "older.example"}, {"domain_machine": None}])
async def test_success_preserves_provenance_and_serving_id(core_schema, probe, original):
    pool = core_schema.pool
    tags = {"fill_checked_at": "previously tried", "startup": True, **original}
    feed, company_id = await _feed(pool, tags=tags)
    async with pool.acquire() as conn:
        old_key = await store.company_identity_key(conn, company_id)
        serving_id = await conn.fetchval(
            "INSERT INTO frontend_identity(kind,key) VALUES ('company',$1) RETURNING id", old_key
        )

    assert await company_domain.try_for_feed(pool, feed)
    assert not await company_domain.try_for_feed(pool, feed)
    row = await _row(pool, company_id)
    assert probe == ["acme.com"]
    assert row["domain"] == "acme.com"
    assert row["reviewed"] == "pending"
    assert row["tags"]["domain_guess"]["added"] is True
    assert row["tags"]["domain_guess"]["checked_at"]
    assert row["tags"]["domain_guess"]["candidate"] == "acme.com"
    assert row["tags"]["startup"] is True
    assert row["tags"]["fill_checked_at"] == "previously tried"
    assert row["tags"]["domain_machine"] == original.get("domain_machine", "acme.com")
    async with pool.acquire() as conn:
        mapping = await conn.fetchrow("SELECT key,previous_keys FROM frontend_identity WHERE id=$1", serving_id)
    assert mapping["key"] == ["domain", "acme.com"]
    assert mapping["previous_keys"] == [old_key]


async def test_failed_attempt_is_committed_before_http_and_not_retried(core_schema, monkeypatch):
    pool = core_schema.pool
    feed, company_id = await _feed(pool)
    calls = []

    async def unavailable(candidate):
        calls.append(candidate)
        # Another connection can see the committed marker while HTTP is in flight.
        row = await _row(pool, company_id)
        assert row["tags"]["domain_guess"]["added"] is False
        return False

    monkeypatch.setattr(company_domain, "_reachable", unavailable)
    assert not await company_domain.try_for_feed(pool, feed)
    assert not await company_domain.try_for_feed(pool, feed)
    assert calls == ["acme.com"]
    assert (await _row(pool, company_id))["domain"] is None


async def test_interrupted_attempt_is_not_retried(core_schema, monkeypatch):
    feed, company_id = await _feed(core_schema.pool)

    async def interrupted(candidate):
        raise asyncio.CancelledError()

    monkeypatch.setattr(company_domain, "_reachable", interrupted)
    with pytest.raises(asyncio.CancelledError):
        await company_domain.try_for_feed(core_schema.pool, feed)
    assert not await company_domain.try_for_feed(core_schema.pool, feed)
    assert (await _row(core_schema.pool, company_id))["tags"]["domain_guess"]["added"] is False


async def test_concurrent_feeds_claim_only_one_attempt(core_schema, probe):
    pool = core_schema.pool
    feed, company_id = await _feed(pool)
    other = replace(feed, source="ashby", key="another-board")
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO token(ats,token,company_id) VALUES ($1,$2,$3)",
            other.source, other.key, company_id,
        )
    results = await asyncio.gather(*[
        company_domain.try_for_feed(pool, candidate) for candidate in (feed, other, feed, other)
    ])
    assert sum(results) == 1
    assert probe == ["acme.com"]


@pytest.mark.parametrize("columns", [
    {"domain": "already.example"}, {"reviewed": "verified"}, {"reviewed": "rejected"},
    {"tags": {"domain_guess": {"candidate": "acme.com", "added": False}}},
])
async def test_existing_answers_or_attempts_are_skipped(core_schema, probe, columns):
    feed, company_id = await _feed(core_schema.pool, **columns)
    before = dict(await _row(core_schema.pool, company_id))
    assert not await company_domain.try_for_feed(core_schema.pool, feed)
    assert dict(await _row(core_schema.pool, company_id)) == before
    assert probe == []


async def test_a_name_with_no_guess_is_marked_without_http(core_schema, probe):
    feed, company_id = await _feed(core_schema.pool, name="LLC")
    assert not await company_domain.try_for_feed(core_schema.pool, feed)
    assert (await _row(core_schema.pool, company_id))["tags"]["domain_guess"]["candidate"] is None
    assert probe == []


async def test_owned_domain_is_skipped_without_merging(core_schema, probe):
    pool = core_schema.pool
    feed, company_id = await _feed(pool)
    async with pool.acquire() as conn:
        owner = await conn.fetchval("INSERT INTO company(name,domain) VALUES ('Owner','ACME.COM') RETURNING id")
    assert not await company_domain.try_for_feed(pool, feed)
    row = await _row(pool, company_id)
    assert row["domain"] is None
    assert row["tags"]["domain_guess"]["added"] is False
    assert (await _row(pool, owner))["domain"] == "ACME.COM"


@pytest.mark.parametrize("column,value", [
    ("domain", "human.example"), ("name", "Corrected name"), ("reviewed", "verified"),
])
async def test_manual_edits_during_http_are_preserved(core_schema, monkeypatch, column, value):
    pool = core_schema.pool
    feed, company_id = await _feed(pool)

    async def edit(candidate):
        async with pool.acquire() as conn:
            await conn.execute(f"UPDATE company SET {column}=$2 WHERE id=$1", company_id, value)
        return True

    monkeypatch.setattr(company_domain, "_reachable", edit)
    assert not await company_domain.try_for_feed(pool, feed)
    row = await _row(pool, company_id)
    assert row[column] == value
    assert row["tags"]["domain_guess"]["added"] is False


@pytest.mark.parametrize("mode", ["304", "count_unchanged", "hash_unchanged", "new", "partial", "native_domain"])
async def test_poll_completion_covers_existing_and_minted_companies(core_schema, monkeypatch, probe, mode):
    pool = core_schema.pool
    feed, company_id = await _feed(pool, name=None if mode in {"new", "partial", "native_domain"} else "Acme Inc")
    record = Record(items=[Item("1", {})], truncated=mode == "partial")
    if mode == "304":
        record = Record(http_status=304)
    elif mode == "count_unchanged":
        record = Record(http_status=200, count_unchanged=True)
    elif mode == "hash_unchanged":
        feed = replace(feed, res_hash=router.snapshot_hash(router.prepare(feed, record)))

    async def harvest(feed, net):
        return record

    def parse(feed, item, envelope):
        return [Posting(
            external_id=item.external_id, company_raw="Acme Inc", title="Intern",
            url_posting="https://jobs.lever.co/acme/1",
            company_domain="native.example" if mode == "native_domain" else None,
        )]

    monkeypatch.setattr(sources, "get", lambda source: harvest)
    monkeypatch.setattr(sources, "parser", lambda source: parse)
    expected = "unchanged" if mode in {"304", "count_unchanged"} else "partial" if mode == "partial" else "ok"
    assert await run.poll(pool, object(), feed, NOW) == expected
    async with pool.acquire() as conn:
        company_id = await conn.fetchval("SELECT company_id FROM token WHERE ats=$1 AND token=$2", feed.source, feed.key)
    row = await _row(pool, company_id)
    if mode == "native_domain":
        assert row["domain"] == "native.example"
        assert "domain_guess" not in row["tags"]
        assert probe == []
    else:
        assert row["domain"] == "acme.com"
        assert row["tags"]["domain_guess"]["added"] is True
        assert probe == ["acme.com"]


async def test_probe_errors_do_not_fail_a_poll(core_schema, monkeypatch):
    feed, company_id = await _feed(core_schema.pool)

    async def harvest(feed, net):
        return Record(http_status=304)

    async def unexpected(candidate):
        raise RuntimeError("unexpected website error")

    monkeypatch.setattr(sources, "get", lambda source: harvest)
    monkeypatch.setattr(company_domain, "_reachable", unexpected)
    assert await run.poll(core_schema.pool, object(), feed, NOW) == "unchanged"
    assert (await _row(core_schema.pool, company_id))["domain"] is None
    assert not await company_domain.try_for_feed(core_schema.pool, feed)
