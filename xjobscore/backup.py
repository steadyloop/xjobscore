"""Export and re-import the durable seed: company, company_alias, token.

Everything else in schema.sql is either derived from these three tables or is
not reproducible at all. `feed` is derived -- promotion rebuilds it from
`token` -- and the posting tables replay from the vendors. What cannot be
replayed (posting history, dedup verdicts, removal marks, every account-scoped
table) is not seed data and is out of scope here: this file restores the
*inputs* to a pipeline run, not a past run's output.

`token.config` is the reason this exists. Nothing in the codebase writes it,
so its rows were typed straight into PostgreSQL by an operator: board
hostnames looked up by hand and two Comeet API tokens that cannot be recovered
from any source at all.

Company identity is remapped, never copied. `company.id` is GENERATED ALWAYS
AS IDENTITY, so its integers mean nothing in a different database and cannot
even be re-inserted without OVERRIDING SYSTEM VALUE. Each row travels under a
natural key instead -- `domain` when it has one (UNIQUE in schema.sql), else
`name:` plus the lowercased name. Name uniqueness is *observed*, not
constrained, so an import that finds one key claiming two companies fails
rather than silently merging them.

The file is JSONL: one row per line, a manifest first, each line tagged with
its table. Line-per-row keeps a re-export diffable against the last one, which
a single JSON document would not be.

SECURITY: the export contains live API credentials (`token.config` holds Comeet
tokens) and must not be committed. The default path is gitignored; keep any
other copy outside the repository.

Run from the repository root with:
  python -m xjobscore.backup --export local/seed.jsonl
  python -m xjobscore.backup --import local/seed.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from asyncpg import Pool

from xjobscore import store

log = logging.getLogger(__name__)

FORMAT_VERSION = 1
DEFAULT_PATH = Path("local/seed.jsonl")

# Column lists are explicit rather than SELECT *: a new column should force a
# deliberate choice here, not silently ride along into every backup.
COMPANY_COLUMNS = ("name", "domain", "domain_verified_at", "description", "labels")
ALIAS_COLUMNS = ("alias_norm", "alias")
TOKEN_COLUMNS = (
    "ats", "token", "status", "muted", "source",
    "checked_at", "discovered_at", "imported_at", "tags", "note", "config",
)


def company_key(name: str, domain: str | None) -> str:
    """The portable identity of a company row.

    `domain` is UNIQUE in schema.sql, so it is the key whenever present. The
    prefix on the fallback keeps the two namespaces from colliding: a company
    literally named "ford.com" cannot impersonate the domain key.
    """
    if domain:
        return domain
    return f"name:{name.strip().lower()}"


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not JSON-serializable: {type(value).__name__}")


def _dump(record: dict[str, Any]) -> str:
    # sort_keys so a re-export of unchanged data is byte-identical and diffs to
    # nothing; asyncpg hands back dict ordering that is not otherwise stable.
    return json.dumps(record, default=_json_default, sort_keys=True, ensure_ascii=False)


@dataclass
class ExportResult:
    company: int = 0
    alias: int = 0
    token: int = 0

    @property
    def total(self) -> int:
        return self.company + self.alias + self.token


@dataclass
class ImportResult:
    company_new: int = 0
    company_updated: int = 0
    alias_new: int = 0
    token_new: int = 0
    token_updated: int = 0
    skipped_unknown_company: list[str] = field(default_factory=list)


async def export_seed(pool: Pool, path: Path) -> ExportResult:
    """Write company, company_alias and token to `path` as JSONL.

    Ordering is by natural key throughout so that two exports of the same data
    produce the same bytes; without it every run would rewrite every line.
    """
    result = ExportResult()
    async with pool.acquire() as conn:
        companies = await conn.fetch(
            f"SELECT id, {', '.join(COMPANY_COLUMNS)} FROM company ORDER BY domain, name"
        )
        # id -> natural key, so alias and token rows can name their company
        # without exporting an integer that means nothing elsewhere.
        key_by_id = {r["id"]: company_key(r["name"], r["domain"]) for r in companies}
        aliases = await conn.fetch(
            f"SELECT {', '.join(ALIAS_COLUMNS)}, company_id FROM company_alias ORDER BY alias_norm"
        )
        tokens = await conn.fetch(
            f"SELECT {', '.join(TOKEN_COLUMNS)}, company_id FROM token ORDER BY ats, token"
        )

    duplicates = len(key_by_id) - len(set(key_by_id.values()))
    if duplicates:
        raise ValueError(
            f"{duplicates} company rows share a natural key; export would be ambiguous. "
            "Give the colliding rows distinct domains before exporting."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    # Written to a temporary sibling and moved into place, so an interrupted
    # export cannot leave a half-file where a good backup used to be.
    tmp = path.with_suffix(path.suffix + ".partial")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(_dump({
            "_": "manifest",
            "version": FORMAT_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "counts": {"company": len(companies), "alias": len(aliases), "token": len(tokens)},
        }) + "\n")
        for row in companies:
            fh.write(_dump({"_": "company", **{c: row[c] for c in COMPANY_COLUMNS}}) + "\n")
            result.company += 1
        for row in aliases:
            fh.write(_dump({
                "_": "alias",
                **{c: row[c] for c in ALIAS_COLUMNS},
                "company": key_by_id.get(row["company_id"]),
            }) + "\n")
            result.alias += 1
        for row in tokens:
            fh.write(_dump({
                "_": "token",
                **{c: row[c] for c in TOKEN_COLUMNS},
                "company": key_by_id.get(row["company_id"]),
            }) + "\n")
            result.token += 1
    tmp.replace(path)
    return result


def read_seed(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse a seed file into its manifest and rows, rejecting a malformed one."""
    manifest: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: not valid JSON: {exc}") from exc
            kind = record.get("_")
            if kind == "manifest":
                manifest = record
                continue
            if kind not in {"company", "alias", "token"}:
                raise ValueError(f"{path}:{lineno}: unknown record type {kind!r}")
            rows.append(record)
    if manifest is None:
        raise ValueError(f"{path}: no manifest line; not a seed file")
    if manifest.get("version") != FORMAT_VERSION:
        raise ValueError(
            f"{path}: format version {manifest.get('version')!r}, expected {FORMAT_VERSION}"
        )
    return manifest, rows


async def import_seed(
    pool: Pool, path: Path, *, replace: bool = False, dry_run: bool = False
) -> ImportResult:
    """Load a seed file, remapping company identity onto this database's ids.

    Existing rows are left alone by default. `token.status`, `muted`, `tags`
    and `note` are operator state that may have moved on since the export, and
    a restore should not quietly undo a local decision -- so the default fills
    only what a rebuild could not invent (`config`, `company_id`). `replace`
    overwrites every exported column instead, which is what a restore into a
    scratch database wants.
    """
    manifest, rows = read_seed(path)
    log.info("seed %s exported_at=%s counts=%s", path, manifest.get("exported_at"), manifest.get("counts"))
    result = ImportResult()
    companies = [r for r in rows if r["_"] == "company"]
    aliases = [r for r in rows if r["_"] == "alias"]
    tokens = [r for r in rows if r["_"] == "token"]

    seen: dict[str, str] = {}
    for row in companies:
        key = company_key(row["name"], row.get("domain"))
        if key in seen:
            raise ValueError(f"{path}: two companies share the natural key {key!r}")
        seen[key] = row["name"]

    async with pool.acquire() as conn:
        # One transaction for the whole file: a seed that fails halfway is
        # worse than one that did not load, because the tokens whose company
        # rows never arrived would be silently dropped.
        async with conn.transaction():
            id_by_key: dict[str, int] = {}
            for row in companies:
                key = company_key(row["name"], row.get("domain"))
                existing = await conn.fetchrow(
                    "SELECT id FROM company WHERE domain IS NOT NULL AND domain = $1",
                    row.get("domain"),
                ) if row.get("domain") else await conn.fetchrow(
                    "SELECT id FROM company WHERE domain IS NULL AND lower(name) = lower($1)",
                    row["name"],
                )
                if existing is None:
                    new_id = await conn.fetchval(
                        """
                        INSERT INTO company (name, domain, domain_verified_at, description, labels)
                        VALUES ($1, $2, $3, $4, $5) RETURNING id
                        """,
                        row["name"], row.get("domain"), _ts(row.get("domain_verified_at")),
                        row.get("description"), row.get("labels") or {},
                    )
                    id_by_key[key] = int(new_id)
                    result.company_new += 1
                else:
                    id_by_key[key] = int(existing["id"])
                    if replace:
                        await conn.execute(
                            """
                            UPDATE company SET name=$2, domain_verified_at=$3,
                                   description=$4, labels=$5 WHERE id=$1
                            """,
                            existing["id"], row["name"], _ts(row.get("domain_verified_at")),
                            row.get("description"), row.get("labels") or {},
                        )
                        result.company_updated += 1

            for row in aliases:
                company_id = id_by_key.get(row.get("company"))
                if company_id is None:
                    result.skipped_unknown_company.append(f"alias:{row['alias_norm']}")
                    continue
                done = await conn.execute(
                    """
                    INSERT INTO company_alias (alias_norm, alias, company_id)
                    VALUES ($1, $2, $3) ON CONFLICT (alias_norm) DO NOTHING
                    """,
                    row["alias_norm"], row["alias"], company_id,
                )
                if done.endswith(" 1"):
                    result.alias_new += 1

            for row in tokens:
                key = row.get("company")
                company_id = id_by_key.get(key) if key else None
                if key and company_id is None:
                    result.skipped_unknown_company.append(f"token:{row['ats']}:{row['token']}")
                    continue
                inserted = await conn.fetchval(
                    """
                    INSERT INTO token (ats, token, company_id, status, muted, source,
                                       checked_at, discovered_at, imported_at, tags, note, config)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (ats, token) DO UPDATE SET
                        config     = EXCLUDED.config,
                        company_id = COALESCE(EXCLUDED.company_id, token.company_id),
                        status     = CASE WHEN $13 THEN EXCLUDED.status ELSE token.status END,
                        muted      = CASE WHEN $13 THEN EXCLUDED.muted  ELSE token.muted  END,
                        tags       = CASE WHEN $13 THEN EXCLUDED.tags   ELSE token.tags   END,
                        note       = CASE WHEN $13 THEN EXCLUDED.note   ELSE token.note   END
                    RETURNING (xmax = 0) AS inserted
                    """,
                    row["ats"], row["token"], company_id, row.get("status", "unknown"),
                    bool(row.get("muted", False)), row.get("source"),
                    _ts(row.get("checked_at")), _ts(row.get("discovered_at")),
                    _ts(row.get("imported_at")), row.get("tags"), row.get("note"),
                    row.get("config") or {}, replace,
                )
                if inserted:
                    result.token_new += 1
                else:
                    result.token_updated += 1

            if dry_run:
                raise _Rollback(result)
    return result


class _Rollback(Exception):
    """Carries the tallied result out through a deliberately aborted transaction."""

    def __init__(self, result: ImportResult) -> None:
        super().__init__("dry run")
        self.result = result


def _ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _env_dsn() -> str | None:
    return os.environ.get("XJOBSCORE_DSN") or os.environ.get("DATABASE_URL")


async def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", help="Postgres DSN; default: $XJOBSCORE_DSN or $DATABASE_URL")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--export", metavar="FILE", nargs="?", const=str(DEFAULT_PATH),
                      help=f"write the seed to FILE (default: {DEFAULT_PATH})")
    # `import` is a keyword, so argparse's inferred dest needs overriding.
    mode.add_argument("--import", metavar="FILE", nargs="?", const=str(DEFAULT_PATH),
                      dest="import_path", help=f"load a seed from FILE (default: {DEFAULT_PATH})")
    parser.add_argument("--replace", action="store_true",
                        help="import: overwrite operator state on existing rows, not just config")
    parser.add_argument("--dry-run", action="store_true",
                        help="import: report what would change, then roll back")
    args = parser.parse_args(argv)

    dsn = args.dsn or _env_dsn()
    if not dsn:
        raise ValueError("no dsn: pass --dsn or set XJOBSCORE_DSN/DATABASE_URL")
    logging.basicConfig(level=os.environ.get("XJOBSCORE_LOG_LEVEL", "INFO"))

    pool = await store.connect(dsn)
    try:
        if args.export:
            path = Path(args.export)
            out = await export_seed(pool, path)
            print(f"wrote {path}: company={out.company} alias={out.alias} token={out.token} "
                  f"({out.total} rows, {path.stat().st_size / 1e6:.1f} MB)")
            print("contains API credentials in token.config -- do not commit this file")
            return 0

        path = Path(args.import_path)
        if not path.exists():
            raise ValueError(f"seed file not found: {path}")
        try:
            res = await import_seed(pool, path, replace=args.replace, dry_run=args.dry_run)
        except _Rollback as rolled:
            res = rolled.result
        print(f"company new={res.company_new} updated={res.company_updated} "
              f"alias new={res.alias_new} "
              f"token new={res.token_new} updated={res.token_updated}"
              f"{' (dry run, rolled back)' if args.dry_run else ''}")
        if res.skipped_unknown_company:
            head = ", ".join(res.skipped_unknown_company[:5])
            print(f"skipped {len(res.skipped_unknown_company)} row(s) with an unknown company: {head}")
        return 0
    finally:
        await pool.close()


def cli() -> int:
    """Sync entry point for the console script; `main` is a coroutine."""
    return asyncio.run(main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(cli())
