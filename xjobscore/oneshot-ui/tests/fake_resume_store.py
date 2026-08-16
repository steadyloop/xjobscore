"""An in-memory stand-in for PostgresResumeStore.

It shares `resume_workspace` with the real store, so it cannot drift on
ordering, position allocation, duplicate-skill 409s, or missing-row 404s — only
on the load/save shell, which is exactly what the opt-in PostgreSQL tests in
test_resume_postgres.py cover.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from oneshot_ui import resume_workspace as workspace
from oneshot_ui.repository import LOCAL_ACCOUNT_ID

RESUME_FIELDS = (
    "name", "target_job_id", "variant", "availability_line", "education_note", "selection",
)
TARGET_FIELDS = ("company", "title", "url", "jd_text", "date_posted", "note")


class _Account:
    def __init__(self) -> None:
        self.workspace = workspace.blank_workspace()
        self.targets: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.next_row_id = 1

    def allocate(self) -> int:
        row_id = self.next_row_id
        self.next_row_id += 1
        return row_id


class InMemoryResumeStore:
    def __init__(
        self,
        account_id: UUID = LOCAL_ACCOUNT_ID,
        accounts: dict[UUID, _Account] | None = None,
    ) -> None:
        self.account_id = account_id
        # Shared across clones so for_account() isolates like the real store.
        self._accounts = {} if accounts is None else accounts

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    def for_account(self, account_id: UUID) -> "InMemoryResumeStore":
        return InMemoryResumeStore(account_id, self._accounts)

    @property
    def _state(self) -> _Account:
        return self._accounts.setdefault(self.account_id, _Account())

    # -- profile ---------------------------------------------------------

    async def get_profile(self):
        return workspace.public_profile(self._state.workspace)

    async def update_profile(self, fields):
        return workspace.apply_profile(self._state.workspace, fields)

    # -- entries and bullets ---------------------------------------------

    async def list_entries(self):
        return workspace.list_entries(self._state.workspace)

    async def create_entry(self, fields):
        return workspace.add_entry(self._state.workspace, fields)

    async def update_entry(self, entry_id, fields):
        return workspace.edit_entry(self._state.workspace, entry_id, fields)

    async def delete_entry(self, entry_id):
        return workspace.remove_entry(self._state.workspace, entry_id)

    async def create_bullet(self, entry_id, fields):
        return workspace.add_bullet(self._state.workspace, entry_id, fields)

    async def update_bullet(self, bullet_id, fields):
        return workspace.edit_bullet(self._state.workspace, bullet_id, fields)

    async def delete_bullet(self, bullet_id):
        return workspace.remove_bullet(self._state.workspace, bullet_id)

    # -- skills ----------------------------------------------------------

    async def list_skills(self):
        return workspace.list_skills(self._state.workspace)

    async def create_skill(self, fields):
        return workspace.add_skill(self._state.workspace, fields)

    async def delete_skill(self, skill_id):
        return workspace.remove_skill(self._state.workspace, skill_id)

    # -- target jobs -----------------------------------------------------

    async def list_target_jobs(self):
        return deepcopy(sorted(self._state.targets, key=lambda t: -t["id"]))

    async def create_target_job(self, fields):
        target = {
            "id": self._state.allocate(),
            "company": fields["company"],
            "title": fields["title"],
            "url": fields.get("url", ""),
            "jd_text": fields.get("jd_text", ""),
            "date_posted": fields.get("date_posted", ""),
            "note": fields.get("note", ""),
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        self._state.targets.append(target)
        return deepcopy(target)

    def _target(self, job_id):
        return next((t for t in self._state.targets if t["id"] == job_id), None)

    async def update_target_job(self, job_id, fields):
        target = self._target(job_id)
        if target is None:
            return None
        for key, value in fields.items():
            if key in TARGET_FIELDS and value is not None:
                target[key] = value
        return deepcopy(target)

    async def delete_target_job(self, job_id):
        target = self._target(job_id)
        if target is None:
            return False
        self._state.targets.remove(target)
        # Mirrors ON DELETE SET NULL (target_job_id) on resume_document.
        for document in self._state.documents:
            if document["target_job_id"] == job_id:
                document["target_job_id"] = None
        return True

    # -- resumes ---------------------------------------------------------

    async def list_resumes(self):
        ordered = sorted(
            self._state.documents, key=lambda d: (d["updated_at"], d["id"]), reverse=True
        )
        return deepcopy(ordered)

    def _document(self, resume_id):
        return next((d for d in self._state.documents if d["id"] == resume_id), None)

    async def get_resume(self, resume_id):
        document = self._document(resume_id)
        return deepcopy(document) if document is not None else None

    async def create_resume(self, fields):
        document = {
            "id": self._state.allocate(),
            "name": fields["name"],
            "target_job_id": fields.get("target_job_id"),
            "variant": fields.get("variant", "swe"),
            "availability_line": fields.get("availability_line", ""),
            "education_note": fields.get("education_note", ""),
            "selection": fields.get("selection", {}),
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        self._state.documents.append(document)
        return deepcopy(document)

    async def update_resume(self, resume_id, fields):
        updates = {
            k: v
            for k, v in fields.items()
            if k in RESUME_FIELDS and v is not None and k != "target_job_id"
        }
        if not updates and "target_job_id" not in fields:
            return await self.get_resume(resume_id)
        document = self._document(resume_id)
        if document is None:
            return None
        document.update(updates)
        if "target_job_id" in fields:
            document["target_job_id"] = fields["target_job_id"]
        return deepcopy(document)

    async def delete_resume(self, resume_id):
        document = self._document(resume_id)
        if document is None:
            return False
        self._state.documents.remove(document)
        return True

    # -- derived ---------------------------------------------------------

    async def coverage(self, job_id, resume_id=None):
        job = self._target(job_id)
        if job is None:
            return None
        shown_ids = set()
        if resume_id is not None:
            document = self._document(resume_id)
            if document is not None:
                shown_ids = set(document["selection"].get("skills", []))
        return workspace.compute_coverage(
            job, workspace.list_skills(self._state.workspace), shown_ids
        )

    async def render_inputs(self, resume_id):
        document = self._document(resume_id)
        if document is None:
            return None
        state = self._state.workspace
        return (
            workspace.public_profile(state),
            deepcopy(document),
            workspace.list_entries(state),
            workspace.list_skills(state),
        )
