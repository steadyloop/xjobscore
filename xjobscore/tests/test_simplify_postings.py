"""Simplify ingestion, provenance and insert-only ATS discovery."""

from datetime import datetime, timedelta, timezone

import pytest

from xjobscore import router, silver, simplify_postings, store
from xjobscore.records import Feed, Item, Record
from xjobscore.sources import docxtract

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)
    monkeypatch.setattr(docxtract, "datetime", Clock)


def payload(**changes):
    row = {
        "id": "listing-1", "title": "Software Engineer Intern",
        "url": "https://jobs.ashbyhq.com/Example/123/application",
        "company_name": "Example Employer", "company_url": "https://simplify.jobs/c/example",
        "locations": ["London, UK", "Greensboro, NC"],
        "active": True, "is_visible": True, "terms": ["Summer 2027"],
        "date_posted": int((NOW - timedelta(days=2)).timestamp()),
        "date_updated": int(NOW.timestamp()), "source": "Simplify",
        "category": "Software", "degrees": ["Bachelor's"], "sponsorship": "Other",
    }
    return {**row, **changes}


async def ingest(conn, rows, *, feed=None, dry_run=False, touch_seen=True):
    feed = feed or Feed(id=1, source="simplify", key="simplifyjobs/summer2027-internships")
    record = Record(items=[Item(row["id"], row) for row in rows])
    stored = await router.persist(conn, feed, router.prepare(feed, record))
    return await silver.process(
        conn, feed, stored, {}, seen_at=NOW, dry_run=dry_run, touch_seen=touch_seen,
    )


@pytest.mark.parametrize("url,expected", [
    ("https://jobs.ashbyhq.com/Example/123", ("ashby", "example", 7)),
    ("https://jobs.lever.co/MixedCase/123", ("lever", "MixedCase", 7)),
    ("https://Acme.wd5.myworkdayjobs.com/en-US/External/job/123", ("workday", "acme/wd5/external", 7)),
    ("https://job-boards.greenhouse.io/Acme/jobs/123", ("greenhouse", "acme", 7)),
    ("https://boards.greenhouse.io/embed/job_app?token=123", None),
    ("https://apply.workable.com/j/123", None),
    ("https://example.com/jobs/123", None),
])
def test_token_extraction_uses_existing_rules_and_identity(url, expected):
    assert simplify_postings.token_candidate(url, 7) == expected


async def test_all_fields_locations_employer_and_token_are_preserved(core_schema):
    async with core_schema.pool.acquire() as conn, conn.transaction():
        aggregator_id = await store.mint_company(conn, "Simplify Jobs", discovered_from="test")
        feed = Feed(id=1, source="simplify", key="simplifyjobs/summer2027-internships", company_id=aggregator_id)
        data = payload()
        counts = await ingest(conn, [data], feed=feed)
        job = await conn.fetchrow("SELECT * FROM job_posting")
        employer = await conn.fetchrow("SELECT * FROM company WHERE id=$1", job["company_id"])
        token = await conn.fetchrow("SELECT * FROM token WHERE ats='ashby' AND token='example'")
        assert counts.created == 1 and counts.secondhand == 0 and counts.minted == 1
        assert job["company_id"] != aggregator_id
        assert employer["name"] == "Example Employer"
        assert job["source_key"] == data["id"]
        assert job["apply_url"] == job["source_url"] == data["url"]
        assert job["posted_at"] == datetime.fromtimestamp(data["date_posted"], timezone.utc)
        assert job["job_type"] == "intern" and job["status"] == "active"
        assert {loc["country"] for loc in job["locations"]} >= {"GB", "US"}
        assert job["attributes"]["simplify"] == data
        assert job["attributes"]["provenance"]["fidelity"] == "secondhand"
        assert token["source"] == "simplify" and token["status"] == "unknown"
        assert token["checked_at"] is None and token["company_id"] == job["company_id"]
        assert token["discovered_at"] == NOW
        assert await conn.fetchval("SELECT count(*) FROM feed") == 0
        assert await conn.fetchval("SELECT count(*) FROM raw_posting WHERE id=$1", job["raw_id"]) == 1


async def test_existing_token_is_untouched_and_repeated_ingest_is_idempotent(core_schema):
    async with core_schema.pool.acquire() as conn, conn.transaction():
        await conn.execute("INSERT INTO token(ats,token,status,source,muted,note) VALUES ('ashby','example','dead','manual',true,'keep me')")
        before = dict(await conn.fetchrow("SELECT * FROM token"))
        first = await ingest(conn, [payload()])
        second = await ingest(conn, [payload()])
        assert first.created == 1 and second.created == 0 and second.unchanged == 1
        assert dict(await conn.fetchrow("SELECT * FROM token")) == before
        assert await conn.fetchval("SELECT count(*) FROM job_posting") == 1
        assert await conn.fetchval("SELECT count(*) FROM company") == 1


async def test_each_employer_resolves_separately(core_schema):
    async with core_schema.pool.acquire() as conn, conn.transaction():
        await ingest(conn, [payload(), payload(id="listing-2", company_name="Second Employer", url="https://jobs.lever.co/Second/123")])
        assert await conn.fetchval("SELECT count(DISTINCT company_id) FROM job_posting") == 2
        assert await conn.fetchval("SELECT count(*) FROM token WHERE source='simplify'") == 2


async def test_filtered_listings_neither_insert_jobs_nor_discover_tokens(core_schema):
    async with core_schema.pool.acquire() as conn, conn.transaction():
        rows = [payload(id="inactive", active=False), payload(id="hidden", is_visible=False),
                payload(id="off-season", terms=["Fall 2027"]),
                payload(id="old", date_posted=int((NOW-timedelta(days=61)).timestamp()))]
        counts = await ingest(conn, rows)
        assert counts.postings == 0
        assert await conn.fetchval("SELECT count(*) FROM job_posting") == 0
        assert await conn.fetchval("SELECT count(*) FROM token") == 0
        assert await conn.fetchval("SELECT count(*) FROM company") == 0


async def test_dry_run_has_no_posting_company_or_token_writes(core_schema):
    async with core_schema.pool.acquire() as conn, conn.transaction():
        await ingest(conn, [payload()], dry_run=True)
        for table in ("job_posting", "company", "token"):
            assert await conn.fetchval(f"SELECT count(*) FROM {table}") == 0


async def test_unknown_url_still_stores_job_and_preserves_extra_fields(core_schema):
    async with core_schema.pool.acquire() as conn, conn.transaction():
        data = payload(url="https://example.com/intern", new_upstream_field="retained")
        await ingest(conn, [data])
        assert await conn.fetchval("SELECT count(*) FROM token") == 0
        attrs = await conn.fetchval("SELECT attributes FROM job_posting")
        assert attrs["simplify"]["new_upstream_field"] == "retained"


async def test_metadata_updates_preserve_gold_attributes(core_schema):
    async with core_schema.pool.acquire() as conn, conn.transaction():
        await ingest(conn, [payload()])
        await conn.execute('UPDATE job_posting SET attributes=attributes || \'{"skills":["python"],"summary":"keep"}\'::jsonb')
        await ingest(conn, [payload(sponsorship="Offers Sponsorship")])
        attrs = await conn.fetchval("SELECT attributes FROM job_posting")
        assert attrs["simplify"]["sponsorship"] == "Offers Sponsorship"
        assert attrs["skills"] == ["python"] and attrs["summary"] == "keep"
