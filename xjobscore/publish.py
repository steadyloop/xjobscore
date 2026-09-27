"""Publish the OneShot serving subset from the home database to thin Neon."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg


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
    "vendor_closed_at",
    "link_marked_at",
    "link_checked_at",
    "link_closed_at",
    "attributes",
    "degree_version",
    "job_domain",
    "job_domain_version",
    "raw_id",
    "removal_marked_at",
)

COMPANY_COLUMNS = (
    "id",
    "name",
    "domain",
    "description",
    "tags",
)

# This is the broad serving cohort, not merely the default Software view. The
# frontend can therefore apply every focus-area filter without needing the home
# database. Eligible duplicate groups publish one active representative; a
# rejected group is deliberately not collapsed because Gold decided its members
# are separate jobs.
OPPORTUNITY_SQL = f"""
    SELECT {', '.join(f'j.{column}' for column in JOB_COLUMNS)}
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

UPSERT_COMPANY_SQL = """
    INSERT INTO company(
        id, name, domain, description, tags
    ) OVERRIDING SYSTEM VALUE
    VALUES($1, $2, $3, $4, $5::jsonb)
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        domain = EXCLUDED.domain,
        description = EXCLUDED.description,
        tags = EXCLUDED.tags
"""

UPSERT_LOGO_SQL = """
    INSERT INTO company_logo(company_id, bytes)
    VALUES($1, $2)
    ON CONFLICT (company_id) DO UPDATE SET bytes = EXCLUDED.bytes
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
"""

# No target row is deleted. A posting that closed, moved out of the supported
# geography, was reclassified, or lost duplicate-representative status simply
# leaves the current cohort and becomes invisible to status='active' UI reads.
SOFT_CLOSE_SQL = """
    UPDATE job_posting
    SET status = 'closed'
    WHERE status = 'active'
      AND NOT (id = ANY($1::bigint[]))
"""

UPSERT_COVERAGE_SQL = """
    INSERT INTO coverage_snapshot(id, payload, computed_at)
    VALUES($1, $2::jsonb, $3)
    ON CONFLICT (id) DO UPDATE SET
        payload = EXCLUDED.payload,
        computed_at = EXCLUDED.computed_at
"""

@dataclass(frozen=True, slots=True)
class Snapshot:
    jobs: tuple[Any, ...]
    companies: tuple[Any, ...]
    logos: tuple[Any, ...]
    coverage: Any | None


@dataclass(frozen=True, slots=True)
class PublishCounts:
    postings: int
    companies: int
    logos: int
    closed: int


def _values(row: Any, columns: Sequence[str]) -> tuple[Any, ...]:
    return tuple(row[column] for column in columns)


def _command_count(result: str) -> int:
    try:
        return int(result.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def read_snapshot(source: Any) -> Snapshot:
    jobs = tuple(await source.fetch(OPPORTUNITY_SQL))
    if not jobs:
        raise RuntimeError(
            "publisher selected zero postings; refusing to soft-close the Neon feed"
        )

    company_ids = sorted({int(row["company_id"]) for row in jobs})
    companies = tuple(
        await source.fetch(
            f"""
            SELECT {', '.join(COMPANY_COLUMNS)}
            FROM company
            WHERE id = ANY($1::bigint[])
            ORDER BY id
            """,
            company_ids,
        )
    )
    logos = tuple(
        await source.fetch(
            """
            SELECT company_id, bytes
            FROM company_logo
            WHERE company_id = ANY($1::bigint[])
            ORDER BY company_id
            """,
            company_ids,
        )
    )
    coverage = await source.fetchrow(
        "SELECT id, payload, computed_at FROM coverage_snapshot WHERE id"
    )
    return Snapshot(jobs, companies, logos, coverage)


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


async def _ensure_coverage_schema(target: Any) -> None:
    uses_id = await target.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'coverage_snapshot'
              AND column_name = 'id'
        )
        """
    )
    if uses_id:
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


async def write_snapshot(
    target: Any, snapshot: Snapshot, *, dry_run: bool = False
) -> PublishCounts:
    await _assert_thin_target(target)
    transaction = target.transaction()
    await transaction.start()
    try:
        await _ensure_coverage_schema(target)
        await target.executemany(
            UPSERT_COMPANY_SQL,
            [_values(row, COMPANY_COLUMNS) for row in snapshot.companies],
        )
        await target.executemany(
            UPSERT_LOGO_SQL,
            [(row["company_id"], row["bytes"]) for row in snapshot.logos],
        )
        await target.executemany(
            UPSERT_JOB_SQL,
            [_values(row, JOB_COLUMNS) for row in snapshot.jobs],
        )
        current_ids = [int(row["id"]) for row in snapshot.jobs]
        closed = _command_count(await target.execute(SOFT_CLOSE_SQL, current_ids))
        if snapshot.coverage is not None:
            await target.execute(
                UPSERT_COVERAGE_SQL,
                snapshot.coverage["id"],
                snapshot.coverage["payload"],
                snapshot.coverage["computed_at"],
            )
        if dry_run:
            await transaction.rollback()
        else:
            await transaction.commit()
    except BaseException:
        await transaction.rollback()
        raise

    return PublishCounts(
        postings=len(snapshot.jobs),
        companies=len(snapshot.companies),
        logos=len(snapshot.logos),
        closed=closed,
    )


async def publish(
    source: Any, target: Any, *, dry_run: bool = False
) -> PublishCounts:
    snapshot = await read_snapshot(source)
    return await write_snapshot(target, snapshot, dry_run=dry_run)


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
    args = parser.parse_args(argv)

    source_dsn = args.source_dsn or os.environ.get("DATABASE_URL")
    target_dsn = args.target_dsn or os.environ.get("PUBLISH_DATABASE_URL")
    if not source_dsn:
        raise ValueError("no source DSN: pass --source-dsn or set DATABASE_URL")
    if not target_dsn:
        raise ValueError(
            "no target DSN: pass --target-dsn or set PUBLISH_DATABASE_URL"
        )
    if source_dsn == target_dsn:
        raise ValueError("source and target DSNs must be different")

    source = await asyncpg.connect(source_dsn)
    try:
        target = await asyncpg.connect(target_dsn)
        try:
            counts = await publish(source, target, dry_run=args.dry_run)
        finally:
            await target.close()
    finally:
        await source.close()

    print(
        f"postings={counts.postings} companies={counts.companies} "
        f"logos={counts.logos} closed={counts.closed}"
        f"{' (dry run, rolled back)' if args.dry_run else ''}"
    )
    return 0


def cli() -> int:
    return asyncio.run(main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(cli())
