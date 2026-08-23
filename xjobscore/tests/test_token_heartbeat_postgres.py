from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os

import pytest

from xjobscore.inspector import token_heartbeat, tokens

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
    await seed_token(pool, "eligible")
    await seed_token(pool, "muted", muted=True)
    await seed_token(pool, "dead", status="dead")

    identities, total = await tokens.promotion_candidates(pool)
    assert identities == [("workable", "eligible")]
    assert total == 1

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
