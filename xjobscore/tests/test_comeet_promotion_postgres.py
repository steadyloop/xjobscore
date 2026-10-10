"""Comeet promotion guards against the real schema.

A Comeet token without the validator's resolved config polls as HTTP 400, and
one board reaches the inventory under several spellings. Both guards live in
the SQL that "Add to feed" and the heartbeat insert under, so they are tested
against PostgreSQL rather than against query strings.
"""

from __future__ import annotations

import pytest

from xjobscore.console import tokens

pytestmark = pytest.mark.asyncio


async def _token(conn, token: str, config: dict, *, ats: str = "comeet", status: str = "live") -> None:
    await conn.execute(
        "INSERT INTO token (ats, token, status, config) VALUES ($1, $2, $3, $4)",
        ats,
        token,
        status,
        config,
    )


async def _feed_keys(conn) -> list[str]:
    return [row["key"] for row in await conn.fetch("SELECT key FROM feed ORDER BY key")]


READY = {"company": "C6.001", "token": "ABCDEF0123456789ABCD"}


async def test_comeet_without_resolved_config_is_not_promoted(core_schema):
    async with core_schema.pool.acquire() as conn:
        await _token(conn, "arbe/C6.001", {})
        await _token(conn, "final/C0.009", {"token": "ABCDEF0123456789ABCD"})  # no board id
        await _token(conn, "ready/C1.001", {**READY, "company": "C1.001"})

    added = await tokens.promote_verified(
        core_schema.pool,
        [("comeet", "arbe/C6.001"), ("comeet", "final/C0.009"), ("comeet", "ready/C1.001")],
    )

    assert added == 1
    async with core_schema.pool.acquire() as conn:
        assert await _feed_keys(conn) == ["ready/C1.001"]


async def test_a_registry_board_id_key_needs_only_the_api_token(core_schema):
    async with core_schema.pool.acquire() as conn:
        await _token(conn, "98.008", {"token": "ABCDEF0123456789ABCD"})

    assert await tokens.promote_verified(core_schema.pool, [("comeet", "98.008")]) == 1


async def test_case_variants_of_one_board_promote_once_in_one_batch(core_schema):
    async with core_schema.pool.acquire() as conn:
        await _token(conn, "arbe/C6.001", READY)
        await _token(conn, "arbe/c6.001", {**READY, "company": "c6.001"})

    added = await tokens.add_vendors_to_feed(core_schema.pool, ["comeet"])

    assert added == 1
    async with core_schema.pool.acquire() as conn:
        assert len(await _feed_keys(conn)) == 1


async def test_a_renamed_slug_is_not_added_beside_the_board_it_duplicates(core_schema):
    async with core_schema.pool.acquire() as conn:
        await _token(conn, "tabtale/32.00E", {**READY, "company": "32.00E"})
        await conn.execute("INSERT INTO feed (source, key) VALUES ('comeet', 'tabtale/32.00E')")
        await _token(conn, "crazylabs/32.00E", {**READY, "company": "32.00E"})

    assert await tokens.promote_verified(core_schema.pool, [("comeet", "crazylabs/32.00E")]) == 0


async def test_other_vendors_are_untouched_by_the_comeet_guards(core_schema):
    async with core_schema.pool.acquire() as conn:
        await _token(conn, "acme", {}, ats="greenhouse")
        await _token(conn, "Acme", {}, ats="lever")

    assert await tokens.add_vendors_to_feed(core_schema.pool, ["greenhouse", "lever"]) == 2
