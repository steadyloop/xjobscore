"""PostgreSQL persistence for the live OneShot UI.

The repository speaks only the tables declared in the root ``schema.sql``.
Harvesting and canonicalization belong to the core pipeline; OneShot is a
reader for jobs and a writer for the single-user application tracker.
"""

import asyncio
import functools
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg

from xjobscore.degrees import canon as degree_canon
from xjobscore.skills import canon


def _remote(column: str | None, attributes: dict[str, Any]) -> str:
    """Prefer the parse-time column; fall back while vendors are migrated."""
    if column and column != "unknown":
        return column
    return attributes.get("remote") or "unknown"


LOCAL_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")

# `job_type` is a parse-time column now, not a registry-derived enrichment, so
# the contract is spelled out here and pinned to schema.sql's CHECK by test.
DEFAULT_JOB_TYPES = ("intern", "coop", "student_program", "research", "part_time")
# API-only token: storage says NULL for "no signal", but the query parameter
# keeps a name so the unclassified pool stays addressable.
UNCLASSIFIED = "unclassified"
VALID_JOB_TYPES = frozenset((*DEFAULT_JOB_TYPES, UNCLASSIFIED))
VALID_DEGREE_FIELDS = frozenset(field.name for field in degree_canon())
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


@functools.lru_cache(maxsize=1)
def _skill_lookups() -> tuple[dict[str, Any], dict[str, Any]]:
    """Index the cached canon by stable id and case-insensitive display name."""
    by_id = canon()
    by_name = {skill.name.casefold(): skill for skill in by_id.values()}
    return by_id, by_name


def _skill_details(attributes: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive display metadata without changing stored canonical skill ids."""
    values = attributes.get("skills")
    if not isinstance(values, list):
        return []

    by_id, by_name = _skill_lookups()
    details: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        raw = value.strip()
        skill = by_id.get(raw) or by_name.get(raw.casefold())
        if skill is None:
            skill_id, name, category = raw, raw, None
        else:
            skill_id, name, category = skill.id, skill.name, skill.category
        if skill_id in seen:
            continue
        seen.add(skill_id)
        details.append({"id": skill_id, "name": name, "category": category})
    return details


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _ilike_pattern(value: str) -> str:
    """A substring pattern matching ``value`` literally.

    ``%`` and ``_`` are ILIKE wildcards, so a typed ``C_`` would otherwise match
    ``C#`` and a lone ``%`` would match the whole catalog. Backslash is
    PostgreSQL's default LIKE escape character, so no ESCAPE clause is needed —
    but it has to be escaped first, both to keep a typed backslash literal and
    because a pattern ending in a lone escape character is an error.
    """
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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

    def for_account(self, account_id: UUID) -> "PostgresRepository":
        """A per-request view of the same pool, scoped to one account.

        Every query already filters on ``self.account_id``; this is the only
        thing that has to vary per request, so cloning the object is cheaper and
        far less invasive than threading an account through ten method
        signatures. The clone is handed the live pool, so ``_owns_pool`` is
        False and its ``close()`` is a no-op.

        Calls ``_ready()`` rather than reading ``self.pool`` so cloning before
        ``connect()`` fails loudly instead of yielding a half-dead store.
        """
        return PostgresRepository(self.dsn, account_id=account_id, pool=self._ready())

    @staticmethod
    def _job(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any]:
        attributes = _json_object(row.get("attributes"))
        return {
            "id": row["id"],
            "source": row.get("source"),
            "source_url": row.get("source_url"),
            "company": row.get("company_name") or "Unknown company",
            "title": row["title"],
            "description": row.get("description"),
            "location": row.get("location_text"),
            "apply_url": row.get("apply_url") or row.get("source_url"),
            "posted_at": _iso(row.get("posted_at")),
            # Most feeds publish no posting date, so the client falls back to
            # this to say "Added 2d ago" rather than "Posted date unavailable".
            # It is also what the "fresh" sort already orders by.
            "first_seen_at": _iso(row.get("first_seen_at")),
            "last_seen_at": _iso(row.get("last_seen_at")),
            "status": row.get("status"),
            # Parse-time columns. `remote` falls back to the legacy
            # attributes.remote for vendors whose parse-time rules have not
            # been transcribed yet, so the UI keeps its signal during the
            # per-vendor migration.
            "job_type": row.get("job_type"),
            "remote": _remote(row.get("remote"), attributes),
            "attributes": attributes,
            # Derived API presentation; attributes.skills remains the durable
            # JSONB array of canonical ids.
            "skills": _skill_details(attributes),
        }

    async def list_jobs(
        self,
        *,
        job_types: Iterable[str] = (),
        degree_fields: Iterable[str] = (),
        exclude_job_ids: Iterable[int] = (),
        hide_tracked: bool = True,
        q: str = "",
        sort: str = "fresh",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: list[Any] = []

        def bind(value: Any) -> str:
            params.append(value)
            return f"${len(params)}"

        clauses = ["j.status = 'active'"]
        if hide_tracked:
            account_marker = bind(self.account_id)
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM job_application a "
                f"WHERE a.account_id = {account_marker} AND a.job_posting_id = j.id)"
            )
        types = tuple(dict.fromkeys(job_types))
        if types:
            # `unclassified` is not a stored value -- it is the NULL pool, so it
            # cannot ride along in the ANY() list and needs its own disjunct.
            named = [value for value in types if value != UNCLASSIFIED]
            wanted = []
            if named:
                wanted.append(f"j.job_type = ANY({bind(named)}::text[])")
            if UNCLASSIFIED in types:
                wanted.append("j.job_type IS NULL")
            clauses.append(f"({' OR '.join(wanted)})")
        fields = tuple(dict.fromkeys(degree_fields))
        if fields:
            marker = bind(list(fields))
            clauses.append(f"(j.attributes -> 'degree_fields') ?| {marker}::text[]")
        excluded = tuple(dict.fromkeys(int(job_id) for job_id in exclude_job_ids))
        if excluded:
            marker = bind(list(excluded))
            clauses.append(f"NOT (j.id = ANY({marker}::bigint[]))")
        if q.strip():
            marker = bind(_ilike_pattern(q.strip()))
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
        # Counting and reading a page use the same filter predicate but
        # neither depends on the other. Run them on separate pool connections
        # so a large count does not hold the first visible results hostage.
        # Keep the filter arguments separate: binding LIMIT/OFFSET below must
        # not change the count query's positional parameters.
        filter_params = tuple(params)
        total_read = pool.fetchval(
            f"SELECT COUNT(*) FROM job_posting j JOIN company c ON c.id = j.company_id WHERE {where}",
            *filter_params,
        )
        limit_marker = bind(max(1, min(limit, 500)))
        offset_marker = bind(max(0, offset))
        page_read = pool.fetch(
            f"""
            SELECT
                j.id, j.source, c.name AS company_name, j.title, j.description,
                j.location_text, j.source_url, j.apply_url, j.posted_at,
                j.first_seen_at, j.last_seen_at, j.status, j.attributes,
                j.job_type, j.remote
            FROM job_posting j
            JOIN company c ON c.id = j.company_id
            WHERE {where}
            ORDER BY {ordering}
            LIMIT {limit_marker} OFFSET {offset_marker}
            """,
            *params,
        )
        total, rows = await asyncio.gather(total_read, page_read)
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
                    COUNT(*) FILTER (WHERE f.enabled AND f.last_poll_at IS NOT NULL) AS polled_count,
                    -- A paused feed is held on purpose: it is overdue by design
                    -- and must not be counted as something to go and fix.
                    COUNT(*) FILTER (
                        WHERE f.enabled AND f.paused_at IS NULL AND (
                            COALESCE(f.strikes, 0) > 0
                            OR (
                                f.due_at < now()
                                AND (f.hold_until IS NULL OR f.hold_until < now())
                            )
                        )
                    ) AS attention_count,
                    MAX(f.last_poll_at) FILTER (WHERE f.enabled) AS last_poll_at
                FROM feed f
                GROUP BY f.source
            ),
            posting_stats AS (
                SELECT
                    f.source,
                    COUNT(j.id) FILTER (WHERE j.status = 'active') AS coverage,
                    COUNT(*) FILTER (
                        WHERE j.first_seen_at >= date_trunc('day', now())
                    ) AS added_today
                FROM feed f
                LEFT JOIN job_posting j
                    ON j.source = f.source || ':' || f.key
                GROUP BY f.source
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
