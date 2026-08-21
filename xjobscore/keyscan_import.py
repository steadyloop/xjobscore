"""Bulk-import keyscan's discovered tokens into xjobscore's own token table.

keyscan (``keyscan/``) discovers ATS tokens by scanning Common Crawl into its
own SQLite database, entirely separate from xjobscore's Postgres schema. This
module is the one-way bridge: it reads keyscan's confirmed live/dead rows,
resolves each to an existing ``company`` by name, and writes only the rows
that resolve -- schema.sql's ``token.imported_at`` column exists for exactly
this ("set when a row is bulk-loaded from an external resolution (e.g.
keyscan)") but had no writer until now.

Unresolvable tokens are skipped, not written with a NULL company_id and not
used to mint new company rows -- company resolution here is lookup-only,
reusing ``store.resolve_company``. A skipped row is retried on every future
run (it stays ``imported_at IS NULL`` on the keyscan side) as the company/alias
tables grow, with no separate bookkeeping needed.

One invocation processes whatever keyscan has queued (``imported_at IS NULL``)
and exits -- repeated invocations are cheap and idempotent, matching every
other entry point in this package (see run.py's module docstring).
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from xjobscore import store

if TYPE_CHECKING:
    from asyncpg import Pool

logger = logging.getLogger(__name__)

__all__ = ["Row", "import_keyscan_tokens", "load_pending", "main", "resolve_ats", "resolve_key"]

DEFAULT_KEYSCAN_DB = Path(__file__).resolve().parent.parent / "keyscan" / "work" / "keyscan.sqlite"

# keyscan's on-disk `ats` label for a rule that has since been renamed. The
# rule itself is unambiguous -- `myworkdayjobs.com` -- only the stored label
# for tokens discovered before the rename lags behind.
_ATS_RENAMES = {"workday": "workday-list"}

# Vendors whose token is a compound path (`tenant/wdN/site`, `tenant/site`)
# rather than a bare company slug -- the company-resolution candidate is the
# first path segment, not the full token.
_TENANT_PREFIXED_ATS = {"workday-list", "oracle"}


@dataclass(frozen=True, slots=True)
class Row:
    keyscan_ats: str  # the label as stored in keyscan's own database
    ats: str  # the label to write into xjobscore (post-rename mapping)
    token: str
    status: str
    checked_at: datetime | None
    discovered_at: datetime


def resolve_ats(keyscan_ats: str) -> str:
    return _ATS_RENAMES.get(keyscan_ats, keyscan_ats)


def resolve_key(ats: str, token: str) -> str:
    """The name-like substring of `token` to resolve a company against."""
    if ats in _TENANT_PREFIXED_ATS:
        return token.split("/", 1)[0]
    return token


def load_pending(db_path: Path) -> list[Row]:
    """Keyscan rows not yet imported: resolved status, never sent before."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT ats, token, status, checked_at, discovered_at
               FROM token
               WHERE status IN ('live', 'dead') AND imported_at IS NULL"""
        ).fetchall()
    finally:
        con.close()
    return [
        Row(
            keyscan_ats=row["ats"],
            ats=resolve_ats(row["ats"]),
            token=row["token"],
            status=row["status"],
            checked_at=datetime.fromisoformat(row["checked_at"]) if row["checked_at"] else None,
            discovered_at=datetime.fromisoformat(row["discovered_at"]),
        )
        for row in rows
    ]


def _mark_imported(db_path: Path, written: list[Row], *, now: str) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.executemany(
            "UPDATE token SET imported_at = ? WHERE ats = ? AND token = ?",
            [(now, row.keyscan_ats, row.token) for row in written],
        )
        con.commit()
    finally:
        con.close()


@dataclass(frozen=True, slots=True)
class Result:
    pending: int
    resolved: int
    written: int
    by_ats: dict[str, tuple[int, int]]  # ats -> (pending, resolved)


async def import_keyscan_tokens(
    pool: Pool, db_path: Path, *, dry_run: bool = False
) -> Result:
    pending = load_pending(db_path)
    by_ats: dict[str, list[int]] = {}
    for row in pending:
        counts = by_ats.setdefault(row.ats, [0, 0])
        counts[0] += 1

    written: list[Row] = []
    async with pool.acquire() as conn:
        aliases = await store.load_alias_map(conn)
        resolved_rows: list[tuple[Row, int]] = []
        for row in pending:
            company_id = await store.resolve_company(conn, resolve_key(row.ats, row.token), aliases=aliases)
            if company_id is None:
                continue
            by_ats[row.ats][1] += 1
            resolved_rows.append((row, company_id))

        if not dry_run:
            now = datetime.now().astimezone()
            async with conn.transaction():
                for row, company_id in resolved_rows:
                    # A resolved row can still be declined: the conflict-branch
                    # WHERE in upsert_keyscan_token refuses to touch a row the
                    # native feeder pipeline already owns. Only what actually
                    # got written is eligible to be marked imported on the
                    # keyscan side -- a declined row must stay retryable, not
                    # be silently recorded as done.
                    ok = await store.upsert_keyscan_token(
                        conn,
                        ats=row.ats,
                        token=row.token,
                        company_id=company_id,
                        status=row.status,
                        checked_at=row.checked_at,
                        discovered_at=row.discovered_at,
                        imported_at=now,
                    )
                    if ok:
                        written.append(row)
            _mark_imported(db_path, written, now=now.isoformat(timespec="seconds"))

    return Result(
        pending=len(pending),
        resolved=sum(r for _, r in by_ats.values()),
        written=len(written),
        by_ats={ats: (p, r) for ats, (p, r) in by_ats.items()},
    )


def _env_dsn() -> str | None:
    return os.environ.get("XJOBSCORE_DSN") or os.environ.get("DATABASE_URL")


async def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", help="Postgres DSN; default: $XJOBSCORE_DSN or $DATABASE_URL")
    parser.add_argument(
        "--keyscan-db", type=Path, default=DEFAULT_KEYSCAN_DB, help="path to keyscan's SQLite database"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would import without writing anything"
    )
    args = parser.parse_args(argv)

    dsn = args.dsn or _env_dsn()
    if not dsn:
        raise ValueError("no dsn: pass --dsn or set XJOBSCORE_DSN/DATABASE_URL")
    if not args.keyscan_db.exists():
        raise ValueError(f"keyscan database not found: {args.keyscan_db}")

    logging.basicConfig(level=os.environ.get("XJOBSCORE_LOG_LEVEL", "INFO"))
    pool = await store.connect(dsn)
    try:
        result = await import_keyscan_tokens(pool, args.keyscan_db, dry_run=args.dry_run)
    finally:
        await pool.close()

    for ats, (pending, resolved) in sorted(result.by_ats.items()):
        print(f"{ats}: pending={pending} resolved={resolved}")
    print(
        f"total pending={result.pending} resolved={result.resolved} written={result.written}"
        f"{' (dry run, nothing written)' if args.dry_run else ''}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
