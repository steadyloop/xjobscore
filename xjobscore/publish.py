"""Publish the OneShot serving subset from the home database to thin Neon."""

from __future__ import annotations

import argparse
import asyncio
import os
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

from xjobscore import store

logger = logging.getLogger(__name__)


JOB_COLUMNS = (
    "id",
    "source",
    "source_key",
    "company_id",
    "title",
    "description",
    "location_text",
    "source_url",
    "apply_url",
    "posted_at",
    "content_hash",
    "remote",
    "part_time",
    "job_type",
    "locations",
    "status",
    "first_seen_at",
    "last_seen_at",
    "missing_since",
    "linked_dead_at",
    "ats_closed_at",
    "paged_closed_at",
    "link_checked_at",
    "attributes",
    "degree_version",
    "job_domain",
    "job_domain_version",
    "raw_id",
)

COMPANY_COLUMNS = (
    "id",
    "name",
    "domain",
    "description",
    "tags",
)

# last_seen_at moves on nearly every cohort row every hour, while real content
# changes number in the dozens. Fingerprinting it would resend the whole cohort
# each run, so it is left out and refreshed only once it drifts (see
# changed_job_ids).
FINGERPRINT_COLUMNS = tuple(column for column in JOB_COLUMNS if column != "last_seen_at")

# The UI renders last_seen_at as "checked N ago"; this bounds how stale that can
# read on Neon, and how often an otherwise unchanged row is rewritten.
DEFAULT_SEEN_DRIFT_S = 21_600


def _fingerprint(alias: str, columns: Sequence[str]) -> str:
    # ROW(...)::text renders timestamptz in the session time zone, so every
    # fingerprint read runs under SET LOCAL TIME ZONE 'UTC' (_fetch_utc).
    return f"md5(ROW({', '.join(f'{alias}.{column}' for column in columns)})::text)"


# This is the broad serving cohort, not merely the default Software view. The
# frontend can therefore apply every focus-area filter without needing the home
# database. Eligible duplicate groups publish one active representative; a
# rejected group is deliberately not collapsed because Gold decided its members
# are separate jobs.
COHORT_SQL = f"""
    SELECT j.id, j.company_id, j.last_seen_at,
           {_fingerprint('j', FINGERPRINT_COLUMNS)} AS fingerprint
    FROM job_posting j
    LEFT JOIN job_posting_dup_group g ON g.id = j.dup_group_id
    WHERE j.status = 'active'
      AND j.job_type = ANY(ARRAY[
          'intern', 'coop', 'student', 'new_grad', 'research'
      ]::text[])
      AND (
          j.locations = '[]'::jsonb
          OR j.locations @> '[{{"country": "US"}}]'::jsonb
      )
      AND (
          NOT (g.primary_posting_id IS NOT NULL AND g.state <> 'rejected')
          OR j.id = g.primary_posting_id
          OR (
              NOT EXISTS (
                  SELECT 1
                  FROM job_posting stored_primary
                  WHERE stored_primary.id = g.primary_posting_id
                    AND stored_primary.dup_group_id = g.id
                    AND stored_primary.status = 'active'
              )
              AND j.id = (
                  SELECT active_member.id
                  FROM job_posting active_member
                  WHERE active_member.dup_group_id = g.id
                    AND active_member.status = 'active'
                  ORDER BY
                      COALESCE(
                          active_member.posted_at,
                          active_member.first_seen_at
                      ) DESC,
                      active_member.id DESC
                  LIMIT 1
              )
          )
      )
    ORDER BY j.id
"""

# Only active rows: a soft-closed row that re-enters the cohort is then absent
# here and is resent, which reopens it.
PUBLISHED_JOBS_SQL = f"""
    SELECT id, last_seen_at,
           {_fingerprint('job_posting', FINGERPRINT_COLUMNS)} AS fingerprint
    FROM job_posting
    WHERE status = 'active'
"""

JOB_ROWS_SQL = f"""
    SELECT {', '.join(JOB_COLUMNS)}
    FROM job_posting
    WHERE id = ANY($1::bigint[])
    ORDER BY id
"""

# Preserve actual closure evidence when a previously served job leaves the
# active cohort. Other cohort departures still close without a fabricated
# source signal (for example a location or duplicate-selection change).
CLOSURE_COLUMNS = (
    "missing_since", "linked_dead_at", "ats_closed_at", "paged_closed_at",
    "link_checked_at",
)
SOURCE_CLOSURES_SQL = f"""
    SELECT id, {', '.join(CLOSURE_COLUMNS)}
    FROM job_posting
    WHERE id = ANY($1::bigint[]) AND status = 'closed'
"""
UPDATE_CLOSURES_SQL = f"""
    UPDATE job_posting
    SET {', '.join(f'{column} = ${i}' for i, column in enumerate(CLOSURE_COLUMNS, 2))}
    WHERE id = $1
"""

COMPANY_FINGERPRINT_SQL = f"""
    SELECT id, {_fingerprint('company', COMPANY_COLUMNS)} AS fingerprint
    FROM company
    WHERE id = ANY($1::bigint[])
"""

COMPANY_ROWS_SQL = f"""
    SELECT {', '.join(COMPANY_COLUMNS)}
    FROM company
    WHERE id = ANY($1::bigint[])
    ORDER BY id
"""

LOGO_FINGERPRINT_SQL = """
    SELECT company_id AS id, md5(bytes) AS fingerprint
    FROM company_logo
    WHERE company_id = ANY($1::bigint[])
"""

LOGO_ROWS_SQL = """
    SELECT company_id, bytes
    FROM company_logo
    WHERE company_id = ANY($1::bigint[])
    ORDER BY company_id
"""

COVERAGE_STAMP_SQL = "SELECT computed_at FROM coverage_snapshot WHERE id"

COVERAGE_ROW_SQL = "SELECT id, payload, computed_at FROM coverage_snapshot WHERE id"


def _distinct_guard(table: str, columns: Sequence[str]) -> str:
    # ON CONFLICT DO UPDATE writes a new row version even when nothing changed;
    # the guard skips identical rows so a resend never churns Neon storage.
    return (
        f"WHERE ROW({', '.join(f'{table}.{column}' for column in columns)}) "
        f"IS DISTINCT FROM ROW({', '.join(f'EXCLUDED.{column}' for column in columns)})"
    )


_COMPANY_UPDATE_COLUMNS = ("name", "domain", "description", "tags")
UPSERT_COMPANY_SQL = f"""
    INSERT INTO company(
        id, name, domain, description, tags
    ) OVERRIDING SYSTEM VALUE
    VALUES($1, $2, $3, $4, $5::jsonb)
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        domain = EXCLUDED.domain,
        description = EXCLUDED.description,
        tags = EXCLUDED.tags
    {_distinct_guard('company', _COMPANY_UPDATE_COLUMNS)}
"""

UPSERT_LOGO_SQL = f"""
    INSERT INTO company_logo(company_id, bytes)
    VALUES($1, $2)
    ON CONFLICT (company_id) DO UPDATE SET bytes = EXCLUDED.bytes
    {_distinct_guard('company_logo', ('bytes',))}
"""

_JOB_PARAMETERS = ", ".join(f"${index}" for index in range(1, len(JOB_COLUMNS) + 1))
_JOB_UPDATE_COLUMNS = tuple(
    column for column in JOB_COLUMNS if column not in {"id", "source", "source_key"}
)
UPSERT_JOB_SQL = f"""
    INSERT INTO job_posting({', '.join(JOB_COLUMNS)})
    VALUES({_JOB_PARAMETERS})
    ON CONFLICT (id) DO UPDATE SET
        {', '.join(f'{column} = EXCLUDED.{column}' for column in _JOB_UPDATE_COLUMNS)},
        source = EXCLUDED.source,
        source_key = EXCLUDED.source_key
    {_distinct_guard('job_posting', (*_JOB_UPDATE_COLUMNS, 'source', 'source_key'))}
"""

# A posting outside the current cohort becomes invisible to active UI reads.
# Keep referenced rows for personal history; purge unreferenced closed rows below.
SOFT_CLOSE_SQL = """
    UPDATE job_posting
    SET status = 'closed'
    WHERE status = 'active'
      AND NOT (id = ANY($1::bigint[]))
"""

DELETE_CLOSED_SQL = """
    DELETE FROM job_posting j
    WHERE j.status = 'closed'
      AND NOT EXISTS (
          SELECT 1 FROM job_application a WHERE a.job_posting_id = j.id
      )
      AND NOT EXISTS (
          SELECT 1 FROM job_feedback f WHERE f.job_posting_id = j.id
      )
"""
DELETE_CLOSED_PROTECTED_SQL = DELETE_CLOSED_SQL + " AND NOT (j.id = ANY($1::bigint[]))"

UPSERT_COVERAGE_SQL = """
    INSERT INTO coverage_snapshot(id, payload, computed_at)
    VALUES($1, $2::jsonb, $3)
    ON CONFLICT (id) DO UPDATE SET
        payload = EXCLUDED.payload,
        computed_at = EXCLUDED.computed_at
"""


@dataclass(frozen=True, slots=True)
class PublishCounts:
    cohort: int
    postings: int
    companies: int
    logos: int
    closed: int
    coverage: bool


def _values(row: Any, columns: Sequence[str]) -> tuple[Any, ...]:
    return tuple(row[column] for column in columns)


def _command_count(result: str) -> int:
    try:
        return int(result.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def _fetch_utc(conn: Any, query: str, *args: Any) -> list[Any]:
    async with conn.transaction():
        await conn.execute("SET LOCAL TIME ZONE 'UTC'")
        return await conn.fetch(query, *args)


def _fingerprints(rows: Sequence[Any]) -> dict[int, str]:
    return {int(row["id"]): row["fingerprint"] for row in rows}


def _differing(source: dict[int, str], target: dict[int, str]) -> list[int]:
    return sorted(key for key, value in source.items() if target.get(key) != value)


def changed_job_ids(
    cohort: Sequence[Any],
    published: Sequence[Any],
    *,
    seen_drift_s: float = DEFAULT_SEEN_DRIFT_S,
) -> list[int]:
    """Cohort ids Neon lacks, holds different content for, or shows stale."""
    target = {int(row["id"]): row for row in published}
    changed = []
    for row in cohort:
        current = target.get(int(row["id"]))
        if current is None or current["fingerprint"] != row["fingerprint"]:
            changed.append(int(row["id"]))
            continue
        seen, shown = row["last_seen_at"], current["last_seen_at"]
        if seen is not None and (
            shown is None or (seen - shown).total_seconds() > seen_drift_s
        ):
            changed.append(int(row["id"]))
    return changed


async def read_cohort(source: Any) -> list[Any]:
    cohort = await _fetch_utc(source, COHORT_SQL)
    if not cohort:
        raise RuntimeError(
            "publisher selected zero postings; refusing to soft-close the Neon feed"
        )
    return cohort


async def _assert_thin_target(target: Any) -> None:
    valid = await target.fetchval(
        """
        SELECT
            to_regclass('job_posting') IS NOT NULL
            AND to_regclass('company') IS NOT NULL
            AND to_regclass('coverage_snapshot') IS NOT NULL
            AND to_regclass('raw_posting') IS NULL
        """
    )
    if not valid:
        raise RuntimeError(
            "publish target is not the thin OneShot schema; refusing to write"
        )


async def _coverage_uses_id(target: Any) -> bool:
    return bool(await target.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'coverage_snapshot'
              AND column_name = 'id'
        )
        """
    ))


async def _ensure_coverage_schema(target: Any) -> None:
    if await _coverage_uses_id(target):
        return
    if await target.fetchval("SELECT COUNT(*) FROM coverage_snapshot"):
        raise RuntimeError(
            "legacy coverage_snapshot contains data; migrate it before publishing"
        )
    # Early thin schemas used two keyed rows. The user's table is empty, so keep
    # it as a recoverable backup and install the current singleton shape. This
    # DDL shares the publish transaction and rolls back if any later write fails.
    await target.execute(
        "ALTER TABLE coverage_snapshot RENAME TO coverage_snapshot_legacy"
    )
    await target.execute(
        """
        CREATE TABLE coverage_snapshot (
            id boolean PRIMARY KEY DEFAULT true CHECK (id),
            payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
            computed_at timestamptz NOT NULL
        )
        """
    )


async def publish(
    source: Any,
    target: Any,
    *,
    dry_run: bool = False,
    seen_drift_s: float = DEFAULT_SEEN_DRIFT_S,
    retained: dict[str, list[Any]] | None = None,
    cleanup_closed: bool = True,
    company_edit_revisions: dict[int, Any] | None = None,
    maintained_company_ids: list[int] | None = None,
) -> PublishCounts:
    """Send Neon only what differs from the home cohort.

    Both sides are fingerprinted in SQL, so an unchanged row costs one md5 on
    each end rather than a full transfer and a rewrite.
    """
    cohort = await read_cohort(source)
    cohort_ids = [int(row["id"]) for row in cohort]
    company_ids = sorted({int(row["company_id"]) for row in cohort} | set(company_edit_revisions or {}) | set(maintained_company_ids or []))

    await _assert_thin_target(target)
    # Source reads can take minutes, particularly when both targets share one
    # snapshot. Complete them outside the target's write transaction: Neon
    # terminates a transaction that sits idle while waiting on the source.
    source_companies = _fingerprints(await _fetch_utc(source, COMPANY_FINGERPRINT_SQL, company_ids))
    source_logos = _fingerprints(await source.fetch(LOGO_FINGERPRINT_SQL, company_ids))
    source_stamp = await source.fetchval(COVERAGE_STAMP_SQL)

    async with target.transaction():
        await target.execute("SET LOCAL TIME ZONE 'UTC'")
        published = await target.fetch(PUBLISHED_JOBS_SQL)
        target_companies = _fingerprints(await target.fetch(COMPANY_FINGERPRINT_SQL, company_ids))
        target_logos = _fingerprints(await target.fetch(LOGO_FINGERPRINT_SQL, company_ids))
        target_stamp = await target.fetchval(COVERAGE_STAMP_SQL) if await _coverage_uses_id(target) else None

    job_ids = changed_job_ids(cohort, published, seen_drift_s=seen_drift_s)
    changed_companies = _differing(source_companies, target_companies)
    changed_logos = _differing(source_logos, target_logos)
    cohort_set = set(cohort_ids)
    departed_ids = {int(row["id"]) for row in published} - cohort_set
    if retained is not None:
        # Retained central history is restored first. Reapply canonical source
        # rows where it overlaps the active cohort, even if the target already
        # matched before that restoration. Identical upserts still skip writes.
        job_ids = sorted(set(job_ids) | ({int(row["id"]) for row in retained["jobs"]} & cohort_set))
        changed_companies = sorted(set(changed_companies) | ({int(row["id"]) for row in retained["companies"]} & source_companies.keys()))
        changed_logos = sorted(set(changed_logos) | ({int(row["company_id"]) for row in retained["logos"]} & source_logos.keys()))
        departed_ids |= {int(row["id"]) for row in retained["jobs"] if row["status"] == "active"} - cohort_set

    companies = [_values(row, COMPANY_COLUMNS) for row in await source.fetch(COMPANY_ROWS_SQL, changed_companies)] if changed_companies else []
    logos = [_values(row, ("company_id", "bytes")) for row in await source.fetch(LOGO_ROWS_SQL, changed_logos)] if changed_logos else []
    jobs = [_values(row, JOB_COLUMNS) for row in await source.fetch(JOB_ROWS_SQL, job_ids)] if job_ids else []
    closures = [_values(row, ("id", *CLOSURE_COLUMNS)) for row in await source.fetch(SOURCE_CLOSURES_SQL, sorted(departed_ids))] if departed_ids else []
    coverage_changed = source_stamp is not None and source_stamp != target_stamp
    coverage = await source.fetchrow(COVERAGE_ROW_SQL) if coverage_changed else None

    transaction = target.transaction()
    await transaction.start()
    try:
        await _ensure_coverage_schema(target)
        await target.execute("SET LOCAL TIME ZONE 'UTC'")

        # Local personal tables stay empty. Retain the public job snapshots
        # referenced by central tracking without copying accounts or tracker rows.
        if retained is not None:
            for query, rows, columns in (
                (UPSERT_COMPANY_SQL, retained["companies"], COMPANY_COLUMNS),
                (UPSERT_LOGO_SQL, retained["logos"], ("company_id", "bytes")),
                (UPSERT_JOB_SQL, retained["jobs"], JOB_COLUMNS),
            ):
                if rows:
                    await target.executemany(query, [_values(row, columns) for row in rows])

        for query, rows in (
            (UPSERT_COMPANY_SQL, companies),
            (UPSERT_LOGO_SQL, logos),
            (UPSERT_JOB_SQL, jobs),
            (UPDATE_CLOSURES_SQL, closures),
        ):
            if rows:
                await target.executemany(query, rows)
        closed = _command_count(await target.execute(SOFT_CLOSE_SQL, cohort_ids))
        if coverage is not None:
            await target.execute(
                UPSERT_COVERAGE_SQL,
                coverage["id"],
                coverage["payload"],
                coverage["computed_at"],
            )
        if cleanup_closed:
            if retained is not None and retained["jobs"]:
                await target.execute(DELETE_CLOSED_PROTECTED_SQL, [row["id"] for row in retained["jobs"]])
            else:
                await target.execute(DELETE_CLOSED_SQL)
        if company_edit_revisions:
            await target.executemany(
                "DELETE FROM company_edit WHERE company_id = $1 AND revision = $2",
                list(company_edit_revisions.items()),
            )
        if dry_run:
            await transaction.rollback()
        else:
            await transaction.commit()
    except BaseException:
        try:
            await transaction.rollback()
        except Exception:
            logger.warning("Publisher rollback failed; preserving the original error", exc_info=True)
        raise

    return PublishCounts(
        cohort=len(cohort),
        postings=len(job_ids),
        companies=len(changed_companies),
        logos=len(changed_logos),
        closed=closed,
        coverage=coverage_changed,
    )


def _load_dotenv() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    for candidate in (Path.cwd() / ".env", repo_root / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().removeprefix("export ").strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key:
                os.environ.setdefault(key, value)


async def connect_target(dsn: str, **connect_kwargs: Any) -> asyncpg.Connection:
    # The runtime's dotenv loader retains quotes, and Neon supplies the libpq
    # channel_binding option, which asyncpg treats as an invalid server setting.
    # Preserve the URL's TLS settings while adapting it for this driver.
    dsn = dsn.strip()
    if len(dsn) >= 2 and dsn[0] == dsn[-1] and dsn[0] in "\"'":
        dsn = dsn[1:-1]
    parsed = urlsplit(dsn)
    dsn = urlunsplit(parsed._replace(query=urlencode([
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "channel_binding"
    ])))
    # Rows read from a store pool carry jsonb as Python objects; the target
    # must encode them the same way or asyncpg rejects a dict where it
    # expects JSON text.
    target = await asyncpg.connect(dsn, **connect_kwargs)
    try:
        await store._install_jsonb_codec(target)
    except BaseException:
        await target.close()
        raise
    return target


async def publish_to(
    source: Any,
    target_dsn: str,
    *,
    dry_run: bool = False,
    seen_drift_s: float = DEFAULT_SEEN_DRIFT_S,
) -> PublishCounts:
    from xjobscore import serving

    # A fresh connection per run lets Neon's compute scale to zero in between.
    target = await connect_target(target_dsn)
    try:
        if not isinstance(source, serving.Source):
            from xjobscore import company_edits
            revisions = await company_edits.sync(source, target, dry_run=dry_run)
            source = serving.Source(source, await serving.prepare(source))
            source.company_edit_revisions = revisions
        source.maintained_company_ids = [int(row['id']) for row in await source.fetch(
            "SELECT id FROM frontend_identity m WHERE kind='company' "
            "AND COALESCE(to_jsonb(m)->'previous_keys', '[]'::jsonb) <> '[]'::jsonb"
        )]
        await serving.validate_target(source, target)
        return await publish(
            source, target, dry_run=dry_run, seen_drift_s=seen_drift_s,
            retained=source.retained, cleanup_closed=source.cleanup_closed,
            company_edit_revisions=source.company_edit_revisions,
            maintained_company_ids=source.maintained_company_ids,
        )
    finally:
        await target.close()


async def publish_targets(
    pool: Any,
    targets: dict[str, str],
    *,
    dry_run: bool = False,
    seen_drift_s: float = DEFAULT_SEEN_DRIFT_S,
) -> dict[str, PublishCounts | BaseException]:
    """Publish one cohort independently to each target using shared serving IDs."""
    from xjobscore import serving

    async def central_references(shared: Any) -> dict[str, list[Any]] | None:
        if "neon" not in targets:
            return None
        conn = None
        try:
            conn = await connect_target(targets["neon"], timeout=15, server_settings={"default_transaction_read_only":"on"})
            jobs = await conn.fetch(f"""
                SELECT {', '.join('j.' + c for c in JOB_COLUMNS)} FROM job_posting j
                WHERE EXISTS (SELECT 1 FROM job_application a WHERE a.job_posting_id=j.id)
                   OR EXISTS (SELECT 1 FROM job_feedback f WHERE f.job_posting_id=j.id)
            """)
            ids = list({row["company_id"] for row in jobs})
            # Retained postings can be old, but company metadata must come
            # from the canonical snapshot whenever its identity still exists.
            companies = {row['id']: row for row in await conn.fetch(COMPANY_ROWS_SQL, ids)}
            companies.update({row['id']: row for row in await serving.Source(shared).fetch(COMPANY_ROWS_SQL, ids)})
            return {
                "jobs": jobs,
                "companies": list(companies.values()),
                "logos": await conn.fetch(LOGO_ROWS_SQL, ids),
            }
        except Exception:
            # A central outage must not stop local updates. Postpone local
            # deletion until central references can be checked again.
            return None
        finally:
            if conn is not None:
                await conn.close()


    async with pool.acquire() as connection:
        # Import administrator edits before opening the shared read snapshot.
        # An unavailable target still cannot prevent the other from publishing.
        from xjobscore import company_edits
        revisions = {}
        if not dry_run:
            for name, dsn in targets.items():
                target = None
                try:
                    target = await connect_target(dsn, timeout=15)
                    revisions[name] = await company_edits.sync(connection, target)
                except Exception as exc:
                    logger.warning("Company edit sync for %s failed: %s", name, exc)
                finally:
                    if target is not None:
                        await target.close()
        await serving.prepare(connection)
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            shared = serving.SharedSnapshot(connection)
            cohort = await read_cohort(serving.Source(shared))
            references = asyncio.create_task(central_references(shared))

            async def send(name: str, dsn: str) -> PublishCounts:
                mapped = serving.Source(shared, cohort)
                mapped.company_edit_revisions = revisions.get(name, {})
                if name == "local":
                    mapped.retained = await references
                    mapped.cleanup_closed = mapped.retained is not None
                return await publish_to(
                    mapped, dsn, dry_run=dry_run, seen_drift_s=seen_drift_s,
                )

            try:
                results = await asyncio.gather(
                    *(send(name, dsn) for name, dsn in targets.items()),
                    return_exceptions=True,
                )
            finally:
                if not references.done():
                    references.cancel()
                await asyncio.gather(references, return_exceptions=True)
    return dict(zip(targets, results))


def format_counts(counts: PublishCounts) -> str:
    return (
        f"cohort={counts.cohort} postings={counts.postings} "
        f"companies={counts.companies} logos={counts.logos} "
        f"closed={counts.closed} coverage={'sent' if counts.coverage else 'unchanged'}"
    )


async def main(argv: Sequence[str] | None = None) -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dsn",
        help="home PostgreSQL DSN; default: $DATABASE_URL",
    )
    parser.add_argument(
        "--target-dsn",
        help="thin Neon DSN; default: $PUBLISH_DATABASE_URL",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--local-target-dsn", help="local frontend DSN; default: $FRONTEND_DATABASE_URL"
    )
    parser.add_argument(
        "--seen-drift-s",
        type=float,
        default=DEFAULT_SEEN_DRIFT_S,
        help="resend an unchanged posting once Neon's last_seen_at lags by this much",
    )
    args = parser.parse_args(argv)

    source_dsn = args.source_dsn or os.environ.get("DATABASE_URL")
    target_dsn = args.target_dsn or os.environ.get("PUBLISH_DATABASE_URL")
    local_dsn = args.local_target_dsn or os.environ.get("FRONTEND_DATABASE_URL")
    if not source_dsn:
        raise ValueError("no source DSN: pass --source-dsn or set DATABASE_URL")
    if not target_dsn and not local_dsn:
        raise ValueError(
            "no target DSN: pass --target-dsn or set PUBLISH_DATABASE_URL"
        )
    if source_dsn in {target_dsn, local_dsn}:
        raise ValueError("source and target DSNs must be different")

    pool = await store.connect(source_dsn)
    try:
        targets = {}
        if local_dsn:
            targets["local"] = local_dsn
        if target_dsn:
            targets["neon"] = target_dsn
        results = await publish_targets(
            pool, targets, dry_run=args.dry_run, seen_drift_s=args.seen_drift_s,
        )
    finally:
        await pool.close()
    failed = False
    for name, result in results.items():
        if isinstance(result, BaseException):
            print(f"{name}: {type(result).__name__}: {result}", file=sys.stderr)
            failed = True
        else:
            print(name + ": " + format_counts(result) + (" (dry run, rolled back)" if args.dry_run else ""))
    return 1 if failed else 0


def cli() -> int:
    return asyncio.run(main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(cli())
