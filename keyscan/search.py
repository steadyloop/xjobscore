"""Search the peeked job titles. Board-level answers, not posting-level ones.

`peek_sweep` already writes every board's titles to `work/peek/titles.jsonl`.
That file is append-only and reread from the top on every question, which is
fine once and wasteful at 390k titles. This module indexes it into the same
SQLite database keyscan already owns and answers two shapes of question:

    which boards post anything matching X       -- the import decision
    which titles matched, on which board        -- the evidence for it

The index is derived state: it is rebuilt from the JSONL, never edited, and
dropping the tables costs only the seconds it takes to reload them.

No domain vocabulary lives here. A caller that knows what "software" means
passes the pattern in (`--regex`, or `--regex-file` for one too long for a
shell argument); keyscan stays a scanner and keeps importing nothing from
xjobscore. That is what lets xjobscore hand its own `job_domain` patterns
straight to this searcher without either side depending on the other.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

__all__ = ["board_matches", "index_titles", "search_titles"]

DEFAULT_TITLES = Path(__file__).with_name("work") / "peek" / "titles.jsonl"


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def index_titles(
    db_path: Path, titles_path: Path = DEFAULT_TITLES, *, rebuild: bool = False
) -> dict:
    """Load `titles.jsonl` into `peeked_title`, skipping boards already there.

    Incremental by board, because a sweep appends boards and never rewrites
    one: a board present in the table has all of its titles. `--rebuild` is
    for the other case, when the JSONL itself was replaced.
    """
    if not titles_path.exists():
        raise FileNotFoundError(f"no peeked titles at {titles_path}")
    con = _connect(db_path)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS peeked_title (
                           ats   TEXT NOT NULL,
                           key   TEXT NOT NULL,
                           title TEXT NOT NULL
                       )""")
        con.execute("""CREATE INDEX IF NOT EXISTS peeked_title_board
                           ON peeked_title(ats, key)""")
        # Titles are searched case-insensitively far more often than they are
        # read back, so the folded form is stored rather than folded per query.
        con.execute("""CREATE INDEX IF NOT EXISTS peeked_title_folded
                           ON peeked_title(lower(title))""")
        if rebuild:
            con.execute("DELETE FROM peeked_title")
        seen = {
            (row["ats"], row["key"])
            for row in con.execute("SELECT DISTINCT ats, key FROM peeked_title")
        }
        boards = titles = skipped = errors = 0
        batch: list[tuple[str, str, str]] = []
        with titles_path.open() as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                pair = (row["ats"], row["key"])
                if pair in seen:
                    skipped += 1
                    continue
                seen.add(pair)
                if row.get("error"):
                    errors += 1
                boards += 1
                for title in row.get("titles") or []:
                    batch.append((row["ats"], row["key"], title))
                    titles += 1
                if len(batch) >= 10_000:
                    con.executemany("INSERT INTO peeked_title VALUES (?,?,?)", batch)
                    batch.clear()
        if batch:
            con.executemany("INSERT INTO peeked_title VALUES (?,?,?)", batch)
        con.commit()
        return {
            "boards_indexed": boards,
            "titles_indexed": titles,
            "boards_skipped": skipped,
            "boards_with_error": errors,
        }
    finally:
        con.close()


def _compile(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


def _rows(con: sqlite3.Connection, ats: str | None):
    if ats:
        return con.execute(
            "SELECT ats, key, title FROM peeked_title WHERE ats = ?", (ats,)
        )
    return con.execute("SELECT ats, key, title FROM peeked_title")


def search_titles(
    db_path: Path, pattern: str, *, ats: str | None = None, limit: int = 100
) -> list[dict]:
    """Matching titles, newest-agnostic, capped at `limit`."""
    matcher = _compile(pattern)
    con = _connect(db_path)
    try:
        out = []
        for row in _rows(con, ats):
            if matcher.search(row["title"]):
                out.append({"ats": row["ats"], "key": row["key"], "title": row["title"]})
                if len(out) >= limit:
                    break
        return out
    finally:
        con.close()


def board_matches(
    db_path: Path,
    pattern: str,
    *,
    ats: str | None = None,
    veto: str | None = None,
    min_hits: int = 1,
) -> dict:
    """Per-board match counts -- the shape the import decision actually needs.

    `veto` drops a title the pattern caught but the caller does not want, so a
    two-stage rule (evidence minus exclusion) survives the trip as data. Boards
    are returned with their hit count so `min_hits` can be retuned without
    re-scanning.
    """
    matcher = _compile(pattern)
    blocker = _compile(veto) if veto else None
    con = _connect(db_path)
    try:
        hits: dict[tuple[str, str], int] = {}
        boards: set[tuple[str, str]] = set()
        scanned = 0
        for row in _rows(con, ats):
            scanned += 1
            pair = (row["ats"], row["key"])
            boards.add(pair)
            title = row["title"]
            if matcher.search(title) and not (blocker and blocker.search(title)):
                hits[pair] = hits.get(pair, 0) + 1
        kept = {pair: n for pair, n in hits.items() if n >= min_hits}
        return {
            "titles_scanned": scanned,
            "boards_scanned": len(boards),
            "boards_matched": len(kept),
            "titles_matched": sum(kept.values()),
            "boards": [
                {"ats": a, "key": k, "hits": n}
                for (a, k), n in sorted(kept.items(), key=lambda kv: -kv[1])
            ],
        }
    finally:
        con.close()
