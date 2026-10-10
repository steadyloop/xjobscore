"""Live discovery tokens still need an implemented polling adapter."""

import pytest

from xjobscore.console import tokens

pytestmark = pytest.mark.asyncio


async def _seed(pool):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO token (ats, token, status) VALUES
                 ('paylocity', 'unsupported-board', 'live'),
                 ('greenhouse', 'supported-board', 'live')"""
        )


@pytest.mark.parametrize("bulk", [False, True])
async def test_promotion_skips_live_tokens_without_an_adapter(core_schema, bulk):
    pool = core_schema.pool
    await _seed(pool)
    if bulk:
        added = await tokens.add_vendors_to_feed(pool, ["paylocity", "greenhouse"])
    else:
        added = await tokens.promote_verified(
            pool, [("paylocity", "unsupported-board"), ("greenhouse", "supported-board")]
        )
    assert added == 1
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT source FROM feed") == "greenhouse"
        assert await conn.fetchval(
            "SELECT count(*) FROM token_log WHERE ats='paylocity' AND status='feed_entered'"
        ) == 0


async def test_disabled_vendor_is_hidden_from_tokens_and_counts(core_schema):
    await _seed(core_schema.pool)
    result = await tokens.query(core_schema.pool, groups=True)
    groups = {row["ats"]: row for row in result["groups"]}
    rows = {row["ats"]: row for row in result["rows"]}
    assert "paylocity" not in groups
    assert groups["greenhouse"]["addable"] == 1
    assert result["counts"]["promotable"] == 1
    assert "paylocity" not in rows
    assert result["counts"]["total"] == 1
    assert rows["greenhouse"]["polling_supported"] is True


@pytest.mark.parametrize("action", ["enable", "bulk_enable", "import", "bulk_import", "unmute"])
async def test_disabled_vendor_cannot_be_activated_through_store(core_schema, action):
    from xjobscore import store

    pool = core_schema.pool
    await _seed(pool)
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO feed(source,key) VALUES ('paylocity','unsupported-board')")
        with pytest.raises(ValueError, match="disabled"):
            if action == "enable":
                await store.set_feed_enabled(conn, source="paylocity", key="unsupported-board", enabled=True)
            elif action == "bulk_enable":
                await store.set_feeds_enabled(conn, [("greenhouse", "supported-board"), ("paylocity", "unsupported-board")], enabled=True)
            elif action == "import":
                await store.import_feed(conn, source="paylocity", key="unsupported-board", enabled=True)
            elif action == "bulk_import":
                await store.import_feeds(conn, [("paylocity", "unsupported-board")], enabled=True)
            else:
                await tokens.set_muted(pool, ats="paylocity", token="unsupported-board", muted=False)
        assert await conn.fetchval("SELECT count(*) FROM feed WHERE polling") == 0


async def test_database_rejects_reenable_even_without_application_guards(core_schema):
    import asyncpg

    await _seed(core_schema.pool)
    async with core_schema.pool.acquire() as conn:
        await conn.execute("INSERT INTO feed(source,key) VALUES ('paylocity','unsupported-board')")
        with pytest.raises(asyncpg.CheckViolationError, match="feed_paylocity_disabled"):
            await conn.execute("UPDATE feed SET polling=true WHERE source='paylocity'")
