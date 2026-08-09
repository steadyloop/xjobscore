from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import replace

from fastapi.testclient import TestClient

from xjobscore.records import Canonical, Fact, Feed, Item, Posting, Record
from xjobscore.sources.catalog import feed_ref

from oneshot_ui.app import create_app
from oneshot_ui.config import Settings
from oneshot_ui.gateway import GatewayResult


def role(
    external_id: str,
    url: str,
    *,
    state: str = "open",
) -> Posting:
    return Posting(
        external_id=external_id,
        title="Summer Software Engineering Intern",
        url_posting=url,
        apply_url=url,
        company_raw="Lifecycle Labs",
        location_raw="Remote, United States",
        description_html="Requirements: Python. Currently enrolled in computer science.",
        state=state,
        closed_evidence="employer marked role closed" if state == "closed" else None,
    )


class ScriptedGateway:
    def __init__(
        self,
        feed: Feed,
        steps: Sequence[tuple[Record, tuple[Canonical, ...]]],
    ) -> None:
        self.feed = feed
        self.steps = tuple(steps)
        self.index = 0
        self.fetch_calls = 0
        self.known_postings = {
            item.external_id: item
            for _, canonical in self.steps
            for item in canonical
            if isinstance(item, Posting)
        }

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def all_feeds(self) -> list[Feed]:
        return [self.feed]

    async def get_feed(self, feed_id: int) -> Feed | None:
        return self.feed if self.feed.id == feed_id else None

    async def get_feed_ref(self, reference: str) -> Feed | None:
        expected = feed_ref(self.feed.source, self.feed.key)
        return self.feed if reference == expected else None

    async def fetch(self, selected: Feed) -> GatewayResult:
        self.fetch_calls += 1
        record, canonical = self.steps[min(self.index, len(self.steps) - 1)]
        self.index += 1
        return GatewayResult(selected, record, canonical, "available")

    def parse(self, selected: Feed, record: Record) -> GatewayResult:
        canonical = tuple(
            self.known_postings[item.external_id]
            for item in record.items
            if item.external_id in self.known_postings
        )
        return GatewayResult(selected, record, canonical, "available")


def client_for(tmp_path, gateway: ScriptedGateway) -> TestClient:
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


def complete_feed(feed_id: int) -> Feed:
    return Feed(
        id=feed_id,
        source="fixture",
        key=f"board-{abs(feed_id)}",
        config={},
        state={},
        company_id="lifecycle-labs",
        company_name="Lifecycle Labs",
        complete=True,
        closes=True,
    )


def test_raw_known_id_retains_membership_without_creating_non_posting_job(tmp_path) -> None:
    posting = role("known-role", "https://jobs.lifecycle.test/known-role")
    feed = complete_feed(-201)
    gateway = ScriptedGateway(
        feed,
        (
            (
                Record(items=[Item(posting.external_id, {"title": posting.title})], http_status=200),
                (posting,),
            ),
            (
                Record(
                    items=[
                        Item(posting.external_id, {"detail": "parser no longer recognizes this shape"}),
                        Item("board-metadata", {"generated_at": "now"}),
                    ],
                    http_status=200,
                ),
                (),
            ),
        ),
    )

    with client_for(tmp_path, gateway) as client:
        enable_fixture_sources(client)
        reference = feed_ref(feed.source, feed.key)
        assert client.post(f"/api/sources/{reference}/fetch").status_code == 200
        job = client.get("/api/jobs").json()["jobs"][0]
        second = client.post(f"/api/sources/{reference}/fetch")
        assert second.status_code == 200, second.text
        current = client.get(f"/api/jobs/{job['id']}").json()
        assert current["status"] == "open"
        assert client.get("/api/jobs").json()["total"] == 1

    connection = sqlite3.connect(tmp_path / "oneshot.sqlite3")
    try:
        membership = connection.execute(
            "SELECT external_id FROM poll_memberships WHERE poll_id = 2 ORDER BY external_id"
        ).fetchall()
        snapshot = connection.execute(
            "SELECT member_count, status FROM poll_snapshots WHERE poll_id = 2"
        ).fetchone()
        assert membership == [("known-role",)]
        assert snapshot == (1, "applied")
    finally:
        connection.close()


def test_same_result_cooldown_fact_applies_before_posting_scoring(tmp_path) -> None:
    posting = role("cooldown-role", "https://jobs.lifecycle.test/cooldown-role")
    cooldown = Fact(
        company_raw="Lifecycle Labs",
        name="cooldown_until",
        value="2099-01-01T00:00:00+00:00",
    )
    feed = complete_feed(-204)
    gateway = ScriptedGateway(
        feed,
        (
            (
                Record(items=[Item(posting.external_id, {"title": posting.title})], http_status=200),
                (posting, cooldown),
            ),
        ),
    )

    with client_for(tmp_path, gateway) as client:
        enable_fixture_sources(client)
        reference = feed_ref(feed.source, feed.key)
        fetched = client.post(f"/api/sources/{reference}/fetch")
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["ingested"]["facts"] == 1
        job = client.get("/api/jobs").json()["jobs"][0]
        assert job["eligibility"] == "ineligible"
        assert job["score_band"] == "log_only"
        assert any("cooldown" in reason.casefold() for reason in job["eligibility_reasons"])


def test_fact_only_cooldown_refreshes_existing_current_job(tmp_path) -> None:
    posting = role("fact-refresh", "https://jobs.lifecycle.test/fact-refresh")
    cooldown = Fact(
        company_raw="Lifecycle Labs",
        name="cooldown_until",
        value="2099-01-01T00:00:00+00:00",
    )
    feed = replace(complete_feed(-205), complete=False, closes=False)
    gateway = ScriptedGateway(
        feed,
        (
            (
                Record(items=[Item(posting.external_id, {"title": posting.title})], http_status=200),
                (posting,),
            ),
            (
                Record(items=[Item("cooldown-fact", {"until": cooldown.value})], http_status=200),
                (cooldown,),
            ),
        ),
    )

    with client_for(tmp_path, gateway) as client:
        enable_fixture_sources(client)
        reference = feed_ref(feed.source, feed.key)
        client.post(f"/api/sources/{reference}/fetch")
        before = client.get("/api/jobs").json()["jobs"][0]
        assert before["eligibility"] != "ineligible"

        refreshed = client.post(f"/api/sources/{reference}/fetch")
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["ingested"]["facts"] == 1
        after = client.get(f"/api/jobs/{before['id']}").json()
        assert after["eligibility"] == "ineligible"
        assert after["score_band"] == "log_only"
        assert any("cooldown" in reason.casefold() for reason in after["eligibility_reasons"])


def test_profile_put_recomputes_enrollment_location_and_graduation_without_fetch(tmp_path) -> None:
    enrollment = replace(
        role("enrollment", "https://jobs.lifecycle.test/enrollment"),
        description_html=(
            "Requirements: Python. Must be currently enrolled full-time in a "
            "computer science program."
        ),
    )
    location = replace(
        role("location", "https://jobs.lifecycle.test/location"),
        location_raw="New York, NY",
    )
    graduation = replace(
        role("graduation", "https://jobs.lifecycle.test/graduation"),
        description_html=(
            "Requirements: Python. Currently enrolled in computer science; "
            "expected graduation 2027."
        ),
    )
    feed = replace(complete_feed(-206), complete=False, closes=False)
    gateway = ScriptedGateway(
        feed,
        (
            (
                Record(
                    items=[
                        Item(item.external_id, {"title": item.title})
                        for item in (enrollment, location, graduation)
                    ],
                    http_status=200,
                ),
                (enrollment, location, graduation),
            ),
        ),
    )

    with client_for(tmp_path, gateway) as client:
        enable_fixture_sources(client)
        reference = feed_ref(feed.source, feed.key)
        assert client.post(f"/api/sources/{reference}/fetch").status_code == 200
        before = client.get("/api/jobs").json()["jobs"]
        assert len(before) == 3
        assert all(item["eligibility"] == "eligible" for item in before)

        profile = client.get("/api/profile").json()
        profile.update(
            graduation_date="2028-12",
            home_location="San Diego, CA",
            relocate_for_internship=False,
            reduced_enrollment=True,
        )
        updated = client.put("/api/profile", json=profile)
        assert updated.status_code == 200, updated.text
        assert gateway.fetch_calls == 1

        after = {
            item["posting_url"].rsplit("/", 1)[-1]: item
            for item in client.get("/api/jobs").json()["jobs"]
        }
        assert after["enrollment"]["eligibility"] == "ineligible"
        assert after["location"]["eligibility"] == "ineligible"
        assert after["graduation"]["eligibility"] == "ineligible"
        assert any(
            "full-time enrollment" in reason.casefold()
            for reason in after["enrollment"]["eligibility_reasons"]
        )
        assert any(
            "location" in reason.casefold()
            for reason in after["location"]["eligibility_reasons"]
        )
        assert any(
            "graduation" in reason.casefold()
            for reason in after["graduation"]["eligibility_reasons"]
        )

    connection = sqlite3.connect(tmp_path / "oneshot.sqlite3")
    try:
        # One initial and one profile-triggered policy snapshot per job.
        assert connection.execute(
            "SELECT COUNT(*) FROM classification_snapshots"
        ).fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM score_snapshots").fetchone()[0] == 6
    finally:
        connection.close()


def test_profile_recompute_preserves_human_opportunity_override_and_audits_it(tmp_path) -> None:
    ambiguous = replace(
        role("human-override", "https://jobs.lifecycle.test/human-override"),
        title="Software Engineering Intern",
        description_html="Requirements: Python. Currently enrolled in computer science.",
    )
    feed = replace(complete_feed(-207), complete=False, closes=False)
    gateway = ScriptedGateway(
        feed,
        (
            (
                Record(items=[Item(ambiguous.external_id, {"title": ambiguous.title})], http_status=200),
                (ambiguous,),
            ),
        ),
    )

    with client_for(tmp_path, gateway) as client:
        enable_fixture_sources(client)
        reference = feed_ref(feed.source, feed.key)
        client.post(f"/api/sources/{reference}/fetch")
        job = client.get("/api/jobs").json()["jobs"][0]
        corrected = client.post(
            f"/api/jobs/{job['id']}/classification",
            json={
                "opportunity_type": "fall_internship",
                "rationale": "Recruiter confirmed a September–December term.",
            },
        )
        assert corrected.status_code == 200, corrected.text

        profile = client.get("/api/profile").json()
        profile["home_location"] = "Los Angeles, CA"
        assert client.put("/api/profile", json=profile).status_code == 200
        assert gateway.fetch_calls == 1
        current = client.get(f"/api/jobs/{job['id']}").json()
        assert current["opportunity_type_slug"] == "fall_internship"

    connection = sqlite3.connect(tmp_path / "oneshot.sqlite3")
    try:
        latest = connection.execute(
            """
            SELECT opportunity_type, algorithm_version, versions_json
            FROM classification_snapshots
            WHERE job_entity_id = ? ORDER BY rowid DESC LIMIT 1
            """,
            (job["id"],),
        ).fetchone()
        assert latest[0] == "fall_internship"
        assert latest[1] == "human-opportunity-override/1"
        assert "human-opportunity-override/1" in latest[2]
    finally:
        connection.close()
