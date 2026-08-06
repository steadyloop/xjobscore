from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from xjobscore.records import Feed, Item, Posting, Record
from xjobscore.sources.catalog import feed_ref

from oneshot_ui.app import create_app
from oneshot_ui.config import Settings
from oneshot_ui.gateway import GatewayResult


class FixtureGateway:
    """A catalog/adapter seam with no network and an observable fetch count."""

    def __init__(self) -> None:
        self.feed = Feed(
            id=-91,
            source="fixture",
            key="student-board",
            config={},
            state={},
            company_id="example",
            company_name="Example Labs",
        )
        self.fetch_calls = 0
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def all_feeds(self) -> list[Feed]:
        return [self.feed]

    async def get_feed(self, feed_id: int) -> Feed | None:
        return self.feed if feed_id == self.feed.id else None

    async def get_feed_ref(self, reference: str) -> Feed | None:
        expected = feed_ref(self.feed.source, self.feed.key)
        return self.feed if reference == expected else None

    async def fetch(self, selected: Feed) -> GatewayResult:
        assert selected.id == self.feed.id
        self.fetch_calls += 1
        postings = tuple(_fixture_postings())
        record = Record(
            items=[Item(posting.external_id, {"title": posting.title}) for posting in postings],
            envelope={"company": "Example Labs"},
            http_status=200,
            validators={"etag": '"fixture-v1"'},
        )
        return GatewayResult(selected, record, postings, "available")


def _fixture_postings() -> list[Posting]:
    common = {
        "company_raw": "Example Labs",
        "location_raw": "Remote, United States",
        "description_html": (
            "<h3>Requirements</h3><p>Python and C++ required. "
            "Currently enrolled in a computer science program.</p>"
        ),
        "published_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
    }
    rows = (
        ("spring-intern", "Winter Software Engineering Intern", "https://jobs.example.test/1"),
        ("spring-coop", "Spring Software Engineering Co-op", "https://jobs.example.test/2"),
        ("student-research", "Student Researcher", "https://jobs.example.test/3"),
        ("summer-intern", "Summer Software Engineering Intern", "https://jobs.example.test/4"),
        ("fall-coop", "Fall Software Engineering Co-op", "https://jobs.example.test/5"),
        ("fall-intern", "Fall Software Engineering Intern", "https://jobs.example.test/6"),
    )
    return [
        Posting(external_id=external_id, title=title, url_posting=url, apply_url=url, **common)
        for external_id, title, url in rows
    ]


def _client(tmp_path):
    gateway = FixtureGateway()
    application = create_app(
        Settings(database_path=tmp_path / "oneshot.sqlite3"),
        gateway=gateway,  # type: ignore[arg-type]
    )
    return TestClient(application), gateway


def _enable_fixture_source(client: TestClient) -> None:
    response = client.put(
        "/api/sources/vendor/fixture/selection", json={"enabled": True}
    )
    assert response.status_code == 200, response.text


def test_startup_is_offline_and_only_explicit_fetch_ingests(tmp_path) -> None:
    client, gateway = _client(tmp_path)
    with client:
        assert gateway.fetch_calls == 0
        health = client.get("/api/health").json()
        sources = client.get("/api/sources").json()
        assert gateway.fetch_calls == 0
        assert health["mode"] == "manual_fetch_only"
        assert health["polling"] is False
        assert sources["total"] == 1
        assert "OneShot" in client.get("/").text
        assert client.get("/static/app.js").status_code == 200

        _enable_fixture_source(client)

        result = client.post(f"/api/sources/{sources['sources'][0]['feed_ref']}/fetch")
        assert result.status_code == 200, result.text
        assert gateway.fetch_calls == 1
        assert result.json()["ingested"] == {
            "postings": 6,
            "created_entities": 6,
            "changed_revisions": 6,
            "exact_links": 0,
            "review_candidates": 0,
            "related_candidates": 0,
            "labels": 0,
            "facts": 0,
        }
        assert client.get("/api/jobs").json()["total"] == 6


def test_six_filters_multiselect_saved_today_and_unclassified(tmp_path) -> None:
    expected = {
        "spring_internship": "Winter Software Engineering Intern",
        "spring_coop": "Spring Software Engineering Co-op",
        "student_researcher": "Student Researcher",
        "summer_internship": "Summer Software Engineering Intern",
        "fall_coop": "Fall Software Engineering Co-op",
        "fall_internship": "Fall Software Engineering Intern",
    }
    client, _ = _client(tmp_path)
    with client:
        _enable_fixture_source(client)
        reference = client.get("/api/sources").json()["sources"][0]["feed_ref"]
        assert client.post(f"/api/sources/{reference}/fetch").status_code == 200

        by_slug: dict[str, dict] = {}
        for slug, title in expected.items():
            response = client.get("/api/jobs", params={"opportunity_type": slug})
            assert response.status_code == 200, response.text
            jobs = response.json()["jobs"]
            assert [job["title"] for job in jobs] == [title]
            assert jobs[0]["opportunity_type_slug"] == slug
            by_slug[slug] = jobs[0]

        combined = client.get(
            "/api/jobs",
            params=[
                ("opportunity_type", "spring_coop"),
                ("opportunity_type", "fall_coop"),
            ],
        ).json()
        assert {job["opportunity_type_slug"] for job in combined["jobs"]} == {
            "spring_coop",
            "fall_coop",
        }

        summer = by_slug["summer_internship"]
        assert client.post(
            f"/api/jobs/{summer['id']}/triage", json={"state": "saved"}
        ).status_code == 200
        saved = client.get(
            "/api/jobs",
            params={"state": "saved", "opportunity_type": "summer_internship"},
        ).json()
        assert [job["id"] for job in saved["jobs"]] == [summer["id"]]
        today = client.get(
            "/api/today", params={"opportunity_type": "summer_internship"}
        ).json()
        assert [job["id"] for job in today["items"]] == [summer["id"]]

        captured = client.post(
            "/api/jobs", json={"url": "https://manual.example.test/jobs/unknown-role"}
        )
        assert captured.status_code == 200, captured.text
        review = client.get("/api/jobs", params={"needs_classification": True}).json()
        assert review["total"] == 1
        assert review["jobs"][0]["needs_classification"] is True
        union = client.get(
            "/api/jobs",
            params=[
                ("opportunity_type", "spring_internship"),
                ("needs_classification", "true"),
            ],
        ).json()
        assert {job["opportunity_type_slug"] for job in union["jobs"]} == {
            "spring_internship",
            "unclassified",
        }
        corrected = client.post(
            f"/api/jobs/{captured.json()['id']}/classification",
            json={
                "opportunity_type": "fall_internship",
                "rationale": "Employer states a September–December internship term.",
            },
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["opportunity_type_slug"] == "fall_internship"
        assert client.get("/api/jobs", params={"needs_classification": True}).json()["total"] == 0


def test_application_requires_confirmation_and_preserves_snapshot(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _enable_fixture_source(client)
        reference = client.get("/api/sources").json()["sources"][0]["feed_ref"]
        client.post(f"/api/sources/{reference}/fetch")
        review_job = client.get(
            "/api/jobs", params={"opportunity_type": "student_researcher"}
        ).json()["jobs"][0]
        job = client.get(
            "/api/jobs", params={"opportunity_type": "summer_internship"}
        ).json()["jobs"][0]
        unrelated_observation = client.get(
            f"/api/jobs/{review_job['id']}"
        ).json()["observations"][0]["id"]

        saved_review = client.post(
            f"/api/jobs/{review_job['id']}/triage", json={"state": "saved"}
        )
        assert saved_review.status_code == 200
        blocked_queue = client.post(
            f"/api/jobs/{review_job['id']}/triage", json={"state": "queued"}
        )
        assert blocked_queue.status_code == 422
        assert client.get(f"/api/jobs/{review_job['id']}").json()["triage_state"] == "saved"
        review_plan = client.post(
            f"/api/jobs/{review_job['id']}/applications",
            json={"stage": "considering"},
        )
        assert review_plan.status_code == 200
        blocked_preparation = client.post(
            f"/api/jobs/{review_job['id']}/applications",
            json={"stage": "preparing"},
        )
        assert blocked_preparation.status_code == 422
        ineligible_submission = client.post(
            f"/api/jobs/{review_job['id']}/applications",
            json={"confirmation": "human_confirmed"},
        )
        assert ineligible_submission.status_code == 422

        closed_job = client.get(
            "/api/jobs", params={"opportunity_type": "fall_coop"}
        ).json()["jobs"][0]
        connection = sqlite3.connect(tmp_path / "oneshot.sqlite3")
        try:
            connection.execute(
                "UPDATE job_entities SET status = 'closed' WHERE id = ?",
                (closed_job["id"],),
            )
            connection.commit()
        finally:
            connection.close()
        closed_submission = client.post(
            f"/api/jobs/{closed_job['id']}/applications",
            json={"confirmation": "human_confirmed"},
        )
        assert closed_submission.status_code == 422

        missing_confirmation = client.post(
            f"/api/jobs/{job['id']}/applications", json={"note": "done"}
        )
        assert missing_confirmation.status_code == 422
        preparing = client.post(
            f"/api/jobs/{job['id']}/applications", json={"stage": "preparing"}
        )
        assert preparing.status_code == 200, preparing.text
        assert preparing.json()["stage"] == "preparing"
        bypass = client.post(
            f"/api/applications/{preparing.json()['id']}/events",
            json={"stage": "submitted", "confirmed": True},
        )
        assert bypass.status_code == 422
        submitted_event_bypass = client.post(
            f"/api/applications/{preparing.json()['id']}/events",
            json={"event_type": "submitted", "confirmed": True},
        )
        assert submitted_event_bypass.status_code == 422
        for downstream_stage in ("assessment", "screen", "interview", "offer", "no_reply"):
            premature = client.post(
                f"/api/applications/{preparing.json()['id']}/events",
                json={"stage": downstream_stage, "confirmed": True},
            )
            assert premature.status_code == 422
            premature_event = client.post(
                f"/api/applications/{preparing.json()['id']}/events",
                json={"event_type": downstream_stage, "confirmed": True},
            )
            assert premature_event.status_code == 422
        wrong_source = client.post(
            f"/api/jobs/{job['id']}/applications",
            json={
                "confirmation": "human_confirmed",
                "source_observation_id": unrelated_observation,
            },
        )
        assert wrong_source.status_code == 422

        created = client.post(
            f"/api/jobs/{job['id']}/applications",
            json={
                "confirmation": "human_confirmed",
                "submitted_at": "2026-08-04",
                "resume_variant": "ml-systems",
                "note": "Submitted on the official employer page.",
            },
        )
        assert created.status_code == 200, created.text
        application = created.json()
        assert application["id"] == preparing.json()["id"]
        assert application["stage"] == "submitted"
        assert application["resume_variant"] == "ml-systems"
        assert {event["event_type"] for event in application["events"]} == {
            "stage_changed",
            "submitted",
        }
        selected_source = client.get(f"/api/jobs/{job['id']}").json()["observations"][0]
        assert application["source_observation_id"] == selected_source["id"]
        assert application["apply_url_snapshot"] == (
            selected_source["apply_url"] or selected_source["posting_url"]
        )
        pipeline = client.get(
            "/api/applications", params={"opportunity_type": "summer_internship"}
        ).json()
        assert [item["id"] for item in pipeline["applications"]] == [application["id"]]
        assert client.get(
            "/api/applications", params={"opportunity_type": "fall_coop"}
        ).json()["applications"] == []
        assert client.get(
            "/api/today", params={"opportunity_type": "summer_internship"}
        ).json()["items"] == []

        moved = client.post(
            f"/api/applications/{application['id']}/events",
            json={
                "stage": "interview",
                "note": "Prepare for recruiter interview",
                "due_at": "2026-08-05T12:00:00Z",
            },
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["stage"] == "interview"
        assert moved.json()["next_due_at"].startswith("2026-08-05T12:00:00")
        assert moved.json()["next_action"] == "Prepare for recruiter interview"
        assert len(moved.json()["events"]) == 3
        due_today = client.get(
            "/api/today", params={"opportunity_type": "summer_internship"}
        ).json()["items"]
        assert [item["id"] for item in due_today] == [job["id"]]
        assert due_today[0]["today_reason"] == "Application action due"

        detail = client.get(f"/api/applications/{application['id']}").json()
        assert detail["company"] == job["company"]
        assert detail["title"] == job["title"]
        assert detail["submitted_at"].startswith("2026-08-04")


def test_batch_fetch_is_selected_and_bounded(tmp_path) -> None:
    client, gateway = _client(tmp_path)
    with client:
        _enable_fixture_source(client)
        reference = client.get("/api/sources").json()["sources"][0]["feed_ref"]
        duplicate_refs = client.post(
            "/api/sources/fetch", json={"feed_refs": [reference, reference]}
        )
        assert duplicate_refs.status_code == 422
        assert gateway.fetch_calls == 0

        fetched = client.post("/api/sources/fetch", json={"feed_refs": [reference]})
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["total"] == 1
        assert gateway.fetch_calls == 1
