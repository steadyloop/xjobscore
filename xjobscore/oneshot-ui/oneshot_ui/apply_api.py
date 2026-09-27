"""Auto-apply routes: preview a Greenhouse plan, then submit once confirmed.

Two POSTs per application. The preview fetches the form's questions and pairs
them with profile and saved answers so the client can show exactly what will
be typed. The submit repeats that pairing with the reviewed answers, refuses
without ``confirm``, drives the hosted form, and only then moves the tracker
card to Applied. Greenhouse only, hosted boards only, for this first rollout.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from .api_models import AutoApplyRequest
from .auth import session_account
from .greenhouse_apply import (
    HostedPosting,
    PlanField,
    SubmissionResult,
    bankable_answers,
    build_plan,
    fetch_job,
    missing_required,
    parse_hosted_url,
    submit_hosted,
)
from .resume_render import build_typst_source, compile_pdf

router = APIRouter(prefix="/api/applications", tags=["auto-apply"])


class GreenhouseClient:
    """The network- and process-facing steps, bundled so tests can swap them."""

    def __init__(self, *, headless: bool | None = None, debug_dir: Path | None = None) -> None:
        if headless is None:
            headless = os.environ.get("ONESHOT_APPLY_HEADFUL", "") not in ("1", "true", "yes")
        self.headless = headless
        self.debug_dir = debug_dir or Path(tempfile.gettempdir()) / "oneshot-apply"

    async def fetch_job(self, posting: HostedPosting) -> dict[str, Any]:
        return await fetch_job(posting)

    async def render_resume(
        self,
        profile: dict[str, Any],
        resume: dict[str, Any],
        entries: list[dict[str, Any]],
        skills: list[dict[str, Any]],
    ) -> tuple[bytes, str]:
        source = build_typst_source(profile, resume, entries, skills)
        pdf, _pages, error = await anyio.to_thread.run_sync(compile_pdf, source)
        if pdf is None:
            raise HTTPException(status_code=503, detail=f"Typst compile failed: {error}")
        name = (profile.get("full_name") or "resume").replace(" ", "_")
        return pdf, f"{name}_Resume.pdf"

    async def submit(
        self,
        posting: HostedPosting,
        plan: list[PlanField],
        resume_pdf: bytes,
        resume_filename: str,
    ) -> SubmissionResult:
        return await submit_hosted(
            posting,
            plan,
            resume_pdf,
            resume_filename,
            headless=self.headless,
            debug_dir=self.debug_dir,
        )


def _stores(request: Request, account_id: UUID) -> tuple[Any, Any, Any]:
    store = request.app.state.repository.for_account(account_id)
    resumes = request.app.state.resume_store
    if resumes is None:
        raise HTTPException(status_code=503, detail="Resume store is not available")
    return store, resumes.for_account(account_id), request.app.state.greenhouse


async def _hosted_application(store: Any, application_id: int) -> tuple[dict[str, Any], HostedPosting]:
    application = await store.get_application(application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if application.get("external"):
        raise HTTPException(status_code=422, detail="Only discovered postings can be auto-applied")
    if application.get("stage") != "considering":
        raise HTTPException(status_code=409, detail="Only a Considering application can be submitted")
    posting = parse_hosted_url(application.get("apply_url_snapshot"))
    if posting is None:
        raise HTTPException(
            status_code=422,
            detail="Auto-apply supports Greenhouse hosted apply pages only",
        )
    return application, posting


async def _fetch(client: Any, posting: HostedPosting) -> dict[str, Any]:
    try:
        return await client.fetch_job(posting)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Greenhouse board API failed: {exc}") from exc


async def _choose_resume(
    resumes: Any,
    application: dict[str, Any],
    resume_id: int | None,
    documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """The resume to attach: the requested id, else the card's variant, else the newest."""
    if documents is None:
        documents = await resumes.list_resumes()
    if resume_id is not None:
        return next((item for item in documents if item.get("id") == resume_id), None)
    wanted = str(application.get("resume_variant") or "").strip().casefold()
    if wanted:
        for item in documents:
            if str(item.get("name") or "").casefold() == wanted:
                return item
    return documents[0] if documents else None


def _preview_payload(
    application: dict[str, Any],
    posting: HostedPosting,
    job: dict[str, Any],
    plan: list[PlanField],
    resume: dict[str, Any] | None,
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "application_id": application["id"],
        "board": posting.board,
        "job_id": posting.job_id,
        "apply_url": posting.apply_url,
        "title": job.get("title") or application.get("title"),
        "company": job.get("company_name") or application.get("company"),
        "resume": {"id": resume["id"], "name": resume.get("name")} if resume else None,
        # Every resume the account has, so the review dialog can offer a
        # different one than the default without another round trip.
        "resumes": [{"id": item["id"], "name": item.get("name")} for item in documents],
        "fields": [item.as_dict() for item in plan],
        "missing": [item.name for item in missing_required(plan)],
    }


@router.post("/{application_id}/auto-apply/preview")
async def auto_apply_preview(
    application_id: int,
    request: Request,
    account_id: UUID = Depends(session_account),
) -> dict[str, Any]:
    store, resumes, client = _stores(request, account_id)
    application, posting = await _hosted_application(store, application_id)
    job = await _fetch(client, posting)
    profile = await resumes.get_profile()
    saved = await store.get_apply_answers()
    plan = build_plan(job, profile, saved)
    documents = await resumes.list_resumes()
    resume = await _choose_resume(resumes, application, None, documents)
    return _preview_payload(application, posting, job, plan, resume, documents)


@router.post("/{application_id}/auto-apply")
async def auto_apply_submit(
    application_id: int,
    command: AutoApplyRequest,
    request: Request,
    account_id: UUID = Depends(session_account),
) -> dict[str, Any]:
    if not command.confirm:
        raise HTTPException(status_code=422, detail="Submission requires confirmation")
    store, resumes, client = _stores(request, account_id)

    # One submission per application at a time; a double click must not send
    # the form twice while the first browser is still filling it in.
    inflight: set[tuple[UUID, int]] = request.app.state.auto_apply_inflight
    key = (account_id, application_id)
    if key in inflight:
        raise HTTPException(status_code=409, detail="This application is already being submitted")
    inflight.add(key)
    try:
        application, posting = await _hosted_application(store, application_id)
        job = await _fetch(client, posting)
        profile = await resumes.get_profile()
        saved = await store.get_apply_answers()
        plan = build_plan(job, profile, saved, command.answers)
        missing = missing_required(plan)
        if missing:
            raise HTTPException(
                status_code=422,
                detail="Required answers are missing: " + ", ".join(item.label for item in missing),
            )
        resume = await _choose_resume(resumes, application, command.resume_id)
        if resume is None:
            raise HTTPException(status_code=422, detail="No resume to attach; build one in the Resume studio first")
        inputs = await resumes.render_inputs(resume["id"])
        if inputs is None:
            raise HTTPException(status_code=404, detail="Resume not found")
        pdf, filename = await client.render_resume(*inputs)

        # Remember reviewed custom answers before the browser runs, so a retry
        # after a form-level failure starts from the same answers.
        await store.merge_apply_answers(bankable_answers(plan))

        result = await client.submit(posting, plan, pdf, filename)
        if not result.ok:
            await store.record_application_event(application_id, "auto_apply_failed", result.message)
            raise HTTPException(status_code=502, detail=result.message)

        updated = await store.update_application(
            application_id,
            {"stage": "applied", "resume_variant": resume.get("name") or None},
        )
        await store.record_application_event(
            application_id,
            "auto_applied",
            f"Submitted through Greenhouse at {posting.apply_url}",
        )
        return {
            "application": updated,
            "result": {
                "message": result.message,
                "final_url": result.final_url,
                "screenshot": result.screenshot,
                "resume": {"id": resume["id"], "name": resume.get("name")},
            },
        }
    finally:
        inflight.discard(key)
