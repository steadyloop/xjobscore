"""Board disappearance through the real poll path, without job-page requests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from xjobscore import run, silver, sources, store
from xjobscore.records import Feed, Item, Posting, Record, Unparseable

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
SOURCE = "lever:missing-test"


def listing(*keys, **kwargs):
    return Record(items=[Item(key, {"title": "Engineer"}) for key in keys], **kwargs)


@pytest.fixture
async def board(core_schema, monkeypatch):
    pool = core_schema.pool
    current = None

    async with pool.acquire() as conn:
        company = await conn.fetchval(
            "INSERT INTO company(name) VALUES ('Missing test') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO token(ats,token,company_id,status) VALUES ('lever','missing-test',$1,'live')",
            company,
        )
        feed_id = await conn.fetchval(
            "INSERT INTO feed(source,key,polling) VALUES ('lever','missing-test',true) RETURNING id"
        )

    async def harvest(feed, net):
        if isinstance(current, Exception):
            raise current
        return current

    def parse(feed, item, envelope):
        if item.payload.get("unparseable"):
            raise Unparseable("invalid job detail")
        return [Posting(
            external_id=item.external_id,
            title=item.payload["title"],
            url_posting=f"https://jobs.example/{item.external_id}",
            source_active=item.payload.get("active"),
        )]

    monkeypatch.setattr(sources, "get", lambda source: harvest)
    monkeypatch.setattr(sources, "parser", lambda source: parse)

    class NoJobRequests:
        async def get(self, *args, **kwargs):
            raise AssertionError("board disappearance must not fetch job links")

    async def poll(record, minutes, *, lose_lease=False):
        nonlocal current
        current = record
        now = NOW + timedelta(minutes=minutes)
        async with pool.acquire() as conn:
            feed = await store.claim_feed(conn, source="lever", key="missing-test", now=now)
            assert feed is not None
            if lose_lease:
                await conn.execute("UPDATE feed SET hold_until=NULL WHERE id=$1", feed_id)
        return await run.poll(pool, NoJobRequests(), feed, now)

    async def state():
        async with pool.acquire() as conn:
            return {row["source_key"]: dict(row) for row in await conn.fetch(
                "SELECT source_key,status,last_seen_at,missing_since,ats_closed_at FROM job_posting WHERE source=$1",
                SOURCE,
            )}

    return SimpleNamespace(pool=pool, feed_id=feed_id, poll=poll, state=state)


async def test_first_absence_survives_repeat_polls_and_resets_on_reappearance(board):
    assert await board.poll(listing("kept", "gone"), 0) == "ok"
    assert await board.poll(listing("kept"), 15) == "ok"
    first = NOW + timedelta(minutes=15)
    assert (await board.state())["gone"]["missing_since"] == first

    for minutes, record in (
        (30, listing("kept")),
        (45, Record(http_status=304)),
        (60, Record(count_unchanged=True, http_status=200, list_total=1)),
    ):
        await board.poll(record, minutes)
        rows = await board.state()
        assert rows["gone"]["missing_since"] == first
        assert rows["gone"]["last_seen_at"] == NOW
        assert rows["gone"]["status"] == "active"
        assert rows["kept"]["missing_since"] is None

    assert await board.poll(listing("kept", "gone"), 75) == "ok"
    assert (await board.state())["gone"]["missing_since"] is None
    assert await board.poll(listing("kept"), 90) == "ok"
    assert (await board.state())["gone"]["missing_since"] == NOW + timedelta(minutes=90)


@pytest.mark.parametrize("record", [listing("kept", truncated=True), OSError("offline")])
async def test_partial_and_failed_polls_do_not_establish_absence(board, record):
    await board.poll(listing("kept", "gone"), 0)
    assert await board.poll(record, 15) in {"partial", "error"}
    assert all(row["missing_since"] is None for row in (await board.state()).values())


async def test_successful_empty_board_marks_every_previous_job_missing(board):
    await board.poll(listing("one", "two"), 0)
    assert await board.poll(listing(), 15) == "ok"
    assert all(row["missing_since"] == NOW + timedelta(minutes=15)
               and row["status"] == "active" for row in (await board.state()).values())


async def test_filtered_snapshots_preserve_omissions_and_the_daily_reconciliation_clock(board):
    await board.poll(listing("kept", "outside-search", list_total=2, full_listing=True), 0)
    for minute in (15, 30):  # changed and identical filtered-snapshot paths
        assert await board.poll(listing("kept", list_total=3, filtered_listing=True), minute) == "ok"
        rows = await board.state()
        assert rows["outside-search"]["missing_since"] is None
        assert rows["outside-search"]["last_seen_at"] == NOW
    async with board.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT list_total,last_full_poll_at FROM feed WHERE id=$1", board.feed_id)
        assert row["list_total"] == 3  # next count probe can short-circuit
        assert row["last_full_poll_at"] == NOW  # filtered polls cannot postpone discovery
    await board.poll(Record(count_unchanged=True, http_status=200), 45)
    assert (await board.state())["outside-search"]["missing_since"] is None
    await board.poll(listing("kept", list_total=1, full_listing=True), 24 * 60)
    assert (await board.state())["outside-search"]["missing_since"] == NOW + timedelta(days=1)


async def test_complete_unchanged_listing_repairs_an_unrecorded_missing_stamp(board):
    await board.poll(listing("kept", "gone"), 0)
    await board.poll(listing("kept"), 15)
    async with board.pool.acquire() as conn:
        await conn.execute("UPDATE job_posting SET missing_since=NULL WHERE source=$1", SOURCE)
    assert await board.poll(listing("kept"), 30) == "ok"
    rows = await board.state()
    assert rows["gone"]["missing_since"] == NOW + timedelta(minutes=30)
    assert rows["kept"]["missing_since"] is None


async def test_reclaimed_worker_does_not_mark_missing(board):
    await board.poll(listing("kept", "gone"), 0)
    await board.poll(listing("kept"), 15, lose_lease=True)
    assert all(row["missing_since"] is None for row in (await board.state()).values())


async def test_missing_write_failure_rolls_back_success_so_the_poll_can_retry(board, monkeypatch):
    await board.poll(listing("kept", "gone"), 0)
    async with board.pool.acquire() as conn:
        original_hash = await conn.fetchval("SELECT res_hash FROM feed WHERE id=$1", board.feed_id)
    real_mark = store.mark_missing_postings

    async def fail(*args, **kwargs):
        raise RuntimeError("missing write failed")

    monkeypatch.setattr(store, "mark_missing_postings", fail)
    with pytest.raises(RuntimeError, match="missing write failed"):
        await board.poll(listing("kept"), 15)
    async with board.pool.acquire() as conn:
        assert await conn.fetchval("SELECT res_hash FROM feed WHERE id=$1", board.feed_id) == original_hash
        assert await conn.fetchval("SELECT count(*) FROM poll_log WHERE feed_id=$1", board.feed_id) == 1
        # Expire the failed worker's lease to exercise its next scheduled retry.
        await conn.execute("UPDATE feed SET hold_until=NULL WHERE id=$1", board.feed_id)
    monkeypatch.setattr(store, "mark_missing_postings", real_mark)
    await board.poll(listing("kept"), 30)
    assert (await board.state())["gone"]["missing_since"] == NOW + timedelta(minutes=30)


async def test_ats_closure_and_disappearance_are_independent(board):
    await board.poll(listing("job"), 0)
    closed = Record(items=[Item("job", {"title": "Engineer", "active": False})])
    await board.poll(closed, 15)
    await board.poll(Record(items=[Item("job", {"title": "Engineer II", "active": False})]), 30)
    row = (await board.state())["job"]
    assert row["status"] == "closed"
    assert row["ats_closed_at"] == NOW + timedelta(minutes=15)
    assert row["missing_since"] is None

    await board.poll(listing(), 45)
    row = (await board.state())["job"]
    assert row["missing_since"] == NOW + timedelta(minutes=45)
    assert row["ats_closed_at"] == NOW + timedelta(minutes=15)

    await board.poll(listing("job"), 60)
    row = (await board.state())["job"]
    assert row["missing_since"] is None
    assert row["status"] == "closed"
    assert row["ats_closed_at"] == NOW + timedelta(minutes=15)


async def test_listed_but_unparseable_job_is_not_missing(board):
    await board.poll(listing("kept", "gone"), 0)
    await board.poll(listing("kept"), 15)
    record = Record(items=[
        Item("kept", {"title": "Engineer"}),
        Item("gone", {"title": "Engineer", "unparseable": True}),
    ])
    for minutes in (30, 45):
        assert await board.poll(record, minutes) == "ok"
        row = (await board.state())["gone"]
        assert row["missing_since"] is None
        assert row["last_seen_at"] == NOW + timedelta(minutes=minutes)
        assert row["status"] == "active"
        assert row["ats_closed_at"] is None


async def test_replay_uses_raw_observation_time_and_preserves_absence(board):
    await board.poll(Record(items=[Item("job", {"title": "Engineer", "active": False})]), 0)
    await board.poll(listing(), 15)
    async with board.pool.acquire() as conn:
        company = await conn.fetchval("SELECT company_id FROM job_posting WHERE source=$1", SOURCE)
        await conn.execute("UPDATE raw_posting SET fetched_at=$1 WHERE source=$2", NOW, SOURCE)
        await conn.execute("UPDATE job_posting SET ats_closed_at=NULL WHERE source=$1", SOURCE)
        feed = Feed(id=board.feed_id, source="lever", key="missing-test", company_id=company)
        await silver.replay(conn, feed, seen_at=NOW + timedelta(days=7))
        await silver.replay(conn, feed, seen_at=NOW + timedelta(days=14))
    row = (await board.state())["job"]
    assert row["ats_closed_at"] == row["last_seen_at"] == NOW
    assert row["missing_since"] == NOW + timedelta(minutes=15)
    assert row["status"] == "closed"


@pytest.mark.parametrize("legacy", [True, False])
async def test_presence_migration_preserves_ats_evidence(board, live_migration, legacy):
    await board.poll(Record(items=[Item("job", {"title": "Engineer", "active": False})]), 0)
    migration = live_migration("20261006_job_posting_presence.sql")
    async with board.pool.acquire() as conn:
        if legacy:
            await conn.execute("ALTER TABLE job_posting RENAME COLUMN ats_closed_at TO vendor_closed_at")
        else:
            await conn.execute("ALTER TABLE job_posting DROP COLUMN ats_closed_at")
        await conn.execute("ALTER TABLE job_posting DROP COLUMN missing_since")
        await conn.execute(migration)
        await conn.execute(migration)
    row = (await board.state())["job"]
    assert row["ats_closed_at"] == (NOW if legacy else None)
    assert row["missing_since"] is None
    assert row["status"] == "closed"
