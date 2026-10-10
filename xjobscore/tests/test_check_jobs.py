"""Scope boundaries and signal updates, using only isolated PostgreSQL tables."""

import os
from pathlib import Path
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest

from xjobscore import check_jobs as check, sources, store
from xjobscore.records import LINK_DEAD, Feed, Item, Posting, Record, Unparseable


@pytest.fixture
async def db():
    dsn = os.environ.get("XJOBSCORE_TEST_DSN")
    if not dsn:
        pytest.skip("set XJOBSCORE_TEST_DSN for PostgreSQL tests")
    schema = "scope_test_" + uuid.uuid4().hex
    setup = await asyncpg.connect(dsn)
    pool = None
    try:
        await setup.execute(f"CREATE SCHEMA {schema}; SET search_path TO {schema}")
        await setup.execute("""
            CREATE TABLE company(id bigint PRIMARY KEY, name text, description text);
            CREATE TABLE token(ats text, token text, company_id bigint, config jsonb);
            CREATE TABLE feed(
                id bigint PRIMARY KEY, source text, key text, polling bool DEFAULT true,
                paused_at timestamptz, hold_until timestamptz, type text DEFAULT 'ATS',
                interval_s int DEFAULT 900, strikes int DEFAULT 0, due_at timestamptz,
                etag text, last_modified text, res_hash text, list_total int,
                last_full_poll_at timestamptz, last_change_at timestamptz);
            CREATE TABLE job_posting_dup_group(id uuid PRIMARY KEY, primary_posting_id bigint, state text);
            CREATE TABLE job_posting(
                id bigint PRIMARY KEY, source text DEFAULT 'lever:example', source_key text,
                job_type text DEFAULT 'intern', locations jsonb DEFAULT '[]',
                status text DEFAULT 'active', title text DEFAULT 'Keep this title',
                dup_group_id uuid, posted_at timestamptz, first_seen_at timestamptz DEFAULT now(),
                last_seen_at timestamptz DEFAULT '2026-01-01', missing_since timestamptz,
                ats_closed_at timestamptz);
            CREATE TABLE raw_posting(
                id bigint GENERATED ALWAYS AS IDENTITY, source text, source_key text,
                data jsonb, data_hash text, fetched_at timestamptz DEFAULT now(),
                UNIQUE(source, source_key, data_hash));
            INSERT INTO feed(id, source, key, etag, res_hash, last_change_at)
            VALUES (1, 'lever', 'example', 'old', 'old', '2026-01-01 00:00:00+00');
            INSERT INTO job_posting(id, source_key)
            SELECT id, id::text FROM generate_series(1, 6) id;
        """)
        ddl = (Path(__file__).parents[2] / "schema.sql").read_text()
        await setup.execute(ddl[ddl.index("CREATE TABLE reconcile_control"):ddl.rindex("COMMIT;")])
        pool = await store.connect(dsn, search_path=schema)
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await setup.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await setup.close()


async def selected(db, *flags):
    return await check.select_jobs(db, check.arguments(flags))


async def test_scope_and_filters(db):
    await db.execute("""
        UPDATE job_posting SET job_type = 'senior' WHERE id = 2;
        UPDATE job_posting SET locations = '[{"country":"CA"}]' WHERE id = 3;
        UPDATE job_posting SET status = 'closed' WHERE id = 4;
        UPDATE job_posting SET source = 'ashby:other' WHERE id = 5;
    """)
    assert [r["id"] for r in await selected(db)] == [1, 5, 6]
    assert len(await selected(db, "--scope", "all")) == 6
    assert [r["id"] for r in await selected(db, "--source", "lever", "--limit", "1")] == [1]
    assert [r["id"] for r in await selected(db, "--feed", "ashby:other")] == [5]
    assert [r["id"] for r in await selected(db, "--job-id", "6", "--job-id", "1")] == [1, 6]
    assert not await selected(db, "--source", "lever", "--feed", "ashby:other")
    assert (await selected(db, "--job-id", "5"))[0]["feed_id"] is None


async def test_scope_reuses_duplicate_representative(db):
    group = uuid.uuid4()
    await db.execute("INSERT INTO job_posting_dup_group VALUES($1, 1, 'accepted')", group)
    await db.execute("UPDATE job_posting SET dup_group_id=$1 WHERE id IN (1,2)", group)
    assert [r["id"] for r in await selected(db)] == [1, 3, 4, 5, 6]
    await db.execute("UPDATE job_posting SET status='closed' WHERE id=1")
    assert [r["id"] for r in await selected(db)] == [2, 3, 4, 5, 6]


@pytest.fixture
def fake_source(monkeypatch):
    def parse(feed, item, envelope):
        if item.payload.get("unparseable"):
            raise Unparseable("unknown payload")
        if item.payload.get("error"):
            raise RuntimeError("parser failure")
        return [Posting(external_id=item.external_id, title="New title", url_posting="https://example.com",
                        source_active=item.payload.get("active"),
                        state="closed" if item.payload.get("probe") else "open",
                        closed_evidence=LINK_DEAD if item.payload.get("probe") else None)]
    monkeypatch.setitem(sources.PARSERS, "lever", parse)

    def install(record):
        async def harvest(feed, net):
            return record
        monkeypatch.setattr(check, "fresh_listing", harvest)
    return install


async def test_only_selected_jobs_update_and_first_timestamps_survive(db, fake_source):
    old = datetime(2026, 1, 2, tzinfo=timezone.utc)
    await db.execute("UPDATE job_posting SET missing_since=$1 WHERE id IN (1, 3, 5)", old)
    await db.execute("UPDATE job_posting SET ats_closed_at=$1, status='closed' WHERE id=2", old)
    before = dict(await db.fetchrow("SELECT * FROM job_posting WHERE id=5"))
    fake_source(Record(items=[Item("1", {}), Item("2", {"active": False}), Item("5", {"active": False})],
                       result_window_truncated=True, full_listing=True))
    jobs = await selected(db, "--scope", "all", "--limit", "4")
    result = await check.check_feed(db, None, Feed(1, "lever", "example"), jobs)
    assert result == {"feed": "lever:example", "checked": 4, "missing": 2, "ats_closed": 1, "unparseable": 0}
    rows = {r["id"]: r for r in await db.fetch("SELECT * FROM job_posting")}
    assert rows[1]["missing_since"] is None
    assert rows[1]["last_seen_at"] > old
    assert rows[2]["ats_closed_at"] == old and rows[2]["status"] == "closed"
    assert rows[3]["missing_since"] == old
    assert rows[4]["missing_since"] > old and rows[4]["status"] == "active"
    assert dict(rows[5]) == before
    assert all(r["title"] == "Keep this title" for r in rows.values())
    assert await db.fetchval("SELECT count(*) FROM raw_posting") == 2
    feed = await db.fetchrow("SELECT * FROM feed")
    assert feed["hold_until"] is None and feed["etag"] is None and feed["res_hash"] is None
    assert feed["last_change_at"] == datetime(2026, 1, 1, tzinfo=timezone.utc)

    # A fresh ATS closure gets stamped once; a later open listing cannot reopen it.
    fake_source(Record(items=[Item("1", {"active": False})]))
    await check.check_feed(db, None, Feed(1, "lever", "example"), jobs)
    closed_at = await db.fetchval("SELECT ats_closed_at FROM job_posting WHERE id=1")
    assert closed_at > old
    fake_source(Record(items=[Item("1", {"active": True})]))
    await check.check_feed(db, None, Feed(1, "lever", "example"), jobs)
    row = await db.fetchrow("SELECT * FROM job_posting WHERE id=1")
    assert row["ats_closed_at"] == closed_at and row["status"] == "closed"
    assert row["missing_since"] is None


@pytest.mark.parametrize("record", [Record(truncated=True), Record(result_window_truncated=True),
                                      Record(count_unchanged=True), Record(http_status=304),
                                      Record(http_status=503), Record(items=[Item("1", {"error": True})])])
async def test_incomplete_or_failed_feed_writes_nothing(db, fake_source, record):
    before = await db.fetch("SELECT * FROM job_posting ORDER BY id")
    fake_source(record)
    result = await check.check_feed(db, None, Feed(1, "lever", "example"), await selected(db))
    assert result["unchecked"] == 6
    assert await db.fetch("SELECT * FROM job_posting ORDER BY id") == before
    assert await db.fetchval("SELECT count(*) FROM raw_posting") == 0
    assert await db.fetchval("SELECT etag FROM feed") == "old"
    assert await db.fetchval("SELECT hold_until FROM feed") is None


async def test_presence_without_inventing_ats_closure(db, fake_source):
    fake_source(Record(items=[Item("1", {"unparseable": True}), Item("2", {"probe": True})]))
    await db.execute("UPDATE job_posting SET missing_since=now() WHERE id IN (1,2)")
    result = await check.check_feed(db, None, Feed(1, "lever", "example"), await selected(db))
    assert result["unparseable"] == 1 and result["ats_closed"] == 0
    assert await db.fetchval("SELECT count(*) FROM job_posting WHERE id IN (1,2) AND missing_since IS NULL AND ats_closed_at IS NULL AND status='active'") == 2


async def test_fetch_failure_releases_lease(db, monkeypatch):
    async def fail(feed, net):
        raise TimeoutError()
    monkeypatch.setattr(check, "fresh_listing", fail)
    result = await check.check_feed(db, None, Feed(1, "lever", "example"), await selected(db))
    assert result["reason"] == "TimeoutError" and result["unchecked"] == 6
    assert await db.fetchval("SELECT hold_until FROM feed") is None
    assert await db.fetchval("SELECT count(*) FROM job_posting WHERE missing_since IS NOT NULL") == 0


async def test_lost_lease_does_not_write_or_release_another_lease(db, monkeypatch):
    async def harvest(feed, net):
        await db.execute("UPDATE feed SET hold_until=now()+interval '2 hours'")
        return Record()
    monkeypatch.setattr(check, "fresh_listing", harvest)
    result = await check.check_feed(db, None, Feed(1, "lever", "example"), await selected(db))
    assert result["reason"] == "lease expired" and result["unchecked"] == 6
    assert await db.fetchval("SELECT hold_until FROM feed") is not None
    assert await db.fetchval("SELECT count(*) FROM job_posting WHERE missing_since IS NOT NULL") == 0


async def test_paused_and_competing_lease(db, fake_source):
    fake_source(Record(items=[]))
    jobs = await selected(db)
    await db.execute("UPDATE feed SET paused_at=now()")
    assert (await check.check_feed(db, None, Feed(1, "lever", "example"), jobs))["unchecked"] == 6
    assert (await check.check_feed(db, None, Feed(1, "lever", "example"), jobs, include_paused=True))["checked"] == 6
    await db.execute("UPDATE feed SET hold_until=now()+interval '1 hour'")
    lease = await db.fetchval("SELECT hold_until FROM feed")
    assert (await check.check_feed(db, None, Feed(1, "lever", "example"), jobs, include_paused=True))["unchecked"] == 6
    assert await db.fetchval("SELECT hold_until FROM feed") == lease


async def test_forces_fresh_json_listing(monkeypatch):
    async def harvest(name, feed, net, *, options):
        assert name == "lever" and options.force_full and options.prior_items is None
        assert all(getattr(feed, field) is None for field in (
            "etag", "last_modified", "res_hash", "list_total", "last_full_poll_at"))
        return Record()
    monkeypatch.setattr(sources.json_api, "harvest", harvest)
    await check.fresh_listing(Feed(1, "lever", "example", etag="old", res_hash="old", list_total=6), None)


async def test_preview_does_not_fetch_or_write(db, monkeypatch, capsys):
    async def connect(dsn):
        return db
    async def forbidden(*args, **kwargs):
        pytest.fail("preview attempted a fetch")
    monkeypatch.setattr(store, "connect", connect)
    monkeypatch.setattr(check, "fresh_listing", forbidden)
    assert await check.run(check.arguments(["--dsn", "unused", "--limit", "2"])) == 0
    assert '"jobs_to_check": 2' in capsys.readouterr().out


async def test_apply_runs_workers_and_reports_skipped_jobs(db, fake_source, monkeypatch, capsys):
    async def connect(dsn):
        return db
    monkeypatch.setattr(store, "connect", connect)
    fake_source(Record(items=[Item("1", {})]))
    await db.execute("UPDATE job_posting SET source='lever:unconfigured' WHERE id=2")
    assert await check.run(check.arguments(["--dsn", "unused", "--limit", "2", "--apply"])) == 1
    output = capsys.readouterr().out
    assert '"checked": 1' in output and '"unchecked": 1' in output


@pytest.mark.parametrize("flag", ["--limit", "--job-id", "--concurrency"])
def test_rejects_nonpositive_limits(flag):
    with pytest.raises(SystemExit):
        check.arguments([flag, "0"])
