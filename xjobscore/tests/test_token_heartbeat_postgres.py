from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os

import pytest

from xjobscore.console import token_heartbeat, tokens

pytestmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"),
    reason="set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests",
)


async def seed_token(pool, token: str, *, status: str = "live", muted: bool = False):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO token(ats, token, status, muted) VALUES ('workable', $1, $2, $3)",
            token,
            status,
            muted,
        )


async def test_heartbeat_candidate_guards_and_mute_race(core_schema):
    pool = core_schema.pool
    # The heartbeat re-probes dead boards looking for resurrections; live ones
    # absent from feed are the operator's backlog and are left alone.
    await seed_token(pool, "eligible", status="dead")
    await seed_token(pool, "muted", status="dead", muted=True)
    await seed_token(pool, "live", status="live")

    identities, total = await tokens.promotion_candidates(pool)
    assert identities == [("workable", "eligible")]
    assert total == 1

    # The probe found it alive again -- promote_verified's guard is 'live',
    # so without the mute this would now promote. Muting mid-cycle is the race.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE token SET status = 'live' WHERE ats='workable' AND token='eligible'"
        )
    await tokens.set_muted(
        pool,
        ats="workable",
        token="eligible",
        muted=True,
    )
    assert await tokens.promote_verified(pool, identities) == 0


async def test_muting_drops_but_unmuting_does_not_recreate_feed(core_schema):
    pool = core_schema.pool
    await seed_token(pool, "active")
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO feed(source, key, polling) VALUES ('workable', 'active', true)"
        )

    muted = await tokens.set_muted(
        pool,
        ats="workable",
        token="active",
        muted=True,
    )
    unmuted = await tokens.set_muted(
        pool,
        ats="workable",
        token="active",
        muted=False,
    )

    async with pool.acquire() as conn:
        feed_exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM feed"
            " WHERE source = 'workable' AND key = 'active')"
        )
    assert muted["feed_dropped"] is True
    assert unmuted["feed_dropped"] is False
    assert feed_exists is False


async def test_pause_switch_survives_and_gates_every_path(core_schema):
    """The switch is a row, not process state: one write, three readers."""
    pool = core_schema.pool
    current = datetime.now(timezone.utc)

    await token_heartbeat.set_paused(pool, True, current=current)

    assert await token_heartbeat.touch_runner(pool, current) is True
    with pytest.raises(token_heartbeat.HeartbeatPaused):
        await token_heartbeat.enqueue_now(pool, current)
    snapshot = await token_heartbeat.heartbeat_snapshot(
        pool,
        current=current,
        configured=True,
        offline_after_s=180,
    )
    assert snapshot["state"] == "paused"
    assert snapshot["paused"] is True
    assert snapshot["paused_changed_at"] == current.isoformat()

    await token_heartbeat.set_paused(pool, False, current=current)

    assert await token_heartbeat.touch_runner(pool, current) is False
    await token_heartbeat.enqueue_now(pool, current)
    assert (
        await token_heartbeat.claim_due(pool, current=current, lease_seconds=900)
        is not None
    )


async def test_heartbeat_lease_is_exclusive_and_status_is_persistent(core_schema):
    pool = core_schema.pool
    current = datetime.now(timezone.utc)

    async def claim():
        return await token_heartbeat.claim_due(
            pool,
            current=current,
            lease_seconds=900,
        )

    leases = await asyncio.gather(claim(), claim())
    claimed = [lease for lease in leases if lease is not None]
    assert len(claimed) == 1

    await token_heartbeat.finish_success(
        pool,
        claimed[0],
        current=current,
        interval_seconds=21600,
        candidates=20,
        validated=20,
        promoted=18,
        rejected=2,
    )
    await token_heartbeat.touch_runner(pool, current)
    snapshot = await token_heartbeat.heartbeat_snapshot(
        pool,
        current=current,
        configured=True,
        offline_after_s=180,
    )

    assert snapshot["state"] == "healthy"
    assert snapshot["last_outcome"] == "success"
    assert snapshot["last_validated"] == 20
    assert snapshot["last_promoted"] == 18
