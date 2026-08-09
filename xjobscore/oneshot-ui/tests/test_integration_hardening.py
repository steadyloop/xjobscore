from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import replace

from fastapi.testclient import TestClient

from xjobscore.records import Feed, Item, Posting, Record
from xjobscore.sources.catalog import feed_ref

from oneshot_ui.app import create_app
from oneshot_ui.config import Settings
from oneshot_ui.gateway import GatewayResult


def posting(external_id: str, url: str, *, title: str = "Summer Software Engineering Intern") -> Posting:
    return Posting(
        external_id=external_id,
        title=title,
        url_posting=url,
        apply_url=url,
        company_raw="Lifecycle Labs",
        location_raw="Remote, United States",
        description_html="Requirements: Python. Currently enrolled in computer science.",
    )


class SequenceGateway:
    def __init__(
        self,
        snapshots: dict[int, list[Iterable[Posting]]],
        *,
        complete: bool = False,
        closes: bool = False,
        parser_error: bool = False,
    ) -> None:
        self.feeds = [
            Feed(
                id=feed_id,
                source="fixture",
                key=f"board-{abs(feed_id)}",
                config={},
                state={},
                company_id="lifecycle-labs",
                company_name="Lifecycle Labs",
                complete=complete,
                closes=closes,
            )
            for feed_id in snapshots
        ]
        self.snapshots = {
            feed_id: [tuple(values) for values in values_by_fetch]
            for feed_id, values_by_fetch in snapshots.items()
        }
        self.indices = {feed_id: 0 for feed_id in snapshots}
        self.parser_error = parser_error
        self.fetch_calls = 0

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
        self.fetch_calls += 1
        index = self.indices[selected.id]
        available = self.snapshots[selected.id]
        values = available[min(index, len(available) - 1)]
        self.indices[selected.id] = index + 1
        record = Record(
            items=[Item(value.external_id, {"title": value.title}) for value in values],
            http_status=200,
        )
        if self.parser_error:
            return GatewayResult(
                selected,
                record,
                values,
                "error",
                "fixture parser stopped after a partial prefix",
            )
        return GatewayResult(selected, record, values, "available")

    def parse(self, selected: Feed, record: Record) -> GatewayResult:
        known = {
            value.external_id: value
            for snapshot in self.snapshots[selected.id]
            for value in snapshot
        }
        values = tuple(
            known[item.external_id]
            for item in record.items
            if item.external_id in known
        )
        if self.parser_error:
            return GatewayResult(
                selected, record, values, "error", "fixture parser still broken"
            )
        return GatewayResult(selected, record, values, "available")


def client_for(tmp_path, gateway: SequenceGateway) -> TestClient:
    return TestClient(
        create_app(
            Settings(database_path=tmp_path / "oneshot.sqlite3"),
            gateway=gateway,  # type: ignore[arg-type]
        )
    )


def enable_fixture_sources(client: TestClient) -> None:
    response = client.put(
        "/api/sources/vendor/fixture/selection", json={"enabled": True}
    )
    assert response.status_code == 200, response.text


def test_complete_manual_snapshots_unlist_close_and_reopen(tmp_path) -> None:
    role = posting("role-1", "https://jobs.lifecycle.test/role-1")
    gateway = SequenceGateway(
        {-101: [[role], [], [], [role]]}, complete=True, closes=True
    )
    client = client_for(tmp_path, gateway)
    with client:
        enable_fixture_sources(client)
        reference = client.get("/api/sources").json()["sources"][0]["feed_ref"]
        first = client.post(f"/api/sources/{reference}/fetch")
        assert first.status_code == 200, first.text
        job_id = client.get("/api/jobs").json()["jobs"][0]["id"]

        client.post(f"/api/sources/{reference}/fetch")
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "unlisted"

        client.post(f"/api/sources/{reference}/fetch")
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "closed"

        client.post(f"/api/sources/{reference}/fetch")
        reopened = client.get(f"/api/jobs/{job_id}").json()
        assert reopened["status"] == "open"
        assert reopened["source_count"] == 1

    connection = sqlite3.connect(tmp_path / "oneshot.sqlite3")
    try:
        events = connection.execute(
            "SELECT from_status, to_status FROM lifecycle_events ORDER BY rowid"
        ).fetchall()
        assert ("open", "unlisted") in events
        assert ("unlisted", "closed") in events
        assert ("closed", "open") in events
    finally:
        connection.close()


def test_suspicious_manual_mass_drop_is_quarantined(tmp_path) -> None:
    baseline = [
        posting(str(index), f"https://jobs.lifecycle.test/{index}")
        for index in range(10)
    ]
    gateway = SequenceGateway(
        {-102: [baseline, []]}, complete=True, closes=True
    )
    client = client_for(tmp_path, gateway)
    with client:
        enable_fixture_sources(client)
        reference = client.get("/api/sources").json()["sources"][0]["feed_ref"]
        client.post(f"/api/sources/{reference}/fetch")
        assert client.get("/api/jobs").json()["total"] == 10
        client.post(f"/api/sources/{reference}/fetch")
        assert client.get("/api/jobs").json()["total"] == 10
        source = client.get("/api/sources").json()["sources"][0]
        assert source["status"] == "quarantined"
        assert "suspicious snapshot drop" in source["snapshot_reason"]

    connection = sqlite3.connect(tmp_path / "oneshot.sqlite3")
    try:
        status, reason = connection.execute(
            "SELECT status, reason FROM poll_snapshots ORDER BY poll_id DESC LIMIT 1"
        ).fetchone()
        assert status == "quarantined"
        assert "suspicious snapshot drop" in reason
    finally:
        connection.close()


def test_saved_job_surfaces_recent_authoritative_posting_change(tmp_path) -> None:
    first = posting(
        "changing-role",
        "https://jobs.lifecycle.test/changing-role",
        title="Summer Software Engineering Intern",
    )
    revised = posting(
        "changing-role",
        "https://jobs.lifecycle.test/changing-role",
        title="Summer Software Engineering Intern — Platform",
    )
    gateway = SequenceGateway({-106: [[first], [revised]]})
    client = client_for(tmp_path, gateway)
    with client:
        enable_fixture_sources(client)
        reference = client.get("/api/sources").json()["sources"][0]["feed_ref"]
        assert client.post(f"/api/sources/{reference}/fetch").status_code == 200
        job = client.get("/api/jobs").json()["jobs"][0]
        assert client.post(
            f"/api/jobs/{job['id']}/triage", json={"state": "saved"}
        ).status_code == 200

        assert client.post(f"/api/sources/{reference}/fetch").status_code == 200
        saved = client.get("/api/jobs", params={"state": "saved"}).json()["jobs"]
        assert len(saved) == 1
        assert saved[0]["changed_recently"] is True
        assert saved[0]["last_changed_at"] is not None
        assert saved[0]["title"] == "Summer Software Engineering Intern — Platform"
        today = client.get("/api/today").json()["items"]
        assert today[0]["today_reason"] == "Posting changed"


def test_partial_parser_prefix_never_enters_board(tmp_path) -> None:
    role = posting("partial", "https://jobs.lifecycle.test/partial")
    gateway = SequenceGateway({-103: [[role]]}, parser_error=True)
    client = client_for(tmp_path, gateway)
    with client:
        enable_fixture_sources(client)
        reference = client.get("/api/sources").json()["sources"][0]["feed_ref"]
        result = client.post(f"/api/sources/{reference}/fetch")
        assert result.status_code == 200, result.text
        assert result.json()["parser_state"] == "error"
        assert result.json()["ingested"]["postings"] == 0
        assert client.get("/api/jobs").json()["total"] == 0


def test_exact_auto_merge_unifies_source_evidence_without_review_surface(tmp_path) -> None:
    url = "https://jobs.lifecycle.test/shared-role"
    gateway = SequenceGateway(
        {
            -104: [[posting("source-a", url)]],
            -105: [[posting("source-b", url)]],
        }
    )
    client = client_for(tmp_path, gateway)
    with client:
        enable_fixture_sources(client)
        sources = client.get("/api/sources").json()["sources"]
        for source in sources:
            result = client.post(f"/api/sources/{source['feed_ref']}/fetch")
            assert result.status_code == 200, result.text

        unified = client.get("/api/jobs").json()
        assert unified["total"] == 1
        assert unified["jobs"][0]["source_count"] == 2
        assert client.get("/api/dedupe").status_code == 404


def test_batch_ingest_failure_does_not_block_later_selected_feed(tmp_path) -> None:
    gateway = SequenceGateway(
        {
            -106: [[posting("first", "https://jobs.lifecycle.test/first")]],
            -107: [[posting("second", "https://jobs.lifecycle.test/second")]],
        }
    )
    client = client_for(tmp_path, gateway)
    with client:
        enable_fixture_sources(client)
        sources = client.get("/api/sources").json()["sources"]
        real_ingestor = client.app.state.ingestor

        class FailFirstIngest:
            async def ingest(self, result):
                if result.feed.id == -106:
                    raise RuntimeError("fixture read-model failure")
                return await real_ingestor.ingest(result)

        client.app.state.ingestor = FailFirstIngest()
        response = client.post(
            "/api/sources/fetch",
            json={"feed_refs": [source["feed_ref"] for source in sources]},
        )
        assert response.status_code == 200, response.text
        results = response.json()["results"]
        assert results[0]["ingestion_error"] == "RuntimeError: fixture read-model failure"
        assert results[1]["ingestion_error"] is None
        assert client.get("/api/jobs").json()["total"] == 1


def test_unknown_window_stays_review_and_profile_enrollment_gate_is_hard(tmp_path) -> None:
    unknown = posting(
        "unknown-window",
        "https://jobs.lifecycle.test/unknown-window",
        title="Software Engineering Intern",
    )
    restricted = replace(
        posting("restricted", "https://jobs.lifecycle.test/restricted"),
        description_html=(
            "Requirements: Python. Must be currently enrolled full-time in "
            "a computer science program."
        ),
    )
    gateway = SequenceGateway({-108: [[unknown, restricted]]})
    client = client_for(tmp_path, gateway)
    with client:
        enable_fixture_sources(client)
        profile = client.get("/api/profile").json()
        profile["reduced_enrollment"] = True
        assert client.put("/api/profile", json=profile).status_code == 200
        reference = client.get("/api/sources").json()["sources"][0]["feed_ref"]
        client.post(f"/api/sources/{reference}/fetch")

        review = client.get("/api/jobs", params={"needs_classification": True}).json()[
            "jobs"
        ][0]
        assert review["eligibility"] == "review"
        assert review["score_band"] == "log_only"
        assert review["submission_route"]["auto_submit"] is False
        corrected = client.post(
            f"/api/jobs/{review['id']}/classification",
            json={
                "opportunity_type": "fall_internship",
                "rationale": "Recruiter confirmed a September–December term.",
            },
        )
        assert corrected.status_code == 200
        corrected_job = corrected.json()
        assert corrected_job["eligibility"] == "eligible"
        assert corrected_job["score"] > review["score"]
        assert corrected_job["score_band"] != "log_only"
        assert corrected_job["submission_route"]["instruction"]
        assert corrected_job["submission_route"]["auto_submit"] is False
        client.post(f"/api/sources/{reference}/fetch")
        assert client.get(f"/api/jobs/{review['id']}").json()[
            "opportunity_type_slug"
        ] == "fall_internship"

        summer = client.get(
            "/api/jobs", params={"opportunity_type": "summer_internship"}
        ).json()["jobs"][0]
        assert summer["eligibility"] == "ineligible"
        assert "Path 4 posting requires full-time enrollment" in summer[
            "eligibility_reasons"
        ]
        assert summer["score_band"] == "log_only"
