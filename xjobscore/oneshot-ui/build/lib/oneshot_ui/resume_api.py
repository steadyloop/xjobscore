"""FastAPI router for the PostgreSQL-backed resume workspace.

Endpoints are `async def` because the store is asyncpg. The one thing that must
not follow them onto the event loop is Typst: `compile_pdf` shells out to a
binary for up to 30 seconds, so it is pushed to a worker thread at the call site
below. Awaiting it inline would stall every other request in the process, not
just this page.
"""

from typing import Any
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .resume_models import (
    BulletCreate,
    BulletUpdate,
    EntryCreate,
    EntryUpdate,
    ProfileUpdate,
    ResumeCreate,
    ResumeUpdate,
    SkillCreate,
    TargetJobCreate,
    TargetJobUpdate,
)
from .resume_render import (
    build_typst_source,
    compile_pdf,
    lint_resume,
    typst_available,
)
from .auth import require_account


async def _bind_account(
    request: Request, account_id: UUID = Depends(require_account)
) -> None:
    """Resolve the account once per request, for every route on this router.

    It has to run as a real dependency rather than a call inside ``store()``:
    only dependencies go through FastAPI's resolution, so only dependencies can
    be replaced via ``dependency_overrides`` in tests.
    """
    request.state.account_id = account_id


router = APIRouter(
    prefix="/api/resume",
    tags=["resume"],
    dependencies=[Depends(_bind_account)],
)


def store(request: Request) -> Any:
    """The resume store, scoped to the account ``_bind_account`` resolved."""
    return request.app.state.resume_store.for_account(request.state.account_id)


def _or_404(result: Any, what: str) -> Any:
    if result is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return result


@router.get("/profile")
async def get_profile(request: Request) -> dict[str, Any]:
    return await store(request).get_profile()


@router.put("/profile")
async def update_profile(request: Request, command: ProfileUpdate) -> dict[str, Any]:
    return await store(request).update_profile(command.model_dump(exclude_unset=True))


@router.get("/entries")
async def list_entries(request: Request) -> dict[str, Any]:
    return {"entries": await store(request).list_entries()}


@router.post("/entries", status_code=201)
async def create_entry(request: Request, command: EntryCreate) -> dict[str, Any]:
    return await store(request).create_entry(command.model_dump())


@router.patch("/entries/{entry_id}")
async def update_entry(request: Request, entry_id: int, command: EntryUpdate) -> dict[str, Any]:
    fields = command.model_dump(exclude_unset=True)
    return _or_404(await store(request).update_entry(entry_id, fields), "Entry")


@router.delete("/entries/{entry_id}", status_code=204)
async def delete_entry(request: Request, entry_id: int) -> Response:
    _or_404(await store(request).delete_entry(entry_id) or None, "Entry")
    return Response(status_code=204)


@router.post("/entries/{entry_id}/bullets", status_code=201)
async def create_bullet(request: Request, entry_id: int, command: BulletCreate) -> dict[str, Any]:
    return _or_404(await store(request).create_bullet(entry_id, command.model_dump()), "Entry")


@router.patch("/bullets/{bullet_id}")
async def update_bullet(request: Request, bullet_id: int, command: BulletUpdate) -> dict[str, Any]:
    fields = command.model_dump(exclude_unset=True)
    return _or_404(await store(request).update_bullet(bullet_id, fields), "Bullet")


@router.delete("/bullets/{bullet_id}", status_code=204)
async def delete_bullet(request: Request, bullet_id: int) -> Response:
    _or_404(await store(request).delete_bullet(bullet_id) or None, "Bullet")
    return Response(status_code=204)


@router.get("/skills")
async def list_skills(request: Request) -> dict[str, Any]:
    return {"skills": await store(request).list_skills()}


@router.post("/skills", status_code=201)
async def create_skill(request: Request, command: SkillCreate) -> dict[str, Any]:
    result = await store(request).create_skill(command.model_dump())
    if result is None:
        raise HTTPException(status_code=409, detail="Skill already exists in that category")
    return result


@router.delete("/skills/{skill_id}", status_code=204)
async def delete_skill(request: Request, skill_id: int) -> Response:
    _or_404(await store(request).delete_skill(skill_id) or None, "Skill")
    return Response(status_code=204)


@router.get("/jobs")
async def list_target_jobs(request: Request) -> dict[str, Any]:
    return {"jobs": await store(request).list_target_jobs()}


@router.post("/jobs", status_code=201)
async def create_target_job(request: Request, command: TargetJobCreate) -> dict[str, Any]:
    return await store(request).create_target_job(command.model_dump())


@router.patch("/jobs/{job_id}")
async def update_target_job(request: Request, job_id: int, command: TargetJobUpdate) -> dict[str, Any]:
    fields = command.model_dump(exclude_unset=True)
    return _or_404(await store(request).update_target_job(job_id, fields), "Target job")


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_target_job(request: Request, job_id: int) -> Response:
    _or_404(await store(request).delete_target_job(job_id) or None, "Target job")
    return Response(status_code=204)


@router.get("/jobs/{job_id}/coverage")
async def coverage(request: Request, job_id: int, resume_id: int | None = None) -> dict[str, Any]:
    return _or_404(await store(request).coverage(job_id, resume_id), "Target job")


@router.get("/resumes")
async def list_resumes(request: Request) -> dict[str, Any]:
    return {"resumes": await store(request).list_resumes(), "typst_available": typst_available()}


@router.post("/resumes", status_code=201)
async def create_resume(request: Request, command: ResumeCreate) -> dict[str, Any]:
    return await store(request).create_resume(command.model_dump())


@router.get("/resumes/{resume_id}")
async def get_resume(request: Request, resume_id: int) -> dict[str, Any]:
    return _or_404(await store(request).get_resume(resume_id), "Resume")


@router.patch("/resumes/{resume_id}")
async def update_resume(request: Request, resume_id: int, command: ResumeUpdate) -> dict[str, Any]:
    fields = command.model_dump(exclude_unset=True)
    return _or_404(await store(request).update_resume(resume_id, fields), "Resume")


@router.delete("/resumes/{resume_id}", status_code=204)
async def delete_resume(request: Request, resume_id: int) -> Response:
    _or_404(await store(request).delete_resume(resume_id) or None, "Resume")
    return Response(status_code=204)


async def _render_inputs(request: Request, resume_id: int) -> tuple[Any, Any, Any, Any]:
    # One call, one snapshot: profile, entries, and skills all live in the same
    # workspace row now, so the four separate reads this replaced could render a
    # resume against a bank state that never existed.
    return _or_404(await store(request).render_inputs(resume_id), "Resume")


async def _compile(source: str) -> tuple[bytes | None, int, str]:
    # Typst is a subprocess with a 30s timeout. On the event loop that would
    # stall the whole process; in a worker thread it stalls only this request.
    return await anyio.to_thread.run_sync(compile_pdf, source)


@router.get("/resumes/{resume_id}/source")
async def resume_source(request: Request, resume_id: int) -> Response:
    profile, resume, entries, skills = await _render_inputs(request, resume_id)
    source = build_typst_source(profile, resume, entries, skills)
    return Response(content=source, media_type="text/plain; charset=utf-8")


@router.get("/resumes/{resume_id}/check")
async def check_resume(request: Request, resume_id: int) -> dict[str, Any]:
    profile, resume, entries, skills = await _render_inputs(request, resume_id)
    page_count = None
    compile_error = ""
    if typst_available():
        source = build_typst_source(profile, resume, entries, skills)
        pdf, page_count, compile_error = await _compile(source)
        if pdf is None:
            page_count = None
    problems = lint_resume(profile, resume, entries, page_count)
    return {
        "problems": problems,
        "page_count": page_count,
        "typst_available": typst_available(),
        "compile_error": compile_error,
    }


@router.get("/resumes/{resume_id}/pdf")
async def resume_pdf(request: Request, resume_id: int) -> Response:
    profile, resume, entries, skills = await _render_inputs(request, resume_id)
    source = build_typst_source(profile, resume, entries, skills)
    pdf, page_count, error = await _compile(source)
    if pdf is None:
        raise HTTPException(status_code=503, detail=f"Typst compile failed: {error}")
    name = (profile.get("full_name") or "resume").replace(" ", "_")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{name}_Resume.pdf"',
            "X-Page-Count": str(page_count),
        },
    )
