"""Read-only views over keyscan's SQLite database for the KeyScan page.

keyscan (``keyscan/``) is standalone: its SQLite file is its only output, and
it imports nothing from xjobscore. This module reads that file and nothing
else -- no writes, no run control. The one write the page can make lives in
``xjobscore.keyscan_import``, which already existed as a CLI.

Every query here is synchronous ``sqlite3``, so the routes that call these
must be plain ``def`` endpoints: FastAPI then runs them in its threadpool
instead of blocking the event loop while a scan holds the file.

Reading a database a scan is writing is safe by construction -- ``initialize``
sets ``PRAGMA journal_mode = WAL`` for exactly this reason (see core.py) -- but
it does mean the file may not exist yet on a machine that has never run a
scan. ``DatabaseMissing`` is that case, and it is a 404 rather than a 500: an
inspector on a laptop with no crawl history is not broken.

Two of the page's columns live inside the ``details`` JSON blob rather than in
columns of their own -- ``url_count`` from discovery and ``job_count`` from
validation -- so they are pulled out with ``json_extract`` here and handed to
the page as ordinary fields.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from typing import Any

__all__ = [
    "DEFAULT_DB_PATH",
    "DatabaseMissing",
    "candidate_count",
    "candidates",
    "coverage",
    "database_path",
    "found",
    "run_log",
    "runs",
    "status",
    "tally",
]

# keyscan writes to ``keyscan/work/`` beside its own package; this file sits at
# xjobscore/inspector/, so the repository root is three parents up.
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "keyscan" / "work" / "keyscan.sqlite"

# A page-sized ceiling, applied to every list query. The tables are unbounded
# -- a seeding run leaves tens of thousands of rows -- and no view here is
# paginated, so the cap is what keeps a stray request cheap.
MAX_ROWS = 500


class DatabaseMissing(RuntimeError):
    """keyscan has never run on this machine, or its workdir moved."""


def database_path() -> Path:
    """Where keyscan's SQLite file lives, overridable for a split deployment."""
    override = os.environ.get("XJOBSCORE_INSPECTOR_KEYSCAN_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = database_path()
    if not path.exists():
        raise DatabaseMissing(f"no keyscan database at {path}")
    # Read-only URI: this module must not be able to write to keyscan's file
    # even by accident, and opening read-only also avoids creating an empty
    # database when the path is wrong.
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    return connection


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _connect() as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


def _limit(value: int, default: int = 100) -> int:
    try:
        return max(1, min(int(value), MAX_ROWS))
    except (TypeError, ValueError):
        return default


def status() -> dict[str, Any] | None:
    """The active run, or the most recent one if nothing is running.

    Mirrors ``Keyscan.status`` without importing it: that method calls
    ``initialize()``, which creates the schema and would write to a database
    this module opens read-only.
    """
    rows = _rows(
        """
        SELECT id, methods_json, config_json, status, phase, checkpoint_json,
               counts_json, artifact_dir, pid, error, started_at, finished_at
          FROM runs
         ORDER BY (status = 'running') DESC, started_at DESC
         LIMIT 1
        """
    )
    if not rows:
        return None
    row = rows[0]
    for key in ("methods", "config", "checkpoint", "counts"):
        row[key] = json.loads(row.pop(f"{key}_json") or "{}")
    return row


def runs(limit: int = 20, *, only_productive: bool = True) -> list[dict[str, Any]]:
    """Run history, newest first, with each run's own counts.

    ``only_productive`` drops runs that found nothing -- the 4-second failures
    where Common Crawl refused us, and cancels that never got going. They are
    noise in a history whose point is "what did each scan turn up"; the rows
    stay in the database, this only stops listing them.
    """
    rows = _rows(
        """
        SELECT id, methods_json, status, counts_json, error, started_at, finished_at
          FROM runs
         ORDER BY started_at DESC
         LIMIT ?
        """,
        (_limit(limit * 4 if only_productive else limit, 20),),
    )
    for row in rows:
        row["methods"] = json.loads(row.pop("methods_json") or "[]")
        row["counts"] = json.loads(row.pop("counts_json") or "{}")
    if only_productive:
        rows = [row for row in rows if (row["counts"].get("found") or 0) > 0]
    return rows


def run_log(run_id: str, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    """Tail one run's ``run.jsonl``.

    The log is a file beside the database, not a table, so this reads it
    directly. ``offset`` is a line count rather than a byte offset, which is
    what lets the page ask for "everything after what I already have" without
    tracking file positions.
    """
    with _connect() as connection:
        row = connection.execute(
            "SELECT artifact_dir FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    if row is None:
        return {"entries": [], "offset": offset, "missing": True}
    path = Path(row["artifact_dir"]) / "run.jsonl"
    if not path.exists():
        return {"entries": [], "offset": offset, "missing": True}

    entries: list[dict[str, Any]] = []
    index = 0
    with path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            if index <= offset:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                # A run still writing its last line is normal, not an error.
                continue
    # Only the tail is ever displayed; keeping the whole file would grow the
    # response without bound on a long crawl.
    if len(entries) > limit:
        entries = entries[-limit:]
    return {"entries": entries, "offset": index, "missing": False}


def found(limit: int = 30) -> list[dict[str, Any]]:
    """Newest discoveries, for the page's "new tokens" feed.

    Ordered by ``discovered_at`` rather than ``list_candidates``' (ats, key),
    because this view answers "what has the running scan just turned up".
    """
    return _rows(
        """
        SELECT ats, key, status, method, discovered_at, checked_at,
               json_extract(details, '$.url_count') AS url_count
          FROM tenants
         ORDER BY discovered_at DESC
         LIMIT ?
        """,
        (_limit(limit, 30),),
    )


def candidates(
    *,
    status_filter: str | None = None,
    source: str | None = None,
    query: str = "",
    imported: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """The candidates table: one row per discovered token."""
    clauses, params = ["1 = 1"], []
    if imported == "pending":
        clauses.append(f"({PENDING_SQL})")
    elif imported == "imported":
        clauses.append("imported_at IS NOT NULL")
    # Both are compared as bind parameters, never interpolated -- `source` in
    # particular arrives straight from a query string.
    if status_filter in {"live", "dead", "unknown"}:
        clauses.append("status = ?")
        params.append(status_filter)
    if source:
        clauses.append("ats = ?")
        params.append(source)
    if query.strip():
        clauses.append("(key LIKE ? OR canonical_url LIKE ?)")
        params.extend([f"%{query.strip()}%"] * 2)
    params.append(_limit(limit))
    params.append(max(0, int(offset or 0)))
    return _rows(
        f"""
        SELECT ats, key, status, method, canonical_url, discovered_at,
               checked_at, imported_at,
               json_extract(details, '$.url_count') AS url_count,
               json_extract(details, '$.job_count') AS job_count
          FROM tenants
         WHERE {" AND ".join(clauses)}
         ORDER BY discovered_at DESC
         LIMIT ? OFFSET ?
        """,
        tuple(params),
    )


def candidate_count(
    *,
    status_filter: str | None = None,
    source: str | None = None,
    query: str = "",
    imported: str | None = None,
) -> int:
    """How many rows the same filters match, so the page can show a denominator.

    Without this the table says "200 shown" and cannot say out of what, which
    reads as "that is all of them".
    """
    clauses, params = ["1 = 1"], []
    if imported == "pending":
        clauses.append(f"({PENDING_SQL})")
    elif imported == "imported":
        clauses.append("imported_at IS NOT NULL")
    if status_filter in {"live", "dead", "unknown"}:
        clauses.append("status = ?")
        params.append(status_filter)
    if source:
        clauses.append("ats = ?")
        params.append(source)
    if query.strip():
        clauses.append("(key LIKE ? OR canonical_url LIKE ?)")
        params.extend([f"%{query.strip()}%"] * 2)
    rows = _rows(
        f"SELECT count(*) AS n FROM tenants WHERE {' AND '.join(clauses)}", tuple(params)
    )
    return rows[0]["n"]


# What ``keyscan_import.load_pending`` selects. Kept identical on purpose: the
# page's "pending" count is a promise about what the import button will do, and
# a resolved-but-dead token is imported too (its row records the death).
PENDING_SQL = "status IN ('live', 'dead') AND imported_at IS NULL"


def tally() -> dict[str, int]:
    """Inventory counts for the page's one-line summary.

    ``pending`` is what ``keyscan_import`` would write on its next run, counted
    with that module's own predicate rather than an approximation of it.
    """
    rows = _rows("SELECT status, count(*) AS n FROM tenants GROUP BY status")
    counts = {row["status"]: row["n"] for row in rows}
    pending = _rows(f"SELECT count(*) AS n FROM tenants WHERE {PENDING_SQL}")
    return {
        "found": sum(counts.values()),
        "live": counts.get("live", 0),
        "dead": counts.get("dead", 0),
        "unknown": counts.get("unknown", 0),
        "imported": _rows("SELECT count(*) AS n FROM tenants WHERE imported_at IS NOT NULL")[0]["n"],
        "pending": pending[0]["n"],
    }


# A fully harvested crawl always has this many in-scope index files (300 warc
# + 300 crawldiagnostics; stable across every crawl since 2015 -- see the
# `footers` table comment in keyscan/core.py). Fewer means the harvest was
# interrupted, and cells computed from it may miss row groups.
COMPLETE_HARVEST_FILES = 600

# How many harvested crawls the matrix renders. Each one costs a read of its
# ~1,800 `footers` rows; the cap keeps the endpoint bounded the way MAX_ROWS
# bounds the list views.
COVERAGE_CRAWLS = 40


def _coverage_vendors() -> list[Any]:
    """Tier-1 vendors, in the group's own fixed order.

    Imported lazily from ``keyscan.rules`` (no duckdb there -- the same
    reasoning as ``keyscan_control``). Tier 1 only: 36 columns drowned the
    page, and tier 1 is where scans actually happen today. The order is a
    column's identity; it must never depend on the data.
    """
    from keyscan.rules import BY_SOURCE, SOURCE_GROUPS

    names = SOURCE_GROUPS.get("tier1", ())
    return [BY_SOURCE[name] for name in names if BY_SOURCE[name].domains]


def coverage() -> dict[str, Any]:
    """The scan-coverage matrix: crawls x vendors, one cell per pair.

    Every cell is decided locally from the `footers` table -- the same range
    comparison the scan itself prunes with -- so the endpoint never touches
    Common Crawl. States:

    - ``scanned``: the pair is in `scanned`; re-scanning it cannot yield more.
    - ``pending``: row groups overlap the vendor's key ranges, with the exact
      download the scan would cost. This is the actionable cell.
    - ``none``: no overlap anywhere in the crawl -- nothing to do, proven.
    """
    from keyscan.rules import surt_range

    vendors = _coverage_vendors()
    ranges = {
        rule.source: [surt_range(domain) for domain in rule.domains]
        for rule in vendors
    }
    done = {
        (row["ats"], row["crawl"]): row["scanned_at"]
        for row in _rows("SELECT ats, crawl, scanned_at FROM scanned")
    }
    crawls = _rows(
        """
        SELECT crawl, count(DISTINCT part) AS files
          FROM footers GROUP BY crawl ORDER BY crawl DESC LIMIT ?
        """,
        (COVERAGE_CRAWLS,),
    )
    matrix = []
    for entry in crawls:
        crawl = entry["crawl"]
        groups = _rows(
            "SELECT part, lo, hi, bytes FROM footers WHERE crawl = ?", (crawl,)
        )
        cells: dict[str, dict[str, Any]] = {}
        for rule in vendors:
            at = done.get((rule.source, crawl))
            if at is not None:
                cells[rule.source] = {"state": "scanned", "at": at}
                continue
            hits = [
                row for row in groups
                if any(
                    not (row["hi"] <= lo or row["lo"] >= hi)
                    for lo, hi in ranges[rule.source]
                )
            ]
            if hits:
                cells[rule.source] = {
                    "state": "pending",
                    "row_groups": len(hits),
                    "files": len({row["part"] for row in hits}),
                    "bytes": sum(row["bytes"] for row in hits),
                }
            else:
                cells[rule.source] = {"state": "none"}
        matrix.append({
            "crawl": crawl,
            "files": entry["files"],
            "partial": entry["files"] < COMPLETE_HARVEST_FILES,
            "cells": cells,
        })
    return {"vendors": [rule.source for rule in vendors], "crawls": matrix}
