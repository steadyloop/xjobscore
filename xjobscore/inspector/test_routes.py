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
    MAX_INTERVAL_S,
    MIN_INTERVAL_S,
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
        # observed. The token tests inject a canned pool instead.
        return self.pool

    async def set_enabled(self, source: str, key: str, enabled: bool) -> dict:
        self.calls.append(("set_enabled", source, key, enabled))
        return {"feed_id": 11, "enabled": enabled}

    async def set_interval(self, source: str, key: str, interval_s: int) -> dict:
        if not MIN_INTERVAL_S <= interval_s <= MAX_INTERVAL_S:
            raise ValueError(
                f"interval_s must be within [{MIN_INTERVAL_S}, {MAX_INTERVAL_S}]"
            )
        self.calls.append(("set_interval", source, key, interval_s))
        return {"feed_id": 11, "interval_s": interval_s}

    async def set_paused(self, source: str, key: str, paused: bool) -> dict:
        self.calls.append(("set_paused", source, key, paused))
        return {"feed_id": 11, "paused": paused}

    async def set_provider_paused(self, source: str, paused: bool) -> dict:
        self.calls.append(("set_provider_paused", source, paused))
        return {"source": source, "paused": paused, "affected": 2013}

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

    async def drop_feed(self, source: str, key: str) -> dict:
        self.calls.append(("drop_feed", source, key))
        return {"feed_id": 11, "dropped": True}

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
        error = getattr(self, "poll_error", None)
        if error is not None:
            raise error
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
    # No alias mirror and no feed company: silver would mint the ATS token's
    # company on the live poll rather than park the posting.
    assert canonical["company_resolution"]["status"] == "would_mint"
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

    Membership is a row in ``feed``, not ``feed.polling``: route-test:demo is
    running and route-off:demo and route-paused:demo are held, and all three
    belong on the Scheduler because Resume has to be reachable. Only
    route-unimported:demo, which has no row at all, is off the page.

    route-off and route-paused are deliberately the same state. Pause and
    disable used to be two columns; they are one now, and the fixture says so.
    """
    for source in ("route-test", "route-off", "route-paused", "route-unimported"):
        monkeypatch.setitem(sources.SOURCES, source, lambda f, n: None)
    policy = FakePolicy(
        snapshot={
            ("route-test", "demo"): {"enabled": True, "interval_s": 900},
            ("route-off", "demo"): {"enabled": False, "interval_s": 900},
            ("route-paused", "demo"): {"enabled": False, "interval_s": 900},
        }
    )
    policy.poll_error = getattr(runner, "poll_error", None)
    return create_app(
        catalog=FakeCatalog(
            Feed(id=1, source="route-test", key="demo", config={}),
            Feed(id=2, source="route-off", key="demo", config={}),
            Feed(id=3, source="route-paused", key="demo", config={}),
            Feed(id=4, source="route-unimported", key="demo", config={}),
        ),
        store=app_store,
        user_agent="route-test",
        policy=policy,
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
    """No poll_log data must read as unknown, never as zero activity."""
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
    assert body["feed_count"] == 3  # every imported feed, running or held
    assert sum(body["counts_by_status"].values()) == body["feed_count"]
    assert "calibrating" not in body["counts_by_status"]


def test_scheduler_feeds_carry_operational_state_and_show_every_imported_feed(
    imported_client,
):
    body = imported_client.get("/api/scheduler/feeds").json()
    assert body["telemetry_available"] is False
    # The row's existence is the membership test. Held feeds stay -- otherwise
    # Resume would be unreachable -- and only the unimported one is absent.
    assert {row["feed_ref"] for row in body["feeds"]} == {
        REF,
        feed_ref("route-off", "demo"),
        feed_ref("route-paused", "demo"),
    }
    assert [row["sched"]["status"]["state"] for row in body["feeds"]] == [
        "never_polled",
        "paused",
        "paused",
    ]

    # The unimported feed is still configured, so the catalog listing keeps it.
    all_refs = {
        row["feed_ref"]
        for row in imported_client.get("/api/feeds").json()["feeds"]
        if row["feed_ref"]
    }
    assert feed_ref("route-unimported", "demo") in all_refs

    row = next(row for row in body["feeds"] if row["feed_ref"] == REF)
    assert row["telemetry"] is None
    assert row["spark"] is None
    sched = row["sched"]
    assert set(sched) == {"requests_per_day", "status"}
    assert sched["requests_per_day"] == 96  # 900 s
    assert sched["status"]["state"] == "never_polled"
    assert sched["status"]["chip"] in {"ok", "unchanged", "partial", "error", "never"}

    filtered = imported_client.get(
        "/api/scheduler/feeds", params={"source": "route-test"}
    )
    assert {row["source"] for row in filtered.json()["feeds"]} == {"route-test"}


def test_paused_feed_stays_on_the_scheduler_and_says_so(imported_client):
    """Pausing holds polling without costing you the feed -- the whole feature."""
    ref = feed_ref("route-paused", "demo")
    body = imported_client.get("/api/scheduler/feeds").json()
    row = next(row for row in body["feeds"] if row["feed_ref"] == ref)

    assert row["policy"]["enabled"] is False
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


@pytest.mark.parametrize("paused", [False, True])
def test_provider_pause_skips_fleet_lookup_and_wakes_on_resume(polling_client, monkeypatch, paused):
    async def no_fleet_lookup():
        raise AssertionError("provider pause must not resolve the full fleet")

    monkeypatch.setattr(polling_client.app.state.inspector, "feed_rows", no_fleet_lookup)
    response = polling_client.post(
        "/api/scheduler/providers/paylocity/pause", json={"paused": paused}
    )
    assert response.status_code == 200
    assert response.json() == {"source": "paylocity", "paused": paused, "affected": 2013}
    assert polling_client.app.state.policy.calls == [("set_provider_paused", "paylocity", paused)]
    assert polling_client.app.state.scheduler_runner.nudges == int(not paused)


def test_provider_pause_reports_an_absent_provider(polling_client, monkeypatch):
    async def absent(source, paused):
        return {"source": source, "paused": paused, "affected": 0}

    monkeypatch.setattr(polling_client.app.state.policy, "set_provider_paused", absent)
    response = polling_client.post(
        "/api/scheduler/providers/missing/pause", json={"paused": True}
    )
    assert response.status_code == 404


def test_provider_pause_reports_database_failure(polling_client, monkeypatch):
    async def unavailable(source, paused):
        raise PolicyUnavailable("database unavailable")

    monkeypatch.setattr(polling_client.app.state.policy, "set_provider_paused", unavailable)
    response = polling_client.post(
        "/api/scheduler/providers/paylocity/pause", json={"paused": True}
    )
    assert response.status_code == 503


def test_scheduler_feed_detail(imported_client):
    body = imported_client.get(f"/api/scheduler/feeds/{REF}").json()
    assert body["feed"]["feed_ref"] == REF
    assert body["events"] == []
    assert imported_client.get("/api/scheduler/feeds/nope:nope").status_code == 404

    # The membership rule applies to the detail route too: a held feed is
    # reachable, an unimported one is not.
    off = feed_ref("route-off", "demo")
    assert imported_client.get(f"/api/scheduler/feeds/{off}").status_code == 200
    gone = feed_ref("route-unimported", "demo")
    assert imported_client.get(f"/api/scheduler/feeds/{gone}").status_code == 404


def test_scheduler_events_degrade_to_empty(imported_client):
    """No pool means no fleet-wide poll log -- an empty list, never a 500."""
    body = imported_client.get("/api/scheduler/events").json()
    assert body["events"] == []
    assert body["telemetry_available"] is False


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


@pytest.mark.parametrize("running", [False, True])
def test_scheduler_pause_and_resume_wake_the_runner(polling_client, running):
    response = polling_client.post("/api/scheduler/control", json={"running": running})
    assert response.status_code == 200
    assert response.json()["control"]["running"] is running
    assert polling_client.app.state.scheduler_runner.nudges == 1


def test_runner_does_not_start_under_default_config(client):
    """The env-var gate (config.runner_enabled()) defaults off in every test process."""
    assert client.app.state.scheduler_runner.status()["alive"] is False


def test_poll_now_all_accepts_an_absent_body(polling_client):
    """An empty POST means "every feed", not 422.

    The body carries one optional filter, so requiring it turns the common
    case -- bring everything forward -- into a client error. Matches
    /api/feeds/{ref}/import, whose body is optional for the same reason.
    """
    for request in ({}, {"json": {}}, {"json": {"source": "route-test"}}):
        response = polling_client.post("/api/scheduler/poll-now", **request)
        assert response.status_code == 202, request

    assert polling_client.post("/api/scheduler/poll-now").json()["source"] is None


def test_poll_now_one_feed(polling_client):
    """The happy path writes the queue position *and* wakes the runner for it.

    Without the nudge the operator waits out a whole tick (60s by default) for
    a button labelled "now", which is the latency this endpoint exists to
    remove.
    """
    runner = polling_client.app.state.scheduler_runner
    response = polling_client.post(f"/api/scheduler/feeds/{REF}/poll-now")
    body = response.json()

    assert response.status_code == 202
    assert body["queued"] is True
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


def test_poll_now_all_refuses_without_a_runner(imported_client):
    response = imported_client.post("/api/scheduler/poll-now")

    assert response.status_code == 409
    assert response.json()["detail"]["hint"] == "no_runner"


def test_scheduler_fetch_queues_one_feed_for_the_runner(polling_client):
    """HTTP writes queue state and never executes a production worker."""
    runner = polling_client.app.state.scheduler_runner
    response = polling_client.post(f"/api/scheduler/feeds/{REF}/fetch")
    body = response.json()

    assert response.status_code == 202
    assert body["queued"] is True
    assert runner.nudges == 1
    assert runner.polls == []
    assert ("poll_now", "route-test", "demo") in polling_client.app.state.policy.calls


def test_scheduler_fetch_unknown_ref_is_404(polling_client):
    response = polling_client.post("/api/scheduler/feeds/nope:nope/fetch")

    assert response.status_code == 404
    assert polling_client.app.state.scheduler_runner.polls == []


def test_scheduler_fetch_refuses_without_a_runner(monkeypatch, app_store):
    app = _imported_app(monkeypatch, app_store, runner=FakeRunner(alive=False))
    with TestClient(app) as test_client:
        response = test_client.post(f"/api/scheduler/feeds/{REF}/fetch")

    assert response.status_code == 409
    assert response.json()["detail"]["hint"] == "no_runner"


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


def test_scheduler_fetch_never_runs_or_reports_a_worker_outcome(monkeypatch, app_store):
    runner = FakeRunner(poll_result={"status": "error", "feed_id": 11, "new_postings": None})
    app = _imported_app(monkeypatch, app_store, runner=runner)
    with TestClient(app) as test_client:
        response = test_client.post(f"/api/scheduler/feeds/{REF}/fetch")

    assert response.status_code == 202
    assert response.json()["queued"] is True
    assert runner.polls == []


def test_system_page_is_served(client):
    response = client.get("/system")
    assert response.status_code == 200
    assert 'id="sched-table"' in response.text


def test_landing_and_old_scheduler_urls_redirect_to_system(client):
    for path in ("/", "/scheduler"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 307, path
        assert response.headers["location"] == "/system", path


def test_feeds_page_moved_to_feeds(client):
    response = client.get("/feeds")
    assert response.status_code == 200
    assert "/static/feeds.js" in response.text


def test_system_live_reports_workers_and_the_feed_window(client):
    body = client.get("/api/system/live").json()
    assert set(body) == {"observed_at", "workers", "feeds", "polls_total", "enrichment"}
    assert body["enrichment"] is None
    assert isinstance(body["polls_total"], int)
    assert body["workers"]["greenhouse"]["width"] >= 1
    assert body["workers"]["greenhouse"]["busy"] == []
    assert "lanes" not in body["workers"]["greenhouse"]
    # Workday runs one pool per data center; its width is their sum.
    workday = body["workers"]["workday"]
    assert [lane["label"] for lane in workday["lanes"]] == ["wd1", "wd3", "wd5", "other"]
    assert workday["width"] == sum(lane["width"] for lane in workday["lanes"])
    assert workday["lanes"][0]["budget"] == 8.0
    assert workday["lanes"][0]["rate_group"] == "wd1.myworkdayjobs.com"
    assert workday["partition"] == {"index": 2, "named": ["wd1", "wd3", "wd5"]}
    assert set(body["feeds"]) == {
        "window_s", "since_start", "polled", "with_new", "new_postings", "partial", "errors",
    }


def test_system_events_without_postgres_are_empty_not_an_error(client):
    response = client.get("/api/system/events")
    assert response.status_code == 200
    assert response.json() == {"events": [], "gold_last": None, "telemetry_available": False}


def test_system_logs_return_lines_after_a_cursor(client):
    import logging

    from xjobscore import activity

    # `uv run xjobscore` attaches the buffer in system.cli; the test app does not.
    probe = logging.getLogger("xjobscore.test")
    probe.addHandler(activity.LOGS)
    try:
        probe.warning("system log probe")
    finally:
        probe.removeHandler(activity.LOGS)
    first = client.get("/api/system/logs").json()
    assert set(first) == {"boot", "lines", "last_seq"}
    assert any(line["msg"] == "system log probe" for line in first["lines"])
    again = client.get(f"/api/system/logs?after={first['last_seq']}").json()
    assert again["lines"] == []


def test_keyscan_reads_report_a_missing_database_as_404(client, tmp_path, monkeypatch):
    """No keyscan database is an empty page, not a server error."""
    monkeypatch.setenv("XJOBSCORE_INSPECTOR_KEYSCAN_DB", str(tmp_path / "absent.sqlite"))

    for path in ("/api/keyscan/status", "/api/keyscan/tally", "/api/keyscan/found"):
        assert client.get(path).status_code == 404, path


def test_keyscan_page_is_served(client):
    response = client.get("/keyscan")

    assert response.status_code == 200
    assert "keyscan/app.js" in response.text


@pytest.fixture
def public_client(monkeypatch, app_store):
    """A client whose policy carries a canned public pool, store is real PG."""

    def build(pool):
        app = create_app(
            catalog=FakeCatalog(),
            store=app_store,
            user_agent="route-test",
            policy=FakePolicy(pool=pool),
        )
        return TestClient(app)

    return build


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
            "muted": 0,
            "unpromoted": 1,
            "promotable": 1,
            "vendors": 1,
            "never_checked": 0,
            "checked_24h": 1,
        }

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetch(self, sql, *args):
        return self.rows

    async def fetchrow(self, sql, *args):
        if "WITH changed AS" in sql:
            return {
                "ats": args[1],
                "token": args[2],
                "muted": args[0],
                "feed_dropped": bool(args[0]),
            }
        if "UPDATE token" in sql:
            field = "tags" if "SET tags" in sql else "note"
            return {"ats": args[1], "token": args[2], field: args[0]}
        return self.counts


def token_row(**overrides):
    row = {
        "ats": "workable",
        "token": "snap",
        "company_id": 12,
        "company": "Snap",
        "status": "live",
        "muted": False,
        "tags": None,
        "note": None,
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
    response = client.get("/tokens")
    assert response.status_code == 200
    assert "Token heartbeat" in response.text
    assert "Add to feed" in response.text
    assert "validate-all-btn" not in response.text
    inactive = response.text.index('value="inactive" selected')
    unknown = response.text.index('value="unknown"')
    muted = response.text.index('value="muted"')
    dead = response.text.index('value="dead"')
    all_tokens = response.text.index('value="all">All (including active)</option>')
    assert inactive < unknown < muted < dead < all_tokens
    assert 'id="sweep"' in response.text
    assert 'value="alive-active"' not in response.text


def test_tokens_503_without_public_pool(client):
    # The default FakePolicy has no pool. An unreachable database is not an
    # empty inventory, and promoting into one must not report success either.
    assert client.get("/api/tokens").status_code == 503
    assert client.get("/api/tokens/heartbeat").status_code == 503


def test_tokens_rejects_unknown_enums(public_client):
    with public_client(FakeTokensPool()) as test_client:
        # Only the four FILTERS keys are legal; the stored status vocabulary
        # ("live") and the feed-state words ("none") are not filter values.
        for value in ("bogus", "live", "none"):
            assert test_client.get(
                "/api/tokens", params={"filter": value}
            ).status_code == 400


def test_tokens_list_shape(public_client):
    with public_client(FakeTokensPool(rows=[token_row()])) as test_client:
        body = test_client.get("/api/tokens").json()

    assert body["postgres"]["connected"] is False
    assert body["counts"]["promotable"] == 1
    assert body["total"] == 1
    (row,) = body["rows"]
    assert (row["ats"], row["token"]) == ("workable", "snap")
    assert row["feed_state"] == "none"
    assert row["discovered_at"] == "2026-08-19T00:00:00+00:00"


def test_token_note_is_saved_and_blank_notes_clear(public_client):
    with public_client(FakeTokensPool()) as test_client:
        saved = test_client.put(
            "/api/tokens/note",
            json={"ats": "workable", "token": "snap", "note": "  follow up  "},
        )
        cleared = test_client.put(
            "/api/tokens/note",
            json={"ats": "workable", "token": "snap", "note": "  "},
        )

    assert saved.status_code == 200
    assert saved.json() == {"ats": "workable", "token": "snap", "note": "follow up"}
    assert cleared.status_code == 200
    assert cleared.json()["note"] is None


def test_token_tags_are_normalized_and_an_empty_list_clears(public_client):
    with public_client(FakeTokensPool()) as test_client:
        saved = test_client.put(
            "/api/tokens/tags",
            json={"ats": "workable", "token": "snap", "tags": [" bio ", "cs", "bio"]},
        )
        cleared = test_client.put(
            "/api/tokens/tags",
            json={"ats": "workable", "token": "snap", "tags": []},
        )

    assert saved.status_code == 200
    assert saved.json() == {"ats": "workable", "token": "snap", "tags": ["bio", "cs"]}
    assert cleared.status_code == 200
    assert cleared.json()["tags"] == []


def test_enable_promotes_only_rows_the_guarded_insert_accepts(public_client):
    with public_client(FakeTokensPool(rows=[token_row()])) as test_client:
        response = test_client.post(
            "/api/tokens/enable",
            json={"tokens": [{"ats": "workable", "token": "snap"}]},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 1
    assert body["skipped"] == 0


def test_enable_reports_tokens_rejected_by_the_guarded_insert(public_client):
    with public_client(FakeTokensPool(rows=[token_row()])) as test_client:
        response = test_client.post(
            "/api/tokens/enable",
            json={
                "tokens": [
                    {"ats": "workable", "token": "snap"},
                    {"ats": "hn", "token": "some-post"},
                ]
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["requested"] == 2
    assert body["imported"] == 1
    assert body["skipped"] == 1


def test_enable_rejects_an_empty_list(public_client):
    with public_client(FakeTokensPool()) as test_client:
        assert test_client.post(
            "/api/tokens/enable", json={"tokens": []}
        ).status_code == 400


def test_token_log_tails_the_newest_checks(public_client, monkeypatch):
    app_module = importlib.import_module("xjobscore.inspector.app")

    async def fake_recent_checks(pool, *, limit):
        assert limit == 25
        return [
            {
                "id": 2,
                "checked_at": "2026-08-27T22:54:22+00:00",
                "ats": "greenhouse",
                "token": "acme",
                "status": "live",
            }
        ]

    monkeypatch.setattr(app_module, "tokens_recent_checks", fake_recent_checks)
    with public_client(FakeTokensPool()) as test_client:
        response = test_client.get("/api/tokens/log?limit=25")

    payload = response.json()
    assert response.status_code == 200
    assert payload["log_available"] is True
    assert payload["checks"][0]["token"] == "acme"


def test_token_log_reports_an_unreadable_log_as_unavailable(public_client, monkeypatch):
    """A missing token_log is not an empty one -- the page must not say zero."""
    app_module = importlib.import_module("xjobscore.inspector.app")

    async def missing_table(pool, *, limit):
        return None

    monkeypatch.setattr(app_module, "tokens_recent_checks", missing_table)
    with public_client(FakeTokensPool()) as test_client:
        payload = test_client.get("/api/tokens/log").json()

    assert payload["log_available"] is False
    assert payload["checks"] == []


def test_heartbeat_status_and_run_now(public_client, monkeypatch):
    app_module = importlib.import_module("xjobscore.inspector.app")
    queued = []

    async def fake_snapshot(pool, **kwargs):
        return {
            "state": "healthy",
            "runner_online": True,
            "eligible": 4,
            "last_validated": 20,
            "last_promoted": 18,
        }

    async def fake_enqueue(pool, current):
        queued.append(current)

    monkeypatch.setattr(app_module, "token_heartbeat_snapshot", fake_snapshot)
    monkeypatch.setattr(app_module, "token_heartbeat_enqueue", fake_enqueue)

    with public_client(FakeTokensPool()) as test_client:
        status = test_client.get("/api/tokens/heartbeat")
        started = test_client.post("/api/tokens/heartbeat/run")

    assert status.status_code == 200
    assert status.json()["eligible"] == 4
    assert started.status_code == 202
    assert len(queued) == 1


def test_heartbeat_run_now_refuses_a_paused_heartbeat(public_client, monkeypatch):
    app_module = importlib.import_module("xjobscore.inspector.app")

    async def paused(pool, current):
        raise app_module.HeartbeatPaused("Token heartbeat is paused")

    monkeypatch.setattr(app_module, "token_heartbeat_enqueue", paused)
    with public_client(FakeTokensPool()) as test_client:
        response = test_client.post("/api/tokens/heartbeat/run")

    assert response.status_code == 409
    assert response.json()["detail"] == "Token heartbeat is paused"


def test_heartbeat_pause_and_resume_round_trip(public_client, monkeypatch):
    """The switch is written to PostgreSQL, not held in this process."""
    app_module = importlib.import_module("xjobscore.inspector.app")
    written = []

    async def fake_set_paused(pool, paused, *, current):
        written.append(paused)

    async def fake_snapshot(pool, **kwargs):
        return {"state": "paused" if written[-1] else "healthy", "paused": written[-1]}

    monkeypatch.setattr(app_module, "token_heartbeat_set_paused", fake_set_paused)
    monkeypatch.setattr(app_module, "token_heartbeat_snapshot", fake_snapshot)

    with public_client(FakeTokensPool()) as test_client:
        paused = test_client.post("/api/tokens/heartbeat/control", json={"paused": True})
        resumed = test_client.post(
            "/api/tokens/heartbeat/control", json={"paused": False}
        )

    assert written == [True, False]
    assert paused.json()["state"] == "paused"
    assert resumed.json()["paused"] is False


def test_heartbeat_run_now_refuses_an_active_cycle(public_client, monkeypatch):
    app_module = importlib.import_module("xjobscore.inspector.app")

    async def already_running(pool, current):
        raise app_module.HeartbeatAlreadyRunning("already running")

    monkeypatch.setattr(app_module, "token_heartbeat_enqueue", already_running)
    with public_client(FakeTokensPool()) as test_client:
        response = test_client.post("/api/tokens/heartbeat/run")

    assert response.status_code == 409
    assert response.json()["detail"] == "already running"


def test_muting_a_token_reports_feed_drop(public_client):
    with public_client(FakeTokensPool()) as test_client:
        response = test_client.put(
            "/api/tokens/muted",
            json={"ats": "workable", "token": "snap", "muted": True},
        )

    assert response.status_code == 200
    assert response.json() == {
        "ats": "workable",
        "token": "snap",
        "muted": True,
        "feed_dropped": True,
    }


def test_legacy_bulk_delete_route_is_removed(public_client):
    with public_client(FakeTokensPool()) as test_client:
        response = test_client.post(
            "/api/feeds/bulk-delete",
            json={"feed_ids": [42]},
        )

    # The literal mutation route is gone. The generic GET-only feed detail
    # route still recognizes this path shape, so POST is correctly refused.
    assert response.status_code == 405


def test_validate_probes_exactly_the_identities_it_is_given(public_client, monkeypatch):
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
    with public_client(FakeTokensPool()) as test_client:
        response = test_client.post(
            "/api/tokens/validate",
            json={"tokens": [{"ats": "workable", "token": "snap"}]},
        )

    assert response.status_code == 200
    # The page sends the rows it is showing; nothing may be added to them.
    assert seen == [[("workable", "snap")]]
    assert response.json()["total"] == 1


def test_validate_by_vendor_expands_to_every_stored_token(public_client, monkeypatch):
    seen = []

    async def fake_validate(identities, **kwargs):
        seen.append(list(identities))
        return []

    monkeypatch.setattr(
        "xjobscore.inspector.validator.validate_tokens", fake_validate
    )
    pool = FakeTokensPool(rows=[token_row(), token_row(token="other")])
    with public_client(pool) as test_client:
        response = test_client.post(
            "/api/tokens/validate", json={"vendor": "workable"}
        )

    assert response.status_code == 200
    assert seen == [[("workable", "snap"), ("workable", "other")]]


def test_validate_refuses_a_batch_too_big_for_one_request(public_client, monkeypatch):
    async def fake_validate(identities, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("oversized batch reached the prober")

    monkeypatch.setattr(
        "xjobscore.inspector.validator.validate_tokens", fake_validate
    )
    oversized = [
        {"ats": "workable", "token": f"t{n}"}
        for n in range(validator.MAX_BATCH + 1)
    ]
    with public_client(FakeTokensPool()) as test_client:
        response = test_client.post("/api/tokens/validate", json={"tokens": oversized})

    # A refusal the caller can see beats a request that dies in a proxy with
    # the work half done.
    assert response.status_code == 400
    assert str(validator.MAX_BATCH) in response.json()["detail"]


def test_validate_needs_tokens_or_a_vendor(public_client):
    with public_client(FakeTokensPool()) as test_client:
        assert test_client.post("/api/tokens/validate", json={}).status_code == 400


# --------------------------------------------------------------- companies


class FakeCompaniesPool:
    """Canned company pool: the counts header, then the page of rows."""

    def __init__(self, rows=None, raises=None):
        self.rows = list(rows if rows is not None else [_company_row()])
        self.raises = raises
        self.counts = {
            "total": 3, "verified": 1, "rejected": 0, "unverified": 2,
            "queue": 1, "no_domain": 0, "serving": 1,
        }

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def fetch(self, sql, *args):
        if "INSERT INTO company_alias" in sql:
            return [{"alias_norm": norm} for norm in args[0]]
        return self.rows

    async def fetchrow(self, sql, *args):
        if "FOR UPDATE" in sql:
            return {"id": 7, "name": "acme-inc", "domain": "guess.example",
                    "description": None, "tags": {}}
        if "SELECT c.id" in sql:
            return self.rows[0] if self.rows else None
        return self.counts

    async def execute(self, sql, *args):
        if self.raises is not None:
            raise self.raises
        return "UPDATE 1"


def _company_row(**overrides):
    row = {
        "id": 7, "name": "acme-inc", "domain": "acme.example",
        "description": "makes things", "tags": {},
        "reviewed": "pending", "reviewed_at": None,
        "active_postings": 12, "alias_count": 1, "has_logo": True,
        "tokens": ["greenhouse:acme"], "sample_title": "Intern",
        "sample_apply_url": "https://acme.example/j/1", "sample_source_url": None,
        "total": 1,
    }
    row.update(overrides)
    return row


def test_companies_lists_the_queue_with_counts(public_client):
    with public_client(FakeCompaniesPool()) as test_client:
        payload = test_client.get("/api/companies").json()
    assert payload["filter"] == "queue"
    assert payload["counts"]["queue"] == 1
    assert payload["rows"][0]["review_state"] == "unverified"
    assert payload["rows"][0]["tokens"] == ["greenhouse:acme"]


def test_companies_rejects_an_unknown_filter(public_client):
    with public_client(FakeCompaniesPool()) as test_client:
        assert test_client.get("/api/companies?filter=nope").status_code == 400


def test_companies_503_without_public_pool(client):
    assert client.get("/api/companies").status_code == 503


def test_company_detail_404s_for_a_missing_row(public_client):
    with public_client(FakeCompaniesPool(rows=[])) as test_client:
        assert test_client.get("/api/companies/999").status_code == 404


def test_company_verify_returns_the_updated_row(public_client):
    verified = _company_row(
        name="Acme, Inc.",
        reviewed="verified",
        tags={"name_machine": "acme-inc"},
    )
    with public_client(FakeCompaniesPool(rows=[verified])) as test_client:
        response = test_client.post(
            "/api/companies/7/verify", json={"name": "Acme, Inc."}
        )
    assert response.status_code == 200
    assert response.json()["name"] == "Acme, Inc."
    assert response.json()["name_machine"] == "acme-inc"


def test_company_verify_reports_a_taken_domain_as_409(public_client):
    # company.domain is UNIQUE: reusing one is a merge, not a verification.
    pool = FakeCompaniesPool(raises=asyncpg.UniqueViolationError("duplicate"))
    with public_client(pool) as test_client:
        response = test_client.post(
            "/api/companies/7/verify", json={"domain": "taken.example"}
        )
    assert response.status_code == 409
    assert response.json()["detail"]["hint"] == "domain-taken"


def test_company_verify_refuses_a_blank_name(public_client):
    with public_client(FakeCompaniesPool()) as test_client:
        response = test_client.post("/api/companies/7/verify", json={"name": "  "})
    assert response.status_code == 400


def test_company_review_marks_and_reopens(public_client):
    rejected = _company_row(reviewed="rejected")
    with public_client(FakeCompaniesPool(rows=[rejected])) as test_client:
        response = test_client.post(
            "/api/companies/7/review", json={"state": "rejected"}
        )
        assert response.status_code == 200
        assert response.json()["review_state"] == "rejected"
        # Anything outside the two verdicts is refused by the body model.
        assert test_client.post(
            "/api/companies/7/review", json={"state": "deleted"}
        ).status_code == 422


def test_companies_page_is_served(client):
    response = client.get("/companies")
    assert response.status_code == 200
    assert "Companies" in response.text
    assert 'id="filter-pills"' in response.text
    assert "/static/companies/app.js" in response.text


# ------------------------------------------------------------------ sweep


class FakeSweep:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.busy = False

    async def backlog(self):
        return {"workable": 3}

    def snapshot(self):
        return {"status": "running" if self.calls else "idle", "vendors": []}

    async def start(self, *, limit, ats=None):
        if self.busy:
            from xjobscore.inspector.token_sweep import SweepAlreadyRunning

            raise SweepAlreadyRunning("A sweep is already running")
        self.calls.append(("start", limit, ats))

    def pause(self, paused):
        self.calls.append(("pause", paused))

    async def cancel(self):
        self.calls.append(("cancel",))

    async def stop(self):
        pass


def sweep_client(app_store, pool, sweep):
    return TestClient(create_app(
        catalog=FakeCatalog(),
        store=app_store,
        user_agent="route-test",
        policy=FakePolicy(pool=pool),
        token_sweep=sweep,
    ))


def test_sweep_routes_drive_the_runner(app_store):
    sweep = FakeSweep()
    with sweep_client(app_store, FakeTokensPool(), sweep) as client:
        idle = client.get("/api/tokens/sweep").json()
        assert idle["status"] == "idle" and idle["backlog"] == {"workable": 3}
        started = client.post("/api/tokens/sweep/start", json={"limit": 100, "ats": ["workday"]})
        assert started.status_code == 202 and started.json()["status"] == "running"
        assert client.post("/api/tokens/sweep/pause", json={"paused": True}).status_code == 200
        assert client.post("/api/tokens/sweep/cancel").status_code == 200
    assert sweep.calls == [("start", 100, ["workday"]), ("pause", True), ("cancel",)]


def test_sweep_start_rejects_bad_limit_and_second_sweep(app_store):
    sweep = FakeSweep()
    with sweep_client(app_store, FakeTokensPool(), sweep) as client:
        assert client.post("/api/tokens/sweep/start", json={"limit": 0}).status_code == 422
        sweep.busy = True
        assert client.post("/api/tokens/sweep/start", json={}).status_code == 409


def test_sweep_start_503_without_pool(app_store):
    with sweep_client(app_store, None, FakeSweep()) as client:
        assert client.post("/api/tokens/sweep/start", json={}).status_code == 503


def test_add_to_feed_by_vendor_skips_tokens_the_vendor_covers(public_client, monkeypatch):
    app_module = importlib.import_module("xjobscore.inspector.app")
    seen = {}

    async def fake_tokens(pool, identities, *, interval_s=None):
        seen["tokens"] = (identities, interval_s)
        return len(identities)

    async def fake_vendors(pool, vendors, *, filter, q, interval_s):
        seen["vendors"] = (vendors, filter, q, interval_s)
        return 10

    monkeypatch.setattr(app_module, "tokens_promote_verified", fake_tokens)
    monkeypatch.setattr(app_module, "tokens_add_vendors_to_feed", fake_vendors)
    with public_client(FakeTokensPool()) as test_client:
        response = test_client.post("/api/tokens/enable", json={
            "tokens": [{"ats": "lever", "token": "a"}, {"ats": "ashby", "token": "b"}],
            "vendors": ["lever"], "filter": "all", "q": "ai", "interval_s": 86400,
        })
        empty = test_client.post("/api/tokens/enable", json={})
        too_fast = test_client.post("/api/tokens/enable", json={"vendors": ["lever"], "interval_s": 5})

    assert response.status_code == 200 and response.json()["imported"] == 11
    assert seen["tokens"] == ([("ashby", "b")], 86400)
    assert seen["vendors"] == (["lever"], "all", "ai", 86400)
    assert empty.status_code == 400 and too_fast.status_code == 422
