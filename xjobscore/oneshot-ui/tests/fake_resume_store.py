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
    "name", "target_job_id", "variant", "template", "availability_line", "education_note", "selection",
)


class _Account:
    def __init__(self) -> None:
        self.workspace = workspace.blank_workspace()
        self.targets: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        # Stands in for the `job_application` rows the real store reads across
        # the table boundary. Seeded by tests via add_application().
        self.applications: dict[int, dict[str, Any]] = {}
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

    def add_application(self, application_id: int, **snapshot: Any) -> None:
        """Seed a queued application for this account (test helper)."""
        self._state.applications[application_id] = {
            "company": snapshot.get("company", "Acme"),
            "title": snapshot.get("title", "Intern"),
            "url": snapshot.get("url", ""),
            "jd_text": snapshot.get("jd_text", ""),
            "date_posted": snapshot.get("date_posted", ""),
        }

    # -- variants --------------------------------------------------------

    async def list_variants(self):
        return workspace.list_variants(self._state.workspace)

    async def set_variants(self, raw):
        return workspace.set_variants(self._state.workspace, raw)

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

    def _check_audience(self, audience):
        workspace.check_audience(self._state.workspace, audience)

    def _check_variant(self, variant):
        workspace.check_variant(self._state.workspace, variant)

    async def create_bullet(self, entry_id, fields):
        self._check_audience(fields.get("audience"))
        return workspace.add_bullet(self._state.workspace, entry_id, fields)

    async def update_bullet(self, bullet_id, fields):
        self._check_audience(fields.get("audience"))
        return workspace.edit_bullet(self._state.workspace, bullet_id, fields)

    async def delete_bullet(self, bullet_id):
        return workspace.remove_bullet(self._state.workspace, bullet_id)

    # -- skills ----------------------------------------------------------

    async def list_skills(self):
        return workspace.list_skills(self._state.workspace)

    async def create_skill(self, fields):
        self._check_audience(fields.get("audience"))
        return workspace.add_skill(self._state.workspace, fields)

    async def update_skill(self, skill_id, fields):
        self._check_audience(fields.get("audience"))
        return workspace.edit_skill(self._state.workspace, skill_id, fields)

    async def delete_skill(self, skill_id):
        return workspace.remove_skill(self._state.workspace, skill_id)

    # -- target jobs -----------------------------------------------------

    async def list_target_jobs(self):
        return deepcopy(sorted(self._state.targets, key=lambda t: -t["id"]))

    async def create_target_from_application(self, application_id):
        snapshot = self._state.applications.get(application_id)
        if snapshot is None:
            return None
        existing = next(
            (t for t in self._state.targets if t["job_application_id"] == application_id),
            None,
        )
        if existing is not None:
            return deepcopy(existing)
        target = {
            "id": self._state.allocate(),
            **snapshot,
            "note": "",
            "job_application_id": application_id,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        self._state.targets.append(target)
        return deepcopy(target)

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
        self._check_variant(fields.get("variant"))
        document = {
            "id": self._state.allocate(),
            "name": fields["name"],
            "target_job_id": fields.get("target_job_id"),
            "variant": fields.get("variant", ""),
            "template": fields.get("template", "main"),
            "availability_line": fields.get("availability_line", ""),
            "education_note": fields.get("education_note", ""),
            "selection": fields.get("selection", {}),
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        self._state.documents.append(document)
        return deepcopy(document)

    async def update_resume(self, resume_id, fields):
        self._check_variant(fields.get("variant"))
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
