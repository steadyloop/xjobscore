"""Scheduler board groups: layout validation and the /api/scheduler/groups routes.

The SQL itself runs against real PostgreSQL in
``tests/test_provider_groups_postgres.py``; these pin the validation rules and
the HTTP contract, including the degraded shape the board renders from.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from xjobscore.inspector.test_support import create_app
from xjobscore.inspector.policy import (
    MAX_GROUP_NAME,
    PolicyUnavailable,
    clean_provider_groups,
)
from xjobscore.inspector.test_routes import FakeCatalog, FakePolicy
from xjobscore.records import Feed


# Validation -----------------------------------------------------------------


def test_clean_trims_names_and_drops_blank_or_repeated_sources():
    cleaned = clean_provider_groups(
        [{"id": 3, "name": "  Watchlist ", "sources": ["workday", " ", "workday", " lever "]}]
    )
    assert cleaned == [{"id": 3, "name": "Watchlist", "sources": ["workday", "lever"]}]


def test_clean_keeps_the_case_the_operator_typed():
    assert clean_provider_groups([{"name": "Banks & Funds"}])[0]["name"] == "Banks & Funds"


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ([{"name": "   "}], "cannot be empty"),
        ([{"name": "x" * (MAX_GROUP_NAME + 1)}], "at most"),
        ([{"name": "ungrouped"}], "reserved"),
        ([{"name": "Finance"}, {"name": "finance"}], "two groups"),
        (
            [{"name": "A", "sources": ["workday"]}, {"name": "B", "sources": ["workday"]}],
            "workday is in both",
        ),
    ],
)
def test_clean_rejects_layouts_the_board_could_not_show(groups, message):
    with pytest.raises(ValueError, match=message):
        clean_provider_groups(groups)


# Routes ---------------------------------------------------------------------


class GroupsPolicy(FakePolicy):
    """Keeps the layout in memory, assigning ids the way the table would."""

    def __init__(self):
        super().__init__({})
        self.layout: list[dict] = []
        self.next_id = 1

    async def provider_groups(self):
        return self.layout

    async def set_provider_groups(self, groups):
        cleaned = clean_provider_groups(groups)
        for group in cleaned:
            if group["id"] is None:
                group["id"] = self.next_id
                self.next_id += 1
        self.layout = cleaned
        return self.layout


class UnmigratedPolicy(FakePolicy):
    def __init__(self):
        super().__init__({})

    async def provider_groups(self):
        raise PolicyUnavailable("provider groups need the migration")

    async def set_provider_groups(self, groups):
        raise PolicyUnavailable("provider groups need the migration")


def make_client(app_store, policy):
    app = create_app(
        catalog=FakeCatalog(Feed(id=1, source="groups-test", key="demo", config={})),
        store=app_store,
        user_agent="groups-test",
        policy=policy,
    )
    return TestClient(app)


def test_groups_round_trip_in_order(app_store):
    with make_client(app_store, GroupsPolicy()) as client:
        assert client.get("/api/scheduler/groups").json() == {
            "available": True,
            "error": None,
            "groups": [],
        }

        saved = client.put(
            "/api/scheduler/groups",
            json={
                "groups": [
                    {"name": "Watchlist", "sources": ["workday", "greenhouse"]},
                    {"name": "Finance", "sources": ["avature"]},
                ]
            },
        ).json()["groups"]
        assert [group["name"] for group in saved] == ["Watchlist", "Finance"]
        assert all(isinstance(group["id"], int) for group in saved)

        # A rename keeps the id; the board's fold state is keyed on it.
        renamed = [{**saved[0], "name": "Core"}, saved[1]]
        body = client.put("/api/scheduler/groups", json={"groups": renamed}).json()
        assert body["groups"][0] == {**saved[0], "name": "Core"}
        assert client.get("/api/scheduler/groups").json()["groups"] == body["groups"]


def test_invalid_layout_is_a_400_and_writes_nothing(app_store):
    policy = GroupsPolicy()
    with make_client(app_store, policy) as client:
        response = client.put(
            "/api/scheduler/groups",
            json={"groups": [{"name": "A", "sources": ["lever"]}, {"name": "B", "sources": ["lever"]}]},
        )
        assert response.status_code == 400
        assert "lever is in both" in response.json()["detail"]
        assert policy.layout == []


def test_unmigrated_database_reads_as_no_groups_and_refuses_writes(app_store):
    with make_client(app_store, UnmigratedPolicy()) as client:
        body = client.get("/api/scheduler/groups").json()
        assert body["available"] is False
        assert body["groups"] == []
        assert "migration" in body["error"]

        response = client.put("/api/scheduler/groups", json={"groups": []})
        assert response.status_code == 503
