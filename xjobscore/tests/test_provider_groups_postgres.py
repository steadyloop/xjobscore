"""Scheduler board groups against the real DDL.

The whole-layout write deletes, renames, reorders and refiles in one
transaction; a fake pool would only assert the SQL we wrote, not that
PostgreSQL accepts it and the cascade and ordering behave.
"""

from __future__ import annotations

import os

import pytest

from xjobscore.console.policy import PolicyStore

pytestmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"),
    reason="set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests",
)


def policy_on(core_schema) -> PolicyStore:
    policy = PolicyStore(core_schema.dsn)
    # Borrow the fixture's pool, which is scoped to the throwaway schema.
    policy._pool = core_schema.pool
    return policy


async def test_a_fresh_database_has_no_groups(core_schema):
    assert await policy_on(core_schema).provider_groups() == []


async def test_layout_round_trips_in_board_order(core_schema):
    policy = policy_on(core_schema)
    saved = await policy.set_provider_groups(
        [
            {"id": None, "name": "Watchlist", "sources": ["workday", "greenhouse"]},
            {"id": None, "name": "Finance", "sources": ["avature"]},
        ]
    )

    assert [(g["name"], g["sources"]) for g in saved] == [
        ("Watchlist", ["greenhouse", "workday"]),
        ("Finance", ["avature"]),
    ]
    assert await policy.provider_groups() == saved


async def test_reorder_rename_and_refile_keep_ids(core_schema):
    policy = policy_on(core_schema)
    watch, finance = await policy.set_provider_groups(
        [
            {"id": None, "name": "Watchlist", "sources": ["workday", "lever"]},
            {"id": None, "name": "Finance", "sources": ["avature"]},
        ]
    )

    # Finance moves to the top under a new name and takes lever with it.
    saved = await policy.set_provider_groups(
        [
            {"id": finance["id"], "name": "Banks & Funds", "sources": ["avature", "lever"]},
            {"id": watch["id"], "name": "Watchlist", "sources": ["workday"]},
        ]
    )

    assert [(g["id"], g["name"], g["sources"]) for g in saved] == [
        (finance["id"], "Banks & Funds", ["avature", "lever"]),
        (watch["id"], "Watchlist", ["workday"]),
    ]


async def test_a_group_left_out_is_deleted_with_its_members(core_schema):
    policy = policy_on(core_schema)
    watch, finance = await policy.set_provider_groups(
        [
            {"id": None, "name": "Watchlist", "sources": ["workday"]},
            {"id": None, "name": "Finance", "sources": ["avature"]},
        ]
    )

    saved = await policy.set_provider_groups([watch])

    assert saved == [watch]
    async with core_schema.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM provider_group_member") == 1


async def test_a_vanished_id_is_recreated_not_refused(core_schema):
    """Undo of a delete, or a stale tab: last write wins."""
    policy = policy_on(core_schema)
    (watch,) = await policy.set_provider_groups(
        [{"id": None, "name": "Watchlist", "sources": ["workday"]}]
    )
    await policy.set_provider_groups([])

    (restored,) = await policy.set_provider_groups([watch])

    assert restored["name"] == "Watchlist"
    assert restored["sources"] == ["workday"]
    assert restored["id"] != watch["id"]


async def test_an_invalid_layout_leaves_the_stored_one_alone(core_schema):
    policy = policy_on(core_schema)
    saved = await policy.set_provider_groups(
        [{"id": None, "name": "Watchlist", "sources": ["workday"]}]
    )

    with pytest.raises(ValueError):
        await policy.set_provider_groups(
            [{"id": None, "name": "A", "sources": ["x"]}, {"id": None, "name": "a"}]
        )

    assert await policy.provider_groups() == saved
