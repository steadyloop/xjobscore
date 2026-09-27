from __future__ import annotations

from fastapi.testclient import TestClient

from xjobscore.inspector.app import create_app


class _Scheduler:
    def status(self):
        return {"alive": True, "accepting": True}

    def nudge(self):
        return None


class _Runtime:
    def __init__(self):
        self.result_store = object()
        self.policy = object()
        self.catalog = object()
        self.inspector = object()
        self.scheduler = _Scheduler()
        self.heartbeat = object()
        self.gold_job = object()
        self.started = 0
        self.stopped = 0

    async def start(self):
        self.started += 1

    async def stop(self):
        self.stopped += 1

    async def status(self):
        return {
            "ready": True,
            "lock_owned": True,
            "postgres": {"connected": True},
            "scheduler": {"alive": True},
            "pipeline": {"backlog": {}},
            "heartbeat": {"state": "healthy"},
        }


def test_unified_runtime_owns_app_lifespan_and_status_routes():
    runtime = _Runtime()

    with TestClient(create_app(runtime=runtime)) as client:
        assert client.get("/healthz").json() == {"ok": True}
        assert client.get("/readyz").status_code == 200
        assert client.get("/api/system").json()["lock_owned"] is True

    assert runtime.started == 1
    assert runtime.stopped == 1
