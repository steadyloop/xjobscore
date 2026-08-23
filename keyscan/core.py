"""The complete standalone keyscan workflow.

It deliberately owns its own SQLite file and artifacts. Nothing in this
module writes the application database or source registry.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import html
from itertools import zip_longest
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import urlencode, urlsplit
import uuid
import xml.etree.ElementTree as ET

import duckdb
import httpx

from xjobscore.net import DEFAULT_USER_AGENT

from .rules import BY_SOURCE, RULES, SAMPLES, STOP, TOKEN_RE, VALIDATED_SOURCES, Rule, expand_sources, extract, surt_range, token_sql


COLLINFO = "https://index.commoncrawl.org/collinfo.json"
CC_CDN = "https://data.commoncrawl.org/"
CC_PATHS = CC_CDN + "crawl-data/{crawl}/cc-index-table.paths.gz"
# Imported, not declared: net.py argues the string must exist exactly once or
# callers drift into presenting themselves differently, and that applies here
# most of all -- keyscan probes the very same vendor hosts the crawl does, so a
# separate identity makes any per-vendor ceiling measured by one untransferable
# to the other. It also replaces a token that named this project and announced
# "token discovery" as its purpose, which is a single WAF rule away from taking
# out every board under a vendor at once.
USER_AGENT = DEFAULT_USER_AGENT
# A 403 body naming one of these is a bot wall refusing *us*, not a tenant
# that happens to be private. Kept in step with `verify._WAF_MARKERS`.
WAF_MARKERS = ("cloudflare", "cf-ray", "access denied", "attention required", "request blocked", "akamai")
# DuckDB exposes no bandwidth limit, and even `threads=1` -- its floor -- pulls
# the index far faster than a shared home connection should sustain.  The only
# remaining lever is duty-cycling: scan a few files, measure what actually
# crossed the link, then sleep long enough to hold this average.
TARGET_MBPS = 60.0
# DuckDB 1.5 expresses ``http_timeout`` in seconds.  This used to be set to
# 120000 as though the value were milliseconds, which turns a two-minute
# ceiling into 33 hours.  Keep the retry window bounded as well: DuckDB's
# default backoff factor of four makes eight retries sleep for roughly nine
# minutes after a fast DNS failure, without refreshing the connection that
# owns the failed resolver state.
HTTP_TIMEOUT_SECONDS = 120
HTTP_RETRIES = 3
HTTP_RETRY_BACKOFF = 2
HTTP_RETRY_WAIT_MS = 250
BATCH_ATTEMPTS = 3
# Smaller batches pace more smoothly but pay more query overhead; eight files is
# a few seconds of transfer, so the sleep between them stays short enough that
# a cancel lands promptly.
BATCH_FILES = 8
# A crawl always ships exactly 600 in-scope files (300 warc + 300
# crawldiagnostics; verified stable across 5 crawls spanning 2024-03..2026-07),
# so this doesn't need to be adaptive the way BATCH_FILES's pacing does --
# every ~100 files is close enough to "6 checkpoints per crawl" regardless of
# vendor count. Not a multiple of BATCH_FILES on purpose: forcing one would
# couple two independent knobs for no benefit.
CHECKPOINT_FILES = 100
# Public GitHub-hosted job boards.  They publish no tokens of their own; each
# listing row carries a per-posting ATS deep link that `extract` reduces to a
# token.  `archive` marks the maintainers' frozen history, worth one seeding
# pass but pointless to re-read on a recurring run.
BOARD_FEEDS: tuple[dict[str, Any], ...] = (
    {"repo": "vanshb03/Summer2027-Internships", "path": ".github/scripts/listings.json"},
    {"repo": "vanshb03/New-Grad-2027", "path": ".github/scripts/listings.json"},
    {"repo": "vanshb03/Summer2027-Internships", "path": "archived/2026/archived.json", "archive": True},
    {"repo": "vanshb03/Summer2027-Internships", "path": "archived/2025/archived.json", "archive": True},
)
# Both boards default to `dev`; neither has a `main`.
BOARD_RAW = "https://raw.githubusercontent.com/{repo}/dev/{path}"
# "validate" discovers nothing.  It exists so a backlog of already-discovered
# tokens can be drained on its own, without waiting behind a fresh crawl.
METHODS = frozenset({"commoncrawl", "board", "validate"})
# Probes run concurrently with discovery.  `_request` holds a global semaphore
# of four, and discovery issues one request at a time, so three validators
# saturate the budget without ever starving the crawl of its slot.
VALIDATION_WORKERS = 3
LIST_KEYS = ("jobs", "offers", "data", "content", "jobPostings", "results", "result", "positions", "items")
TOTAL_KEYS = ("total", "totalFound", "totalCount", "count")
NAME_KEYS = ("name", "companyName", "organizationName")
GENERIC_NAMES = frozenset({"apply", "careers", "career", "jobs", "job board", "open positions", "positions"})
COMEET_API_TOKEN = re.compile(
    r"(?:[?&]token=|[\"']token[\"']\s*[:=]\s*[\"'])([A-Za-z0-9]{16,})",
    re.I,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


# keyscan.probe returns live/absent/inconclusive; token.status is constrained
# to exactly these three (CHECK status IN ('unknown','live','dead')).
_STATUS = {"live": "live", "absent": "dead", "inconclusive": "unknown"}


def _merge(old: dict[str, Any], new: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Combine ``token.details`` dicts across sightings.

    A fresh value wins unless it is ``None``, in which case the previously
    recorded value (if any) survives -- e.g. a probe that finds no company
    name must not erase one discovery already found.  Explicit overrides
    (``verdict``, ``job_count``) always win, including a fresh ``None``.
    """
    merged = dict(old)
    for key, value in (new or {}).items():
        if value is not None or key not in merged:
            merged[key] = value
    merged.update(overrides)
    return merged


def _canonical_token(source: str, token: str) -> str:
    """Return keyscan's one stored spelling for a vendor endpoint."""
    return token.lower() if source == "workday-list" else token


_CRAWL_WEEK = re.compile(r"^CC-MAIN-(\d{4})-(\d{2})$")


def _iso_date(value: str, option: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise KeyscanError(f"{option} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise KeyscanError(f"{option} must be YYYY-MM-DD")
    return parsed


def _date_range(date_from: str | None, date_to: str | None) -> tuple[date, date] | None:
    if not date_from and not date_to:
        return None
    if not date_from or not date_to:
        raise KeyscanError("commoncrawl requires both --from and --to")
    start, end = _iso_date(date_from, "--from"), _iso_date(date_to, "--to")
    if start > end:
        raise KeyscanError("--from must not be later than --to")
    return start, end


def _select_crawls(collections: list[dict[str, Any]], config: dict[str, Any]) -> list[str]:
    """Select snapshots overlapping an inclusive calendar-date range.

    The collection API publishes each snapshot's exact ``from``/``to`` crawl
    timestamps; use those rather than treating the week-like ID as its actual
    coverage. The ID-derived week remains a fallback for fixtures or older API
    responses. Legacy saved runs retain their old newest-N behavior so they
    remain resumable.
    """
    selected_range = _date_range(config.get("date_from"), config.get("date_to"))
    if selected_range is None:
        return [row["id"] for row in collections[: int(config.get("crawls", 6))]]
    start, end = selected_range
    result: list[str] = []
    for row in collections:
        crawl = str(row.get("id", ""))
        try:
            crawl_start = datetime.fromisoformat(str(row["from"])).date()
            crawl_end = datetime.fromisoformat(str(row["to"])).date()
        except (KeyError, TypeError, ValueError):
            match = _CRAWL_WEEK.fullmatch(crawl)
            if match is None:
                continue
            try:
                crawl_start = date.fromisocalendar(int(match[1]), int(match[2]), 1)
            except ValueError:
                continue
            crawl_end = crawl_start + timedelta(days=6)
        if crawl_start <= end and crawl_end >= start:
            result.append(crawl)
    return result


def _migrate_workday_tokens(con: sqlite3.Connection) -> None:
    """Fold the retired Workday source into its listing-only replacement.

    Historical databases can contain both labels and casing variants of the
    same tenant endpoint.  Rebuild just those rows as one lowercase
    ``workday-list`` identity, keeping the latest validation result and merging
    discovery evidence.  ``imported_at`` follows the winning validation row so
    a newer, not-yet-imported verdict remains pending.
    """
    dirty = con.execute(
        """SELECT 1 FROM token
           WHERE ats = 'workday'
              OR (ats = 'workday-list' AND token <> lower(token))
           LIMIT 1"""
    ).fetchone()
    if dirty is None:
        return

    rows = con.execute(
        "SELECT * FROM token WHERE ats IN ('workday', 'workday-list')"
    ).fetchall()
    groups: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault(row["token"].lower(), []).append(row)

    con.execute("DELETE FROM token WHERE ats IN ('workday', 'workday-list')")
    rule = BY_SOURCE["workday-list"]
    for token, sightings in groups.items():
        # ISO timestamps sort chronologically. A checked row outranks an
        # unchecked discovery; exact ties prefer the current source label.
        winner = max(
            sightings,
            key=lambda row: (
                row["checked_at"] is not None,
                row["checked_at"] or "",
                row["ats"] == "workday-list",
            ),
        )
        ordered = sorted(
            sightings,
            key=lambda row: (row["discovered_at"], row["ats"], row["token"]),
        )
        urls: list[str] = []
        details: dict[str, Any] = {}
        for row in ordered:
            urls.extend(json.loads(row["urls"]))
            details = _merge(details, json.loads(row["details"]))
        # Validation-specific detail belongs with the same row as status and
        # checked_at, so replay it last after combining discovery evidence.
        details = _merge(details, json.loads(winner["details"]))
        canonical_url = rule.board_url.format(**rule.urls(token))
        con.execute(
            """INSERT INTO token(
                   ats, token, status, canonical_url, urls, method, run_id,
                   details, discovered_at, checked_at, imported_at
               ) VALUES ('workday-list', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                token,
                winner["status"],
                canonical_url,
                _json(list(dict.fromkeys(urls))[:5]),
                next((row["method"] for row in reversed(ordered) if row["method"]), None),
                winner["run_id"],
                _json(details),
                min(row["discovered_at"] for row in sightings),
                winner["checked_at"],
                winner["imported_at"],
            ),
        )



def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in ("urls", "details"):
        if key in result:
            result[key] = json.loads(result[key])
    return result


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _merged_query(rules: list[Rule], urls: list[str]) -> str:
    """One pass over the index that classifies every vendor in `rules`.

    Reading a crawl's 600 parquet files costs ~180MB of footer/metadata traffic
    before a single matching row is returned, and that cost is identical for
    every vendor.  Scanning per vendor pays it once per vendor; this pays it
    once per crawl.  The extraction itself is unchanged -- the same
    `surt_range`, `where_sql` and `token_sql` fragments the per-vendor query
    used -- so `selftest` still covers it.
    """
    # Both CASEs must branch in the same order so `source` and `token` stay
    # aligned.  A row dispatched to the wrong vendor is self-correcting: that
    # vendor's regex will not match, `token` becomes '', and the outer WHERE
    # drops it.
    sources = " ".join(
        f"WHEN ({rule.where_sql}) THEN {_sql_str(rule.source)}" for rule in rules
    )
    tokens = " ".join(
        f"WHEN ({rule.where_sql}) THEN {token_sql(rule)}" for rule in rules
    )
    # Kept as a top-level disjunction rather than folded into each branch:
    # DuckDB's zone maps prune row groups from this, which is the entire reason
    # a ~600GB index costs a few hundred MB to scan.
    ranges = " OR ".join(
        f"(url_surtkey >= '{low}' AND url_surtkey < '{high}')"
        for rule in rules for low, high in map(surt_range, rule.domains)
    )
    hosts = " OR ".join(f"({rule.where_sql})" for rule in rules)
    stop = ", ".join(_sql_str(value) for value in sorted(STOP))
    files = "[" + ", ".join(_sql_str(url) for url in urls) + "]"
    return f"""
        SELECT source, token, count(*) AS url_count, min(url, 5) AS urls
        FROM (
            SELECT CASE {sources} END AS source,
                   CASE {tokens} END AS token,
                   url
            FROM read_parquet({files})
            WHERE ({ranges}) AND ({hosts})
        )
        WHERE source IS NOT NULL AND token <> ''
          AND lower(split_part(token, '/', 1)) NOT IN ({stop})
          AND regexp_matches(token, '{TOKEN_RE.pattern}')
        GROUP BY source, token
    """


def _duckdb_http() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    for setting in (
        "SET enable_http_metadata_cache=true",
        f"SET http_retries={HTTP_RETRIES}",
        f"SET http_retry_backoff={HTTP_RETRY_BACKOFF}",
        f"SET http_retry_wait_ms={HTTP_RETRY_WAIT_MS}",
        f"SET http_timeout={HTTP_TIMEOUT_SECONDS}",
        "SET preserve_insertion_order=false",
    ):
        try:
            con.execute(setting)
        except duckdb.Error:
            pass
    return con


def _batch_connection(
    rows: list[tuple[str, str, int, list[str]]] | None = None,
) -> duckdb.DuckDBPyConnection:
    """Open the remote reader and restore already-completed batch results.

    A failed httpfs request can leave its curl connection or resolver state
    unusable for every built-in retry. Reopening DuckDB gives the next outer
    attempt a genuinely fresh HTTP client. Completed batches live in Python
    until the per-vendor parquet caches are committed, so rebuilding the small
    local accumulator does not re-read any successful remote batch.
    """
    con = _duckdb_http()
    try:
        con.execute(
            "CREATE TEMP TABLE _batches"
            "(source VARCHAR, token VARCHAR, url_count BIGINT, urls VARCHAR[])"
        )
        if rows:
            con.executemany("INSERT INTO _batches VALUES (?, ?, ?, ?)", rows)
    except Exception:
        con.close()
        raise
    return con


def _read_cache(target: Path) -> list[dict[str, Any]]:
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT token, url_count, urls FROM read_parquet({_sql_str(str(target))})"
        ).fetchall()
    finally:
        con.close()
    return [{"token": row[0], "url_count": int(row[1]), "urls": list(row[2])} for row in rows]


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), max(1, size)):
        yield items[start:start + max(1, size)]


def _link_bytes() -> int | None:
    """Bytes received on the machine's physical interfaces, or None.

    Counted at the link rather than inside DuckDB because DuckDB reports no
    transfer statistics, and because the number we actually care about is what
    the household's connection is carrying -- if something else is already
    using it, the scan should yield rather than add to it.
    """
    try:
        procfs = Path("/proc/net/dev")
        if procfs.exists():
            total = 0
            for line in procfs.read_text().splitlines()[2:]:
                name, _, counters = line.partition(":")
                if name.strip().startswith(("lo", "docker", "veth", "br-")):
                    continue
                total += int(counters.split()[0])
            return total or None
        # macOS prints one row per address, so the same interface repeats; take
        # each interface's first row only.
        out = subprocess.run(
            ["netstat", "-ib"], capture_output=True, text=True, timeout=5,
        ).stdout
        total, seen = 0, set()
        for line in out.splitlines()[1:]:
            fields = line.split()
            if len(fields) > 6 and fields[0] not in seen and not fields[0].startswith("lo"):
                seen.add(fields[0])
                try:
                    total += int(fields[6])
                except ValueError:
                    continue
        return total or None
    except Exception:
        return None


def _sleep(seconds: float, should_cancel: Callable[[], None] | None) -> None:
    """Sleep in slices so a cancellation is noticed mid-pause, not after it."""
    deadline = time.monotonic() + seconds
    while True:
        if should_cancel is not None:
            should_cancel()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.5, remaining))


def _pace_seconds(sent: int | None, elapsed: float, target_bps: float) -> float:
    """How long to wait after a batch to hold `target_bps` on average.

    `sent` is None when the link counters could not be read; a fixed 50% duty
    cycle then keeps the scan bounded rather than unlimited, which is the safe
    direction to fail in.
    """
    if target_bps <= 0:
        return 0.0
    if sent is None:
        return elapsed
    if sent <= 0:
        return 0.0
    return max(0.0, sent * 8 / target_bps - elapsed)


def cc_scan_sources(
    crawl: str, rules: list[Rule], urls: list[str], target_for: Callable[[str], Path],
    *, target_mbps: float = TARGET_MBPS, batch_files: int = BATCH_FILES,
    checkpoint_files: int = CHECKPOINT_FILES,
    batch_attempts: int = BATCH_ATTEMPTS,
    should_cancel: Callable[[], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    on_candidates: Callable[[list[tuple[str, str, int, list[str]]]], None] | None = None,
    on_retry: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Scan every vendor in `rules` in ONE pass over the crawl's columnar index.

    The merged result is split back out to one cache file per vendor, so
    caching, `cc_done` checkpointing and `keyscan verify` keep the per-vendor
    granularity they had when each vendor got its own scan.
    """
    # A vendor already cached for this crawl is left out of the query entirely,
    # so re-runs and mixed `--sources` selections cost nothing.
    pending = [rule for rule in rules if rule.domains and not target_for(rule.source).exists()]
    if pending:
        for rule in pending:
            target_for(rule.source).parent.mkdir(parents=True, exist_ok=True)
        batches = list(_chunks(urls, batch_files))
        # Reuse one connection while it is healthy: `enable_http_metadata_cache`
        # is per-connection. A remote I/O failure is the exception; recovery
        # rebuilds the local accumulator on a fresh connection below.
        completed_rows: list[tuple[str, str, int, list[str]]] = []
        con = _batch_connection()
        try:
            # A summary every `checkpoint_files` files is the only visibility
            # into a merged scan from the outside: it runs as one blocking
            # DuckDB call handed to a worker thread, so nothing else surfaces
            # until the whole thing returns -- which, at 60 Mbps over 600
            # files, can be well over an hour of silence otherwise.
            files_done = 0
            checkpoint_at = checkpoint_files
            checkpoint_started = time.monotonic()
            checkpoint_vendors: set[str] = set()
            checkpoint_rows: list[tuple[str, str, int, list[str]]] = []
            for index, batch in enumerate(batches):
                if should_cancel is not None:
                    should_cancel()
                before, started = _link_bytes(), time.monotonic()
                attempts = max(1, batch_attempts)
                for attempt in range(1, attempts + 1):
                    try:
                        rows = con.execute(
                            f"INSERT INTO _batches {_merged_query(pending, batch)} "
                            "RETURNING source, token, url_count, urls"
                        ).fetchall()
                        break
                    except (duckdb.ConnectionException, duckdb.HTTPException, duckdb.IOException) as exc:
                        if attempt == attempts:
                            raise
                        if should_cancel is not None:
                            should_cancel()
                        if on_retry is not None:
                            on_retry({
                                "files": files_done,
                                "batch_files": len(batch),
                                "attempt": attempt + 1,
                                "max_attempts": attempts,
                                "error": f"{type(exc).__name__}: {exc}",
                            })
                        con.close()
                        con = _batch_connection(completed_rows)
                completed_rows.extend(rows)
                elapsed = time.monotonic() - started
                after = _link_bytes()
                sent = None if before is None or after is None else max(0, after - before)
                if on_progress is not None:
                    on_progress(index + 1, len(batches))
                files_done += len(batch)
                checkpoint_rows.extend(rows)
                checkpoint_vendors.update(row[0] for row in rows)
                if files_done >= checkpoint_at:
                    # Candidates go out before the checkpoint log, not after:
                    # this is what lets validation start on this window's
                    # tokens while the scan is still on file 100 of 600,
                    # rather than waiting for all six checkpoints to pass.
                    if on_candidates is not None and checkpoint_rows:
                        on_candidates(checkpoint_rows)
                    if on_checkpoint is not None:
                        on_checkpoint({
                            "files": files_done, "total_files": len(urls),
                            "vendors": len(checkpoint_vendors), "candidates": len(checkpoint_rows),
                            "took_ms": round(1000 * (time.monotonic() - checkpoint_started)),
                        })
                    checkpoint_at += checkpoint_files
                    checkpoint_started = time.monotonic()
                    checkpoint_vendors = set()
                    checkpoint_rows = []
                _sleep(_pace_seconds(sent, elapsed, target_mbps * 1_000_000), should_cancel)
            # Each batch reported its own count and its own five smallest URLs;
            # summing and re-taking the smallest five reproduces exactly what a
            # single unbatched pass would have written.
            con.execute(
                """CREATE TEMP TABLE _final AS
                   SELECT source, token, sum(url_count)::BIGINT AS url_count,
                          list_sort(flatten(list(urls)))[1:5] AS urls
                   FROM _batches GROUP BY source, token"""
            )
            for rule in pending:
                target = target_for(rule.source)
                temp = target.with_suffix(".part")
                temp.unlink(missing_ok=True)
                con.execute(
                    f"""COPY (SELECT token, url_count, urls FROM _final
                              WHERE source = {_sql_str(rule.source)})
                        TO {_sql_str(str(temp))} (FORMAT PARQUET)"""
                )
                temp.replace(target)
        finally:
            con.close()
    results: dict[str, list[dict[str, Any]]] = {}
    for rule in rules:
        target = target_for(rule.source)
        results[rule.source] = _read_cache(target) if target.exists() else []
    return results


def cc_scan(
    crawl: str, rule: Rule, urls: list[str], target: Path,
    *, target_mbps: float = TARGET_MBPS, batch_files: int = BATCH_FILES,
    should_cancel: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """Single-vendor wrapper over :func:`cc_scan_sources`, used by
    :mod:`keyscan.verify` so it queries the index exactly as the crawl does --
    and is paced by the same default, so no caller can reach Common Crawl
    unthrottled without asking for it.
    """
    return cc_scan_sources(
        crawl, [rule], urls, lambda _source: target,
        target_mbps=target_mbps, batch_files=batch_files, should_cancel=should_cancel,
    ).get(rule.source, [])


class KeyscanError(RuntimeError):
    pass


class ActiveRunError(KeyscanError):
    pass


class Keyscan:
    """Durable token discovery runner with no dependency on cc_ats."""

    def __init__(
        self,
        workdir: Path | str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        request_delay: float = 0.0,
        target_mbps: float = TARGET_MBPS,
        checkpoint_files: int = CHECKPOINT_FILES,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.workdir = Path(workdir) if workdir else Path(__file__).with_name("work")
        self.db_path = self.workdir / "keyscan.sqlite"
        self.transport = transport
        self.request_delay = request_delay
        # Ceiling on the crawl's average transfer rate; 0 disables pacing.
        self.target_mbps = target_mbps
        # How many files a merged scan processes before logging progress and
        # recording whatever it has found so far. Constructor-configurable,
        # not just a module constant, so a test can force a checkpoint inside
        # a handful of synthetic files instead of needing 100+ real ones.
        self.checkpoint_files = checkpoint_files
        # Optional human-facing observer for durable log events. The CLI uses
        # this to show a deliberately small progress stream on stderr while
        # leaving stdout available for the final machine-readable result.
        self.progress = progress
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(4)
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._controls: dict[str, str | None] = {}
        self._run_id: str | None = None
        self._phase_started: tuple[str, float] | None = None
        self._cancelled = False
        self._initialized = False
        # Set only while a run streams validation; `_record_candidate` uses its
        # presence to decide whether a newly seen token is probed immediately.
        # Keyed by (ats, token), the token table's primary key.
        self._validation_queue: asyncio.Queue[tuple[str, str]] | None = None
        self._validating: set[tuple[str, str]] = set()
        self._validated_count = 0
        # Set once a vendor refuses us; stops validation for the rest of
        # the run. Discovery is unaffected -- Common Crawl is not the
        # party doing the refusing.
        self._blocked: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Storage and public inspection
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        if self._initialized:
            return
        self.workdir.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            # WAL lets a SQL client read the file while a scan writes to it,
            # which matters because the database is keyscan's only output.
            con.execute("PRAGMA journal_mode = WAL")
            con.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    methods_json TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('running','interrupted','cancelled','completed','failed')),
                    phase TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    counts_json TEXT NOT NULL DEFAULT '{}',
                    artifact_dir TEXT NOT NULL,
                    pid INTEGER,
                    error TEXT,
                    -- Set the first time a vendor refuses us (429/Retry-After,
                    -- or a 403 carrying a WAF fingerprint). Validation stops
                    -- for the rest of the run; this records who, when and why
                    -- so a later run is not left guessing why tokens are still
                    -- `unknown`.
                    blocked_json TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS keyscan_one_active
                    ON runs(status) WHERE status = 'running';
                CREATE TABLE IF NOT EXISTS token (
                    ats           TEXT NOT NULL CHECK (ats <> 'workday'),
                    token         TEXT NOT NULL,
                    status        TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (status IN ('unknown','live','dead')),
                    canonical_url TEXT NOT NULL,
                    urls          TEXT NOT NULL DEFAULT '[]',
                    method        TEXT,
                    run_id        TEXT REFERENCES runs(id) ON DELETE SET NULL,
                    details       TEXT NOT NULL DEFAULT '{}',
                    discovered_at TEXT NOT NULL,
                    checked_at    TEXT,
                    -- Set by an external bulk-import (xjobscore/keyscan_import.py)
                    -- once this row has been written to xjobscore's own token
                    -- table. NULL means never imported.
                    imported_at   TEXT,
                    PRIMARY KEY (ats, token)
                );
                CREATE INDEX IF NOT EXISTS keyscan_token_review
                    ON token(status, ats);
                CREATE INDEX IF NOT EXISTS keyscan_token_run
                    ON token(run_id);
                CREATE UNIQUE INDEX IF NOT EXISTS keyscan_token_canonical
                    ON token(ats, canonical_url);
                """
            )
            # CREATE TABLE IF NOT EXISTS does not add columns to a table that
            # already exists on disk -- upgrade older databases in place.
            existing_columns = {row["name"] for row in con.execute("PRAGMA table_info(token)")}
            if "imported_at" not in existing_columns:
                con.execute("ALTER TABLE token ADD COLUMN imported_at TEXT")
            _migrate_workday_tokens(con)
            # Existing SQLite tables cannot gain a CHECK constraint in place.
            # Triggers give upgraded databases the same invariant as fresh
            # databases after the legacy rows above have been consolidated.
            con.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS keyscan_token_workday_insert
                BEFORE INSERT ON token WHEN NEW.ats = 'workday'
                BEGIN
                    SELECT RAISE(ABORT, 'workday source was renamed to workday-list');
                END;
                CREATE TRIGGER IF NOT EXISTS keyscan_token_workday_update
                BEFORE UPDATE OF ats ON token WHEN NEW.ats = 'workday'
                BEGIN
                    SELECT RAISE(ABORT, 'workday source was renamed to workday-list');
                END;
                """
            )
            run_columns = {row["name"] for row in con.execute("PRAGMA table_info(runs)")}
            if "blocked_json" not in run_columns:
                con.execute("ALTER TABLE runs ADD COLUMN blocked_json TEXT")
            # A process crash cannot retain a usable in-memory task.  Do not
            # interrupt a currently running CLI merely because another
            # process is inspecting its status.
            for row in con.execute("SELECT id, pid FROM runs WHERE status = 'running'"):
                if not _pid_alive(row["pid"]):
                    con.execute(
                        "UPDATE runs SET status = 'interrupted', phase = 'interrupted', finished_at = ? WHERE id = ?",
                        (utc_now(), row["id"]),
                    )
        self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def status(self, run_id: str | None = None) -> dict[str, Any] | None:
        self.initialize()
        sql = "SELECT * FROM runs" + (" WHERE id = ?" if run_id else " ORDER BY created_at DESC LIMIT 1")
        with self._connect() as con:
            row = con.execute(sql, (run_id,) if run_id else ()).fetchone()
        result = _row(row)
        if result is None:
            return None
        for key in ("methods_json", "config_json", "checkpoint_json", "counts_json"):
            result[key[:-5]] = json.loads(result.pop(key))
        blocked = result.pop("blocked_json", None)
        result["blocked"] = json.loads(blocked) if blocked else None
        return result

    def list_candidates(
        self, *, status: str | None = None,
        source: str | None = None, query: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.initialize()
        clauses, params = ["1 = 1"], []
        for column, value in (("status", status), ("ats", source)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        if query.strip():
            clauses.append(
                "(token LIKE ? OR canonical_url LIKE ? OR json_extract(details, '$.company_name') LIKE ?)"
            )
            params.extend([f"%{query.strip()}%"] * 3)
        params.append(max(1, min(limit, 1000)))
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM token WHERE " + " AND ".join(clauses) + " ORDER BY ats, token LIMIT ?",
                params,
            ).fetchall()
        return [_row(row) for row in rows if row is not None]

    async def robots(self, sources: Iterable[str] | None = None) -> list[dict[str, str]]:
        """Report whether shared ATS hosts explicitly block Common Crawl."""
        selected = expand_sources(tuple(dict.fromkeys(sources or ())))
        unknown = set(selected) - BY_SOURCE.keys()
        if unknown:
            raise KeyscanError(f"unknown source(s): {', '.join(sorted(unknown))}")
        rules = [rule for rule in RULES if not selected or rule.source in selected]
        async with httpx.AsyncClient(
            timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT},
            transport=self.transport,
        ) as client:
            return list(await asyncio.gather(*(self._robots_check(client, rule) for rule in rules)))

    @staticmethod
    async def _robots_check(client: httpx.AsyncClient, rule: Rule) -> dict[str, str]:
        host = _shared_board_host(rule)
        if host is None:
            return {"source": rule.source, "host": "(per-tenant host)", "status": "unknown"}
        try:
            response = await client.get(f"https://{host}/robots.txt")
        except httpx.HTTPError as exc:
            return {
                "source": rule.source, "host": host,
                "status": f"unreachable:{type(exc).__name__}",
            }
        if response.status_code != 200:
            return {"source": rule.source, "host": host, "status": f"missing:{response.status_code}"}
        status = "blocked" if _blocks_ccbot(response.text) else "allowed"
        return {"source": rule.source, "host": host, "status": status}

    # ------------------------------------------------------------------
    # Runs, checkpoints and logs
    # ------------------------------------------------------------------

    def _new_run(
        self, methods: Iterable[str], crawls: int | None = None,
        sources: Iterable[str] | None = None, archives: bool = False, *,
        date_from: str | None = None, date_to: str | None = None,
    ) -> str:
        selected = tuple(dict.fromkeys(methods))
        unknown = set(selected) - METHODS
        if not selected or unknown:
            detail = ", ".join(sorted(unknown)) if unknown else "at least one method"
            raise KeyscanError(f"unknown method(s): {detail}")
        selected_range = _date_range(date_from, date_to)
        if selected_range is not None and crawls is not None:
            raise KeyscanError("use a date range or crawls, not both")
        if "commoncrawl" in selected and selected_range is None and crawls is None:
            raise KeyscanError("commoncrawl requires both --from and --to")
        if crawls is not None and not 1 <= crawls <= 12:
            raise KeyscanError("crawls must be between 1 and 12")
        selected_sources = expand_sources(tuple(dict.fromkeys(sources or ())))
        unknown_sources = set(selected_sources) - BY_SOURCE.keys()
        if unknown_sources:
            raise KeyscanError(f"unknown source(s): {', '.join(sorted(unknown_sources))}")
        self.initialize()
        run_id = uuid.uuid4().hex
        artifact = self.workdir / "runs" / run_id
        artifact.mkdir(parents=True)
        checkpoint = {"board_index": 0, "cc_done": []}
        config: dict[str, Any] = {
            "sources": selected_sources,
            "archives": bool(archives),
        }
        if selected_range is not None:
            config.update(date_from=date_from, date_to=date_to)
        elif crawls is not None:
            config["crawls"] = crawls
        now = utc_now()
        try:
            with self._connect() as con:
                con.execute(
                    """INSERT INTO runs(id, methods_json, config_json, status, phase, checkpoint_json,
                       artifact_dir, pid, created_at, started_at)
                       VALUES (?, ?, ?, 'running', 'starting', ?, ?, ?, ?, ?)""",
                    (run_id, _json(selected), _json(config), _json(checkpoint), str(artifact), os.getpid(), now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ActiveRunError("a keyscan run is already active") from exc
        self._run_id = run_id
        self._log(
            "info", "starting", "run started", methods=selected, crawls=crawls,
            date_from=date_from, date_to=date_to, sources=selected_sources or "all",
            archives=bool(archives),
        )
        return run_id

    async def run(
        self, methods: Iterable[str], crawls: int | None = None,
        sources: Iterable[str] | None = None, archives: bool = False, *,
        date_from: str | None = None, date_to: str | None = None,
    ) -> str:
        return await self._run(self._new_run(
            methods, crawls, sources, archives, date_from=date_from, date_to=date_to,
        ))

    @staticmethod
    def _selected_sources(run: dict[str, Any]) -> set[str] | None:
        selected = run["config"].get("sources", ())
        return set(selected) if selected else None

    async def resume(self, run_id: str) -> str:
        self.initialize()
        with self._connect() as con:
            run = con.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run is None:
                raise KeyscanError(f"run {run_id} does not exist")
            if run["status"] not in {"interrupted", "cancelled", "failed"}:
                raise KeyscanError(f"run {run_id} cannot be resumed from {run['status']}")
            try:
                con.execute("UPDATE runs SET status = 'running', phase = 'resuming', pid = ?, error = NULL, finished_at = NULL WHERE id = ?", (os.getpid(), run_id))
            except sqlite3.IntegrityError as exc:
                raise ActiveRunError("a keyscan run is already active") from exc
        self._run_id = run_id
        self._log("info", "resuming", "run resumed")
        return await self._run(run_id)

    def cancel(self, run_id: str) -> None:
        if self._run_id == run_id:
            self._cancelled = True
            return
        self.initialize()
        with self._connect() as con:
            row = con.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyscanError(f"run {run_id} does not exist")
            if row["status"] == "running":
                con.execute("UPDATE runs SET status = 'cancelled', phase = 'cancelled', finished_at = ? WHERE id = ?", (utc_now(), run_id))

    async def _run(self, run_id: str) -> str:
        self._cancelled = False
        self._run_id = run_id
        run = self.status(run_id)
        assert run is not None
        try:
            async with httpx.AsyncClient(
                timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}, transport=self.transport
            ) as client:
                self._client = client
                queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
                self._validation_queue = queue
                self._validating.clear()
                self._controls.clear()
                self._validated_count = 0
                self._blocked = None
                workers = [
                    asyncio.create_task(self._validation_worker(run))
                    for _ in range(VALIDATION_WORKERS)
                ]
                try:
                    for method in run["methods"]:
                        self._check_cancelled()
                        if method == "commoncrawl":
                            await self._commoncrawl(run)
                        elif method == "board":
                            await self._boards(run)
                        # "validate" discovers nothing; the sweep below is it.
                    await queue.join()
                    self._check_cancelled()
                    # Streaming leaves nothing for the sweep in a clean run; it
                    # still drains a pre-existing backlog and anything a probe
                    # dropped, through the same concurrent workers.
                    await self._validate(run)
                    await queue.join()
                finally:
                    # Probes must not outlive the client they borrow, so the
                    # workers are torn down before anything else unwinds.
                    self._validation_queue = None
                    for worker in workers:
                        worker.cancel()
                    await asyncio.gather(*workers, return_exceptions=True)
                self._check_cancelled()
            self._finish("completed")
        except asyncio.CancelledError:
            self._finish("interrupted")
            raise
        except KeyboardInterrupt:
            self._finish("interrupted")
            raise
        except _Cancelled:
            self._finish("cancelled")
        except Exception as exc:
            self._log("error", "failed", "run failed", error=f"{type(exc).__name__}: {exc}")
            self._finish("failed", f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self._client = None
            self._run_id = None
        return run_id

    def _finish(self, status: str, error: str | None = None) -> None:
        if not self._run_id:
            return
        self._close_phase_timing()
        counts = self._counts(self._run_id)
        with self._connect() as con:
            con.execute(
                "UPDATE runs SET status = ?, phase = ?, counts_json = ?, error = ?, finished_at = ? WHERE id = ?",
                (status, status, _json(counts), error, utc_now(), self._run_id),
            )
        self._log("info" if status == "completed" else "warning", status, f"run {status}", **counts)

    def _run_row(self, run_id: str) -> dict[str, Any]:
        run = self.status(run_id)
        if run is None:
            raise KeyscanError(f"run {run_id} does not exist")
        return run

    def _checkpoint(self, **changes: Any) -> None:
        assert self._run_id
        run = self._run_row(self._run_id)
        checkpoint = run["checkpoint"]
        checkpoint.update(changes)
        with self._connect() as con:
            con.execute("UPDATE runs SET checkpoint_json = ? WHERE id = ?", (_json(checkpoint), self._run_id))

    def _phase(self, phase: str) -> None:
        assert self._run_id
        now = time.monotonic()
        self._close_phase_timing(now)
        with self._connect() as con:
            con.execute("UPDATE runs SET phase = ? WHERE id = ?", (phase, self._run_id))
        self._phase_started = (phase, now)
        self._log("info", phase, f"phase {phase}")

    def _close_phase_timing(self, finished: float | None = None) -> None:
        """Append a completed-phase measurement without persisting new state."""
        if self._phase_started is None:
            return
        phase, started = self._phase_started
        self._phase_started = None
        elapsed_ms = round(1000 * ((finished or time.monotonic()) - started))
        self._log("info", phase, "phase timing", event="phase_timing", elapsed_ms=elapsed_ms)

    def _artifact(self) -> Path:
        assert self._run_id
        run = self._run_row(self._run_id)
        return Path(run["artifact_dir"])

    def _log(self, level: str, phase: str, message: str, **details: Any) -> None:
        if not self._run_id:
            return
        artifact = self._artifact()
        artifact.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": utc_now(), "run_id": self._run_id, "level": level,
            "phase": phase, "message": message, **details,
        }
        with (artifact / "run.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(_json(entry) + "\n")
        if self.progress is not None:
            try:
                self.progress(entry)
            except Exception:
                # Display is ancillary. A closed stderr pipe or custom
                # renderer must never stop a durable scan.
                pass

    def tail_log(self, run_id: str, offset: int = 0, limit: int = 200) -> tuple[list[dict[str, Any]], int]:
        run = self.status(run_id)
        if run is None:
            raise KeyscanError(f"run {run_id} does not exist")
        path = Path(run["artifact_dir"]) / "run.jsonl"
        if not path.exists():
            return [], 0
        entries: list[dict[str, Any]] = []
        with path.open("rb") as handle:
            handle.seek(max(0, offset))
            for _ in range(max(1, min(limit, 1000))):
                line = handle.readline()
                if not line:
                    break
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return entries, handle.tell()

    def _counts(self, run_id: str) -> dict[str, int]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT status, count(*) AS n FROM token WHERE run_id = ? GROUP BY status",
                (run_id,)
            ).fetchall()
        counts = {row["status"]: row["n"] for row in rows}
        return {"found": sum(counts.values()), **counts}

    def _check_cancelled(self) -> None:
        if self._cancelled:
            raise _Cancelled()
        if self._run_id:
            with self._connect() as con:
                row = con.execute("SELECT status FROM runs WHERE id = ?", (self._run_id,)).fetchone()
            if row is None or row["status"] != "running":
                raise _Cancelled()

    # ------------------------------------------------------------------
    # HTTP and candidate persistence
    # ------------------------------------------------------------------

    async def _request(
        self, method: str, url: str, *, json_body: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response | None:
        assert self._client is not None
        host = (urlsplit(url).hostname or "").lower()
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        started = time.monotonic()
        retry_causes: list[str] = []
        throttle_delay_ms = 0
        retry_delay_ms = 0
        response: httpx.Response | None = None
        outcome = "transport_error"
        async with self._semaphore, lock:
            queue_ms = round(1000 * (time.monotonic() - started))
            last = self._host_last.get(host, 0.0)
            wait = self.request_delay - (time.monotonic() - last)
            if wait > 0:
                throttle_delay_ms = round(1000 * wait)
                self._log(
                    "info", "network", "request delay", event="delay", cause="configured_host_delay",
                    host=host, delay_ms=throttle_delay_ms,
                )
                await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()
            for attempt in range(3):
                try:
                    response = await self._client.request(method, url, json=json_body, headers=headers)
                except asyncio.CancelledError:
                    self._log(
                        "warning", "network", "request timing", event="request_timing", method=method,
                        host=host, attempts=attempt + 1, outcome="cancelled", queue_ms=queue_ms,
                        throttle_delay_ms=throttle_delay_ms, retry_delay_ms=retry_delay_ms,
                        elapsed_ms=round(1000 * (time.monotonic() - started)),
                    )
                    raise
                except httpx.TimeoutException:
                    response = None
                    outcome = "timeout"
                    retry_causes.append("timeout")
                except httpx.HTTPError:
                    response = None
                    outcome = "transport_error"
                    retry_causes.append("transport_error")
                else:
                    if self._note_refusal(host, url, response):
                        # Retrying a refusal only deepens it, and every further
                        # probe this run is now pointless.
                        outcome = "blocked"
                        break
                    if response.status_code not in {429, 500, 502, 503, 504}:
                        outcome = "ok" if response.status_code < 400 else "http_error"
                        break
                    outcome = "http_error"
                    retry_causes.append(f"http_{response.status_code}")
                if attempt < 2:
                    backoff = 0 if self.transport else 2**attempt
                    retry_delay_ms += round(1000 * backoff)
                    await asyncio.sleep(backoff)
        details: dict[str, Any] = {
            "event": "request_timing", "method": method, "host": host, "attempts": attempt + 1,
            "outcome": outcome, "queue_ms": queue_ms, "throttle_delay_ms": throttle_delay_ms,
            "retry_delay_ms": retry_delay_ms, "elapsed_ms": round(1000 * (time.monotonic() - started)),
        }
        if response is not None:
            details["status_code"] = response.status_code
        if retry_causes:
            details["retry_causes"] = retry_causes
        self._log("info" if outcome == "ok" else "warning", "network", "request timing", **details)
        return response

    def _note_refusal(self, host: str, url: str, response: httpx.Response) -> bool:
        """Record the first refusal of the run and return True if this is one.

        A 429 (or any `Retry-After`) is unambiguous. A bare 403 is not: on
        vendors that give every tenant its own host, a single company's WAF
        says nothing about the vendor, so only a 403 that names a bot wall
        counts -- the same distinction `keyscan verify` draws.
        """
        status = response.status_code
        retry_after = response.headers.get("retry-after")
        if status == 429 or retry_after:
            reason = "rate_limited"
        elif status == 403 and any(
            marker in response.text[:4000].lower() for marker in WAF_MARKERS
        ):
            reason = "blocked"
        else:
            return False
        if self._blocked is None:
            self._blocked = {
                "reason": reason, "host": host, "url": url,
                "status_code": status, "retry_after": retry_after, "at": utc_now(),
            }
            self._log(
                "warning", "validate", "vendor refused us; validation stops for this run",
                event="blocked", **self._blocked,
            )
            self._persist_blocked()
        return True

    def _persist_blocked(self) -> None:
        if not self._run_id:
            return
        with self._connect() as con:
            con.execute(
                "UPDATE runs SET blocked_json = ? WHERE id = ?",
                (_json(self._blocked), self._run_id),
            )

    def _record_candidate(
        self, source: str, token: str, method: str, urls: list[str], *,
        details: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        assert self._run_id
        token = _canonical_token(source, token)
        rule = BY_SOURCE[source]
        canonical = rule.board_url.format(**rule.urls(token))
        now = utc_now()
        details = details or {}
        with self._connect() as con:
            # Comeet candidates are promoted after probing from the public
            # locator (slug/board-id) to adapter input (board-id/api-token).
            # Their canonical public board URL remains stable across that
            # promotion, so it is the durable rediscovery key.
            existing = (
                con.execute(
                    "SELECT token, urls, details FROM token WHERE ats = ? AND canonical_url = ?",
                    (source, canonical),
                ).fetchone()
                if source == "comeet" else
                con.execute(
                    "SELECT token, urls, details FROM token WHERE ats = ? AND token = ?",
                    (source, token),
                ).fetchone()
            )
            if existing is None:
                con.execute(
                    """INSERT INTO token(ats, token, canonical_url, urls, method, run_id, details, discovered_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (source, token, canonical, _json(urls[:5]), method, self._run_id, _json(details), now),
                )
                key_token = token
            else:
                key_token = existing["token"]
                merged_urls = list(dict.fromkeys([*json.loads(existing["urls"]), *urls]))[:5]
                merged_details = _merge(json.loads(existing["details"]), details)
                con.execute(
                    """UPDATE token
                       SET urls = ?, method = COALESCE(method, ?), run_id = ?, details = ?
                       WHERE ats = ? AND token = ?""",
                    (_json(merged_urls), method, self._run_id, _json(merged_details), source, key_token),
                )
        key = (source, key_token)
        if self._validation_queue is not None:
            self._validation_queue.put_nowait(key)
        return key

    def _set_validation(
        self, key: tuple[str, str], validation: str, job_count: int | None, details: dict[str, Any],
    ) -> None:
        assert self._run_id
        ats, token = key
        now = utc_now()
        adapter_token = details.pop("_comeet_adapter_token", None)
        with self._connect() as con:
            if adapter_token:
                con.execute("UPDATE token SET token = ? WHERE ats = ? AND token = ?", (adapter_token, ats, token))
                token = adapter_token
            existing = con.execute("SELECT details FROM token WHERE ats = ? AND token = ?", (ats, token)).fetchone()
            assert existing is not None
            merged = _merge(json.loads(existing["details"]), details, verdict=validation, job_count=job_count)
            con.execute(
                """UPDATE token SET status = ?, checked_at = ?, run_id = ?, details = ?
                   WHERE ats = ? AND token = ?""",
                (_STATUS[validation], now, self._run_id, _json(merged), ats, token),
            )

    # ------------------------------------------------------------------
    # Secondary GitHub-hosted job boards
    # ------------------------------------------------------------------

    async def _boards(self, run: dict[str, Any]) -> None:
        self._phase("board")
        selected_sources = self._selected_sources(run)
        archives = bool(run["config"].get("archives"))
        feeds = [feed for feed in BOARD_FEEDS if archives or not feed.get("archive")]
        start = int(run["checkpoint"].get("board_index", 0))
        self._log("info", "board", "scanning secondary job boards", total=len(feeds), start=start, archives=archives)
        for index, feed in enumerate(feeds[start:], start):
            self._check_cancelled()
            url = BOARD_RAW.format(**feed)
            response = await self._request("GET", url)
            if response is not None and response.status_code < 400:
                self._discover_listings(response, url, feed["repo"], selected_sources)
            self._checkpoint(board_index=index + 1)

    def _discover_listings(
        self, response: httpx.Response, feed_url: str, repo: str,
        selected_sources: set[str] | None = None,
    ) -> None:
        try:
            rows = response.json()
        except ValueError:
            self._log("warning", "board", "listings payload was not valid JSON", url=feed_url)
            return
        if not isinstance(rows, list):
            self._log("warning", "board", "listings payload was not a list", url=feed_url)
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            found = extract(row.get("url") or "")
            if not found or (selected_sources is not None and found[0] not in selected_sources):
                continue
            url = str(row.get("url") or "")
            self._record_candidate(
                *found, f"board:{feed_url}", [url] if url else [],
                details={
                    "repo": repo, "title": row.get("title"), "active": row.get("active"),
                    "company_name": row.get("company_name") or None,
                },
            )

    # ------------------------------------------------------------------
    # Common Crawl URL index
    # ------------------------------------------------------------------

    async def _commoncrawl(self, run: dict[str, Any]) -> None:
        self._phase("commoncrawl")
        response = await self._request("GET", COLLINFO)
        if response is None or response.status_code != 200:
            raise KeyscanError("unable to retrieve Common Crawl collection list")
        crawls = _select_crawls(response.json(), run["config"])
        if not crawls:
            if run["config"].get("date_from"):
                raise KeyscanError(
                    "no Common Crawl snapshots overlap "
                    f"{run['config']['date_from']} through {run['config']['date_to']}"
                )
            raise KeyscanError("no Common Crawl snapshots available")
        done = set(run["checkpoint"].get("cc_done", []))
        selected_sources = self._selected_sources(run)
        rules = [rule for rule in RULES if selected_sources is None or rule.source in selected_sources]
        failed_crawls: list[str] = []
        for crawl_index, crawl in enumerate(crawls, start=1):
            self._check_cancelled()
            pending: list[Rule] = []
            for rule in rules:
                marker = f"{crawl}/{rule.source}"
                if marker in done:
                    continue
                if not rule.domains:
                    self._log("info", "commoncrawl", "vendor has no bounded crawl domain", crawl=crawl, source=rule.source)
                    done.add(marker)
                    self._checkpoint(cc_done=sorted(done))
                    continue
                pending.append(rule)
            if not pending:
                continue
            manifest = await self._request("GET", CC_PATHS.format(crawl=crawl))
            if manifest is None or manifest.status_code != 200:
                self._log("warning", "commoncrawl", "crawl manifest unavailable", crawl=crawl)
                failed_crawls.append(crawl)
                continue
            try:
                paths = gzip.decompress(manifest.content).decode().splitlines()
            except (OSError, UnicodeDecodeError) as exc:
                self._log("warning", "commoncrawl", "invalid crawl manifest", crawl=crawl, error=str(exc))
                failed_crawls.append(crawl)
                continue
            urls = [CC_CDN + path for path in paths if path.endswith(".parquet") and ("/subset=warc/" in path or "/subset=crawldiagnostics/" in path)]
            self._log(
                "info", "commoncrawl", "crawl started", event="crawl_start",
                crawl=crawl, snapshot=crawl_index, snapshots=len(crawls),
                files=len(urls), vendors=len(pending),
            )
            # One scan for every outstanding vendor.  Reading the crawl's
            # parquet footers costs the same whether one vendor or nine are
            # being extracted, so paying it per vendor was pure waste.
            started = time.monotonic()
            try:
                loop = asyncio.get_running_loop()
                records = await asyncio.to_thread(self._cc_scan_sources, crawl, pending, urls, loop)
            except _Cancelled:
                # `_Cancelled` is an Exception, so without this it would be
                # swallowed below and the run would roll on to the next crawl.
                raise
            except Exception as exc:
                sources = [rule.source for rule in pending]
                self._log("warning", "commoncrawl", "crawl scan failed", crawl=crawl, sources=sources, error=str(exc))
                self._log(
                    "warning", "commoncrawl", "Common Crawl merged timing", event="operation_timing",
                    operation="commoncrawl_merged_scan", crawl=crawl, sources=sources,
                    outcome="error", elapsed_ms=round(1000 * (time.monotonic() - started)),
                )
                failed_crawls.append(crawl)
                continue
            elapsed_ms = round(1000 * (time.monotonic() - started))
            self._log(
                "info", "commoncrawl", "Common Crawl merged timing", event="operation_timing",
                operation="commoncrawl_merged_scan", crawl=crawl,
                sources=[rule.source for rule in pending], outcome="ok",
                candidates=sum(len(rows) for rows in records.values()), elapsed_ms=elapsed_ms,
                files=len(urls), batches=-(-len(urls) // BATCH_FILES),
                target_mbps=self.target_mbps,
            )
            for rule in pending:
                self._check_cancelled()
                rows = records.get(rule.source, [])
                for record in rows:
                    self._record_candidate(rule.source, record["token"], "cc", record["urls"], details={"crawl": crawl, "url_count": record["url_count"]})
                done.add(f"{crawl}/{rule.source}")
                self._checkpoint(cc_done=sorted(done))
                self._log("info", "commoncrawl", "vendor scanned", crawl=crawl, source=rule.source, candidates=len(rows))
        if failed_crawls:
            raise KeyscanError(
                f"{len(failed_crawls)} Common Crawl snapshot(s) failed: "
                + ", ".join(failed_crawls)
            )

    def _cc_scan_sources(
        self, crawl: str, rules: list[Rule], urls: list[str], loop: asyncio.AbstractEventLoop,
    ) -> dict[str, list[dict[str, Any]]]:
        artifact = self._artifact() / "commoncrawl" / crawl
        return cc_scan_sources(
            crawl, rules, urls, lambda source: artifact / f"{source}.parquet",
            target_mbps=self.target_mbps, checkpoint_files=self.checkpoint_files,
            # Runs in a worker thread, so this opens its own SQLite connection
            # each call; that is what makes a mid-scan `keyscan cancel` land.
            should_cancel=self._check_cancelled,
            # Same worker thread as should_cancel above; `_log` only appends to
            # a file, no shared connection to open per call.
            on_checkpoint=lambda stats: self._log(
                "info", "commoncrawl", "scan checkpoint", event="scan_checkpoint", crawl=crawl, **stats,
            ),
            on_retry=lambda stats: self._log(
                "warning", "commoncrawl", "retrying crawl batch with fresh connection",
                event="batch_retry", crawl=crawl, **stats,
            ),
            # `_record_candidate` ends by pushing onto `self._validation_queue`,
            # an `asyncio.Queue` -- calling that directly from this worker
            # thread is undefined behaviour, asyncio.Queue has no thread-safe
            # path. `call_soon_threadsafe` hands the whole call back to the
            # loop instead. This is what lets validation start on a vendor's
            # tokens at the first checkpoint that finds any, rather than
            # waiting for all 600 files -- the merge otherwise made every
            # candidate wait for the entire scan, silently losing the overlap
            # `_validation_worker` was built for.
            on_candidates=lambda rows: loop.call_soon_threadsafe(
                self._record_checkpoint_candidates, crawl, rows,
            ),
        )

    def _record_checkpoint_candidates(
        self, crawl: str, rows: list[tuple[str, str, int, list[str]]],
    ) -> None:
        """Runs on the event loop via `call_soon_threadsafe` -- see the comment
        at its call site in `_cc_scan_sources`. A token seen again in a later
        checkpoint, or in the final post-scan pass, just re-upserts through the
        same `_record_candidate` path every other discovery method uses; that
        merge is already idempotent, so recording early costs nothing beyond
        letting the same token's evidence arrive in more than one call.
        """
        for source, token, url_count, urls in rows:
            self._record_candidate(source, token, "cc", urls, details={"crawl": crawl, "url_count": url_count})

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    async def _validation_worker(self, run: dict[str, Any]) -> None:
        """Probe discovered tokens as they arrive, alongside the crawl.

        Vendor hosts are serialised by `_request`'s per-host lock, so probing
        only at the end of a run leaves those hosts idle for its whole
        duration and then queues every probe behind one lock.  Overlapping the
        two fills time already being paid for, and publishes each token the
        moment it verifies rather than after the last crawl finishes.
        """
        assert self._validation_queue is not None
        selected_sources = self._selected_sources(run)
        while True:
            key = await self._validation_queue.get()
            try:
                # A cancelled run still drains, so `queue.join()` cannot hang.
                if self._cancelled or self._blocked or key in self._validating:
                    continue
                candidate = self._pending_validation(key, selected_sources)
                if candidate is None:
                    continue
                self._validating.add(key)
                try:
                    validation, job_count, details = await self._probe(candidate)
                    if self._blocked:
                        # Refused, not answered. Recording it would stamp
                        # `checked_at` and leave the token frozen at `unknown`
                        # -- streaming validation only ever picks up rows where
                        # that column is still NULL, so the next run would skip
                        # a token nobody has actually checked.
                        continue
                    self._set_validation(key, validation, job_count, details)
                finally:
                    self._validating.discard(key)
                self._validated_count += 1
                if self._validated_count % 250 == 0:
                    self._log(
                        "info", "validate", "validation progress",
                        validated=self._validated_count, queued=self._validation_queue.qsize(),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One unprobeable token must not abort the crawl; the final
                # sweep retries it because it is still unvalidated.
                self._log(
                    "warning", "validate", "candidate probe failed",
                    ats=key[0], token=key[1], error=f"{type(exc).__name__}: {exc}",
                )
            finally:
                self._validation_queue.task_done()

    def _pending_validation(
        self, key: tuple[str, str], selected_sources: set[str] | None,
    ) -> dict[str, Any] | None:
        ats, token = key
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM token WHERE ats = ? AND token = ? AND checked_at IS NULL",
                (ats, token),
            ).fetchone()
        candidate = _row(row)
        if candidate is None:
            return None
        if selected_sources is not None and candidate["ats"] not in selected_sources:
            return None
        if candidate["ats"] not in VALIDATED_SOURCES:
            return None
        return candidate

    async def _validate(self, run: dict[str, Any]) -> None:
        """Sweep whatever streaming validation did not already cover.

        Unlike streaming discovery (which probes a token only once, the
        moment it is first seen), an explicit `--methods validate` sweep
        reprobes unconditionally -- every selected token, regardless of
        `checked_at` -- so a backlog drain or a deliberate re-check always
        does something.  The one exception is a token this same run already
        probed a moment ago via streaming validation: re-probing it here
        would just waste a request on an answer already fresh.
        """
        self._phase("validate")
        with self._connect() as con:
            candidates = con.execute(
                """SELECT ats, token FROM token
                   WHERE run_id IS NOT ? OR checked_at IS NULL
                   ORDER BY ats, token""",
                (self._run_id,),
            ).fetchall()
        selected_sources = self._selected_sources(run)
        # Discovery records a vendor's tokens consecutively, so probing in
        # table order would park every worker on one host behind `_request`'s
        # per-host lock.  Round-robin across sources keeps them on different
        # hosts and lets the pool actually run concurrently.
        by_source: dict[str, list[tuple[str, str]]] = {}
        for row in candidates:
            if row["ats"] not in VALIDATED_SOURCES:
                continue
            if selected_sources is None or row["ats"] in selected_sources:
                by_source.setdefault(row["ats"], []).append((row["ats"], row["token"]))
        pending = [
            key
            for group in zip_longest(*by_source.values())
            for key in group
            if key is not None
        ]
        self._log(
            "info", "validate", "validating candidates",
            total=len(pending), sources=len(by_source),
        )
        assert self._validation_queue is not None
        # Reuse the streaming workers so a large backlog drains concurrently
        # instead of queueing behind one per-host lock.
        for key in pending:
            self._check_cancelled()
            if self._blocked:
                self._log(
                    "warning", "validate", "validation stopped; vendor refused us",
                    event="blocked_skip", remaining=len(pending) - pending.index(key),
                    **self._blocked,
                )
                return
            self._validation_queue.put_nowait(key)

    async def _probe(self, candidate: dict[str, Any]) -> tuple[str, int | None, dict[str, Any]]:
        # Delegates to the shared engine in ``keyscan.probe`` so the crawl and
        # one-off validators run identical per-vendor rules. Imported lazily to
        # break the import cycle (probe imports pure helpers from this module).
        from .probe import probe_rule
        rule = BY_SOURCE[candidate["ats"]]
        return await probe_rule(
            rule,
            candidate["token"],
            request=self._request,
            controls=self._controls,
            canonical_url=candidate.get("canonical_url", ""),
        )


class _Cancelled(Exception):
    pass


def _job_count(payload: Any) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return None
    for key in TOTAL_KEYS:
        if isinstance(payload.get(key), int):
            return payload[key]
    for key in LIST_KEYS:
        if isinstance(payload.get(key), list):
            return len(payload[key])
    meta = payload.get("meta")
    if isinstance(meta, dict):
        for key in (*TOTAL_KEYS, "totalNumber"):
            if isinstance(meta.get(key), int):
                return meta[key]
    data = payload.get("data")
    if isinstance(data, dict):
        return _job_count(data)
    return None


def _count_at_path(payload: Any, path: tuple[str | int, ...]) -> int | None:
    value = payload
    for part in path:
        if isinstance(part, int) and isinstance(value, list) and 0 <= part < len(value):
            value = value[part]
        elif isinstance(part, str) and isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return len(value)
    return None


def _company_name(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in NAME_KEYS:
        value = payload.get(key)
        if isinstance(value, str):
            name = _clean_company_name(value)
            if name:
                return name
    company = payload.get("company")
    if isinstance(company, dict):
        name = _company_name(company)
        if name:
            return name
    for key in LIST_KEYS:
        values = payload.get(key)
        if isinstance(values, list) and values and isinstance(values[0], dict):
            company = values[0].get("company")
            if isinstance(company, dict):
                name = _company_name(company)
                if name:
                    return name
    return None


def _clean_company_name(value: str) -> str | None:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n|\u2013\u2014-")
    if not 2 < len(value) <= 200 or value.lower() in GENERIC_NAMES:
        return None
    return value


def _html_company_name(text: str) -> str | None:
    """Return only high-confidence names from a validated board page."""
    meta_patterns = (
        r'<meta\s+[^>]*property=["\']og:site_name["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta\s+[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:site_name["\']',
    )
    for pattern in meta_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            name = _clean_company_name(match.group(1))
            if name:
                return name

    for tag in ("title", "h1"):
        match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", text, re.I | re.S)
        if not match:
            continue
        value = html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
        at_match = re.match(r"\s*(?:careers?|jobs?|open positions)\s+at\s+(.+?)\s*$", value, re.I)
        if at_match:
            name = _clean_company_name(at_match.group(1))
            if name:
                return name
        suffix_match = re.match(
            r"\s*(.+?)\s*(?:[|\u2013\u2014-]\s*)?(?:careers?|jobs?|open positions|job openings)\s*$",
            value,
            re.I,
        )
        if suffix_match:
            name = _clean_company_name(suffix_match.group(1))
            if name:
                return name
    return None


def _xml_job_count(text: str) -> int | None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    tags = [element.tag.rsplit("}", 1)[-1].lower() for element in root.iter()]
    if not any(tag in {"rss", "feed", "jobs", "positions", "openings", "workzag-jobs"} for tag in tags):
        return None
    return sum(tag in {"entry", "item", "job", "position", "opening"} for tag in tags)


def _html_fingerprint(text: str, token: str) -> str:
    normalized = text.replace(token.lower(), "{token}")
    normalized = re.sub(r"\b[0-9a-f]{8,}\b", "{id}", normalized)
    normalized = re.sub(r"\d+", "{n}", normalized)
    normalized = re.sub(r"\s+", " ", normalized)[:8000]
    return hashlib.sha256(normalized.encode()).hexdigest()


def _shared_board_host(rule: Rule) -> str | None:
    fields = {"token": "keyscan-dynamic-token"}
    fields.update({f"t{index}": f"keyscan-dynamic-part-{index}" for index in range(8)})
    host = (urlsplit(rule.board_url.format(**fields)).hostname or "").lower()
    if not host or "keyscan-dynamic-" in host:
        return None
    return host


def _blocks_ccbot(text: str) -> bool:
    groups: dict[str, list[str]] = {}
    agents: list[str] = []
    directives_started = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip().lower() for part in line.split(":", 1))
        if key == "user-agent":
            if directives_started:
                agents = []
                directives_started = False
            agents.append(value)
            groups.setdefault(value, [])
        elif key == "disallow" and agents:
            directives_started = True
            for agent in agents:
                groups.setdefault(agent, []).append(value)
    directives = groups.get("ccbot", groups.get("*", []))
    return "/" in directives


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def selftest() -> list[str]:
    """Return all fixture failures; an empty result means Python and SQL agree."""
    failures: list[str] = []
    con = duckdb.connect()
    try:
        for url, expected in SAMPLES:
            actual = extract(url)
            if actual != expected:
                failures.append(f"python {url}: expected {expected}, got {actual}")
                continue
            if expected is None:
                continue
            rule = BY_SOURCE[expected[0]]
            sql = "SELECT " + token_sql(rule) + " FROM (VALUES (?)) AS input(url)"
            found = con.execute(sql, [url]).fetchone()[0] or None
            if found != expected[1]:
                failures.append(f"duckdb {url}: expected {expected[1]}, got {found}")
    finally:
        con.close()
    return failures
