from __future__ import annotations

from fastapi.testclient import TestClient

from xjobscore.records import Feed, Record
from xjobscore.sources.catalog import feed_ref

from oneshot_ui.app import create_app
from oneshot_ui.config import Settings
from oneshot_ui.gateway import GatewayResult


class SelectionGateway:
    def __init__(self) -> None:
        self.feeds = [
            Feed(
                id=-301,
                source="workday",
                key="acme",
                config={},
                state={},
                company_id="acme",
                company_name="Acme Corporation",
            ),
            Feed(
                id=-302,
                source="workday",
                key="contoso",
                config={},
                state={},
                company_id="contoso",
                company_name="Contoso",
            ),
            Feed(
                id=-303,
                source="greenhouse",
                key="example",
                config={},
                state={},
                company_id="example",
                company_name="Example Labs",
            ),
        ]
        self.fetch_calls: list[int] = []

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def all_feeds(self) -> list[Feed]:
        return list(self.feeds)

    async def get_feed(self, feed_id: int) -> Feed | None:
        return next((feed for feed in self.feeds if feed.id == feed_id), None)

    async def get_feed_ref(self, reference: str) -> Feed | None:
        return next(
            (
                feed
                for feed in self.feeds
                if feed_ref(feed.source, feed.key) == reference
            ),
            None,
        )

    async def fetch(self, selected: Feed) -> GatewayResult:
        self.fetch_calls.append(selected.id)
        return GatewayResult(
            selected,
            Record(items=[], http_status=200),
            (),
            "available",
        )

    @staticmethod
    def parse(selected: Feed, record: Record) -> GatewayResult:
        return GatewayResult(selected, record, (), "available")


def selection_client(tmp_path, gateway: SelectionGateway | None = None) -> tuple[TestClient, SelectionGateway]:
    selected_gateway = gateway or SelectionGateway()
    return (
        TestClient(
            create_app(
                Settings(database_path=tmp_path / "oneshot.sqlite3"),
                gateway=selected_gateway,  # type: ignore[arg-type]
            )
        ),
        selected_gateway,
    )


def test_sources_default_disabled_and_manual_fetch_requires_selection(tmp_path) -> None:
    client, gateway = selection_client(tmp_path)
    acme_ref = feed_ref("workday", "acme")
    with client:
        all_groups = client.get(
            "/api/source-groups", params={"selection": "all"}
        ).json()
        assert all_groups["total_available"] == 3
        assert all_groups["total_enabled"] == 0
        assert [(group["source"], group["available_count"]) for group in all_groups["vendors"]] == [
            ("greenhouse", 1),
            ("workday", 2),
        ]
        assert client.get("/api/source-groups").json()["vendors"] == []

        blocked = client.post(f"/api/sources/{acme_ref}/fetch")
        assert blocked.status_code == 409
        assert gateway.fetch_calls == []

        enabled = client.put(
            f"/api/sources/{acme_ref}/selection", json={"enabled": True}
        )
        assert enabled.status_code == 200
        assert enabled.json()["selected"] is True
        active = client.get("/api/source-groups").json()
        assert active["total_enabled"] == 1
        assert active["vendors"][0]["source"] == "workday"
        assert [feed["key"] for feed in active["vendors"][0]["feeds"]] == ["acme"]

        fetched = client.post(f"/api/sources/{acme_ref}/fetch")
        assert fetched.status_code == 200
        assert gateway.fetch_calls == [-301]


def test_source_coverage_is_read_only_and_grouped_by_manifest_source(tmp_path) -> None:
    client, _ = selection_client(tmp_path)
    with client:
        coverage = client.get("/api/source-coverage")
        assert coverage.status_code == 200
        payload = coverage.json()
        assert payload["source_count"] == 2
        assert payload["total_coverage"] == 0
        rows = {row["source"]: row for row in payload["sources"]}
        assert rows["greenhouse"] == {
            "source": "greenhouse",
            "type": "ATS",
            "board_count": 1,
            "coverage": 0,
            "last_poll_at": None,
            "health": "Not polled",
            "added_today": 0,
        }
        assert rows["workday"]["type"] == "ATS"
        assert rows["workday"]["board_count"] == 2


def test_vendor_toggle_is_atomic_searchable_and_persistent(tmp_path) -> None:
    gateway = SelectionGateway()
    client, _ = selection_client(tmp_path, gateway)
    with client:
        enabled = client.put(
            "/api/sources/vendor/workday/selection", json={"enabled": True}
        )
        assert enabled.status_code == 200
        assert enabled.json()["affected"] == 2
        workday = client.get(
            "/api/source-groups", params={"selection": "all", "q": "contoso"}
        ).json()["vendors"][0]
        assert workday["enabled_count"] == 2
        assert workday["available_count"] == 2
        assert [feed["company_name"] for feed in workday["feeds"]] == ["Contoso"]

    reopened, _ = selection_client(tmp_path, gateway)
    with reopened:
        active = reopened.get("/api/source-groups").json()
        assert active["total_enabled"] == 2
        disabled = reopened.put(
            "/api/sources/vendor/workday/selection", json={"enabled": False}
        )
        assert disabled.status_code == 200
        assert reopened.get("/api/source-groups").json()["vendors"] == []
        available = reopened.get(
            "/api/source-groups", params={"selection": "disabled"}
        ).json()
        assert {group["source"] for group in available["vendors"]} == {
            "greenhouse",
            "workday",
        }


def test_flat_source_api_exposes_selection_without_hiding_catalog_availability(tmp_path) -> None:
    client, _ = selection_client(tmp_path)
    with client:
        rows = client.get("/api/sources", params={"limit": 10}).json()
        assert rows["total"] == 3
        assert all(source["selected"] is False for source in rows["sources"])
        assert client.get(
            "/api/sources", params={"selection": "enabled"}
        ).json()["total"] == 0
        assert client.put(
            "/api/sources/not-a-feed/selection", json={"enabled": True}
        ).status_code == 404


def test_batch_fetch_preflights_every_source_before_network_work(tmp_path) -> None:
    client, gateway = selection_client(tmp_path)
    acme_ref = feed_ref("workday", "acme")
    contoso_ref = feed_ref("workday", "contoso")
    with client:
        assert client.put(
            f"/api/sources/{acme_ref}/selection", json={"enabled": True}
        ).status_code == 200
        blocked = client.post(
            "/api/sources/fetch", json={"feed_refs": [acme_ref, contoso_ref]}
        )
        assert blocked.status_code == 409
        assert gateway.fetch_calls == []
