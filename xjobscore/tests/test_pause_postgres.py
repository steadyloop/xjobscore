"""Pause persistence, atomic bulk writes, and scheduler claims against real SQL."""

from datetime import datetime, timedelta, timezone
import os

import pytest

from xjobscore import store
from xjobscore.console.policy import PolicyStore

pytestmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"),
    reason="set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests",
)


def policy_on(core_schema):
    policy = PolicyStore(core_schema.dsn)
    policy._pool = core_schema.pool
    return policy


async def seed_feeds(pool, count=2013):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO token(ats, token) SELECT 'pause_test', i::text FROM generate_series(1, $1::int) i",
            count,
        )
        await conn.execute(
            "INSERT INTO feed(source, key, polling) SELECT ats, token, token::int > 73 FROM token"
        )
    return [("pause_test", str(i)) for i in range(1, count + 1)]


async def test_bulk_pause_persists_and_blocks_claims_then_resumes(core_schema):
    pool = core_schema.pool
    await seed_feeds(pool)
    policy = policy_on(core_schema)
    result = await policy.set_provider_paused("pause_test", True)
    assert result == {"source": "pause_test", "paused": True, "affected": 2013}

    # Recreate the policy reader as a page reload / new backend would do.
    restarted = policy_on(core_schema)
    snapshot = await restarted.snapshot()
    assert len(snapshot) == 2013
    assert all(not row["enabled"] for row in snapshot.values())
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        assert await store.claim_due_feeds(conn, now, 10, source="pause_test") == []
        assert await store.claim_feed(conn, source="pause_test", key="74", now=now) is None

    result = await restarted.set_provider_paused("pause_test", False)
    assert result["affected"] == 2013
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM feed WHERE polling") == 2013
        assert len(await store.claim_due_feeds(conn, now, 1, source="pause_test")) == 1


async def test_bulk_pause_rolls_back_if_a_feed_write_fails(core_schema):
    pool = core_schema.pool
    await seed_feeds(pool, 100)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE FUNCTION reject_pause() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.key = '99' AND NOT NEW.polling THEN
                    RAISE EXCEPTION 'simulated write failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_pause BEFORE UPDATE ON feed
                FOR EACH ROW EXECUTE FUNCTION reject_pause();
        """)
    with pytest.raises(Exception, match="simulated write failure"):
        await policy_on(core_schema).set_provider_paused("pause_test", True)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM feed WHERE polling") == 27


async def test_provider_pause_keeps_other_providers_and_global_switch_running(core_schema):
    pool = core_schema.pool
    await seed_feeds(pool, 100)
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO token(ats, token) VALUES ('gem', 'other')")
        await conn.execute("INSERT INTO feed(source, key, polling) VALUES ('gem', 'other', true)")
    assert (await policy_on(core_schema).set_provider_paused("pause_test", True))["affected"] == 100
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM feed WHERE polling") == 1
        assert await conn.fetchval("SELECT polling FROM feed WHERE source='gem'") is True
        assert await conn.fetchval("SELECT running FROM scheduler_control") is True


@pytest.mark.parametrize("success", [False, True])
async def test_finishing_an_inflight_poll_does_not_undo_pause(core_schema, success):
    pool = core_schema.pool
    await seed_feeds(pool, 100)
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        (feed,) = await store.claim_due_feeds(conn, now, 1, source="pause_test")
    await policy_on(core_schema).set_paused(feed.source, feed.key, True)
    async with pool.acquire() as conn:
        if success:
            assert await store.finish_schedule_success(
                conn, feed, now=now, etag=None, res_hash=None,
                due_at=now + timedelta(minutes=15), changed=False,
            )
        else:
            assert await store.finish_schedule_failure(
                conn, feed, now=now, due_at=now + timedelta(minutes=15),
            )
        assert await conn.fetchval("SELECT polling FROM feed WHERE id=$1", feed.id) is False


async def test_global_pause_survives_a_new_policy_reader(core_schema):
    policy = policy_on(core_schema)
    await policy.set_scheduler_running(False)
    restarted = policy_on(core_schema)
    assert (await restarted.scheduler_control())["running"] is False
    await restarted.set_scheduler_running(True)
    assert (await policy.scheduler_control())["running"] is True


async def test_disabled_provider_can_be_paused_but_not_resumed(core_schema):
    async with core_schema.pool.acquire() as conn:
        await conn.execute("INSERT INTO token(ats, token) VALUES ('paylocity', 'disabled')")
        await conn.execute("INSERT INTO feed(source, key, polling) VALUES ('paylocity', 'disabled', false)")
    policy = policy_on(core_schema)
    assert (await policy.set_provider_paused("paylocity", True))["affected"] == 1
    with pytest.raises(ValueError, match="disabled"):
        await policy.set_provider_paused("paylocity", False)
    async with core_schema.pool.acquire() as conn:
        assert await conn.fetchval("SELECT polling FROM feed") is False
