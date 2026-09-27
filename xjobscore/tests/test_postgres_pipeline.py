from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from xjobscore import router, run, sources, store
from xjobscore.records import Feed, Item, Posting, Record


ONESHOT_PACKAGE = Path(__file__).parent.parent / "oneshot-ui"
if str(ONESHOT_PACKAGE) not in sys.path:
    sys.path.insert(0, str(ONESHOT_PACKAGE))

from oneshot_ui.repository import PostgresRepository  # noqa: E402


pytestmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"),
    reason="set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_poll_creates_a_job_visible_to_oneshot(monkeypatch, core_schema):
    suffix = uuid4().hex
    source = f"pipeline_test_{suffix}"
    key = "board"
    second_key = "board-two"
    title = f"Schema Pipeline Intern {suffix}"
    now = datetime.now(timezone.utc)
    pool = core_schema.pool
    # connect() returns early when handed a pool, so the repository reads the
    # same throwaway schema the pipeline just wrote (repository.py:83-85).
    repository = PostgresRepository(core_schema.dsn, pool=pool)

    async def harvest(feed, net):
        return Record(
            items=[Item("job-1", {"title": title})],
            http_status=200,
            etag='"pipeline-test"',
        )

    def parse(feed, item, envelope):
        return [
            Posting(
                external_id=item.external_id,
                title=item.payload["title"],
                url_posting="https://pipeline.test/job-1",
                company_raw="Pipeline Test",
                description_html="<p>Build the ingestion pipeline.</p>",
            )
        ]

    monkeypatch.setattr(sources, "get", lambda name: harvest)
    monkeypatch.setattr(sources, "parser", lambda name: parse)

    async with pool.acquire() as conn:
        company_id = await conn.fetchval(
            "INSERT INTO company(name, domain) VALUES ($1, $2) RETURNING id",
            "Pipeline Test",
            f"{suffix}.pipeline.test",
        )
        await conn.execute(
            "INSERT INTO token(ats, token, company_id, status) VALUES ($1, $2, $3, 'live')",
            source,
            key,
            company_id,
        )
        feed_id = await conn.fetchval(
            "INSERT INTO feed(source, key, polling, due_at, hold_until) VALUES ($1, $2, true, $3, $3) RETURNING id",
            source,
            key,
            now,
        )

    async def claim_one():
        async with pool.acquire() as conn:
            return await store.claim_due_feeds(conn, now, 1)

    # The SKIP LOCKED assertion below is only meaningful while this feed is the
    # only claimable row. Against a populated database the two claims take
    # *different* feeds and both succeed -- which is SKIP LOCKED working, not
    # failing, but it tests nothing and polls real feeds through the fake
    # harvester. Assert the precondition so a future leak of isolation fails
    # here, with the reason, instead of further down looking like a lock bug.
    async with pool.acquire() as conn:
        claimable = await conn.fetchval(
            """
            SELECT count(*) FROM feed f
            WHERE f.polling AND f.paused_at IS NULL AND f.due_at <= $1
              AND (f.hold_until IS NULL OR f.hold_until <= $1)
            """,
            now,
        )
    assert claimable == 1, f"expected an isolated schema, found {claimable} due feeds"

    attempts = await asyncio.gather(claim_one(), claim_one())
    assert sorted(len(result) for result in attempts) == [0, 1]
    claimed = next(result for result in attempts if result)

    assert len(claimed) == 1
    feed = replace(claimed[0], type="ATS")
    # The claim must carry the lease it just took, not the value the row
    # held beforehand. A data-modifying CTE's writes are invisible to the
    # rest of its own query, so reading hold_until off `feed` instead
    # of the CTE hands back the stale value and every finish_schedule_*()
    # guard silently misses.
    async with pool.acquire() as conn:
        stored_hold = await conn.fetchval(
            "SELECT hold_until FROM feed WHERE id = $1", feed_id
        )
    assert feed.hold_until == stored_hold

    assert await run.poll(pool, object(), feed, now) == "ok"

    # poll() returns "ok" whether or not the lease held -- a lost lease is
    # only a log line -- so assert on the observation it gates instead.
    # Without this the Scheduler page's whole telemetry stays empty.
    async with pool.acquire() as conn:
        observation = await conn.fetchrow(
            "SELECT status, new_postings FROM poll_log WHERE feed_id = $1",
            feed_id,
        )
    assert observation is not None, "poll recorded no observation: lease was lost"
    assert observation["status"] == "ok"
    assert observation["new_postings"] == 1

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO token(ats, token, company_id, status) VALUES ($1, $2, $3, 'live')",
            source,
            second_key,
            company_id,
        )
        second_feed_id = await conn.fetchval(
            "INSERT INTO feed(source, key, polling, due_at) VALUES ($1, $2, true, $3) RETURNING id",
            source,
            second_key,
            now,
        )
        second_claim = await store.claim_due_feeds(conn, now, 1)

    assert len(second_claim) == 1
    assert await run.poll(
        pool, object(), replace(second_claim[0], type="ATS"), now
    ) == "ok"

    await repository.connect()
    result = await repository.list_jobs(q=title)
    assert result["total"] == 2
    assert {job["title"] for job in result["jobs"]} == {title}
    listing = result["jobs"][0]
    assert listing["title"] == title
    assert listing["summary"] is None
    assert "description" not in listing


@pytest.mark.asyncio
async def test_claim_due_feeds_filters_by_source(core_schema):
    """run.cycle's per-vendor workers each claim from their own queue only."""
    suffix = uuid4().hex
    left, right = f"vendor_a_{suffix}", f"vendor_b_{suffix}"
    now = datetime.now(timezone.utc)
    pool = core_schema.pool

    async with pool.acquire() as conn:
        for source in (left, right):
            await conn.execute(
                "INSERT INTO token(ats, token, status) VALUES ($1, 'board', 'live')", source
            )
            await conn.execute(
                "INSERT INTO feed(source, key, polling, due_at) VALUES ($1, 'board', true, $2)",
                source,
                now,
            )
        assert await store.due_sources(conn, now) == sorted([left, right])

        claimed = await store.claim_due_feeds(conn, now, 5, source=left)
        assert [(feed.source, feed.key) for feed in claimed] == [(left, "board")]
        # Same data-modifying-CTE trap as the unfiltered claim: the lease must
        # come back from the CTE, not the pre-claim row.
        stored_hold = await conn.fetchval(
            "SELECT hold_until FROM feed WHERE source = $1", left
        )
        assert claimed[0].hold_until == stored_hold

        # Leased now, so a second claim of that vendor finds nothing -- while
        # the other vendor's queue is untouched by any of it.
        assert await store.claim_due_feeds(conn, now, 5, source=left) == []
        assert await store.due_sources(conn, now) == [right]

    async def claim_right():
        async with pool.acquire() as conn:
            return await store.claim_due_feeds(conn, now, 1, source=right)

    attempts = await asyncio.gather(claim_right(), claim_right())
    assert sorted(len(result) for result in attempts) == [0, 1]  # SKIP LOCKED

    async with pool.acquire() as conn:
        assert await store.due_sources(conn, now) == []


@pytest.mark.asyncio
async def test_due_feeds_reads_the_whole_queue_without_a_limit(core_schema):
    """The dry-run snapshot: no lease exists to bound a refilling claim."""
    suffix = uuid4().hex
    source = f"vendor_c_{suffix}"
    now = datetime.now(timezone.utc)
    pool = core_schema.pool

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO token(ats, token, status) VALUES ($1, 'board', 'live')", source
        )
        for index in range(3):
            await conn.execute(
                "INSERT INTO token(ats, token, status) VALUES ($1, $2, 'live')",
                source,
                f"board-{index}",
            )
            await conn.execute(
                "INSERT INTO feed(source, key, polling, due_at) VALUES ($1, $2, true, $3)",
                source,
                f"board-{index}",
                now - timedelta(seconds=index),
            )

        every = await store.due_feeds(conn, now, source=source)
        assert len(every) == 3
        assert [feed.due_at for feed in every] == sorted(feed.due_at for feed in every)
        assert len(await store.due_feeds(conn, now, 2, source=source)) == 2
        # Reading takes no lease, so the same rows are still claimable.
        assert len(await store.due_feeds(conn, now, source=source)) == 3


@pytest.mark.asyncio
async def test_claim_feed_leases_one_feed_regardless_of_due_at(core_schema):
    """The Scheduler page's on-demand fetch: claimable now even when not due,
    refused when held, disabled, or paused -- and the returned lease must be
    usable by finish_schedule_success (the CTE-visibility regression guard)."""
    suffix = uuid4().hex
    source = f"claim_feed_test_{suffix}"
    now = datetime.now(timezone.utc)
    pool = core_schema.pool

    async with pool.acquire() as conn:
        feed_id = await conn.fetchval(
            "INSERT INTO feed(source, key, polling, due_at) "
            "VALUES ($1, 'board', true, $2) RETURNING id",
            source,
            now + timedelta(days=1),
        )
        disabled_id = await conn.fetchval(
            "INSERT INTO feed(source, key, polling, due_at) VALUES ($1, 'off', false, $2) RETURNING id",
            source,
            now,
        )
        paused_id = await conn.fetchval(
            "INSERT INTO feed(source, key, polling, due_at, paused_at) "
            "VALUES ($1, 'held', true, $2, $2) RETURNING id",
            source,
            now,
        )

    async with pool.acquire() as conn:
        feed = await store.claim_feed(
            conn, source=source, key="board", now=now, lease_seconds=120
        )
    assert feed is not None
    assert feed.id == feed_id

    async with pool.acquire() as conn:
        # Held by the claim above: a second claim refuses, never double-polls.
        assert await store.claim_feed(conn, source=source, key="board", now=now) is None
        # Policy still holds without a due filter: disabled and paused refuse.
        assert await store.claim_feed(conn, source=source, key="off", now=now) is None
        assert await store.claim_feed(conn, source=source, key="held", now=now) is None

        # The claim must carry the lease it just wrote, not the pre-claim
        # value (a data-modifying CTE's writes are invisible to its own
        # query). finish_schedule_success matching proves it end-to-end: a
        # stale lease here means every on-demand poll silently skips its
        # poll_log row.
        stored_hold = await conn.fetchval(
            "SELECT hold_until FROM feed WHERE id = $1", feed_id
        )
        assert feed.hold_until == stored_hold
        assert await store.finish_schedule_success(
            conn, feed, now=now, due_at=now, etag=None, res_hash=None, changed=False
        )


@pytest.mark.asyncio
async def test_prior_items_round_trips_the_newest_bronze_revision_per_posting(core_schema):
    """`router.prior_items` feeds `run.poll`'s revision-checked detail skip
    (`sources.reusing`). It must survive a real write/read round trip byte for
    byte, return only the newest of several stored revisions per posting, and
    never raise on a row `unwrap` cannot parse -- a cache miss there must cost
    one posting's reuse, not the whole poll."""
    suffix = uuid4().hex
    source = f"prior_items_test_{suffix}"
    key = "board"
    pool = core_schema.pool
    feed = Feed(id=1, source=source, key=key, config={})
    full_source = store.source_instance(feed)

    async def write(item: Item) -> None:
        prepared = router.prepare(feed, Record(items=[item]))
        async with pool.acquire() as conn:
            await router.persist(conn, feed, prepared)

    await write(Item("1", {"id": "1", "rev": "a", "body": "first"}))
    # A second, later revision of the same posting -- prior_items must return
    # only the newest, never the stale one and never both.
    await write(Item("1", {"id": "1", "rev": "b", "body": "second"}))
    await write(Item("2", {"id": "2", "rev": "z", "body": "untouched"}))

    async with pool.acquire() as conn:
        # A row router.unwrap cannot parse (unversioned, pre-dating the bronze
        # wrapper) must be skipped, not crash a read every poll now runs.
        await conn.execute(
            "INSERT INTO raw_posting(source, source_key, data, data_hash) "
            "VALUES ($1, $2, $3, $4)",
            full_source,
            "3",
            {"unversioned": True},
            "deadbeef",
        )

    async with pool.acquire() as conn:
        prior = await router.prior_items(conn, full_source)

    assert set(prior) == {"1", "2"}
    assert prior["1"].external_id == "1"
    assert prior["1"].payload["body"] == "second"
    assert prior["2"].payload["body"] == "untouched"
