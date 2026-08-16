"""PostgreSQL store for the resume workspace.

Three tables, all keyed by account: ``resume_workspace`` holds the whole content
bank as one jsonb row, ``resume_target`` the pasted job descriptions, and
``resume_document`` the assembled resumes.

The bank is one row rather than a table per content type because the atomic unit
is the bullet, not the resume: a resume is a *selection* of bullet and skill ids
plus template parameters, and those ids have to stay stable. All the list work
lives in :mod:`resume_workspace`; this module is the load / mutate / save shell
around it.

asyncpg returns ``jsonb`` as a ``str`` unless a codec is registered, so every
read decodes explicitly and every write passes ``json.dumps`` with a ``::jsonb``
cast — the same convention ``repository.py`` uses. A pool-level codec was
rejected: it would not exist on a pool handed in through ``for_account`` or a
test fixture, so the bug would appear on some code paths only.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime
import json
from typing import Any, AsyncIterator
from uuid import UUID

import asyncpg

from . import resume_workspace as workspace
from .repository import LOCAL_ACCOUNT_ID

RESUME_FIELDS = (
    "name", "target_job_id", "variant", "availability_line", "education_note", "selection",
)
TARGET_FIELDS = ("company", "title", "url", "jd_text", "date_posted", "note")


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _json_value(value: Any, fallback: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback if value is None else value


def _target(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "company": row["company"],
        "title": row["title"],
        "url": row["url"],
        "jd_text": row["jd_text"],
        "date_posted": row["date_posted"],
        "note": row["note"],
        "created_at": _iso(row["created_at"]),
    }


def _document(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "target_job_id": row["target_job_id"],
        "variant": row["variant"],
        "availability_line": row["availability_line"],
        "education_note": row["education_note"],
        "selection": _json_value(row["selection"], {}),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


class PostgresResumeStore:
    """Resume-studio queries and commands over an ``asyncpg`` pool."""

    def __init__(
        self,
        dsn: str,
        *,
        account_id: UUID = LOCAL_ACCOUNT_ID,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        self.dsn = dsn
        self.account_id = account_id
        self.pool = pool
        self._owns_pool = pool is None

    async def connect(self) -> None:
        if self.pool is not None:
            return
        if not self.dsn:
            raise RuntimeError(
                "No PostgreSQL DSN. Set XJOBSCORE_ONESHOT_DSN, XJOBSCORE_DSN, "
                "or DATABASE_URL."
            )
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=8)

    async def close(self) -> None:
        if self.pool is not None and self._owns_pool:
            await self.pool.close()
            self.pool = None

    def _ready(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("PostgresResumeStore.connect() has not been called")
        return self.pool

    def for_account(self, account_id: UUID) -> "PostgresResumeStore":
        """A per-request view of the same pool, scoped to one account."""
        return PostgresResumeStore(self.dsn, account_id=account_id, pool=self._ready())

    # -- workspace access ------------------------------------------------

    @staticmethod
    def _decode(row: asyncpg.Record | None) -> dict[str, Any]:
        if row is None:
            return workspace.blank_workspace()
        return workspace.normalize(
            {
                "profile": _json_value(row["profile"], {}),
                "entries": _json_value(row["entries"], []),
                "skills": _json_value(row["skills"], []),
                "next_item_id": row["next_item_id"],
            }
        )

    async def _read(self) -> dict[str, Any]:
        """Read-only view. Creates no row: a GET must not write."""
        row = await self._ready().fetchrow(
            """
            SELECT profile, entries, skills, next_item_id
            FROM resume_workspace WHERE account_id = $1
            """,
            self.account_id,
        )
        return self._decode(row)

    @asynccontextmanager
    async def _edit(self) -> AsyncIterator[dict[str, Any]]:
        """Lock the workspace row, yield it for mutation, write it back.

        The upsert creates the row on first use and row-locks it either way, in
        one round trip. The lock is load-bearing: without it two concurrent
        bullet inserts both read the same ``entries`` array and the second write
        silently discards the first.

        The write-back runs only on a clean exit — an exception propagates and
        the transaction rolls back.
        """
        async with self._ready().acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                INSERT INTO resume_workspace (account_id) VALUES ($1)
                ON CONFLICT (account_id) DO UPDATE
                    SET updated_at = resume_workspace.updated_at
                RETURNING profile, entries, skills, next_item_id
                """,
                self.account_id,
            )
            state = self._decode(row)
            yield state
            await connection.execute(
                """
                UPDATE resume_workspace
                SET profile      = $2::jsonb,
                    entries      = $3::jsonb,
                    skills       = $4::jsonb,
                    next_item_id = $5,
                    updated_at   = now()
                WHERE account_id = $1
                """,
                self.account_id,
                json.dumps(state["profile"]),
                json.dumps(state["entries"]),
                json.dumps(state["skills"]),
                state["next_item_id"],
            )

    # -- profile ---------------------------------------------------------

    async def get_profile(self) -> dict[str, Any]:
        return workspace.public_profile(await self._read())

    async def update_profile(self, fields: dict[str, Any]) -> dict[str, Any]:
        async with self._edit() as state:
            return workspace.apply_profile(state, fields)

    # -- entries and bullets ---------------------------------------------

    async def list_entries(self) -> list[dict[str, Any]]:
        return workspace.list_entries(await self._read())

    async def create_entry(self, fields: dict[str, Any]) -> dict[str, Any]:
        async with self._edit() as state:
            return workspace.add_entry(state, fields)

    async def update_entry(
        self, entry_id: int, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        async with self._edit() as state:
            return workspace.edit_entry(state, entry_id, fields)

    async def delete_entry(self, entry_id: int) -> bool:
        async with self._edit() as state:
            return workspace.remove_entry(state, entry_id)

    async def create_bullet(
        self, entry_id: int, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        async with self._edit() as state:
            return workspace.add_bullet(state, entry_id, fields)

    async def update_bullet(
        self, bullet_id: int, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        async with self._edit() as state:
            return workspace.edit_bullet(state, bullet_id, fields)

    async def delete_bullet(self, bullet_id: int) -> bool:
        async with self._edit() as state:
            return workspace.remove_bullet(state, bullet_id)

    # -- skills ----------------------------------------------------------

    async def list_skills(self) -> list[dict[str, Any]]:
        return workspace.list_skills(await self._read())

    async def create_skill(self, fields: dict[str, Any]) -> dict[str, Any] | None:
        async with self._edit() as state:
            return workspace.add_skill(state, fields)

    async def delete_skill(self, skill_id: int) -> bool:
        async with self._edit() as state:
            return workspace.remove_skill(state, skill_id)

    # -- target jobs -----------------------------------------------------

    async def list_target_jobs(self) -> list[dict[str, Any]]:
        rows = await self._ready().fetch(
            "SELECT * FROM resume_target WHERE account_id = $1 ORDER BY id DESC",
            self.account_id,
        )
        return [_target(row) for row in rows]

    async def create_target_job(self, fields: dict[str, Any]) -> dict[str, Any]:
        row = await self._ready().fetchrow(
            """
            INSERT INTO resume_target
                (account_id, company, title, url, jd_text, date_posted, note)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            self.account_id,
            fields["company"],
            fields["title"],
            fields.get("url", ""),
            fields.get("jd_text", ""),
            fields.get("date_posted", ""),
            fields.get("note", ""),
        )
        return _target(row)

    async def _get_target(self, job_id: int) -> dict[str, Any] | None:
        row = await self._ready().fetchrow(
            "SELECT * FROM resume_target WHERE id = $1 AND account_id = $2",
            job_id,
            self.account_id,
        )
        return _target(row) if row is not None else None

    async def update_target_job(
        self, job_id: int, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        updates = {k: v for k, v in fields.items() if k in TARGET_FIELDS and v is not None}
        if not updates:
            return await self._get_target(job_id)
        columns = list(updates)
        assignments = ", ".join(f"{name} = ${i + 3}" for i, name in enumerate(columns))
        row = await self._ready().fetchrow(
            f"""
            UPDATE resume_target SET {assignments}
            WHERE id = $1 AND account_id = $2
            RETURNING *
            """,
            job_id,
            self.account_id,
            *(updates[name] for name in columns),
        )
        return _target(row) if row is not None else None

    async def delete_target_job(self, job_id: int) -> bool:
        result = await self._ready().execute(
            "DELETE FROM resume_target WHERE id = $1 AND account_id = $2",
            job_id,
            self.account_id,
        )
        return result != "DELETE 0"

    # -- resumes ---------------------------------------------------------

    async def list_resumes(self) -> list[dict[str, Any]]:
        rows = await self._ready().fetch(
            """
            SELECT * FROM resume_document WHERE account_id = $1
            ORDER BY updated_at DESC, id DESC
            """,
            self.account_id,
        )
        return [_document(row) for row in rows]

    async def get_resume(self, resume_id: int) -> dict[str, Any] | None:
        row = await self._ready().fetchrow(
            "SELECT * FROM resume_document WHERE id = $1 AND account_id = $2",
            resume_id,
            self.account_id,
        )
        return _document(row) if row is not None else None

    async def create_resume(self, fields: dict[str, Any]) -> dict[str, Any]:
        row = await self._ready().fetchrow(
            """
            INSERT INTO resume_document
                (account_id, name, target_job_id, variant, availability_line,
                 education_note, selection)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            RETURNING *
            """,
            self.account_id,
            fields["name"],
            fields.get("target_job_id"),
            fields.get("variant", "swe"),
            fields.get("availability_line", ""),
            fields.get("education_note", ""),
            json.dumps(fields.get("selection", {})),
        )
        return _document(row)

    async def update_resume(
        self, resume_id: int, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        updates = {
            k: v
            for k, v in fields.items()
            if k in RESUME_FIELDS and v is not None and k != "target_job_id"
        }
        if not updates and "target_job_id" not in fields:
            return await self.get_resume(resume_id)
        # target_job_id is handled apart from the "not None" filter above so the
        # link can be *cleared* with an explicit null. The client sends this key
        # on every save, so this is the hot path, not an edge case.
        if "target_job_id" in fields:
            updates["target_job_id"] = fields["target_job_id"]

        columns = list(updates)
        assignments = ", ".join(
            f"{name} = ${i + 3}{'::jsonb' if name == 'selection' else ''}"
            for i, name in enumerate(columns)
        )
        values = [
            json.dumps(updates[name]) if name == "selection" else updates[name]
            for name in columns
        ]
        row = await self._ready().fetchrow(
            f"""
            UPDATE resume_document SET {assignments}, updated_at = now()
            WHERE id = $1 AND account_id = $2
            RETURNING *
            """,
            resume_id,
            self.account_id,
            *values,
        )
        return _document(row) if row is not None else None

    async def delete_resume(self, resume_id: int) -> bool:
        result = await self._ready().execute(
            "DELETE FROM resume_document WHERE id = $1 AND account_id = $2",
            resume_id,
            self.account_id,
        )
        return result != "DELETE 0"

    # -- derived ---------------------------------------------------------

    async def coverage(
        self, job_id: int, resume_id: int | None = None
    ) -> dict[str, Any] | None:
        job = await self._get_target(job_id)
        if job is None:
            return None
        skills = workspace.list_skills(await self._read())
        shown_ids: set[int] = set()
        if resume_id is not None:
            resume = await self.get_resume(resume_id)
            if resume is not None:
                shown_ids = set(resume["selection"].get("skills", []))
        return workspace.compute_coverage(job, skills, shown_ids)

    async def render_inputs(
        self, resume_id: int
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]] | None:
        """Everything the Typst renderer needs, from one consistent snapshot.

        The old four-call version could interleave with an edit and render a
        resume against a bank that never existed. Profile, entries, and skills
        now come out of a single row, so this is two queries.
        """
        resume = await self.get_resume(resume_id)
        if resume is None:
            return None
        state = await self._read()
        return (
            workspace.public_profile(state),
            resume,
            workspace.list_entries(state),
            workspace.list_skills(state),
        )
