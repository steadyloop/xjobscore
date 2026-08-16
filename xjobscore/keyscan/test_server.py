from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from xjobscore.keyscan.core import Keyscan
from xjobscore.keyscan.server import create_app


class FixtureScan(Keyscan):
    def _company_seeds(self):
        return [{"domain": "acme.test", "company_id": 999999, "company_name": "Acme"}]


async def handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "acme.test":
        return httpx.Response(200, text='<a href="https://boards.greenhouse.io/acme">Apply</a>')
    if request.url.host == "boards-api.greenhouse.io":
        return httpx.Response(200, json={"jobs": [{"id": 1}, {"id": 2}]})
    return httpx.Response(404)


def client(tmp_path: Path) -> tuple[TestClient, Keyscan]:
    scan = FixtureScan(tmp_path / "work", transport=httpx.MockTransport(handler))
    return TestClient(create_app(scan)), scan


def test_run_completes_and_tokens_are_pollable_by_cursor(tmp_path: Path):
    api, _ = client(tmp_path)
    with api:
        assert api.get("/health").json()["running"] is False
        started = api.post("/runs", json={"methods": ["company"]})
        assert started.status_code == 202
        run_id = started.json()["run_id"]

        # TestClient drives the same loop, so the run has finished by the time
        # the next request is served.
        status = api.get(f"/runs/{run_id}").json()
        assert status["status"] == "completed"

        page = api.get("/candidates", params={"since_seq": 0}).json()
        assert [(row["source"], row["token"], row["job_count"]) for row in page["candidates"]] == [
            ("greenhouse", "acme", 2)
        ]
        assert page["more"] is False
        assert api.get("/candidates", params={"since_seq": page["next_seq"]}).json()["candidates"] == []


def test_invalid_and_conflicting_run_requests_are_rejected(tmp_path: Path):
    api, scan = client(tmp_path)
    with api:
        assert api.post("/runs", json={"methods": ["nonsense"]}).status_code == 400
        assert api.post("/runs", json={"methods": []}).status_code == 400
        assert api.post("/runs", json={"methods": ["company"], "sources": ["not-an-ats"]}).status_code == 400

        # A run left active by another process still blocks a new one.
        scan.begin(["company"], 6)
        assert api.post("/runs", json={"methods": ["company"]}).status_code == 409


def test_missing_run_and_empty_history_return_404(tmp_path: Path):
    api, _ = client(tmp_path)
    with api:
        assert api.get("/runs").status_code == 404
        assert api.get("/runs/nope").status_code == 404
        assert api.get("/runs/nope/logs").status_code == 404


def test_logs_and_cancel_are_reachable_over_http(tmp_path: Path):
    api, _ = client(tmp_path)
    with api:
        run_id = api.post("/runs", json={"methods": ["company"]}).json()["run_id"]
        logs = api.get(f"/runs/{run_id}/logs").json()
        assert any(entry["message"] == "run started" for entry in logs["entries"])
        assert logs["next_offset"] > 0
        # Cancelling a finished run is a no-op that still reports its state.
        assert api.post(f"/runs/{run_id}/cancel").json()["status"] == "completed"


def test_candidates_endpoint_validates_its_query_bounds(tmp_path: Path):
    api, _ = client(tmp_path)
    with api:
        assert api.get("/candidates", params={"since_seq": -1}).status_code == 422
        assert api.get("/candidates", params={"limit": 5000}).status_code == 422
        assert api.get("/candidates", params={"validation": "all"}).status_code == 200
