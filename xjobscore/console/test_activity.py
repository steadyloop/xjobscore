"""Activity API contracts without a PostgreSQL dependency or runtime startup."""
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from xjobscore.console.test_support import create_app
from xjobscore.console.app import create_app as runtime_app


class Policy:
    def __init__(self, pool=None):
        self.pool = pool

    async def pool_or_none(self):
        return self.pool


class Connection:
    def __init__(self, run):
        self.run = run

    async def fetch(self, query, limit):
        assert "detail ? 'gold'" in query
        return [self.run]

    async def fetchrow(self, query):
        assert "finished_at IS NOT NULL" in query
        assert "job_type = 'feed'" in query
        return self.run

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def test_activity_history_serializes_immediate_enrichment_with_timings():
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    run = {
        "id": 1, "job_type": "feed", "identity": "lever:acme", "state": "complete",
        "started_at": now, "finished_at": now, "error": None,
        "detail": {"gold": {"postings": 3, "stage_ms": {"skills": 250}, "duration_ms": 300}},
    }
    pool = SimpleNamespace(acquire=lambda: Connection(run))
    client = TestClient(create_app(store=object(), policy=Policy(pool)))
    response = client.get("/api/system/events").json()
    assert response["telemetry_available"] is True
    assert response["gold_last"]["finished_at"] == now.isoformat()
    assert response["gold_last"]["detail"]["gold"]["postings"] == 3
    assert response["events"][0]["job_type"] == "feed"


def test_activity_history_distinguishes_unavailable_from_empty():
    client = TestClient(create_app(store=object(), policy=Policy()))
    body = client.get("/api/system/events").json()
    assert body == {"events": [], "gold_last": None, "telemetry_available": False}


def test_live_activity_exposes_worker_deadline_without_starting_or_changing_it():
    status = {"alive": True, "accepting": True, "checking": False, "next_check_at": "2026-10-06T00:01:00+00:00"}
    runtime = SimpleNamespace(
        result_store=object(), policy=Policy(), catalog=object(), console=object(),
        scheduler=SimpleNamespace(rate_groups=lambda: {}), heartbeat=object(),
        gold_worker=SimpleNamespace(status=lambda: status),
    )
    client = TestClient(runtime_app(runtime=runtime))
    assert client.get("/api/system/live").json()["enrichment"] == status


def test_system_page_has_one_activity_stream_and_preserves_scheduler():
    client = TestClient(create_app(store=object(), policy=Policy()))
    response = client.get("/system")
    assert response.status_code == 200
    assert 'id="activity-stream"' in response.text
    assert 'id="sched-table"' in response.text
    for old_id in ("server-log-lines", "live-log-lines", "system-events-lines"):
        assert f'id="{old_id}"' not in response.text
