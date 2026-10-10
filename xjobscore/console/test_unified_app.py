from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from xjobscore.console.app import create_app


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
        self.console = object()
        self.scheduler = _Scheduler()
        self.heartbeat = object()
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


def test_dedup_routes_queue_work_on_the_unified_runtime():
    calls = []
    pool = object()

    async def pool_or_none():
        return pool

    async def queue_dedup(actual_pool, *, current):
        assert actual_pool is pool
        calls.append("queue")
        return {"state": "queued", "generation": len(calls)}

    async def coordinator_status(actual_pool):
        assert actual_pool is pool
        return {"gold": {"state": "pending"}}

    runtime = _Runtime()
    runtime.policy = SimpleNamespace(pool_or_none=pool_or_none)
    runtime.coordinator = SimpleNamespace(queue_dedup=queue_dedup, status=coordinator_status)
    runtime.gold_worker = SimpleNamespace(nudge=lambda: calls.append("nudge"))

    with TestClient(create_app(runtime=runtime)) as client:
        assert client.post("/api/gold/dedup").json()["state"] == "queued"
        assert client.get("/api/gold/dedup").json() == {"state": "pending"}

    assert calls == ["queue", "nudge"]
