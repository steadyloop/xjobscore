"""The append-only probe log, against the real DDL.

``token.status``/``checked_at`` hold only the latest answer, so ``token_log``
is the only place a token's history exists. These tests run the actual
statements: a unit test with a fake pool would assert the SQL we wrote, not the
SQL PostgreSQL accepts.

No network: a token whose ``ats`` has no keyscan rule short-circuits to
"unknown" before any HTTP happens, which is exactly the persistence path we
want to exercise.
"""

from __future__ import annotations

import os

import pytest

from xjobscore.console import tokens, validator
from xjobscore.console.policy import DROP_FEED_SQL

pytestmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"),
    reason="set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests",
)


async def seed_token(pool, ats: str, token: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO token(ats, token) VALUES ($1, $2)", ats, token
        )


async def test_every_probe_appends_a_row_and_the_tail_is_newest_first(core_schema):
    pool = core_schema.pool
    await seed_token(pool, "no-such-vendor", "alpha")
    await seed_token(pool, "no-such-vendor", "beta")

    await validator.validate_token("no-such-vendor", "alpha", pool=pool)
    await validator.validate_token("no-such-vendor", "beta", pool=pool)
    # Re-probing the same token appends again rather than overwriting: two
    # checks of one board are two events, which is the whole point of a log.
    await validator.validate_token("no-such-vendor", "alpha", pool=pool)

    tail = await tokens.recent_checks(pool, limit=10)

    assert [(row["token"], row["status"]) for row in tail] == [
        ("alpha", "unknown"),
        ("beta", "unknown"),
        ("alpha", "unknown"),
    ]
    assert [row["id"] for row in tail] == sorted(
        (row["id"] for row in tail), reverse=True
    )


async def test_a_token_outside_the_inventory_logs_nothing(core_schema):
    """The INSERT is fed by the UPDATE, so a missed row writes no history."""
    pool = core_schema.pool

    result = await validator.validate_token("no-such-vendor", "ghost", pool=pool)

    assert result["status"] == "unknown"
    assert "persist_error" not in result["details"]
    assert await tokens.recent_checks(pool, limit=10) == []


async def test_the_log_and_the_token_row_agree_after_a_probe(core_schema):
    pool = core_schema.pool
    await seed_token(pool, "no-such-vendor", "alpha")

    await validator.validate_token("no-such-vendor", "alpha", pool=pool)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT t.status, t.checked_at, l.status AS logged, l.checked_at AS at"
            "  FROM token AS t JOIN token_log AS l"
            "    ON l.ats = t.ats AND l.token = t.token"
            " WHERE t.ats = 'no-such-vendor'"
        )
    assert row["status"] == row["logged"]
    assert row["checked_at"] == row["at"]


async def test_missing_table_reads_as_unavailable_not_empty(core_schema):
    pool = core_schema.pool
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE token_log")

    assert await tokens.recent_checks(pool, limit=10) is None


async def seed_live(pool, ats: str, token: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO token(ats, token, status) VALUES ($1, $2, 'live')", ats, token
        )


async def test_promotion_and_drop_bracket_a_token_with_two_rows(core_schema):
    """The pair `feed` cannot hold: it records neither a birth nor a death."""
    pool = core_schema.pool
    await seed_live(pool, "workable", "snap")

    assert await tokens.promote_verified(pool, [("workable", "snap")]) == 1

    # PolicyStore opens its own pool from a DSN and cannot be pointed at the
    # throwaway schema, so run its statement against this one.
    dropped_id = await pool.fetchval(DROP_FEED_SQL, "workable", "snap")
    assert dropped_id is not None

    events = await tokens.recent_feed_events(pool, limit=10)
    assert [(row["token"], row["status"]) for row in events] == [
        ("snap", "feed_exited"),
        ("snap", "feed_entered"),
    ]
    # The drop is the whole point: `feed` is gone, the history is not.
    assert await pool.fetchval("SELECT count(*) FROM feed") == 0


async def test_muting_a_promoted_token_logs_the_exit(core_schema):
    pool = core_schema.pool
    await seed_live(pool, "greenhouse", "stripe")
    await tokens.promote_verified(pool, [("greenhouse", "stripe")])

    saved = await tokens.set_muted(pool, ats="greenhouse", token="stripe", muted=True)

    assert saved["feed_dropped"] is True
    events = await tokens.recent_feed_events(pool, limit=10)
    assert [row["status"] for row in events] == ["feed_exited", "feed_entered"]


async def test_the_two_tails_never_show_each_other_s_rows(core_schema):
    """One column, two verbs -- so every read has to name the ones it wants."""
    pool = core_schema.pool
    await seed_live(pool, "no-such-vendor", "alpha")
    await validator.validate_token("no-such-vendor", "alpha", pool=pool)

    await seed_live(pool, "workable", "snap")
    await tokens.promote_verified(pool, [("workable", "snap")])

    checks = await tokens.recent_checks(pool, limit=10)
    events = await tokens.recent_feed_events(pool, limit=10)

    assert [row["status"] for row in checks] == ["unknown"]
    assert [row["status"] for row in events] == ["feed_entered"]
