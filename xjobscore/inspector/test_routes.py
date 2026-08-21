"""HTTP-level tests over create_app with injected collaborators."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import importlib

import asyncpg
import pytest
from fastapi.testclient import TestClient

from xjobscore import sources
from xjobscore.inspector import validator
from xjobscore.inspector.app import create_app
from xjobscore.inspector.db import feed_ref
from xjobscore.inspector.policy import (
    FeedNotInPostgres,
    PolicyUnavailable,
    PollNowRefused,
)
from xjobscore.records import Feed, Item, Posting, Record

REF = feed_ref("route-test", "demo")
MISSING_SOURCE_REF = feed_ref("route-unregistered", "demo")


class FakePolicy:
    """Recording policy double; snapshot=None simulates degraded mode."""

    def __init__(self, snapshot: dict | None = None, pool=None):
        self.snapshot_value = snapshot
        self.pool = pool
        self.calls: list[tuple] = []
        self.control_value: dict | None = (
            {"running": False, "changed_at": None, "note": None}
            if snapshot is not None
            else None
        )

    def status(self) -> dict:
        connected = self.snapshot_value is not None
        return {"connected": connected, "dsn_configured": connected, "error": None}

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def snapshot(self) -> dict | None:
        return self.snapshot_value

    async def pool_or_none(self):
        # None by default: no PostgreSQL in route tests, so the poll-telemetry
        # reads are skipped entirely -- the degraded state the Scheduler page
        # still has to render correctly, independently of whatever run.py has
        # observed. The Postings tests inject a canned pool instead.
        return self.pool

    async def set_enabled(self, source: str, key: str, enabled: bool) -> dict:
        self.calls.append(("set_enabled", source, key, enabled))
        return {"feed_id": 11, "enabled": enabled}

    async def set_interval(self, source: str, key: str, interval_s: int) -> dict:
        self.calls.append(("set_interval", source, key, interval_s))
        return {"feed_id": 11, "interval_s": interval_s}

    async def set_paused(self, source: str, key: str, paused: bool) -> dict:
        self.calls.append(("set_paused", source, key, paused))
        return {"feed_id": 11, "paused": paused}

    async def set_feeds_enabled(self, identities: list[tuple], enabled: bool) -> dict:
        self.calls.append(("set_feeds_enabled", tuple(identities), enabled))
        return {
            "enabled": enabled,
            "requested": len(identities),
            "affected": len(identities),
        }

    async def import_feed(self, source: str, key: str, enabled: bool = True) -> dict:
        self.calls.append(("import_feed", source, key, enabled))
        return {"feed_id": 11, "enabled": enabled}

    async def import_feeds(self, identities: list[tuple], *, enabled: bool = False) -> dict:
        self.calls.append(("import_feeds", tuple(identities), enabled))
        deduped = list(dict.fromkeys(identities))
        return {
            "requested": len(deduped),
            "imported": len(deduped),
            "existing": 0,
            "enabled": enabled,
        }

    async def poll_now(self, source: str, key: str) -> dict:
        self.calls.append(("poll_now", source, key))
        return {"feed_id": 11, "due_at": "now"}

    async def poll_now_all(self, source: str | None) -> dict:
        self.calls.append(("poll_now_all", source))
        return {"source": source, "updated": 1}

    async def scheduler_control(self) -> dict | None:
        return self.control_value

    async def set_scheduler_running(self, running: bool, note: str | None = None) -> dict:
        self.calls.append(("set_scheduler_running", running, note))
        if self.control_value is None:
            raise PolicyUnavailable("no DSN configured")
        self.control_value = {
            "running": running,
            "changed_at": "2026-01-01T00:00:00+00:00",
            "note": note,
        }
        return {"running": running, "changed_at": self.control_value["changed_at"]}


class FakeRunner:
    """A runner that is alive without a loop, so poll-now's happy path is testable.

    The real SchedulerRunner is inert in every test process
    (config.runner_enabled() defaults off), and "Poll now" now refuses when no
    runner is listening -- so reaching the 200 requires standing one in.
    """

    def __init__(
        self,
        *,
        alive: bool = True,
        poll_result: dict | None = None,
        poll_error: Exception | None = None,
    ) -> None:
        self.alive = alive
        self.nudges = 0
        self.polls: list[tuple[str, str]] = []
        self.poll_result = poll_result
        self.poll_error = poll_error

    def status(self) -> dict:
        return {
            "alive": self.alive,
            "last_cycle_at": None,
            "last_polled": None,
            "consecutive_errors": 0,
            "last_error": None,
        }

    def nudge(self) -> None:
        self.nudges += 1

    async def poll_feed_now(self, source: str, key: str) -> dict:
        self.polls.append((source, key))
        if self.poll_error is not None:
            raise self.poll_error
        return self.poll_result or {"status": "ok", "feed_id": 11, "new_postings": 2}

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class FakeGoldJob:
    """Recording double for GoldJob: lets a test drive state without a real DB."""

    def __init__(self) -> None:
        self.state = "idle"
        self.starts = 0

    def as_dict(self) -> dict:
        return {"state": self.state, "started_at": None, "finished_at": None,
                "counts": None, "error": None}

    def start(self) -> None:
        if self.state == "running":
            raise RuntimeError("dedup rebuild already running")
        self.starts += 1
        self.state = "running"


class FakeCatalog:
    def __init__(self, *feeds: Feed):
        self.feeds = list(feeds)

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def all_feeds(self) -> list[Feed]:
        return list(self.feeds)

    async def get_feed(self, feed_id: int) -> Feed | None:
        return next((feed for feed in self.feeds if feed.id == feed_id), None)

    async def get_feed_ref(self, ref: str) -> Feed | None:
        return next(
            (feed for feed in self.feeds if feed_ref(feed.source, feed.key) == ref),
            None,
        )


@pytest.fixture
def client(monkeypatch, app_store):
    async def harvest(_feed, _net):
        return Record(
            items=[Item("job-1", {"title": "Demo", "location": "NYC"})],
            envelope={"company": "Acme"},
            http_status=200,
        )

    def parse(_feed, item, _envelope):
        return [
            Posting(
                external_id=item.external_id,
                title=item.payload["title"],
                url_posting="https://jobs.example/1",
                company_raw="Acme",
            )
        ]

    monkeypatch.setitem(sources.SOURCES, "route-test", harvest)
    monkeypatch.setitem(sources.PARSERS, "route-test", parse)
    # Route tests exercise HTTP behavior, not the registry company mirror.
    monkeypatch.setattr("xjobscore.inspector.persistence.registry_companies", tuple)
    app = create_app(
        catalog=FakeCatalog(
            Feed(id=1, source="route-test", key="demo", config={}),
            Feed(id=2, source="route-unregistered", key="demo", config={}),
        ),
        store=app_store,
        user_agent="route-test",
        policy=FakePolicy(),
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def gold_client(app_store):
    """A client whose GoldJob is a recording double, for single-flight semantics."""
    app = create_app(
        catalog=FakeCatalog(),
        store=app_store,
        user_agent="route-test",
        policy=FakePolicy(),
        gold_job=FakeGoldJob(),
    )
    with TestClient(app) as test_client:
        yield test_client


def test_gold_dedup_single_flight(gold_client):
    first = gold_client.post("/api/gold/dedup")
    assert first.status_code == 200
    assert first.json()["state"] == "running"

    second = gold_client.post("/api/gold/dedup")
    assert second.status_code == 409

    assert gold_client.app.state.gold_job.starts == 1
    assert gold_client.get("/api/gold/dedup").json()["state"] == "running"


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert set(body["postgres"]) == {"connected", "dsn_configured", "error"}


def test_feeds_lists_configured_rows(client):
    payload = client.get("/api/feeds").json()
    rows = payload["feeds"]
    assert set(payload["postgres"]) == {"connected", "dsn_configured", "error"}
    row = next(row for row in rows if row["feed_ref"] == REF)

    assert row["source"] == "route-test"
    assert row["configured"] is True
    assert row["source_registered"] is True
    assert row["last_result"] is None
    assert row["profile"] == {
        "adapter": "bespoke", "formats": [], "flavor": None, "archetypes": [],
    }


def test_feeds_carry_last_result_after_a_fetch(client):
    client.post(f"/api/feeds/{REF}/fetch")
    rows = client.get("/api/feeds").json()["feeds"]
    row = next(row for row in rows if row["feed_ref"] == REF)

    assert row["last_result"]["item_count"] == 1
    assert row["last_result"]["status"] == "ok"
    assert row["last_result"]["elapsed_ms"] is not None


def test_fetch_result_item_envelope_round_trip(client):
    summary = client.post(f"/api/feeds/{REF}/fetch").json()
    assert summary["status"] == "ok"
    assert summary["item_count"] == 1

    result = client.get(f"/api/results/{REF}").json()
    assert result["result"]["result_id"] == summary["result_id"]
    assert result["envelope"] == {"company": "Acme"}
    assert result["items"][0]["external_id"] == "job-1"
    assert result["has_more"] is False
    assert "summary" in result["network"]
    assert result["profile"]["adapter"] == "bespoke"
    # The fake harvester never touches the network, so no content-type exists.
    assert result["wire_content_type"] is None

    item = client.get(f"/api/results/{REF}/items/0").json()
    assert item["payload"]["title"] == "Demo"

    envelope = client.get(f"/api/results/{REF}/envelope").json()
    assert envelope["envelope"] == {"company": "Acme"}


def test_parsed_round_trip(client):
    client.post(f"/api/feeds/{REF}/fetch")

    parsed = client.get(f"/api/results/{REF}/parsed").json()
    assert parsed["status"] == "ok"
    assert parsed["summary"]["postings"] == 1
    assert parsed["items"][0]["canonical_count"] == 1

    detail = client.get(f"/api/results/{REF}/parsed/0").json()
    canonical = detail["canonicals"][0]
    assert canonical["type"] == "Posting"
    assert canonical["classification"] == "posting"
    # No alias mirror and no feed company: silver would park this posting.
    assert canonical["company_resolution"]["status"] == "would_park"
    assert canonical["title_normalized"] == "demo"

    assert client.get(f"/api/results/{REF}/parsed/9").status_code == 404


def test_result_parsed_only_filters_and_paginates_server_side(client, monkeypatch):
    async def harvest(_feed, _net):
        return Record(
            items=[
                Item("job-1", {"title": "Kept"}),
                Item("job-2", {"title": "Filtered"}),
                Item("job-3", {"title": "Kept too"}),
            ],
            envelope={"company": "Acme"},
            http_status=200,
        )

    def parse(_feed, item, _envelope):
        if item.external_id == "job-2":
            return []
        return [
            Posting(
                external_id=item.external_id,
                title=item.payload["title"],
                url_posting="https://jobs.example/1",
                company_raw="Acme",
            )
        ]

    monkeypatch.setitem(sources.SOURCES, "route-test", harvest)
    monkeypatch.setitem(sources.PARSERS, "route-test", parse)
    client.post(f"/api/feeds/{REF}/fetch")

    unfiltered = client.get(f"/api/results/{REF}").json()
    assert [item["external_id"] for item in unfiltered["items"]] == ["job-1", "job-2", "job-3"]
    assert unfiltered["total_count"] == 3

    filtered = client.get(f"/api/results/{REF}?parsed_only=true").json()
    assert [item["external_id"] for item in filtered["items"]] == ["job-1", "job-3"]
    assert filtered["total_count"] == 2
    assert filtered["has_more"] is False

    first_page = client.get(f"/api/results/{REF}?parsed_only=true&limit=1").json()
    assert [item["external_id"] for item in first_page["items"]] == ["job-1"]
    assert first_page["has_more"] is True

    second_page = client.get(f"/api/results/{REF}?parsed_only=true&limit=1&offset=1").json()
    assert [item["external_id"] for item in second_page["items"]] == ["job-3"]
    assert second_page["has_more"] is False


def test_run_history_and_network_endpoints(client):
    old = client.post(f"/api/feeds/{REF}/fetch").json()
    latest = client.post(f"/api/feeds/{REF}/fetch").json()

    runs = client.get(f"/api/feeds/{REF}/runs").json()["runs"]
    assert len(runs) == 2
    assert runs[0]["trigger"] == "manual"

    network = client.get(f"/api/results/{REF}/network").json()
    assert "summary" in network and "requests" in network

    assert client.get(f"/api/runs/{old['result_id']}/network").status_code == 404
    by_id = client.get(f"/api/runs/{latest['result_id']}/network")
    assert by_id.status_code == 200
    assert client.get("/api/runs/nope/network").status_code == 404

    stats = client.get("/api/stats/sources").json()["sources"]
    assert stats[0]["source"] == "route-test"
    assert stats[0]["run_count"] == 2


def test_result_before_any_fetch_is_404(client):
    assert client.get(f"/api/results/{REF}").status_code == 404
    assert client.get(f"/api/results/{REF}/items/0").status_code == 404


def test_fetch_unknown_ref_is_404(client):
    assert client.post(f"/api/feeds/{'0' * 64}/fetch").status_code == 404


def test_fetch_unregistered_source_is_422(client):
    response = client.post(f"/api/feeds/{MISSING_SOURCE_REF}/fetch")
    assert response.status_code == 422
    assert "route-unregistered" in response.json()["detail"]


def test_item_index_out_of_range_is_404(client):
    client.post(f"/api/feeds/{REF}/fetch")
    assert client.get(f"/api/results/{REF}/items/5").status_code == 404


def test_watch_and_scheduler_control_endpoints_are_gone(client):
    assert client.post(f"/api/feeds/{REF}/watch", json={"watched": True}).status_code == 404
    assert client.post("/api/watch", json={"source": "route-test", "watched": True}).status_code == 404
    assert client.get("/api/scheduler").status_code == 404
    assert client.post("/api/scheduler", json={"action": "resume"}).status_code == 404

    health = client.get("/api/health").json()
    assert "scheduler" not in health


def _imported_app(monkeypatch, app_store, runner=None):
    """Four feeds covering every Scheduler membership case.

    route-test:demo is enabled and belongs on the Scheduler; route-off:demo is
    in PostgreSQL but switched off; route-unimported:demo has no row at all.
    route-paused:demo is enabled *and* held -- pausing must not evict a feed,
    so it belongs on the Scheduler exactly like route-test:demo.
    """
    for source in ("route-test", "route-off", "route-paused", "route-unimported"):
        monkeypatch.setitem(sources.SOURCES, source, lambda f, n: None)
    monkeypatch.setattr("xjobscore.inspector.persistence.registry_companies", tuple)
    return create_app(
        catalog=FakeCatalog(
            Feed(id=1, source="route-test", key="demo", config={}),
            Feed(id=2, source="route-off", key="demo", config={}),
            Feed(id=3, source="route-paused", key="demo", config={}),
            Feed(id=4, source="route-unimported", key="demo", config={}),
        ),
        store=app_store,
        user_agent="route-test",
        policy=FakePolicy(
            snapshot={
                ("route-test", "demo"): {"enabled": True, "interval_s": 900},
                ("route-off", "demo"): {"enabled": False, "interval_s": 900},
                ("route-paused", "demo"): {
                    "enabled": True,
                    "interval_s": 900,
                    "paused_at": "2026-08-14T12:00:00+00:00",
                },
            }
        ),
        runner=runner,
    )


@pytest.fixture
def imported_client(monkeypatch, app_store):
    with TestClient(_imported_app(monkeypatch, app_store)) as test_client:
        yield test_client


@pytest.fixture
def polling_client(monkeypatch, app_store):
    """imported_client with a runner that reports itself alive, for poll-now."""
    app = _imported_app(monkeypatch, app_store, runner=FakeRunner())
    with TestClient(app) as test_client:
        yield test_client


def test_scheduler_overview_without_telemetry(imported_client):
    """No poll_observation data must read as unknown, never as zero activity."""
    body = imported_client.get("/api/scheduler/overview").json()
    assert set(body) >= {
        "postgres",
        "telemetry_available",
        "feed_count",
        "tiles",
        "counts_by_status",
    }
    assert body["telemetry_available"] is False
    assert body["tiles"] is None  # not {"requests_today": 0, ...}
    assert body["feed_count"] == 2  # the enabled feed and the enabled-but-paused one
    assert sum(body["counts_by_status"].values()) == body["feed_count"]


def test_scheduler_feeds_carry_estimates_and_show_only_enabled(imported_client):
    body = imported_client.get("/api/scheduler/feeds").json()
    assert body["telemetry_available"] is False
    # feed.enabled is the membership test: neither the switched-off feed nor the
    # unimported one is being polled, so neither belongs here. The paused feed is
    # still enabled, so it does.
    assert {row["feed_ref"] for row in body["feeds"]} == {
        REF,
        feed_ref("route-paused", "demo"),
    }

    # ...while the Feeds page keeps every configured feed, since that is where
    # the toggle lives -- and that toggle is the only way onto this page.
    all_refs = {
        row["feed_ref"]
        for row in imported_client.get("/api/feeds").json()["feeds"]
        if row["feed_ref"]
    }
    assert feed_ref("route-off", "demo") in all_refs
    assert feed_ref("route-unimported", "demo") in all_refs

    row = next(row for row in body["feeds"] if row["feed_ref"] == REF)
    assert row["telemetry"] is None
    assert row["spark"] is None
    sched = row["sched"]
    # Unknown rate => no cadence recommendation, and no verdict to act on.
    assert sched["lambda"] is None
    assert sched["ideal_interval_s"] is None
    assert sched["verdict"] is None
    assert sched["requests_per_day"] == 96  # 900 s
    assert sched["status"]["state"] == "never_polled"
    assert sched["status"]["chip"] in {"ok", "not_modified", "partial", "error", "never"}

    filtered = imported_client.get(
        "/api/scheduler/feeds", params={"source": "route-test"}
    )
    assert {row["source"] for row in filtered.json()["feeds"]} == {"route-test"}


def test_paused_feed_stays_on_the_scheduler_and_says_so(imported_client):
    """Pausing holds polling without costing you the feed -- the whole feature."""
    ref = feed_ref("route-paused", "demo")
    body = imported_client.get("/api/scheduler/feeds").json()
    row = next(row for row in body["feeds"] if row["feed_ref"] == ref)

    assert row["policy"]["enabled"] is True
    assert row["policy"]["paused_at"] == "2026-08-14T12:00:00+00:00"
    assert row["sched"]["status"]["state"] == "paused"

    # And the detail route serves it too, so Resume is reachable.
    assert imported_client.get(f"/api/scheduler/feeds/{ref}").status_code == 200


def test_pausing_is_forwarded_and_an_empty_body_is_still_rejected(imported_client):
    response = imported_client.post(f"/api/feeds/{REF}/policy", json={"paused": True})
    assert response.status_code == 200
    assert response.json()["paused"] is True
    assert imported_client.post(f"/api/feeds/{REF}/policy", json={}).status_code == 400


def test_bulk_policy_covers_feeds_that_were_never_imported(imported_client):
    """The per-vendor switch has to reach catalog-only feeds.

    Gating the batch on in_postgres is what made it a no-op: on the source that
    matters (Ashby, 2,591 of 2,626 feeds) nothing is imported until this flip
    imports it, so an unimported ref must be forwarded, not filtered out.
    """
    unimported = feed_ref("route-unimported", "demo")
    response = imported_client.post(
        "/api/feeds/bulk-policy",
        json={"refs": [REF, unimported], "enabled": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requested"] == 2
    assert body["unknown"] == []

    policy = imported_client.app.state.policy
    call = next(c for c in policy.calls if c[0] == "set_feeds_enabled")
    assert call[1] == (("route-test", "demo"), ("route-unimported", "demo"))
    assert call[2] is True


def test_bulk_policy_reports_unknown_refs_without_failing_the_batch(imported_client):
    """A stale tab must not turn a valid flip into a 404 for every feed."""
    response = imported_client.post(
        "/api/feeds/bulk-policy",
        json={"refs": [REF, "nope:nope"], "enabled": False},
    )
    assert response.status_code == 200
    assert response.json()["unknown"] == ["nope:nope"]

    assert (
        imported_client.post(
            "/api/feeds/bulk-policy", json={"refs": ["nope:nope"], "enabled": True}
        ).status_code
        == 404
    )
    assert (
        imported_client.post(
            "/api/feeds/bulk-policy", json={"refs": [], "enabled": True}
        ).status_code
        == 400
    )


def test_scheduler_feed_detail(imported_client):
    body = imported_client.get(f"/api/scheduler/feeds/{REF}").json()
    assert body["feed"]["feed_ref"] == REF
    assert body["events"] == []
    assert imported_client.get("/api/scheduler/feeds/nope:nope").status_code == 404

    # The membership rule applies to the detail route too, not just the list.
    off = feed_ref("route-off", "demo")
    assert imported_client.get(f"/api/scheduler/feeds/{off}").status_code == 404


def test_scheduler_survives_degraded_postgres(client):
    """Degraded policy: the page still answers 200 rather than flashing empty."""
    overview = client.get("/api/scheduler/overview")
    assert overview.status_code == 200
    assert overview.json()["telemetry_available"] is False

    feeds = client.get("/api/scheduler/feeds").json()["feeds"]
    row = next(row for row in feeds if row["feed_ref"] == REF)
    assert row["policy"] is None
    assert row["sched"]["status"]["state"] == "unknown"


def test_scheduler_control_round_trip(imported_client):
    body = imported_client.get("/api/scheduler/control").json()
    assert body["control"] == {"running": False, "changed_at": None, "note": None}
    assert body["runner"]["alive"] is False  # config.runner_enabled() defaults off

    set_body = imported_client.post("/api/scheduler/control", json={"running": True}).json()
    assert set_body["control"]["running"] is True
    assert set_body["control"]["changed_at"] is not None

    # The overview response folds the same control/runner state in, so the
    # Scheduler page's single poll picks up a pause/resume without a second call.
    overview = imported_client.get("/api/scheduler/overview").json()
    assert overview["control"]["running"] is True
    assert overview["runner"]["alive"] is False


def test_scheduler_control_degraded_postgres(client):
    """Degraded policy: reads answer with control=None, writes 503 like every other policy mutation."""
    body = client.get("/api/scheduler/control").json()
    assert body["control"] is None

    response = client.post("/api/scheduler/control", json={"running": True})
    assert response.status_code == 503


def test_runner_does_not_start_under_default_config(client):
    """The env-var gate (config.runner_enabled()) defaults off in every test process."""
    assert client.app.state.scheduler_runner.status()["alive"] is False


def test_poll_now_all_accepts_an_absent_body(imported_client):
    """An empty POST means "every feed", not 422.

    The body carries one optional filter, so requiring it turns the common
    case -- bring everything forward -- into a client error. Matches
    /api/feeds/{ref}/import, whose body is optional for the same reason.
    """
    for request in ({}, {"json": {}}, {"json": {"source": "route-test"}}):
        response = imported_client.post("/api/scheduler/poll-now", **request)
        assert response.status_code == 200, request

    assert imported_client.post("/api/scheduler/poll-now").json()["source"] is None


def test_poll_now_one_feed(polling_client):
    """The happy path writes the queue position *and* wakes the runner for it.

    Without the nudge the operator waits out a whole tick (60s by default) for
    a button labelled "now", which is the latency this endpoint exists to
    remove.
    """
    runner = polling_client.app.state.scheduler_runner
    body = polling_client.post(f"/api/scheduler/feeds/{REF}/poll-now").json()

    assert body["runner_alive"] is True
    assert runner.nudges == 1
    assert ("poll_now", "route-test", "demo") in polling_client.app.state.policy.calls
    assert (
        polling_client.post("/api/scheduler/feeds/nope:nope/poll-now").status_code == 404
    )


def test_poll_now_refuses_when_no_runner_is_listening(imported_client):
    """A 200 here would promise a poll that nothing in any process would claim.

    config.runner_enabled() is off in every test process, which is also the
    default deployment -- so this is the state an operator hits first.
    """
    response = imported_client.post(f"/api/scheduler/feeds/{REF}/poll-now")

    assert response.status_code == 409
    assert response.json()["detail"]["hint"] == "no_runner"
    # Refused before any write: the queue position is left exactly as it was.
    assert imported_client.app.state.policy.calls == []


def test_poll_now_all_still_queues_without_a_runner(imported_client):
    """The bulk button means "queue the catalog", not "poll it now" -- unchanged."""
    body = imported_client.post("/api/scheduler/poll-now").json()

    assert body["updated"] == 1
    assert body["runner_alive"] is False


def test_scheduler_fetch_polls_one_feed_through_the_runner(polling_client):
    """Fetch all's per-feed request: production poll, answered when done."""
    runner = polling_client.app.state.scheduler_runner
    body = polling_client.post(f"/api/scheduler/feeds/{REF}/fetch").json()

    assert body == {"status": "ok", "feed_id": 11, "new_postings": 2}
    assert runner.polls == [("route-test", "demo")]


def test_scheduler_fetch_unknown_ref_is_404(polling_client):
    response = polling_client.post("/api/scheduler/feeds/nope:nope/fetch")

    assert response.status_code == 404
    assert polling_client.app.state.scheduler_runner.polls == []


def test_scheduler_fetch_needs_no_runner_loop(monkeypatch, app_store):
    """The contract Fetch all exists for: the browser is the scheduler.

    alive=False is every default deployment (config.runner_enabled() off);
    poll-now refuses there, this endpoint must not.
    """
    app = _imported_app(monkeypatch, app_store, runner=FakeRunner(alive=False))
    with TestClient(app) as test_client:
        response = test_client.post(f"/api/scheduler/feeds/{REF}/fetch")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize(
    ("error", "status", "hint"),
    [
        (PollNowRefused("paused", reason="feed_paused"), 409, "feed_paused"),
        (PollNowRefused("held", reason="already_polling"), 409, "already_polling"),
        (PollNowRefused("off", reason="feed_disabled"), 409, "feed_disabled"),
        (FeedNotInPostgres("route-test:demo"), 409, "import"),
    ],
)
def test_scheduler_fetch_refusals_carry_their_reason(
    monkeypatch, app_store, error, status, hint
):
    app = _imported_app(monkeypatch, app_store, runner=FakeRunner(poll_error=error))
    with TestClient(app) as test_client:
        response = test_client.post(f"/api/scheduler/feeds/{REF}/fetch")

    assert response.status_code == status
    assert response.json()["detail"]["hint"] == hint


def test_scheduler_fetch_degraded_postgres_is_503(monkeypatch, app_store):
    runner = FakeRunner(poll_error=PolicyUnavailable("PostgreSQL unavailable"))
    app = _imported_app(monkeypatch, app_store, runner=runner)
    with TestClient(app) as test_client:
        assert test_client.post(f"/api/scheduler/feeds/{REF}/fetch").status_code == 503


def test_scheduler_fetch_harvest_failure_is_a_200(monkeypatch, app_store):
    """A poll that ran and failed is an outcome, not an HTTP error."""
    runner = FakeRunner(poll_result={"status": "error", "feed_id": 11, "new_postings": None})
    app = _imported_app(monkeypatch, app_store, runner=runner)
    with TestClient(app) as test_client:
        body = test_client.post(f"/api/scheduler/feeds/{REF}/fetch").json()

    assert body["status"] == "error"
    assert body["new_postings"] is None


def test_scheduler_page_is_served(client):
    assert client.get("/scheduler").status_code == 200


def test_postings_page_offers_duplicates_instead_of_any_status(client):
    response = client.get("/postings")

    assert response.status_code == 200
    assert '<option value="duplicates">Duplicate review</option>' in response.text
    assert '<option value="removal">Marked for removal</option>' in response.text
    assert "Any status" not in response.text


def test_retired_postings_old_routes_are_not_found(client):
    for path in (
        "/postings-old",
        "/api/postings-old",
        "/api/postings-old/a-result/items/0",
        "/static/postings-old/index.html",
        "/static/postings-old/app.js",
    ):
        assert client.get(path).status_code == 404


# --------------------------------------------------------------- postings

class _FakeQueryTransaction:
    """Records whether the query page's connection was ever taken to a
    read-only, always-rolled-back transaction -- see query.run()."""

    def __init__(self, *, readonly):
        self.readonly = readonly
        self.started = False
        self.rolled_back = False
        self.committed = False

    async def start(self):
        self.started = True

    async def rollback(self):
        self.rolled_back = True

    async def commit(self):  # pragma: no cover -- must never be called
        self.committed = True


class _FakeQueryCursor:
    def __init__(self, records):
        self._records = records

    async def fetch(self, n):
        return self._records[:n]


class _FakeAttr:
    def __init__(self, name, type_name):
        self.name = name
        self.type = type("Type", (), {"name": type_name})


class _FakeQueryPrepared:
    def __init__(self, columns, records):
        self._attrs = [_FakeAttr(name, "text") for name in columns]
        self._records = records

    def get_attributes(self):
        return self._attrs

    async def cursor(self):
        return _FakeQueryCursor(self._records)


class FakePublicPool:
    """Canned job_posting pool: first fetch answers the vendor list, the
    second the page itself; fetchrow answers the detail query.

    Doubles as the Query page's connection (``acquire()`` yields ``self``,
    same as everywhere else here) -- ``transaction``/``execute``/``prepare``
    below stand in for the read-only-transaction protocol ``query.run()``
    drives.
    """

    def __init__(
        self,
        rows=(),
        row=None,
        fingerprint=None,
        schema_rows=(),
        query_columns=(),
        query_rows=(),
        prepare_error=None,
    ):
        self.fetches = [[{"vendor": "route-test"}], list(rows)]
        self.row = row
        self.fingerprint = fingerprint
        self.schema_rows = list(schema_rows)
        self.query_columns = list(query_columns)
        self.query_rows = list(query_rows)
        self.prepare_error = prepare_error
        self.transactions: list[_FakeQueryTransaction] = []
        self.executed: list[str] = []
        self.prepared_sql: str | None = None

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetch(self, sql, *args):
        if "information_schema" in sql:
            return self.schema_rows
        return self.fetches.pop(0) if self.fetches else []

    async def fetchrow(self, sql, *args):
        return self.row

    async def fetchval(self, sql, *args):
        return self.fingerprint

    def transaction(self, *, readonly=False, **kwargs):
        tr = _FakeQueryTransaction(readonly=readonly)
        self.transactions.append(tr)
        return tr

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def prepare(self, sql):
        self.prepared_sql = sql
        if self.prepare_error is not None:
            raise self.prepare_error
        return _FakeQueryPrepared(self.query_columns, self.query_rows)


def posting_row(**overrides):
    row = {
        "id": 1,
        "source": "route-test:demo",
        "source_key": "job-1",
        "company": "Acme",
        "title": "ML Intern",
        "location_text": "NYC",
        "source_url": "https://jobs.example/1",
        "apply_url": None,
        "posted_at": None,
        "status": "active",
        "first_seen_at": datetime(2026, 8, 15, tzinfo=timezone.utc),
        "last_seen_at": datetime(2026, 8, 15, tzinfo=timezone.utc),
        "attributes": {"version": 1},
        "dup_group_id": None,
        "has_description": False,
        "total": 1,
    }
    row.update(overrides)
    return row


@pytest.fixture
def postings_client(monkeypatch, app_store):
    """A client whose policy carries a canned public pool, store is real PG."""

    def build(pool):
        monkeypatch.setattr(
            "xjobscore.inspector.persistence.registry_companies", tuple
        )
        app = create_app(
            catalog=FakeCatalog(),
            store=app_store,
            user_agent="route-test",
            policy=FakePolicy(pool=pool),
        )
        return TestClient(app)

    return build


def test_postings_503_without_public_pool(client):
    # The default FakePolicy has no pool: both reads must answer degraded,
    # not empty -- an unreachable database is not "no postings".
    assert client.get("/api/postings").status_code == 503
    assert client.get("/api/postings/1").status_code == 503
    assert client.get("/api/duplicate-groups").status_code == 503
    assert client.post(
        "/api/duplicate-groups/00000000-0000-0000-0000-000000000001/review",
        json={"decision": "correct"},
    ).status_code == 503


def test_postings_rejects_unknown_enums(postings_client):
    with postings_client(FakePublicPool()) as test_client:
        for params in ({"label_state": "bogus"}, {"order": "bogus"}, {"status": "bogus"}):
            assert test_client.get("/api/postings", params=params).status_code == 400

        assert test_client.get(
            "/api/duplicate-groups", params={"review_state": "pending"}
        ).status_code == 400


def test_postings_list_shape(postings_client):
    with postings_client(FakePublicPool(rows=[posting_row()])) as test_client:
        body = test_client.get("/api/postings").json()

    assert body["total"] == 1
    assert body["sources"] == ["route-test"]
    (row,) = body["rows"]
    assert row["title"] == "ML Intern"
    assert row["label"] is None
    assert "total" not in row
    assert body["label_counts"]["total"] == 0


def test_postings_removal_status_is_accepted(postings_client):
    with postings_client(FakePublicPool(rows=[posting_row()])) as test_client:
        response = test_client.get("/api/postings", params={"status": "removal"})

    assert response.status_code == 200
    assert response.json()["filters"]["status"] == "removal"


def test_posting_detail_404_when_unknown(postings_client):
    with postings_client(FakePublicPool(row=None)) as test_client:
        assert test_client.get("/api/postings/999").status_code == 404


def test_posting_labels_roundtrip(postings_client):
    """Labels written through the API come back merged into the list."""
    pool = FakePublicPool(rows=[posting_row()])
    with postings_client(pool) as test_client:
        written = test_client.post(
            "/api/postings/labels",
            json={"labels": [{
                "source": "route-test:demo",
                "source_key": "job-1",
                "label": "relevant",
                "note": "looks right",
                "title_snapshot": "ML Intern",
            }]},
        )
        assert written.json() == {"written": 1, "cleared": 0}

        pool.fetches = [[{"vendor": "route-test"}], [posting_row()]]
        (row,) = test_client.get("/api/postings").json()["rows"]
        assert row["label"] == "relevant"
        assert row["note"] == "looks right"

        cleared = test_client.post(
            "/api/postings/labels",
            json={"labels": [{
                "source": "route-test:demo",
                "source_key": "job-1",
                "label": None,
            }]},
        )
        assert cleared.json() == {"written": 0, "cleared": 1}


def test_posting_labels_rejects_empty_and_unknown_values(client):
    assert client.post("/api/postings/labels", json={"labels": []}).status_code == 400
    bad = client.post(
        "/api/postings/labels",
        json={"labels": [{"source": "s", "source_key": "k", "label": "great"}]},
    )
    assert bad.status_code == 422


# ------------------------------------------------------------- Review page

class FakeReviewPool:
    """Canned opus-review pool.

    review.query() makes three fetches in order -- vendor list, the
    unfiltered _COUNTS rows, then the page itself -- one more than
    FakePublicPool's two, so this is a separate double rather than a reuse.
    """

    def __init__(self, rows=(), counts=(), row=None):
        self.fetches = [[{"vendor": "route-test"}], list(counts), list(rows)]
        self.row = row
        self.transactions: list[bool] = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetch(self, sql, *args):
        return self.fetches.pop(0) if self.fetches else []

    async def fetchrow(self, sql, *args):
        return self.row

    @asynccontextmanager
    async def transaction(self):
        self.transactions.append(True)
        yield


def review_row(**overrides):
    row = {
        "id": 1,
        "source": "route-test:demo",
        "source_key": "job-1",
        "company": "Etched",
        "title": "ML Systems Intern",
        "location_text": "SF",
        "source_url": "https://jobs.example/1",
        "apply_url": None,
        "posted_at": None,
        "status": "active",
        "job_type": "intern",
        "cs_field": True,
        "ck_list": "ai:infra",
        "has_description": False,
        "attributes": {
            "version": 1,
            "opus": "tech",
            "opus_by": "machine",
            "opus_machine": "tech",
        },
        "total": 1,
    }
    row.update(overrides)
    return row


def review_count_row(**overrides):
    row = {"label": "tech", "opus_by": "machine", "changed": False}
    row.update(overrides)
    return row


def test_review_page_is_served(client):
    assert client.get("/review").status_code == 200


def test_review_503_without_public_pool(client):
    # The default FakePolicy has no pool: both reads must answer degraded,
    # not empty -- an unreachable database is not "nothing to review".
    assert client.get("/api/review").status_code == 503
    assert client.get("/api/review/1").status_code == 503
    assert client.post(
        "/api/review/labels", json={"labels": [{"id": 1, "label": "tech"}]}
    ).status_code == 503


def test_review_rejects_unknown_enums(postings_client):
    with postings_client(FakeReviewPool()) as test_client:
        for params in (
            {"label": "bogus"},
            {"state": "bogus"},
            {"order": "bogus"},
        ):
            assert test_client.get("/api/review", params=params).status_code == 400


def test_review_labels_rejects_unknown_bucket(client):
    bad = client.post(
        "/api/review/labels",
        json={"labels": [{"id": 1, "label": "great"}]},
    )
    assert bad.status_code == 422


def test_review_labels_rejects_empty(client):
    assert client.post("/api/review/labels", json={"labels": []}).status_code == 400


def test_review_defaults_to_unreviewed(postings_client):
    with postings_client(FakeReviewPool()) as test_client:
        response = test_client.get("/api/review")
    assert response.json()["filters"]["state"] == "unreviewed"


def test_review_list_shape(postings_client):
    pool = FakeReviewPool(rows=[review_row()], counts=[review_count_row()])
    with postings_client(pool) as test_client:
        body = test_client.get("/api/review").json()

    assert body["total"] == 1
    assert body["sources"] == ["route-test"]
    (row,) = body["rows"]
    assert row["title"] == "ML Systems Intern"
    assert row["opus"] == "tech"
    assert row["opus_by"] == "machine"
    assert row["opus_machine"] == "tech"
    assert row["cs_field"] is True
    assert row["ck_list"] == "ai:infra"
    assert "attributes" not in row
    assert "total" not in row
    assert body["label_counts"]["tech"] == 1
    assert body["state_counts"]["unreviewed"] == 1


def test_review_detail_404_when_unknown(postings_client):
    with postings_client(FakeReviewPool(row=None)) as test_client:
        assert test_client.get("/api/review/999").status_code == 404


def test_review_labels_write_reaches_the_pool(postings_client):
    pool = FakeReviewPool(row={"id": 1})
    with postings_client(pool) as test_client:
        response = test_client.post(
            "/api/review/labels", json={"labels": [{"id": 1, "label": "non-tech"}]}
        )
    assert response.status_code == 200
    assert response.json() == {"written": 1}
    assert pool.transactions  # went through conn.transaction()


def test_duplicate_group_list_contract(monkeypatch, postings_client):
    async def fake_query(pool, reviews, **kwargs):
        assert kwargs["review_state"] == "needs_review"
        return {
            "sources": ["route-test"],
            "filters": kwargs,
            "total": 1,
            "offset": kwargs["offset"],
            "limit": kwargs["limit"],
            "review_counts": {
                "all": 1,
                "unreviewed": 0,
                "correct": 0,
                "incorrect": 0,
                "needs_review": 1,
                "hidden": 0,
            },
            "groups": [{"id": "00000000-0000-0000-0000-000000000001"}],
        }

    app_module = importlib.import_module("xjobscore.inspector.app")
    monkeypatch.setattr(app_module, "duplicate_group_query", fake_query)
    with postings_client(FakePublicPool()) as test_client:
        response = test_client.get(
            "/api/duplicate-groups", params={"review_state": "needs_review"}
        )

    assert response.status_code == 200
    assert response.json()["groups"][0]["id"].endswith("0001")


def test_duplicate_group_review_write_and_clear(postings_client):
    group_id = "00000000-0000-0000-0000-000000000001"
    with postings_client(FakePublicPool(fingerprint="fingerprint")) as test_client:
        saved = test_client.post(
            f"/api/duplicate-groups/{group_id}/review",
            json={"decision": "correct", "note": " same opening "},
        )
        hidden = test_client.post(
            f"/api/duplicate-groups/{group_id}/review",
            json={"decision": "hidden"},
        )
        cleared = test_client.post(
            f"/api/duplicate-groups/{group_id}/review",
            json={"decision": None},
        )

    assert saved.status_code == 200
    assert saved.json()["review"] == {"decision": "correct", "note": "same opening"}
    assert hidden.status_code == 200
    assert hidden.json()["review"] == {"decision": "hidden", "note": None}
    assert cleared.status_code == 200
    assert cleared.json()["review"] is None


def test_duplicate_group_review_rejects_unknown_and_missing(postings_client):
    group_id = "00000000-0000-0000-0000-000000000001"
    with postings_client(FakePublicPool(fingerprint=None)) as test_client:
        assert test_client.post(
            f"/api/duplicate-groups/{group_id}/review",
            json={"decision": "correct"},
        ).status_code == 404
        assert test_client.post(
            f"/api/duplicate-groups/{group_id}/review",
            json={"decision": "confirmed"},
        ).status_code == 422


# --------------------------------------------------------------------- query

def test_query_page_is_served(client):
    assert client.get("/query").status_code == 200


def test_query_503_without_public_pool(client):
    assert client.post("/api/query", json={"sql": "select 1"}).status_code == 503
    assert client.get("/api/query/schema").status_code == 503


def test_query_run_returns_columns_and_rows(postings_client):
    pool = FakePublicPool(query_columns=["id", "title"], query_rows=[(1, "ML Intern")])
    with postings_client(pool) as test_client:
        response = test_client.post(
            "/api/query", json={"sql": "select id, title from job_posting"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["columns"] == ["id", "title"]
    assert body["rows"] == [[1, "ML Intern"]]
    assert body["row_count"] == 1
    assert body["truncated"] is False
    # The read-only-transaction contract, exercised end to end through the route.
    [transaction] = pool.transactions
    assert transaction.readonly is True
    assert transaction.rolled_back is True
    assert transaction.committed is False


def test_query_rejects_writes_before_touching_the_pool(postings_client):
    pool = FakePublicPool()
    with postings_client(pool) as test_client:
        response = test_client.post(
            "/api/query", json={"sql": "update job_posting set status = 'closed'"}
        )

    assert response.status_code == 400
    assert pool.transactions == []
    assert pool.prepared_sql is None


def test_query_postgres_error_is_400_not_503(postings_client):
    """A PostgresError from the admin's own SQL is their mistake, not an
    outage -- unlike the postings routes, which treat the same exception as
    the database being unhealthy."""
    pool = FakePublicPool(prepare_error=asyncpg.PostgresSyntaxError("syntax error"))
    with postings_client(pool) as test_client:
        response = test_client.post("/api/query", json={"sql": "select bad syntax"})

    assert response.status_code == 400
    assert "syntax error" in response.json()["detail"]["message"]
    # Even a failed prepare must have rolled back, never committed.
    [transaction] = pool.transactions
    assert transaction.rolled_back is True
    assert transaction.committed is False


def test_query_schema_shapes_tables(postings_client):
    pool = FakePublicPool(
        schema_rows=[
            {
                "table_name": "company",
                "column_name": "id",
                "data_type": "bigint",
                "ordinal_position": 1,
                "approx_rows": 42,
            },
            {
                "table_name": "company",
                "column_name": "name",
                "data_type": "text",
                "ordinal_position": 2,
                "approx_rows": 42,
            },
        ]
    )
    with postings_client(pool) as test_client:
        response = test_client.get("/api/query/schema")

    assert response.status_code == 200
    assert response.json()["tables"] == [
        {
            "name": "company",
            "approx_rows": 42,
            "columns": [
                {"name": "id", "type": "bigint"},
                {"name": "name", "type": "text"},
            ],
        }
    ]


# ---------------------------------------------------------------- fields

class FakeFieldsPool:
    """Canned raw_posting pool for the Fields routes.

    ``fields.py`` issues six different statements; this dispatches on a
    marker unique to each rather than on call order, so a test can exercise
    one route without pretending to know how many queries the others made.
    """

    def __init__(
        self,
        *,
        adapters=(),
        kinds=(),
        feeds=(),
        survey=(),
        values=(),
        examples=(),
        sampled=0,
        postings=0,
    ):
        self.adapters = list(adapters)
        self.kinds = list(kinds)
        self.feeds = list(feeds)
        self.survey = list(survey)
        self.values = list(values)
        self.examples = list(examples)
        self.sampled = sampled
        self.postings = postings
        self.transactions: list[_FakeQueryTransaction] = []
        self.executed: list[str] = []
        self.params: list[tuple] = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetch(self, sql, *args):
        self.params.append(args)
        if "first_ids" in sql:
            return self.kinds
        if "pg_column_size" in sql:
            return self.adapters
        if "count(DISTINCT source_key) AS postings" in sql:
            return self.feeds
        if "WITH RECURSIVE" in sql:
            return self.survey
        if "IS NOT DISTINCT FROM" in sql:
            return self.examples
        return self.values

    async def fetchval(self, sql, *args):
        self.params.append(args)
        return self.postings if "SELECT count(*) FROM (" in sql else self.sampled

    def transaction(self, *, readonly=False, **kwargs):
        tr = _FakeQueryTransaction(readonly=readonly)
        self.transactions.append(tr)
        return tr

    async def execute(self, sql, *args):
        self.executed.append(sql)


def survey_row(**overrides):
    row = {
        "path": "departments[].name",
        "occurrences": 400,
        "postings": 180,
        "filled": 380,
        "distinct_values": 42,
        "kinds": ["string"],
        "example": "Engineering",
    }
    row.update(overrides)
    return row


def test_fields_page_is_served(client):
    assert client.get("/fields").status_code == 200


def test_fields_503_without_public_pool(client):
    for path in (
        "/api/fields/adapters",
        "/api/fields/feeds?adapter=greenhouse",
        "/api/fields/survey?adapter=greenhouse",
        "/api/fields/values?adapter=greenhouse&path=title",
        "/api/fields/examples?adapter=greenhouse&path=title",
    ):
        assert client.get(path).status_code == 503, path


def test_fields_mapping_needs_no_database(client):
    """Config introspection is pure, so it must keep answering while
    PostgreSQL is down -- the `client` fixture has no public pool."""
    response = client.get("/api/fields/mapping?adapter=greenhouse")
    assert response.status_code == 200
    body = response.json()
    assert body["engine"] == "json-api"
    assert body["paths"]["title"] == ["title", "job_type"]
    assert body["domains"]["attributes.remote"] == ["yes", "hybrid"]


def test_fields_mapping_rejects_a_bad_adapter(client):
    assert client.get("/api/fields/mapping?adapter=a'b").status_code == 400


def test_fields_adapters_marks_document_payloads_unsurveyable(postings_client):
    pool = FakeFieldsPool(
        adapters=[
            {
                "adapter": "greenhouse",
                "revisions": 32_753,
                "postings": 17_935,
                "feeds": 165,
                "payload_bytes": 185_436_616,
                "last_fetched_at": None,
            },
            {
                "adapter": "taleo",
                "revisions": 2_162,
                "postings": 1_168,
                "feeds": 4,
                "payload_bytes": 57_000_000,
                "last_fetched_at": None,
            },
        ],
        kinds=[
            {"adapter": "greenhouse", "kind": "json"},
            {"adapter": "taleo", "kind": "html"},
        ],
    )
    with postings_client(pool) as test_client:
        response = test_client.get("/api/fields/adapters?force=true")

    assert response.status_code == 200
    rows = {row["adapter"]: row for row in response.json()["adapters"]}
    assert rows["greenhouse"]["surveyable"] is True
    # An html payload is a document in a string: there is no field tree, and
    # the page must say so rather than render an empty survey.
    assert rows["taleo"]["surveyable"] is False
    assert rows["taleo"]["kind"] == "html"


def test_fields_survey_is_labelled_approximate_and_annotated(postings_client):
    pool = FakeFieldsPool(
        survey=[survey_row(path="metadata[].value_type"),
                survey_row(path="title", distinct_values=1_881)],
        sampled=2_000,
    )
    with postings_client(pool) as test_client:
        response = test_client.get("/api/fields/survey?adapter=greenhouse&sample=2000")

    assert response.status_code == 200
    body = response.json()
    assert body["exact"] is False
    assert body["sampled"] == 2_000
    rows = {row["path"]: row for row in body["paths"]}
    assert rows["title"]["mapped"] == {"fields": ["title", "job_type"], "inherited": None}
    # Carried inside a mapped ancestor rather than read by name.
    assert rows["metadata[].value_type"]["mapped"] == {
        "fields": ["source_extra.metadata"],
        "inherited": "metadata",
    }
    [transaction] = pool.transactions
    assert transaction.readonly is True
    assert transaction.rolled_back is True
    assert transaction.committed is False


def test_fields_survey_rejects_an_unoffered_sample_size(postings_client):
    with postings_client(FakeFieldsPool()) as test_client:
        assert (
            test_client.get("/api/fields/survey?adapter=greenhouse&sample=999").status_code
            == 400
        )


def test_fields_survey_rejects_a_feed_from_another_adapter(postings_client):
    with postings_client(FakeFieldsPool()) as test_client:
        response = test_client.get(
            "/api/fields/survey?adapter=greenhouse&feed=ashby:acme"
        )
    assert response.status_code == 400


def test_fields_values_is_exact_and_carries_the_collapse_chain(postings_client):
    pool = FakeFieldsPool(
        values=[
            {"value": "Remote", "n": 3_711, "total": 18_111, "distinct_values": 3},
            {"value": "OnSite", "n": 7_267, "total": 18_111, "distinct_values": 3},
            {"value": "Hybrid", "n": 4_273, "total": 18_111, "distinct_values": 3},
        ],
        postings=18_111,
    )
    with postings_client(pool) as test_client:
        response = test_client.get(
            "/api/fields/values?adapter=ashby&path=workplaceType&limit=10"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["exact"] is True
    assert body["postings"] == 18_111
    assert body["total"] == 18_111
    # ashby reads workplaceType twice: raw for the legacy silver attribute, and
    # again in the parse-time `remote` rules that will eventually replace it.
    assert body["mapped"]["fields"] == ["workplace_type_raw", "remote"]
    legacy, parsed = body["chains"]
    assert legacy["target"] == "attributes.remote"
    assert legacy["domain"] == ["yes", "hybrid"]
    assert parsed["target"] == "remote"
    assert parsed["domain"] == ["yes", "hybrid", "unknown"]
    chain = legacy
    # The point of the page: OnSite is stated by 7,267 postings and collapses
    # to nothing, because silver never records a negative remote signal.
    assert body["collapsed"]["workplace_type_raw"] == {
        "Remote": "yes",
        "OnSite": None,
        "Hybrid": "hybrid",
    }


def test_fields_values_reports_an_unmapped_path_as_bronze_only(postings_client):
    pool = FakeFieldsPool(
        values=[{"value": "Engineering", "n": 741, "total": 17_935, "distinct_values": 2_747}],
        postings=17_935,
    )
    with postings_client(pool) as test_client:
        response = test_client.get(
            "/api/fields/values?adapter=greenhouse&path=metadata[].value_type&limit=1"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is True
    assert body["chains"][0]["projected"] is False
    assert body["collapsed"] == {}


@pytest.mark.parametrize(
    "query",
    [
        "adapter=greenhouse&path=",
        "adapter=greenhouse&path=a';drop table raw_posting--",
        "adapter=greenhouse&path=a.b.c.d.e.f.g.h.i.j.k.l.m.n",
        "adapter=green'house&path=title",
    ],
)
def test_fields_values_rejects_a_bad_request(postings_client, query):
    with postings_client(FakeFieldsPool()) as test_client:
        assert test_client.get(f"/api/fields/values?{query}").status_code == 400


def test_fields_examples_returns_whole_payloads(postings_client):
    pool = FakeFieldsPool(
        examples=[
            {
                "id": 12,
                "source": "greenhouse:acme",
                "source_key": "job-1",
                "fetched_at": None,
                "p": {"title": "ML Intern", "departments": [{"name": "Engineering"}]},
            }
        ]
    )
    with postings_client(pool) as test_client:
        response = test_client.get(
            "/api/fields/examples?adapter=greenhouse&path=departments[].name&value=Engineering"
        )

    assert response.status_code == 200
    [example] = response.json()["examples"]
    # A 63-bit id must survive as a string, matching RunSnapshot.summary.
    assert example["id"] == "12"
    assert example["payload"]["title"] == "ML Intern"


# ------------------------------------------------------------------ tokens


class FakeTokensPool:
    """Canned token pool: the counts header, then the page of rows.

    tokens.query runs one fetchrow (the counts) then one fetch (the page).
    """

    def __init__(self, rows=(), counts=None):
        self.rows = list(rows)
        self.counts = counts or {
            "total": 1,
            "live": 1,
            "dead": 0,
            "unknown": 0,
            "unpromoted": 1,
            "promotable": 1,
        }

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetch(self, sql, *args):
        return self.rows

    async def fetchrow(self, sql, *args):
        return self.counts


def token_row(**overrides):
    row = {
        "ats": "workable",
        "token": "snap",
        "company_id": 12,
        "company": "Snap",
        "status": "live",
        "source": "slug",
        "discovered_at": datetime(2026, 8, 19, tzinfo=timezone.utc),
        "checked_at": datetime(2026, 8, 19, tzinfo=timezone.utc),
        "feed_id": None,
        "feed_state": "none",
        "total": 1,
    }
    row.update(overrides)
    return row


def test_tokens_page_is_served(client):
    assert client.get("/tokens").status_code == 200


def test_tokens_503_without_public_pool(client):
    # The default FakePolicy has no pool. An unreachable database is not an
    # empty inventory, and promoting into one must not report success either.
    assert client.get("/api/tokens").status_code == 503


def test_tokens_rejects_unknown_enums(postings_client):
    with postings_client(FakeTokensPool()) as test_client:
        # Only the four FILTERS keys are legal; the stored status vocabulary
        # ("live") and the feed-state words ("none") are not filter values.
        for value in ("bogus", "live", "none"):
            assert test_client.get(
                "/api/tokens", params={"filter": value}
            ).status_code == 400


def test_tokens_list_shape(postings_client):
    with postings_client(FakeTokensPool(rows=[token_row()])) as test_client:
        body = test_client.get("/api/tokens").json()

    assert body["postgres"]["connected"] is False
    assert body["counts"]["promotable"] == 1
    assert body["total"] == 1
    (row,) = body["rows"]
    assert (row["ats"], row["token"]) == ("workable", "snap")
    assert row["feed_state"] == "none"
    assert row["discovered_at"] == "2026-08-19T00:00:00+00:00"


def test_enable_forwards_identities_and_always_enables(postings_client):
    with postings_client(FakeTokensPool()) as test_client:
        response = test_client.post(
            "/api/tokens/enable",
            json={"tokens": [{"ats": "workable", "token": "snap"}]},
        )
        policy = test_client.app.state.policy

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 1
    assert body["skipped_non_ats"] == 0
    # enabled=True is the new contract: Enable is the one place a feed row
    # gets created active, and it never touches a row that already exists.
    assert policy.calls[-1] == ("import_feeds", (("workable", "snap"),), True)


def test_enable_skips_non_ats_identities(postings_client):
    with postings_client(FakeTokensPool()) as test_client:
        response = test_client.post(
            "/api/tokens/enable",
            json={
                "tokens": [
                    {"ats": "workable", "token": "snap"},
                    {"ats": "hn", "token": "some-post"},
                ]
            },
        )
        policy = test_client.app.state.policy

    assert response.status_code == 200
    body = response.json()
    assert body["skipped_non_ats"] == 1
    # Only the ATS identity ever reaches the store.
    assert policy.calls[-1] == ("import_feeds", (("workable", "snap"),), True)


def test_enable_rejects_an_empty_list(postings_client):
    with postings_client(FakeTokensPool()) as test_client:
        assert test_client.post(
            "/api/tokens/enable", json={"tokens": []}
        ).status_code == 400


def test_validate_probes_exactly_the_identities_it_is_given(postings_client, monkeypatch):
    seen = []

    async def fake_validate(identities, **kwargs):
        seen.append(list(identities))
        return [
            {"ats": ats, "token": token, "status": "live", "checked_at": "now", "details": {}}
            for ats, token in identities
        ]

    monkeypatch.setattr(
        "xjobscore.inspector.validator.validate_tokens", fake_validate
    )
    with postings_client(FakeTokensPool()) as test_client:
        response = test_client.post(
            "/api/tokens/validate",
            json={"tokens": [{"ats": "workable", "token": "snap"}]},
        )

    assert response.status_code == 200
    # The page sends the rows it is showing; nothing may be added to them.
    assert seen == [[("workable", "snap")]]
    assert response.json()["total"] == 1


def test_validate_by_vendor_expands_to_every_stored_token(postings_client, monkeypatch):
    seen = []

    async def fake_validate(identities, **kwargs):
        seen.append(list(identities))
        return []

    monkeypatch.setattr(
        "xjobscore.inspector.validator.validate_tokens", fake_validate
    )
    pool = FakeTokensPool(rows=[token_row(), token_row(token="other")])
    with postings_client(pool) as test_client:
        response = test_client.post(
            "/api/tokens/validate", json={"vendor": "workable"}
        )

    assert response.status_code == 200
    assert seen == [[("workable", "snap"), ("workable", "other")]]


def test_validate_refuses_a_batch_too_big_for_one_request(postings_client, monkeypatch):
    async def fake_validate(identities, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("oversized batch reached the prober")

    monkeypatch.setattr(
        "xjobscore.inspector.validator.validate_tokens", fake_validate
    )
    oversized = [
        {"ats": "workable", "token": f"t{n}"}
        for n in range(validator.MAX_BATCH + 1)
    ]
    with postings_client(FakeTokensPool()) as test_client:
        response = test_client.post("/api/tokens/validate", json={"tokens": oversized})

    # A refusal the caller can see beats a request that dies in a proxy with
    # the work half done.
    assert response.status_code == 400
    assert str(validator.MAX_BATCH) in response.json()["detail"]


def test_validate_needs_tokens_or_a_vendor(postings_client):
    with postings_client(FakeTokensPool()) as test_client:
        assert test_client.post("/api/tokens/validate", json={}).status_code == 400
