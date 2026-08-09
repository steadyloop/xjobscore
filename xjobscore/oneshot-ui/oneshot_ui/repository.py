"""PostgreSQL persistence for the live OneShot UI.

The repository speaks only the tables declared in the root ``schema.sql``.
Harvesting and canonicalization belong to the core pipeline; OneShot is a
reader for jobs and a writer for the single-user application tracker.
"""

from datetime import date, datetime, time, timedelta, timezone
import json
from typing import Any, Iterable
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg


LOCAL_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")

VALID_OPPORTUNITY_TYPES = frozenset(
    {
        "spring_internship",
        "spring_coop",
        "student_researcher",
        "summer_internship",
        "fall_coop",
        "fall_internship",
        "student_role",
        "fellowship",
        "unclassified",
        "review",
    }
)
VALID_APPLICATION_STAGES = frozenset(
    {"considering", "applied", "assessment", "interview", "offer", "closed"}
)
VALID_CLOSED_REASONS = frozenset({"rejected", "withdrawn", "no_reply"})


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _timestamp(value: Any, *, end_of_day: bool = False) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    try:
        if len(raw) == 10:
            clock = time.max if end_of_day else time.min
            return datetime.combine(date.fromisoformat(raw), clock, tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("application dates must be ISO-8601 values") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class PostgresRepository:
    """OneShot queries and commands over an ``asyncpg`` pool."""

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
            raise RuntimeError("PostgresRepository.connect() has not been called")
        return self.pool

    @staticmethod
    def _job(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any]:
        attributes = _json_object(row.get("attributes"))
        return {
            "id": row["id"],
            "company": row.get("company_name") or "Unknown company",
            "title": row["title"],
            "description": row.get("description"),
            "location": row.get("location_text"),
            "apply_url": row.get("apply_url") or row.get("source_url"),
            "posted_at": _iso(row.get("posted_at")),
            "last_seen_at": _iso(row.get("last_seen_at")),
            "status": row.get("status"),
            "attributes": attributes,
        }

    async def list_jobs(
        self,
        *,
        opportunity_types: Iterable[str] = (),
        q: str = "",
        sort: str = "fresh",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: list[Any] = [self.account_id]

        def bind(value: Any) -> str:
            params.append(value)
            return f"${len(params)}"

        clauses = [
            "j.status = 'active'",
            "NOT EXISTS (SELECT 1 FROM job_application a WHERE a.account_id = $1 AND a.job_posting_id = j.id)",
        ]
        types = tuple(dict.fromkeys(opportunity_types))
        if types:
            marker = bind(list(types))
            clauses.append(f"j.attributes ->> 'opportunity_type' = ANY({marker}::text[])")
        if q.strip():
            marker = bind(f"%{q.strip()}%")
            clauses.append(
                f"(c.name ILIKE {marker} OR j.title ILIKE {marker} "
                f"OR COALESCE(j.description, '') ILIKE {marker})"
            )
        where = " AND ".join(clauses)
        ordering = {
            "company": "c.name, j.title, j.id",
            "title": "j.title, c.name, j.id",
            "fresh": "COALESCE(j.posted_at, j.first_seen_at) DESC, j.id DESC",
        }.get(sort, "COALESCE(j.posted_at, j.first_seen_at) DESC, j.id DESC")
        pool = self._ready()
        total = await pool.fetchval(
            f"SELECT COUNT(*) FROM job_posting j JOIN company c ON c.id = j.company_id WHERE {where}",
            *params,
        )
        limit_marker = bind(max(1, min(limit, 500)))
        offset_marker = bind(max(0, offset))
        rows = await pool.fetch(
            f"""
            SELECT
                j.id, c.name AS company_name, j.title, j.description,
                j.location_text, j.source_url, j.apply_url, j.posted_at,
                j.last_seen_at, j.status, j.attributes
            FROM job_posting j
            JOIN company c ON c.id = j.company_id
            WHERE {where}
            ORDER BY {ordering}
            LIMIT {limit_marker} OFFSET {offset_marker}
            """,
            *params,
        )
        return {
            "jobs": [self._job(row) for row in rows],
            "total": int(total or 0),
            "limit": limit,
            "offset": offset,
        }

    async def source_coverage(self) -> dict[str, Any]:
        rows = await self._ready().fetch(
            """
            WITH feed_stats AS (
                SELECT
                    f.source,
                    COUNT(*) FILTER (WHERE f.enabled) AS board_count,
                    COUNT(*) FILTER (WHERE f.enabled AND s.last_poll_at IS NOT NULL) AS polled_count,
                    COUNT(*) FILTER (
                        WHERE f.enabled AND (
                            s.disabled_at IS NOT NULL
                            OR COALESCE(s.strikes, 0) > 0
                            OR (
                                s.due_at < now()
                                AND (s.hold_until IS NULL OR s.hold_until < now())
                            )
                        )
                    ) AS attention_count,
                    MAX(s.last_poll_at) FILTER (WHERE f.enabled) AS last_poll_at
                FROM feed f
                LEFT JOIN schedule s ON s.feed_id = f.id
                GROUP BY f.source
            ),
            posting_stats AS (
                SELECT
                    source,
                    COUNT(*) FILTER (WHERE status = 'active') AS coverage,
                    COUNT(*) FILTER (
                        WHERE first_seen_at >= date_trunc('day', now())
                    ) AS added_today
                FROM job_posting
                GROUP BY source
            )
            SELECT
                f.source,
                f.board_count,
                f.polled_count,
                f.attention_count,
                f.last_poll_at,
                COALESCE(p.coverage, 0) AS coverage,
                COALESCE(p.added_today, 0) AS added_today
            FROM feed_stats f
            LEFT JOIN posting_stats p ON p.source = f.source
            ORDER BY COALESCE(p.coverage, 0) DESC, f.source
            """
        )
        items = []
        for row in rows:
            board_count = int(row["board_count"] or 0)
            polled_count = int(row["polled_count"] or 0)
            attention_count = int(row["attention_count"] or 0)
            if board_count == 0:
                health = "Disabled"
            elif polled_count == 0:
                health = "Not polled"
            elif attention_count:
                health = "Needs attention"
            elif polled_count < board_count:
                health = "Partial"
            else:
                health = "Healthy"
            items.append(
                {
                    "source": row["source"],
                    "type": "ATS",
                    "board_count": board_count,
                    "coverage": int(row["coverage"] or 0),
                    "last_poll_at": _iso(row["last_poll_at"]),
                    "health": health,
                    "added_today": int(row["added_today"] or 0),
                }
            )
        return {
            "sources": items,
            "source_count": len(items),
            "total_coverage": sum(item["coverage"] for item in items),
            "total_added_today": sum(item["added_today"] for item in items),
        }

    @staticmethod
    def _application(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any]:
        external = row.get("job_posting_id") is None
        return {
            "id": row["id"],
            "job_id": row.get("job_posting_id"),
            "external": external,
            "company": row["company_snapshot"],
            "title": row["title_snapshot"],
            "location": row.get("location_snapshot"),
            "stage": row["stage"],
            "closed_reason": row.get("closed_reason"),
            "apply_url_snapshot": row.get("apply_url_snapshot"),
            "submitted_at": _iso(row.get("submitted_at")),
            "next_action": row.get("next_action"),
            "next_due_at": _iso(row.get("next_due_at")),
            "note": row.get("note") or "",
            "updated_at": _iso(row.get("updated_at")),
        }

    async def _get_application(
        self, connection: asyncpg.Connection, application_id: int
    ) -> dict[str, Any] | None:
        row = await connection.fetchrow(
            """
            SELECT
                id, job_posting_id, company_snapshot, title_snapshot,
                location_snapshot, stage, closed_reason, apply_url_snapshot,
                submitted_at, next_action, next_due_at, note, updated_at
            FROM job_application a
            WHERE a.id = $1 AND a.account_id = $2
            """,
            application_id,
            self.account_id,
        )
        if row is None:
            return None
        result = self._application(row)
        events = await connection.fetch(
            """
            SELECT id, event_type, to_stage, occurred_at, note
            FROM job_application_event
            WHERE job_application_id = $1
            ORDER BY occurred_at DESC, id DESC
            """,
            application_id,
        )
        result["events"] = [
            {
                "id": event["id"],
                "event_type": event["event_type"],
                "to_stage": event["to_stage"],
                "occurred_at": _iso(event["occurred_at"]),
                "note": event["note"],
            }
            for event in events
        ]
        return result

    async def list_applications(self) -> dict[str, Any]:
        rows = await self._ready().fetch(
            """
            SELECT
                id, job_posting_id, company_snapshot, title_snapshot,
                location_snapshot, stage, closed_reason, apply_url_snapshot,
                submitted_at, next_action, next_due_at, note, updated_at
            FROM job_application a
            WHERE a.account_id = $1
            ORDER BY
                CASE a.stage
                    WHEN 'offer' THEN 1
                    WHEN 'interview' THEN 2
                    WHEN 'assessment' THEN 3
                    WHEN 'applied' THEN 4
                    WHEN 'considering' THEN 5
                    ELSE 6
                END,
                a.next_due_at NULLS LAST,
                a.updated_at DESC
            """,
            self.account_id,
        )
        return {"applications": [self._application(row) for row in rows]}

    async def get_application(self, application_id: int) -> dict[str, Any] | None:
        async with self._ready().acquire() as connection:
            return await self._get_application(connection, application_id)

    async def create_application(
        self,
        job_id: int,
    ) -> dict[str, Any] | None:
        pool = self._ready()
        async with pool.acquire() as connection, connection.transaction():
            job = await connection.fetchrow(
                """
                SELECT
                    j.id, j.title, j.location_text, j.source_url, j.apply_url,
                    c.name AS company_name
                FROM job_posting j
                JOIN company c ON c.id = j.company_id
                WHERE j.id = $1
                FOR SHARE OF j
                """,
                job_id,
            )
            if job is None:
                return None
            existing = await connection.fetchrow(
                """
                SELECT id FROM job_application
                WHERE account_id = $1 AND job_posting_id = $2
                """,
                self.account_id,
                job_id,
            )
            if existing:
                result = await self._get_application(connection, existing["id"])
                assert result is not None
                result["already_exists"] = True
                return result
            application_id = await connection.fetchval(
                """
                INSERT INTO job_application(
                    account_id, job_posting_id, company_snapshot, title_snapshot,
                    location_snapshot, apply_url_snapshot, stage
                ) VALUES ($1, $2, $3, $4, $5, $6, 'considering')
                RETURNING id
                """,
                self.account_id,
                job_id,
                job["company_name"],
                job["title"],
                job["location_text"],
                job["apply_url"] or job["source_url"],
            )
            await connection.execute(
                """
                INSERT INTO job_application_event(
                    job_application_id, event_type, to_stage, occurred_at, note
                ) VALUES ($1, $2, $3, COALESCE($4, now()), $5)
                """,
                application_id,
                "stage_changed",
                "considering",
                None,
                None,
            )
            return await self._get_application(connection, application_id)

    async def create_external_application(
        self,
        *,
        company: str,
        title: str,
        url: str,
        location: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        company, title, url = company.strip(), title.strip(), url.strip()
        parsed = urlsplit(url)
        if not company or not title:
            raise ValueError("company and title are required")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("application URL must be HTTP(S)")
        pool = self._ready()
        async with pool.acquire() as connection, connection.transaction():
            application_id = await connection.fetchval(
                """
                INSERT INTO job_application(
                    account_id, job_posting_id, company_snapshot, title_snapshot,
                    location_snapshot, apply_url_snapshot, stage, note
                ) VALUES ($1, NULL, $2, $3, $4, $5, 'considering', $6)
                RETURNING id
                """,
                self.account_id,
                company,
                title,
                location.strip(),
                url,
                note,
            )
            await connection.execute(
                """
                INSERT INTO job_application_event(
                    job_application_id, event_type, to_stage, note
                ) VALUES ($1, 'stage_changed', 'considering', $2)
                """,
                application_id,
                note or None,
            )
            result = await self._get_application(connection, application_id)
            assert result is not None
            return result

    async def update_application(
        self, application_id: int, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        pool = self._ready()
        async with pool.acquire() as connection, connection.transaction():
            current = await connection.fetchrow(
                """
                SELECT
                    job_posting_id, company_snapshot, title_snapshot,
                    apply_url_snapshot, stage, closed_reason, submitted_at
                FROM job_application
                WHERE id = $1 AND account_id = $2
                FOR UPDATE
                """,
                application_id,
                self.account_id,
            )
            if current is None:
                return None
            external = current["job_posting_id"] is None
            for request_name, column in (
                ("company", "company_snapshot"),
                ("title", "title_snapshot"),
                ("apply_url_snapshot", "apply_url_snapshot"),
            ):
                if not external and request_name in changes and changes[request_name] != current[column]:
                    raise ValueError("discovered application snapshots cannot be edited")

            stage = changes.get("stage", current["stage"])
            closed_reason = changes.get("closed_reason", current["closed_reason"])
            if stage not in VALID_APPLICATION_STAGES:
                raise ValueError("unknown application stage")
            if stage == "closed" and closed_reason not in VALID_CLOSED_REASONS:
                raise ValueError("closed_reason is required when stage is closed")
            if stage != "closed":
                if changes.get("closed_reason") is not None:
                    raise ValueError("closed_reason is only valid when stage is closed")
                closed_reason = None
            stage_changed = stage != current["stage"]
            if stage_changed and stage == "applied" and not changes.get("confirmed"):
                raise ValueError("human submission confirmation is required")

            values: dict[str, Any] = {
                "stage": stage,
                "closed_reason": closed_reason,
            }
            if external:
                if "company" in changes:
                    company = str(changes["company"] or "").strip()
                    if not company:
                        raise ValueError("company is required")
                    values["company_snapshot"] = company
                if "title" in changes:
                    title = str(changes["title"] or "").strip()
                    if not title:
                        raise ValueError("title is required")
                    values["title_snapshot"] = title
                if "apply_url_snapshot" in changes:
                    url = str(changes["apply_url_snapshot"] or "").strip()
                    parsed = urlsplit(url)
                    if url and (parsed.scheme not in {"http", "https"} or not parsed.hostname):
                        raise ValueError("application URL must be HTTP(S)")
                    values["apply_url_snapshot"] = url or None
            for name in ("next_action", "note"):
                if name in changes:
                    values[name] = changes[name] or ""
            if "submitted_at" in changes:
                values["submitted_at"] = _timestamp(changes["submitted_at"])
            if "next_due_at" in changes:
                values["next_due_at"] = _timestamp(changes["next_due_at"], end_of_day=True)
            if stage_changed and stage == "applied":
                submitted = values.get("submitted_at") or current["submitted_at"] or datetime.now(timezone.utc)
                values["submitted_at"] = submitted
                values.setdefault("next_action", "Follow up if no response")
                values.setdefault("next_due_at", submitted + timedelta(days=14))

            assignments: list[str] = []
            params: list[Any] = []
            for column, value in values.items():
                params.append(value)
                assignments.append(f"{column} = ${len(params)}")
            params.extend((application_id, self.account_id))
            await connection.execute(
                f"""
                UPDATE job_application
                SET {', '.join(assignments)}, updated_at = now()
                WHERE id = ${len(params) - 1} AND account_id = ${len(params)}
                """,
                *params,
            )
            if stage_changed:
                await connection.execute(
                    """
                    INSERT INTO job_application_event(
                        job_application_id, event_type, to_stage, occurred_at, note
                    ) VALUES ($1, 'stage_changed', $2, now(), $3)
                    """,
                    application_id,
                    stage,
                    changes.get("note") or None,
                )
            return await self._get_application(connection, application_id)

    async def delete_application(self, application_id: int) -> bool:
        result = await self._ready().execute(
            "DELETE FROM job_application WHERE id = $1 AND account_id = $2",
            application_id,
            self.account_id,
        )
        return result == "DELETE 1"
