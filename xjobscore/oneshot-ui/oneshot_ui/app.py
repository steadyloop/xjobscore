"""FastAPI surface for the local, manual-fetch-only OneShot board."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api_models import (
    ApplicationCreate,
    ApplicationEventCreate,
    CaptureResolveCommand,
    ClassificationCorrection,
    DedupeDecisionCommand,
    FetchBatchCommand,
    ManualCaptureCommand,
    ProfileUpdate,
    SourceSelectionCommand,
    TriageCommand,
)
from .board import BoardRepository, VALID_TYPES
from .config import Settings, load_settings
from .db import Database
from .gateway import AdapterGateway
from .ingest import BoardIngestor, IngestCounts
from .scheduler import (
    FeedNotEnabled,
    FeedNotFound,
    ManualFetchCoordinator,
    ManualFetchResult,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"


def _fetch_summary(
    result: ManualFetchResult,
    counts: IngestCounts,
    *,
    ingestion_error: str | None = None,
) -> dict[str, Any]:
    return {
        "feed": {
            "source": result.feed.source,
            "key": result.feed.key,
            "company": result.feed.company_name,
        },
        "poll_id": result.poll_id,
        "outcome": result.outcome,
        "health": result.health,
        "parser_state": result.parser_state,
        "http_status": result.record.http_status if result.record else None,
        "item_count": len(result.record.items) if result.record else 0,
        "error": result.error,
        "ingestion_error": ingestion_error,
        "ingested": asdict(counts),
    }


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    gateway: AdapterGateway | None = None,
) -> FastAPI:
    resolved = settings or load_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        db = database or Database(resolved.database_path)
        adapter_gateway = gateway or AdapterGateway(user_agent=resolved.user_agent)
        coordinator = ManualFetchCoordinator(
            db,
            adapter_gateway,
            max_concurrency=resolved.fetch_concurrency,
            max_selection=25,
        )
        await coordinator.initialize()  # catalog metadata only; performs zero fetches
        application.state.database = db
        application.state.coordinator = coordinator
        application.state.ingestor = BoardIngestor(db)
        application.state.board = BoardRepository(db)
        try:
            yield
        finally:
            await coordinator.close()

    application = FastAPI(
        title="xjobscore OneShot",
        version="0.1.0",
        lifespan=lifespan,
    )

    def board(request: Request) -> BoardRepository:
        return request.app.state.board

    @application.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        return await board(request).health()

    @application.get("/api/jobs")
    async def jobs(
        request: Request,
        opportunity_type: list[str] = Query(default=[]),
        q: str = "",
        state: str | None = None,
        eligibility: str | None = None,
        sort: str = "fresh",
        needs_classification: bool = False,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        compact: bool = False,
    ) -> dict[str, Any]:
        del compact  # presentation-only; accepted for a stable browser contract
        unknown = [value for value in opportunity_type if value not in VALID_TYPES]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown opportunity_type: {unknown[0]}")
        return await board(request).list_jobs(
            opportunity_types=opportunity_type,
            q=q,
            state=state,
            eligibility=eligibility,
            sort=sort,
            include_unclassified=needs_classification,
            limit=limit,
            offset=offset,
        )

    @application.post("/api/jobs")
    async def capture_job(request: Request, command: ManualCaptureCommand) -> dict[str, Any]:
        if not command.url:
            raise HTTPException(status_code=422, detail="url is required")
        try:
            return await board(request).capture_job(
                url=command.url,
                company=command.company,
                title=command.title,
                description=command.description,
                location=command.location,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get("/api/jobs/{job_id}")
    async def job_detail(request: Request, job_id: str) -> dict[str, Any]:
        result = await board(request).get_job(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    @application.post("/api/jobs/{job_id}/triage")
    async def triage(
        request: Request, job_id: str, command: TriageCommand
    ) -> dict[str, Any]:
        try:
            result = await board(request).set_triage(job_id, command.state)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    @application.post("/api/jobs/{job_id}/classification")
    async def correct_classification(
        request: Request, job_id: str, command: ClassificationCorrection
    ) -> dict[str, Any]:
        result = await board(request).set_classification(
            job_id, command.opportunity_type, command.rationale
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    @application.post("/api/capture/resolve")
    async def resolve_capture(
        request: Request, command: CaptureResolveCommand
    ) -> dict[str, Any]:
        if not command.url:
            raise HTTPException(status_code=422, detail="url is required")
        return await board(request).resolve_capture(command.url)

    @application.get("/api/today")
    async def today(
        request: Request,
        opportunity_type: list[str] = Query(default=[]),
        needs_classification: bool = False,
    ) -> dict[str, Any]:
        unknown = [value for value in opportunity_type if value not in VALID_TYPES]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown opportunity_type: {unknown[0]}")
        return await board(request).today(opportunity_type, needs_classification)

    @application.get("/api/sources")
    async def sources(
        request: Request,
        q: str = "",
        source: str | None = None,
        selection: Literal["all", "enabled", "disabled"] = "all",
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return await board(request).list_sources(
            q=q, source=source, selection=selection, limit=limit, offset=offset
        )

    @application.get("/api/source-groups")
    async def source_groups(
        request: Request,
        q: str = "",
        selection: Literal["all", "enabled", "disabled"] = "enabled",
    ) -> dict[str, Any]:
        return await board(request).list_source_groups(q=q, selection=selection)

    @application.put("/api/sources/vendor/{source}/selection")
    async def select_vendor(
        request: Request, source: str, command: SourceSelectionCommand
    ) -> dict[str, Any]:
        result = await board(request).set_vendor_selection(source, command.enabled)
        if result is None:
            raise HTTPException(status_code=404, detail="Catalog source not found")
        return result

    @application.put("/api/sources/{feed_reference}/selection")
    async def select_source(
        request: Request, feed_reference: str, command: SourceSelectionCommand
    ) -> dict[str, Any]:
        result = await board(request).set_source_selection(
            feed_reference, command.enabled
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Catalog feed not found")
        return result

    async def run_fetch(request: Request, feed_reference: str) -> dict[str, Any]:
        try:
            fetched = await request.app.state.coordinator.trigger(feed_reference)
        except FeedNotFound as exc:
            raise HTTPException(status_code=404, detail="Feed not found") from exc
        except FeedNotEnabled as exc:
            raise HTTPException(
                status_code=409,
                detail="Enable this company in Source Coverage before fetching",
            ) from exc
        try:
            counts = await request.app.state.ingestor.ingest(fetched)
        except Exception as exc:
            return _fetch_summary(
                fetched,
                IngestCounts(),
                ingestion_error=f"{type(exc).__name__}: {exc}",
            )
        return _fetch_summary(fetched, counts)

    @application.post("/api/sources/{feed_reference}/fetch")
    async def fetch_source(request: Request, feed_reference: str) -> dict[str, Any]:
        return await run_fetch(request, feed_reference)

    @application.post("/api/sources/{feed_reference}/replay")
    async def replay_source(request: Request, feed_reference: str) -> dict[str, Any]:
        try:
            replayed = await request.app.state.coordinator.replay_latest(feed_reference)
        except FeedNotFound as exc:
            raise HTTPException(status_code=404, detail="Feed not found") from exc
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            counts = await request.app.state.ingestor.ingest(replayed)
            summary = _fetch_summary(replayed, counts)
        except Exception as exc:
            summary = _fetch_summary(
                replayed,
                IngestCounts(),
                ingestion_error=f"{type(exc).__name__}: {exc}",
            )
        summary["replayed"] = True
        return summary

    @application.post("/api/sources/fetch")
    async def fetch_sources(
        request: Request, command: FetchBatchCommand
    ) -> dict[str, Any]:
        try:
            fetched = await request.app.state.coordinator.trigger_many(command.feed_refs)
        except FeedNotFound as exc:
            raise HTTPException(status_code=404, detail=f"Feed not found: {exc}") from exc
        except FeedNotEnabled as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Enable every selected company before fetching: {exc}",
            ) from exc
        results = []
        for item in fetched:
            try:
                counts = await request.app.state.ingestor.ingest(item)
                results.append(_fetch_summary(item, counts))
            except Exception as exc:
                # Each network result is already durably captured. A broken
                # parser/read-model item must not strand later selected feeds.
                results.append(
                    _fetch_summary(
                        item,
                        IngestCounts(),
                        ingestion_error=f"{type(exc).__name__}: {exc}",
                    )
                )
        return {"results": results, "total": len(results)}

    @application.get("/api/dedupe")
    async def dedupe(request: Request) -> dict[str, Any]:
        return await board(request).list_dedupe()

    @application.post("/api/dedupe/{candidate_id}/decision")
    async def dedupe_decision(
        request: Request, candidate_id: str, command: DedupeDecisionCommand
    ) -> dict[str, Any]:
        try:
            result = await board(request).decide_dedupe(
                candidate_id, command.action, command.rationale
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Dedupe candidate not found")
        return result

    @application.post("/api/jobs/{job_id}/applications")
    async def create_application_record(
        request: Request, job_id: str, command: ApplicationCreate
    ) -> dict[str, Any]:
        try:
            result = await board(request).create_application(
                job_id,
                stage=command.stage,
                note=command.note,
                submitted_at=command.submitted_at,
                resume_variant=command.resume_variant,
                source_observation_id=command.source_observation_id,
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    @application.get("/api/applications")
    async def applications(
        request: Request,
        opportunity_type: list[str] = Query(default=[]),
        needs_classification: bool = False,
    ) -> dict[str, Any]:
        unknown = [value for value in opportunity_type if value not in VALID_TYPES]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown opportunity_type: {unknown[0]}")
        return await board(request).list_applications(
            opportunity_type, needs_classification
        )

    @application.get("/api/applications/{application_id}")
    async def application_detail(request: Request, application_id: str) -> dict[str, Any]:
        result = await board(request).get_application(application_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Application not found")
        return result

    @application.post("/api/applications/{application_id}/events")
    async def application_event(
        request: Request,
        application_id: str,
        command: ApplicationEventCreate,
    ) -> dict[str, Any]:
        try:
            result = await board(request).add_application_event(
                application_id,
                event_type=command.event_type,
                stage=command.stage,
                note=command.note,
                occurred_at=command.occurred_at.isoformat() if command.occurred_at else None,
                due_at=command.due_at.isoformat() if command.due_at else None,
                confirmed=command.confirmed,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Application not found")
        return result

    @application.get("/api/profile")
    async def profile(request: Request) -> dict[str, Any]:
        return await board(request).get_profile()

    @application.put("/api/profile")
    async def update_profile(
        request: Request, command: ProfileUpdate
    ) -> dict[str, Any]:
        profile = await board(request).update_profile(command.model_dump())
        # Profile policy is evaluated entirely from durable observations. A
        # preference change must take effect immediately without refetching a
        # vendor or waiting for another manual source action.
        await request.app.state.ingestor.recompute_profile(profile)
        return profile

    @application.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return application


app = create_app()
