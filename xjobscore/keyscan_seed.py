"""Import keyscan tokens that have no company yet, minting the company row.

``keyscan_import`` is lookup-only by design: a token whose slug resolves to no
existing ``company`` is skipped and retried forever. That is the right default
for a bulk sweep of 17k tokens, most of which nobody has decided to follow.
It is the wrong behaviour for a hand-picked batch, where the whole point is
that these employers are *new* -- 329 of 336 resolve to nothing, so lookup-only
would import seven rows and drop the rest.

This module is the other half. It takes an explicit list of tokens, resolves
what it can, and mints a ``company`` for what it cannot:

- ``company.reviewed`` stays ``'pending'``. A minted row is a guess, not a
  verdict: its name is whatever keyscan read off the board (or, for 77 of the
  batch, the URL slug itself), and its domain is unknown. The Companies page
  is where that gets settled, and these rows land in its queue by construction.
- ``company_alias`` gets every spelling of both the name and the token slug,
  ``ON CONFLICT DO NOTHING``. ``silver`` resolves incoming ``company_raw``
  through that table, so without the slug alias the postings this import exists
  to collect would arrive and fail to resolve to the row just minted for them.
- ``feed`` rows are created too. A ``token`` row is inert -- nothing polls it --
  so an import without feeds produces companies with zero postings forever.

Selection lives outside this module. It reads a CSV of ``ats,token,name`` so
that *what to import* stays an analysis question, reviewable on its own, and
this file stays a writer that can be read for correctness in one sitting.

Two databases, one batch. PostgreSQL commits first and keyscan's ``imported_at``
is stamped only for rows that actually landed; if the process dies in between,
the batch is simply retried, and the retry resolves through the alias rows the
first attempt wrote rather than minting a second company.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xjobscore import store
from xjobscore.keyscan_import import DEFAULT_KEYSCAN_DB, resolve_ats, resolve_key

if TYPE_CHECKING:
    from asyncpg import Connection, Pool

logger = logging.getLogger(__name__)

__all__ = ["Candidate", "Result", "load_candidates", "seed_keyscan_tokens", "main"]


@dataclass(frozen=True, slots=True)
class Candidate:
    keyscan_ats: str  # keyscan's own label, used to stamp its row
    ats: str  # post-rename label written to PostgreSQL
    token: str
    name: str  # keyscan's name for the board; "" when it never captured one


@dataclass
class Result:
    total: int = 0
    resolved: int = 0  # matched an existing company
    minted: int = 0  # created a company
    tokens_written: int = 0
    feeds_created: int = 0
    feeds_existing: int = 0
    marked: int = 0
    declined: list[str] = field(default_factory=list)


def load_candidates(path: Path) -> list[Candidate]:
    """Read the selection CSV: ``ats,token,name`` with a header row."""
    rows: list[Candidate] = []
    with path.open(newline="") as handle:
        for record in csv.DictReader(handle):
            keyscan_ats = record["ats"].strip()
            token = record["token"].strip()
            if not keyscan_ats or not token:
                raise ValueError(f"blank ats/token in {path}: {record!r}")
            rows.append(
                Candidate(
                    keyscan_ats=keyscan_ats,
                    ats=resolve_ats(keyscan_ats),
                    token=token,
                    name=(record.get("name") or "").strip(),
                )
            )
    seen = {(row.ats, row.token) for row in rows}
    if len(seen) != len(rows):
        raise ValueError(f"duplicate (ats, token) in {path}")
    return rows


def _display_name(candidate: Candidate) -> str:
    """What to call the company. The slug is the fallback, never a blank name.

    ``company.name`` is NOT NULL and the frontend prints it on every posting,
    so a nameless board becomes `windborne-systems` rather than an empty cell --
    ugly, visibly wrong, and therefore certain to be corrected in the queue.
    """
    return candidate.name or resolve_key(candidate.ats, candidate.token)


async def _mint(
    conn: Connection, candidate: Candidate, *, name: str, tag: str | None
) -> int:
    """Create the company row for a token nothing else resolves.

    *tag* marks a batch for later removal. It is a jsonb key on `tags` rather
    than a column because it is temporary by construction -- a recon batch that
    gets torn down should leave no trace in the schema once it is gone, and
    `tags ? 'recon'` is both the UI's exclusion filter and the teardown's
    selector.
    """
    tags: dict[str, Any] = {}
    if tag:
        tags[tag] = datetime.now(timezone.utc).date().isoformat()
    return await store.mint_company(conn, name, discovered_from="keyscan", tags=tags)


def _mark_imported(db_path: Path, rows: list[Candidate], *, now: str) -> int:
    con = sqlite3.connect(db_path)
    try:
        cursor = con.executemany(
            "UPDATE tenants SET imported_at = ? WHERE ats = ? AND key = ?",
            [(now, row.keyscan_ats, row.token) for row in rows],
        )
        con.commit()
        return cursor.rowcount
    finally:
        con.close()


async def seed_keyscan_tokens(
    pool: Pool,
    candidates: list[Candidate],
    *,
    db_path: Path,
    enable_feeds: bool = True,
    tag: str | None = None,
    dry_run: bool = False,
) -> Result:
    result = Result(total=len(candidates))
    if not candidates:
        return result

    # Whole seconds on both sides: keyscan's sqlite stores this same stamp at
    # second precision, so microseconds here would leave one batch unjoinable.
    now = datetime.now().astimezone().replace(microsecond=0)
    written: list[Candidate] = []

    async with pool.acquire() as conn:
        aliases = await store.load_alias_map(conn)
        async with conn.transaction():
            for candidate in candidates:
                name = _display_name(candidate)
                company_id = await store.resolve_company(
                    conn, resolve_key(candidate.ats, candidate.token), aliases=aliases
                )
                if company_id is None and candidate.name:
                    company_id = await store.resolve_company(
                        conn, candidate.name, aliases=aliases
                    )
                if company_id is None:
                    if dry_run:
                        result.minted += 1
                        continue
                    company_id = await _mint(conn, candidate, name=name, tag=tag)
                    result.minted += 1
                    # Feed the in-memory map as we go: two tokens naming the
                    # same employer must land on one company, and the map is
                    # the only thing that sees the row we just inserted.
                    for key in store.alias_candidates(name):
                        aliases.setdefault(key, company_id)
                else:
                    result.resolved += 1

                if dry_run:
                    continue

                await store.write_company_aliases(
                    conn, company_id, name, resolve_key(candidate.ats, candidate.token)
                )
                for key in store.alias_candidates(
                    resolve_key(candidate.ats, candidate.token)
                ):
                    aliases.setdefault(key, company_id)

                # The conflict branch declines any token the native feeder
                # already owns; only what actually landed may be marked done.
                ok = await store.upsert_keyscan_token(
                    conn,
                    ats=candidate.ats,
                    token=candidate.token,
                    company_id=company_id,
                    status="live",
                    checked_at=now,
                    discovered_at=now,
                    imported_at=now,
                )
                if ok:
                    written.append(candidate)
                    result.tokens_written += 1
                else:
                    result.declined.append(f"{candidate.ats}/{candidate.token}")

            if not dry_run and written:
                created, existing = await store.import_feeds(
                    conn,
                    [(row.ats, row.token) for row in written],
                    enabled=enable_feeds,
                )
                result.feeds_created, result.feeds_existing = created, existing

    if not dry_run and written:
        result.marked = _mark_imported(
            db_path, written, now=now.isoformat(timespec="seconds")
        )
    return result


async def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("candidates", type=Path, help="CSV of ats,token,name")
    parser.add_argument("--dsn", help="Postgres DSN; default: $DATABASE_URL")
    parser.add_argument("--keyscan-db", type=Path, default=DEFAULT_KEYSCAN_DB)
    parser.add_argument(
        "--feeds-off",
        action="store_true",
        help="create feed rows with polling disabled (default: enabled)",
    )
    parser.add_argument(
        "--tag",
        help="jsonb key stamped on every minted company, e.g. 'recon' -- the "
        "selector the teardown and the UI exclusion both use",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    dsn = args.dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        raise ValueError("no dsn: pass --dsn or set DATABASE_URL")
    if not args.keyscan_db.exists():
        raise ValueError(f"keyscan database not found: {args.keyscan_db}")

    logging.basicConfig(level=os.environ.get("XJOBSCORE_LOG_LEVEL", "INFO"))
    candidates = load_candidates(args.candidates)
    pool = await store.connect(dsn)
    try:
        result = await seed_keyscan_tokens(
            pool,
            candidates,
            db_path=args.keyscan_db,
            enable_feeds=not args.feeds_off,
            tag=args.tag,
            dry_run=args.dry_run,
        )
    finally:
        await pool.close()

    print(
        f"candidates={result.total} resolved={result.resolved} minted={result.minted}\n"
        f"tokens={result.tokens_written} feeds=+{result.feeds_created} "
        f"(existing {result.feeds_existing}) keyscan_marked={result.marked}"
        f"{' (dry run, nothing written)' if args.dry_run else ''}"
    )
    for declined in result.declined:
        print(f"declined (owned by another source): {declined}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
