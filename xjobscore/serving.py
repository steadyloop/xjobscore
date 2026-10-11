"""Stable serving IDs shared by independent local and Neon publications."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from xjobscore import publish
from xjobscore.store import COMPANY_KEY, IDENTITY_LOCK


JOB_KEY = "jsonb_build_array(j.source, j.source_key)"
JOB_JOIN = f"JOIN frontend_identity jm ON jm.kind = 'job' AND jm.key = {JOB_KEY}"
COMPANY_JOIN = f"JOIN frontend_identity cm ON cm.kind = 'company' AND cm.key = {COMPANY_KEY}"
JOINS = f"{JOB_JOIN} JOIN company c ON c.id = j.company_id {COMPANY_JOIN}"


def _job_column(column: str) -> str:
    if column == "id":
        return "jm.id AS id"
    if column == "company_id":
        return "cm.id AS company_id"
    return f"j.{column}"


def _mapped_fingerprint(sql: str) -> str:
    return sql.replace("ROW(j.id,", "ROW(jm.id,").replace(
        "j.company_id, j.title", "cm.id, j.title"
    )


COHORT_SQL = _mapped_fingerprint(publish.COHORT_SQL).replace(
    "SELECT j.id, j.company_id,", "SELECT jm.id AS id, cm.id AS company_id,"
).replace("FROM job_posting j", f"FROM job_posting j {JOINS}", 1)

JOB_ROWS_SQL = f"""
    SELECT {', '.join(_job_column(c) for c in publish.JOB_COLUMNS)}
    FROM job_posting j {JOINS}
    WHERE jm.id = ANY($1::bigint[]) ORDER BY jm.id
"""
JOB_IDENTITIES_SQL = "SELECT id, source, source_key FROM job_posting WHERE id=ANY($1::bigint[])"
MAPPED_JOB_IDENTITIES_SQL = f"SELECT jm.id, j.source, j.source_key FROM job_posting j {JOB_JOIN} WHERE jm.id=ANY($1::bigint[])"
COMPANY_FINGERPRINT_SQL = f"""
    SELECT cm.id, {publish._fingerprint('c', publish.COMPANY_COLUMNS).replace('ROW(c.id,', 'ROW(cm.id,')} AS fingerprint
    FROM company c {COMPANY_JOIN} WHERE cm.id = ANY($1::bigint[])
"""
COMPANY_ROWS_SQL = f"""
    SELECT cm.id, c.name, c.domain, c.description, c.tags
    FROM company c {COMPANY_JOIN}
    WHERE cm.id = ANY($1::bigint[]) ORDER BY cm.id
"""
LOGO_FINGERPRINT_SQL = f"""
    SELECT cm.id, md5(l.bytes) AS fingerprint
    FROM company_logo l JOIN company c ON c.id = l.company_id {COMPANY_JOIN}
    WHERE cm.id = ANY($1::bigint[])
"""
LOGO_ROWS_SQL = f"""
    SELECT cm.id AS company_id, l.bytes
    FROM company_logo l JOIN company c ON c.id = l.company_id {COMPANY_JOIN}
    WHERE cm.id = ANY($1::bigint[]) ORDER BY cm.id
"""
SOURCE_CLOSURES_SQL = f"""
    SELECT jm.id, {', '.join('j.' + c for c in publish.CLOSURE_COLUMNS)}
    FROM job_posting j {JOB_JOIN}
    WHERE jm.id = ANY($1::bigint[]) AND j.status = 'closed'
"""
QUERIES = {
    JOB_IDENTITIES_SQL: MAPPED_JOB_IDENTITIES_SQL,
    publish.COHORT_SQL: COHORT_SQL,
    publish.JOB_ROWS_SQL: JOB_ROWS_SQL,
    publish.COMPANY_FINGERPRINT_SQL: COMPANY_FINGERPRINT_SQL,
    publish.COMPANY_ROWS_SQL: COMPANY_ROWS_SQL,
    publish.LOGO_FINGERPRINT_SQL: LOGO_FINGERPRINT_SQL,
    publish.LOGO_ROWS_SQL: LOGO_ROWS_SQL,
    publish.SOURCE_CLOSURES_SQL: SOURCE_CLOSURES_SQL,
}


class Source:
    """Expose pipeline rows with serving IDs, without changing pipeline IDs."""

    def __init__(self, connection: Any, cohort: list[Any] | None = None) -> None:
        self.connection = connection
        self.cohort = cohort
        self.retained: dict[str, list[Any]] | None = None
        self.cleanup_closed = True
        self.company_edit_revisions: dict[int, Any] = {}
        self.maintained_company_ids: list[int] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        if query == publish.COHORT_SQL and self.cohort is not None:
            return self.cohort
        return await self.connection.fetch(QUERIES.get(query, query), *args)


class SharedSnapshot:
    """Serialize and cache reads of one PostgreSQL snapshot across both targets."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.lock = asyncio.Lock()
        self.owner = None
        self.cache: dict[Any, Any] = {}

    @asynccontextmanager
    async def transaction(self):
        if self.owner is asyncio.current_task():
            yield
            return
        async with self.lock:
            self.owner = asyncio.current_task()
            try:
                yield
            finally:
                self.owner = None

    async def _read(self, method: str, query: str, args: tuple[Any, ...]):
        key = (method, query, tuple(tuple(arg) if isinstance(arg, list) else arg for arg in args))
        async with self.transaction():
            if key not in self.cache:
                self.cache[key] = await getattr(self.connection, method)(query, *args)
            return self.cache[key]

    async def fetch(self, query: str, *args: Any):
        return await self._read("fetch", query, args)

    async def fetchrow(self, query: str, *args: Any):
        return await self._read("fetchrow", query, args)

    async def fetchval(self, query: str, *args: Any):
        return await self._read("fetchval", query, args)

    async def execute(self, query: str, *args: Any):
        async with self.transaction():
            return await self.connection.execute(query, *args)


async def prepare(connection: Any) -> list[Any]:
    """Allocate identities once before either target receives the same cohort."""
    if await connection.fetchval("SELECT to_regclass('frontend_identity')") is None:
        raise RuntimeError("Initialize the local frontend publication identities before publishing")
    if not await connection.fetchval(
        "SELECT EXISTS (SELECT 1 FROM frontend_identity WHERE kind='meta' AND key='[\"initialized\"]'::jsonb)"
    ):
        raise RuntimeError("Seed frontend identities from the existing Neon database before publishing")
    async with connection.transaction(isolation="repeatable_read"):
        cohort = await publish.read_cohort(connection)
        job_ids = [row["id"] for row in cohort]
        company_ids = list({row["company_id"] for row in cohort})
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock($1)", IDENTITY_LOCK)
            await connection.execute(f"""
                INSERT INTO frontend_identity(kind, key)
                SELECT 'company', {COMPANY_KEY} FROM company c
                WHERE c.id = ANY($1::bigint[]) AND NOT EXISTS (
                    SELECT 1 FROM frontend_identity m
                    WHERE m.kind='company' AND m.key={COMPANY_KEY}
                ) ON CONFLICT (kind, key) DO NOTHING
            """, company_ids)
            await connection.execute(f"""
                INSERT INTO frontend_identity(kind, key)
                SELECT 'job', {JOB_KEY} FROM job_posting j
                WHERE j.id = ANY($1::bigint[]) AND NOT EXISTS (
                    SELECT 1 FROM frontend_identity m
                    WHERE m.kind='job' AND m.key={JOB_KEY}
                ) ON CONFLICT (kind, key) DO NOTHING
            """, job_ids)
        mapped = await publish._fetch_utc(connection, COHORT_SQL)
        if len(mapped) != len(cohort):
            raise RuntimeError("Serving cohort contains an unmapped identity; refusing incomplete publication")
        return mapped


async def validate_target(source: Any, target: Any) -> None:
    """Refuse to overwrite a numeric ID that belongs to a different job."""
    cohort = await publish.read_cohort(source)
    rows = await source.fetch(JOB_IDENTITIES_SQL, [r['id'] for r in cohort])
    if source.retained is not None:
        rows = [*rows, *source.retained["jobs"]]
    if not rows:
        return
    conflicts = await target.fetchval("""
        SELECT count(*) FROM unnest($1::bigint[], $2::text[], $3::text[])
            AS incoming(id, source, source_key)
        JOIN job_posting stored ON stored.id=incoming.id
        WHERE (stored.source, stored.source_key) IS DISTINCT FROM
              (incoming.source, incoming.source_key)
    """, [r['id'] for r in rows], [r['source'] for r in rows], [r['source_key'] for r in rows])
    if conflicts:
        raise RuntimeError(f"{conflicts} serving job IDs belong to different postings; refusing to publish")
    companies = await source.fetch(publish.COMPANY_ROWS_SQL, list({r['company_id'] for r in cohort} | set(source.maintained_company_ids)))
    if source.retained is not None:
        by_id = {row['id']: row for row in source.retained["companies"]}
        by_id.update({row['id']: row for row in companies})
        companies = list(by_id.values())
    history = await source.fetch("""
        SELECT id, COALESCE(to_jsonb(m)->'previous_keys', '[]'::jsonb) AS previous_keys
        FROM frontend_identity m WHERE kind='company' AND id=ANY($1::bigint[])
    """, [row['id'] for row in companies])
    allowed = []
    for row in history:
        keys = row['previous_keys']
        if isinstance(keys, str):
            keys = json.loads(keys)
        for key in keys:
            if key[0] == 'domain':
                allowed.append((row['id'], key[1], None))
            elif key[0] == 'source_id':
                allowed.append((row['id'], None, key[2]))
    conflicts = await target.fetchval("""
        SELECT count(*) FROM unnest($1::bigint[], $2::text[], $3::text[])
            AS incoming(id, domain, name)
        JOIN company stored ON stored.id=incoming.id
        WHERE (lower(stored.domain) IS DISTINCT FROM lower(incoming.domain)
           OR (stored.domain IS NULL AND incoming.domain IS NULL AND stored.name <> incoming.name))
        AND NOT EXISTS (
            SELECT 1 FROM unnest($4::bigint[], $5::text[], $6::text[]) AS prior(id, domain, name)
            WHERE prior.id = stored.id
              AND lower(prior.domain) IS NOT DISTINCT FROM lower(stored.domain)
              AND (prior.domain IS NOT NULL OR prior.name = stored.name)
        )
    """, [r['id'] for r in companies], [r['domain'] for r in companies], [r['name'] for r in companies],
        [r[0] for r in allowed], [r[1] for r in allowed], [r[2] for r in allowed])
    if conflicts:
        raise RuntimeError(f"{conflicts} serving company IDs belong to different companies; refusing to publish")
