"""FastAPI entry point for the localhost feed inspector."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from xjobscore.inspector import config
from xjobscore.inspector.db import CatalogDB
from xjobscore.inspector.persistence import ResultStore
from xjobscore.inspector.postings import detail as posting_detail
from xjobscore.inspector.postings import query as posting_query
from xjobscore.inspector.service import InspectorService
from xjobscore.sources import UnknownSource

STATIC_DIR = Path(__file__).resolve().parent / "static"


class FetchOptions(BaseModel):
    force_full: bool = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    database = CatalogDB()
    await database.connect()
    app.state.inspector_db = database
    result_store = ResultStore(config.persistence_path())
    inspector = InspectorService(
        database, user_agent=config.user_agent(), result_store=result_store
    )
    app.state.inspector = inspector
    try:
        yield
    finally:
        await database.disconnect()


app = FastAPI(title="xjobscore inspector", lifespan=lifespan)


def service() -> InspectorService:
    return app.state.inspector


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/api/feeds")
async def feeds() -> dict[str, Any]:
    rows = await service().feed_rows()
    return {"feeds": rows}


@app.post("/api/feeds/{feed_ref}/fetch")
async def fetch(feed_ref: str, body: FetchOptions | None = None) -> dict[str, Any]:
    options = body or FetchOptions()
    try:
        snapshot = await service().fetch_once_ref(feed_ref, force_full=options.force_full)
    except KeyError:
        raise HTTPException(status_code=404, detail="Feed not found") from None
    except UnknownSource as exc:
        raise HTTPException(
            status_code=422, detail=f"No registered harvester for source: {exc.args[0]}"
        ) from None
    return snapshot.summary


@app.get("/api/results")
async def result_groups() -> dict[str, Any]:
    return {"sources": await service().source_groups()}


@app.get("/api/results/{feed_ref}")
async def result(
    feed_ref: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    snapshot = await service().get_result_ref(feed_ref)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No in-memory result for this feed")
    end = min(offset + limit, snapshot.item_count)
    return {
        "result": snapshot.summary,
        "network": snapshot.network,
        "envelope": snapshot.envelope,
        "items": [snapshot.item_summary(i) for i in range(offset, end)],
        "offset": offset,
        "limit": limit,
        "has_more": end < snapshot.item_count,
    }


@app.get("/api/results/{feed_ref}/items/{item_index}")
async def item(feed_ref: str, item_index: int) -> dict[str, Any]:
    snapshot = await service().get_result_ref(feed_ref)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No in-memory result for this feed")
    if item_index < 0 or item_index >= snapshot.item_count:
        raise HTTPException(status_code=404, detail="Item not found")
    return snapshot.item_detail(item_index)


@app.get("/api/results/{feed_ref}/envelope")
async def envelope(feed_ref: str) -> dict[str, Any]:
    snapshot = await service().get_result_ref(feed_ref)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No in-memory result for this feed")
    return snapshot.envelope_detail()


@app.get("/api/postings")
async def postings(
    source: str | None = None,
    search: str = "",
    mode: str = "exclude_full_time",
    us_only: bool = True,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    if mode not in {
        "all",
        "exclude_full_time",
        "intern_explicit",
        "intern_implicit",
        "full_time_internship",
    }:
        raise HTTPException(status_code=400, detail=f"Unknown posting filter mode: {mode}")
    try:
        return posting_query(
            config.persistence_path(),
            source=source,
            search=search,
            mode=mode,
            us_only=us_only,
            limit=limit,
            offset=offset,
        )
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail=f"Inspector database unavailable: {exc}") from exc


@app.get("/api/postings/{result_id}/items/{item_index}")
async def posting_item(result_id: str, item_index: int) -> dict[str, Any]:
    try:
        result = posting_detail(config.persistence_path(), result_id, item_index)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail=f"Inspector database unavailable: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Posting not found")
    return result


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
