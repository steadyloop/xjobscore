"""Keep board counts out of job hashes without duplicating legacy raw rows."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import os
from uuid import uuid4

import asyncpg
import pytest

from xjobscore import router, store
from xjobscore.records import Feed, Item, Record


VENDORS = ("oracle", "greenhouse", "icims")


def record(vendor, total=10, *, title="Engineer"):
    counts = {"oracle": {"TotalJobsCount": total}, "icims": {"totalCount": total},
              "greenhouse": {"meta": {"total": total, "company": "Acme"}}}
    return Record(items=[Item("job-1", {"title": title, "active": True})],
                  envelope={**counts[vendor], "company": "Acme"})


def legacy_hash(feed, raw):
    volatile = router.VOLATILE.get(feed.source, frozenset())
    return router.encoded_hash({"version": 1, "kind": raw.items[0].kind,
        "payload": router._strip(raw.items[0].payload, volatile),
        "envelope": router._strip(raw.envelope, volatile)})


@pytest.mark.parametrize("vendor", VENDORS)
@pytest.mark.parametrize("total", [0, 11, None])
def test_count_change_preserves_hash_and_original_payload(vendor, total):
    feed = Feed(1, vendor, "acme")
    first, second = record(vendor), record(vendor, total)
    original = deepcopy(second)
    a, b = router.prepare(feed, first), router.prepare(feed, second)
    assert a[0].data_hash == b[0].data_hash
    assert router.snapshot_hash(a) == router.snapshot_hash(b)
    assert router.unwrap(b[0].data) == (replace(second.items[0], external_id=""), second.envelope)
    assert second == original


@pytest.mark.parametrize("vendor", VENDORS)
@pytest.mark.parametrize("field,value", [("title", "Changed"), ("description", "New body"),
    ("active", False), ("location", "Boston"), ("apply_url", "https://example.com/new"),
    ("TotalJobsCount", 11), ("totalCount", 11), ("total", 11)])
def test_actual_job_fields_including_same_named_counts_still_change_hash(vendor, field, value):
    feed = Feed(1, vendor, "acme")
    first, second = record(vendor), record(vendor, 11)
    second.items[0].payload[field] = value
    assert router.prepare(feed, first)[0].data_hash != router.prepare(feed, second)[0].data_hash


@pytest.mark.parametrize("vendor", VENDORS)
def test_other_envelope_fields_and_membership_still_change_hash(vendor):
    feed = Feed(1, vendor, "acme")
    first, second = record(vendor), record(vendor, 11)
    second.envelope["company"] = "Different company"
    assert router.prepare(feed, first)[0].data_hash != router.prepare(feed, second)[0].data_hash
    if vendor == "greenhouse":
        second = record(vendor, 11)
        second.envelope["meta"]["company"] = "Different company"
        assert router.prepare(feed, first)[0].data_hash != router.prepare(feed, second)[0].data_hash
    changed_id = replace(first, items=[replace(first.items[0], external_id="job-2")])
    assert router.snapshot_hash(router.prepare(feed, first)) != router.snapshot_hash(router.prepare(feed, changed_id))
    assert router.snapshot_hash(router.prepare(feed, first)) != router.snapshot_hash([])


@pytest.mark.parametrize("vendor", ["workday", "lever", "ashby", "rippling", "taleo"])
def test_other_sources_keep_the_exact_old_hash(vendor):
    feed = Feed(1, vendor, "acme")
    raw = Record(items=[Item("1", {"title": "Engineer", "postedOn": "Today", "logo": "logo"})],
                 envelope={"TotalJobsCount": 1, "totalCount": 1, "meta": {"total": 1}})
    assert router.prepare(feed, raw)[0].data_hash == legacy_hash(feed, raw)


async def test_other_sources_do_not_read_legacy_rows(monkeypatch):
    async def forbidden(*args):
        pytest.fail("unaffected source attempted a compatibility lookup")
    async def save(*args, **kwargs):
        return 7, True
    monkeypatch.setattr(store, "latest_raw", forbidden)
    monkeypatch.setattr(store, "save_raw", save)
    feed = Feed(1, "lever", "acme")
    assert (await router.persist(None, feed, router.prepare(feed, Record(items=[Item("1", {})]))))[0].created


@pytest.fixture
async def bronze_db():
    dsn = os.environ.get("XJOBSCORE_TEST_DSN")
    if not dsn:
        pytest.skip("set XJOBSCORE_TEST_DSN for isolated PostgreSQL tests")
    schema = "bronze_counts_" + uuid4().hex
    setup = await asyncpg.connect(dsn)
    pool = None
    try:
        await setup.execute(f"CREATE SCHEMA {schema}; SET search_path TO {schema}")
        await setup.execute("""
            CREATE TABLE raw_posting(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                source text NOT NULL, source_key text NOT NULL, data jsonb NOT NULL,
                data_hash text NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE(source, source_key, data_hash));
            CREATE TABLE job_posting(id bigint PRIMARY KEY, raw_id bigint REFERENCES raw_posting(id));
        """)
        pool = await store.connect(dsn, search_path=schema)
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await setup.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await setup.close()


async def seed_legacy(conn, feed):
    raw = record(feed.source)
    data = router.wrap(raw.items[0].payload, envelope=raw.envelope)
    raw_id, _ = await store.save_raw(conn, source=store.source_instance(feed), source_key="job-1",
                                   data=data, data_hash=legacy_hash(feed, raw))
    await conn.execute("INSERT INTO job_posting VALUES(1,$1)", raw_id)
    return raw_id


@pytest.mark.parametrize("vendor", VENDORS)
async def test_legacy_counts_reuse_raw_id_without_rewriting_evidence(bronze_db, vendor):
    feed = Feed(1, vendor, "acme")
    async with bronze_db.acquire() as conn:
        raw_id = await seed_legacy(conn, feed)
        before = await conn.fetchrow("SELECT * FROM raw_posting WHERE id=$1", raw_id)
        for total in (11, 12, 10):
            current = record(vendor, total)
            stored = (await router.persist(conn, feed, router.prepare(feed, current)))[0]
            assert stored.raw_id == raw_id and not stored.created
            assert stored.data_hash == before["data_hash"]
            assert router.unwrap(stored.data) == (replace(current.items[0], external_id=""), current.envelope)
        assert await conn.fetchval("SELECT count(*) FROM raw_posting") == 1
        assert await conn.fetchrow("SELECT * FROM raw_posting WHERE id=$1", raw_id) == before
        assert await conn.fetchval("SELECT raw_id FROM job_posting WHERE id=1") == raw_id

        # A real edit creates one revision; later count changes reuse that new row.
        edit = record(vendor, 13, title="Senior Engineer")
        changed = (await router.persist(conn, feed, router.prepare(feed, edit)))[0]
        assert changed.created and changed.raw_id != raw_id
        assert router.unwrap(changed.data) == (replace(edit.items[0], external_id=""), edit.envelope)
        again = (await router.persist(conn, feed, router.prepare(feed, record(vendor, 14, title="Senior Engineer"))))[0]
        assert not again.created and again.raw_id == changed.raw_id
        assert await conn.fetchval("SELECT count(*) FROM raw_posting") == 2


@pytest.mark.parametrize("vendor", VENDORS)
async def test_fresh_counts_deduplicate_and_changes_roll_back(bronze_db, vendor):
    feed = Feed(1, vendor, "acme")
    async with bronze_db.acquire() as conn:
        initial = (await router.persist(conn, feed, router.prepare(feed, record(vendor))))[0]
        assert initial.created
        repeated = (await router.persist(conn, feed, router.prepare(feed, record(vendor, 11))))[0]
        assert repeated.raw_id == initial.raw_id and not repeated.created
        with pytest.raises(RuntimeError):
            async with conn.transaction():
                stored = await router.persist(conn, feed, router.prepare(feed, record(vendor, 12, title="Changed")))
                assert stored[0].created
                raise RuntimeError("roll back")
        assert await conn.fetchval("SELECT count(*) FROM raw_posting") == 1


async def test_competing_writers_create_one_real_revision(bronze_db):
    feed = Feed(1, "oracle", "acme")
    async with bronze_db.acquire() as conn:
        await seed_legacy(conn, feed)
    async def write():
        async with bronze_db.acquire() as conn, conn.transaction():
            return (await router.persist(conn, feed, router.prepare(feed, record("oracle", 11, title="Changed"))))[0]
    first, second = await asyncio.gather(write(), write())
    assert first.raw_id == second.raw_id and sum([first.created, second.created]) == 1
    assert await bronze_db.fetchval("SELECT count(*) FROM raw_posting") == 2
