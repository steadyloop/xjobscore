"""Demo mode: offline start, real pipeline output, and no unfilled data.

These tests deliberately avoid ``oneshot_ui.app``'s module-level import chain
beyond ``create_app`` so they run in a checkout without ``xjobscore.inspector``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from oneshot_ui.app import create_app
from oneshot_ui.config import Settings, load_settings
from oneshot_ui.db import Database
from oneshot_ui.demo import DemoGateway, build_coordinator, seed, seed_if_empty, verify
from oneshot_ui.demo.dataset import DEMO_FEEDS, FAILING_FEED_ID


def _settings(tmp_path) -> Settings:
    return Settings(database_path=tmp_path / "demo.sqlite3", demo=True)


@pytest.fixture
async def seeded(tmp_path):
    database = Database(tmp_path / "demo.sqlite3")
    coordinator = build_coordinator(database)
    await coordinator.initialize()
    try:
        report = await seed(database, coordinator)
        yield database, report
    finally:
        await coordinator.close()


# -- configuration ------------------------------------------------------


def test_demo_flag_selects_a_separate_database(monkeypatch) -> None:
    monkeypatch.delenv("XJOBSCORE_ONESHOT_DB", raising=False)
    monkeypatch.delenv("XJOBSCORE_ONESHOT_DEMO", raising=False)
    ordinary = load_settings()
    monkeypatch.setenv("XJOBSCORE_ONESHOT_DEMO", "1")
    demo = load_settings()
    assert ordinary.demo is False
    assert demo.demo is True
    assert demo.database_path != ordinary.database_path
    assert demo.database_path.name == "demo.sqlite3"
    assert ordinary.database_path.name == "oneshot.sqlite3"


def test_demo_gateway_never_reaches_the_network_or_postgres() -> None:
    gateway = DemoGateway()
    # SimpleCatalog would open an asyncpg connection here; this one holds only
    # the in-process catalog and no HTTP pool.
    assert not hasattr(gateway, "net")
    assert len(gateway.feeds) == len(DEMO_FEEDS)


# -- the seeded corpus --------------------------------------------------


async def test_seed_produces_no_unfilled_data(seeded) -> None:
    database, _ = seeded
    report = await verify(database)
    assert report.empty_tables == [], f"empty tables: {report.empty_tables}"
    assert report.null_columns == [], f"all-NULL columns: {report.null_columns}"


async def test_seed_is_idempotent(seeded, tmp_path) -> None:
    database, first = seeded
    coordinator = build_coordinator(database)
    await coordinator.initialize()
    try:
        assert await seed_if_empty(database, coordinator) is None
    finally:
        await coordinator.gateway.disconnect()
    row = await database.fetch_one(
        "SELECT COUNT(*) AS total FROM job_entities WHERE status != 'merged'"
    )
    assert row is not None and int(row["total"]) == first.jobs


async def test_dedupe_outcomes_come_from_the_real_engine(seeded) -> None:
    _, report = seeded
    # These three are produced by compare_postings, not written by the seeder.
    assert report.counts["exact_links"] == 1
    assert report.counts["review_candidates"] == 1
    assert report.counts["related_candidates"] == 1


async def test_a_dropped_posting_is_unlisted_by_the_reconciler(seeded) -> None:
    database, _ = seeded
    row = await database.fetch_one(
        "SELECT status, absence_count FROM posting_observations WHERE external_id = 'ag-dropped'"
    )
    assert row is not None
    assert row["status"] == "unlisted"
    assert int(row["absence_count"]) == 1


async def test_the_failing_feed_records_a_real_error(seeded) -> None:
    database, _ = seeded
    row = await database.fetch_one(
        "SELECT outcome, error FROM polls WHERE feed_id = ? ORDER BY id DESC LIMIT 1",
        (FAILING_FEED_ID,),
    )
    assert row is not None
    assert row["outcome"] == "error"
    assert "503" in str(row["error"])


# -- the served application ---------------------------------------------


def test_app_starts_offline_and_serves_a_populated_board(tmp_path) -> None:
    application = create_app(_settings(tmp_path))
    with TestClient(application) as client:
        health = client.get("/api/health").json()
        assert health["counts"]["jobs"] > 0
        assert health["counts"]["applications"] == 10

        jobs = client.get("/api/jobs?limit=200").json()
        assert jobs["total"] > 0
        assert all(job["score_components"] for job in jobs["jobs"])

        # Every pipeline column has a card.
        stages = {
            item["stage"] for item in client.get("/api/applications").json()["applications"]
        }
        assert stages == {
            "considering", "preparing", "submitted", "assessment", "screen",
            "interview", "offer", "rejected", "withdrawn", "no_reply",
        }

        # Every Today branch is reachable.
        reasons = {item["today_reason"] for item in client.get("/api/today").json()["items"]}
        assert reasons == {
            "Application action due", "Posting changed", "Posting deadline approaching",
            "Queued to apply", "One-shot review", "Tailored application window",
            "Saved for review",
        }


def test_the_cross_vendor_merge_is_visible_as_two_sources(tmp_path) -> None:
    application = create_app(_settings(tmp_path))
    with TestClient(application) as client:
        jobs = client.get("/api/jobs?limit=200").json()["jobs"]
        merged = [job for job in jobs if job["source_count"] > 1]
        assert len(merged) == 1
        detail = client.get(f"/api/jobs/{merged[0]['id']}").json()
        assert {item["source"] for item in detail["observations"]} == {
            "greenhouse",
            "workday",
        }
        assert {item["requisition_id"] for item in detail["observations"]} == {"NR-4471"}
