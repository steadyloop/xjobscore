"""HTTP daemon exposing keyscan runs and the tokens they have verified.

The scan writes SQLite and this layer only reads it, which is the same split
the blocking CLI already has.  A backend polls `GET /candidates` with the
`next_seq` it was last handed and receives every token validated since.

There is no authentication and the default bind is loopback, matching the rest
of the project's local-development posture.  Do not expose the port.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Body, FastAPI, HTTPException, Query

from .core import ActiveRunError, Keyscan, KeyscanError


def create_app(scan: Keyscan) -> FastAPI:
    # The task is the daemon's own handle on an in-process run.  The
    # `keyscan_one_active` index remains the real cross-process guard.
    state: dict[str, Any] = {"task": None}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        task = state["task"]
        if task is not None and not task.done():
            # Record an interrupted run rather than dropping the task
            # mid-probe; `resume` picks it back up from its checkpoint.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title="keyscan", version="1.0", lifespan=lifespan)

    def _active() -> bool:
        task = state["task"]
        return task is not None and not task.done()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "workdir": str(scan.workdir), "running": _active()}

    @app.post("/runs", status_code=202)
    async def start_run(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        if _active():
            raise HTTPException(409, "a keyscan run is already active")
        try:
            run_id = scan.begin(
                payload.get("methods") or [],
                int(payload.get("crawls", 6)),
                payload.get("sources") or None,
            )
        except ActiveRunError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (KeyscanError, TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        state["task"] = asyncio.create_task(scan.execute(run_id))
        return {"run_id": run_id, "status": "running"}

    @app.post("/runs/{run_id}/resume", status_code=202)
    async def resume_run(run_id: str) -> dict[str, Any]:
        if _active():
            raise HTTPException(409, "a keyscan run is already active")
        try:
            state["task"] = asyncio.create_task(scan.resume(run_id))
        except ActiveRunError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyscanError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"run_id": run_id, "status": "running"}

    @app.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict[str, Any]:
        try:
            scan.cancel(run_id)
        except KeyscanError as exc:
            raise HTTPException(404, str(exc)) from exc
        return scan.status(run_id) or {}

    @app.get("/runs")
    def latest_run() -> dict[str, Any]:
        current = scan.status()
        if current is None:
            raise HTTPException(404, "no keyscan runs")
        return current

    @app.get("/runs/{run_id}")
    def run_status(run_id: str) -> dict[str, Any]:
        current = scan.status(run_id)
        if current is None:
            raise HTTPException(404, f"run {run_id} does not exist")
        return current

    @app.get("/runs/{run_id}/logs")
    def run_logs(run_id: str, offset: int = 0, limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
        try:
            entries, next_offset = scan.tail_log(run_id, offset, limit)
        except KeyscanError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"entries": entries, "next_offset": next_offset}

    @app.get("/candidates")
    def candidates(
        since_seq: int = Query(0, ge=0),
        validation: str | None = Query("live"),
        source: str | None = None,
        limit: int = Query(500, ge=1, le=1000),
    ) -> dict[str, Any]:
        """Poll for tokens validated after `since_seq`.

        Pass the returned `next_seq` on the following call.  `more` is true
        when the page filled, meaning the caller should poll again at once
        instead of waiting out its interval.
        """
        if validation in {"", "all"}:
            validation = None
        return scan.candidates_since(
            since_seq=since_seq, validation=validation, source=source, limit=limit,
        )

    return app


def serve(workdir: Path | str | None = None, host: str = "127.0.0.1", port: int = 8787) -> None:
    import uvicorn

    scan = Keyscan(workdir)
    scan.initialize()
    uvicorn.run(create_app(scan), host=host, port=port)
