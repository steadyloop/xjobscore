"""FastAPI surface for the PostgreSQL-backed OneShot UI."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .api_models import (
    ApplicationUpdate,
    ExternalApplicationCreate,
)
from .config import Settings, load_settings
from .repository import PostgresRepository, VALID_OPPORTUNITY_TYPES


STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    settings: Settings | None = None,
    *,
    repository: Any | None = None,
) -> FastAPI:
    resolved = settings or load_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        store = repository or PostgresRepository(resolved.dsn)
        connect = getattr(store, "connect", None)
        if callable(connect):
            await connect()
        application.state.repository = store
        try:
            yield
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                await close()

    application = FastAPI(
        title="xjobscore OneShot",
        version="0.2.0",
        lifespan=lifespan,
    )

    def store(request: Request) -> Any:
        return request.app.state.repository

    @application.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @application.get("/api/jobs")
    async def jobs(
        request: Request,
        opportunity_type: list[str] = Query(default=[]),
        q: str = "",
        sort: str = "fresh",
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        unknown = [value for value in opportunity_type if value not in VALID_OPPORTUNITY_TYPES]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown opportunity_type: {unknown[0]}",
            )
        return await store(request).list_jobs(
            opportunity_types=opportunity_type,
            q=q,
            sort=sort,
            limit=limit,
            offset=offset,
        )

    @application.get("/api/source-coverage")
    async def source_coverage(request: Request) -> dict[str, Any]:
        return await store(request).source_coverage()

    @application.post("/api/jobs/{job_id}/applications")
    async def create_application_record(
        request: Request,
        job_id: int,
    ) -> dict[str, Any]:
        try:
            result = await store(request).create_application(job_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    @application.post("/api/applications/external")
    async def create_external_application_record(
        request: Request,
        command: ExternalApplicationCreate,
    ) -> dict[str, Any]:
        try:
            return await store(request).create_external_application(
                company=command.company,
                title=command.title,
                url=command.url,
                location=command.location,
                note=command.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get("/api/applications")
    async def applications(request: Request) -> dict[str, Any]:
        return await store(request).list_applications()

    @application.get("/api/applications/{application_id}")
    async def application_detail(
        request: Request,
        application_id: int,
    ) -> dict[str, Any]:
        result = await store(request).get_application(application_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Application not found")
        return result

    @application.patch("/api/applications/{application_id}")
    async def update_application(
        request: Request,
        application_id: int,
        command: ApplicationUpdate,
    ) -> dict[str, Any]:
        try:
            result = await store(request).update_application(
                application_id,
                command.model_dump(exclude_unset=True),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Application not found")
        return result

    @application.delete("/api/applications/{application_id}", status_code=204)
    async def delete_application(request: Request, application_id: int) -> Response:
        if not await store(request).delete_application(application_id):
            raise HTTPException(status_code=404, detail="Application not found")
        return Response(status_code=204)

    @application.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/discover/")
    async def discover() -> FileResponse:
        return FileResponse(STATIC_DIR / "discover" / "index.html")

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return application


app = create_app()
