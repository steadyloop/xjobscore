"""The complete standalone tokscan workflow.

It deliberately owns its own SQLite file and artifacts.  Nothing in this
module writes the application database or source catalog.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterable
from urllib.parse import urlencode, urljoin, urlsplit
import uuid
import xml.etree.ElementTree as ET

import duckdb
import httpx

from .rules import BY_SOURCE, RULES, SAMPLES, STOP, TOKEN_RE, Rule, extract, surt_range, token_sql


COLLINFO = "https://index.commoncrawl.org/collinfo.json"
CC_CDN = "https://data.commoncrawl.org/"
CC_PATHS = CC_CDN + "crawl-data/{crawl}/cc-index-table.paths.gz"
WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
USER_AGENT = "xjobscore-tokscan/1.0 (+local token discovery)"
METHODS = frozenset({"company", "commoncrawl", "wayback"})
LIST_KEYS = ("jobs", "offers", "data", "content", "jobPostings", "results", "result", "positions", "items")
TOTAL_KEYS = ("total", "totalFound", "totalCount", "count")
ATTR_URL = re.compile(r"(?:href|src|action|content)\s*=\s*['\"]([^'\"]+)['\"]", re.I)
ABS_URL = re.compile(r"https?://[^\s'\"<>\\]+", re.I)
CAREERS = re.compile(r"(?:career|jobs?|vacanc|opening|position|hiring)", re.I)
COMEET_API_TOKEN = re.compile(
    r"(?:[?&]token=|[\"']token[\"']\s*[:=]\s*[\"'])([A-Za-z0-9]{16,})",
    re.I,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _toml_string(value: str) -> str:
    """A TOML basic string has the same escaping rules needed here as JSON."""
    return json.dumps(value, ensure_ascii=False)


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class TokscanError(RuntimeError):
    pass


class ActiveRunError(TokscanError):
    pass


class Tokscan:
    """Durable token discovery runner with no dependency on cc_ats."""

    def __init__(
        self,
        workdir: Path | str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        request_delay: float = 0.0,
    ) -> None:
        self.workdir = Path(workdir) if workdir else Path(__file__).with_name("work")
        self.db_path = self.workdir / "tokscan.sqlite"
        self.transport = transport
        self.request_delay = request_delay
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(4)
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._controls: dict[str, str | None] = {}
        self._run_id: str | None = None
        self._phase_started: tuple[str, float] | None = None
        self._cancelled = False
        self._initialized = False

    # ------------------------------------------------------------------
    # Storage and public inspection
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        if self._initialized:
            return
        self.workdir.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
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
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS tokscan_one_active
                    ON runs(status) WHERE status = 'running';
                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    token TEXT NOT NULL,
                    company_id TEXT,
                    company_name TEXT,
                    canonical_url TEXT NOT NULL,
                    validation TEXT NOT NULL DEFAULT 'unvalidated'
                        CHECK (validation IN ('unvalidated','live','absent','inconclusive')),
                    decision TEXT NOT NULL DEFAULT 'pending'
                        CHECK (decision IN ('pending','accepted','rejected')),
                    job_count INTEGER,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_validated_at TEXT,
                    UNIQUE(source, token)
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
                    method TEXT NOT NULL CHECK (method IN ('company','commoncrawl','wayback','probe')),
                    evidence_url TEXT NOT NULL,
                    referrer_url TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    observed_at TEXT NOT NULL,
                    UNIQUE(run_id, candidate_id, method, evidence_url, referrer_url)
                );
                CREATE INDEX IF NOT EXISTS tokscan_candidates_review
                    ON candidates(validation, decision, source, id);
                CREATE INDEX IF NOT EXISTS tokscan_evidence_candidate
                    ON evidence(candidate_id, observed_at DESC);
                """
            )
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
        return result

    def list_candidates(
        self, *, validation: str | None = None, decision: str | None = None,
        source: str | None = None, query: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.initialize()
        clauses, params = ["1 = 1"], []
        for column, value in (("validation", validation), ("decision", decision), ("source", source)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        if query.strip():
            clauses.append("(token LIKE ? OR company_name LIKE ? OR canonical_url LIKE ?)")
            params.extend([f"%{query.strip()}%"] * 3)
        params.append(max(1, min(limit, 1000)))
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM candidates WHERE " + " AND ".join(clauses) + " ORDER BY source, token LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def decide(self, candidate_id: int, decision: str) -> dict[str, Any]:
        if decision not in {"accepted", "rejected"}:
            raise TokscanError("decision must be accepted or rejected")
        self.initialize()
        with self._connect() as con:
            row = con.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
            if row is None:
                raise TokscanError(f"candidate {candidate_id} does not exist")
            if row["decision"] != "pending":
                raise TokscanError(f"candidate {candidate_id} is already {row['decision']}")
            if decision == "accepted" and row["validation"] != "live":
                raise TokscanError("only live candidates may be accepted")
            con.execute("UPDATE candidates SET decision = ? WHERE id = ?", (decision, candidate_id))
            changed = con.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        assert changed is not None
        return dict(changed)

    def export(self, path: Path | str, *, accepted_only: bool = True) -> int:
        self.initialize()
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        where = "WHERE decision = 'accepted'" if accepted_only else ""
        with self._connect() as con, destination.open("w", encoding="utf-8") as handle:
            rows = con.execute(f"SELECT * FROM candidates {where} ORDER BY source, token").fetchall()
            for row in rows:
                candidate = dict(row)
                evidence = [
                    dict(item) for item in con.execute(
                        "SELECT method, evidence_url, referrer_url, details_json, observed_at FROM evidence WHERE candidate_id = ? ORDER BY id",
                        (candidate["id"],),
                    )
                ]
                candidate["adapter_available"] = self._adapter_available(candidate["source"])
                candidate["evidence"] = [
                    {**item, "details": json.loads(item.pop("details_json"))} for item in evidence
                ]
                handle.write(_json(candidate) + "\n")
        return len(rows)

    def export_catalog(self, directory: Path | str | None = None, *, accepted_only: bool = False) -> dict[str, int]:
        """Write a tokscan-local mirror of the application catalog TOMLs.

        SQLite remains authoritative. This is a human-readable handoff: one
        TOML per ATS containing live candidates. Unknown company domains stay
        explicitly marked ``unknown`` rather than being guessed.
        """
        self.initialize()
        destination = Path(directory) if directory else self.workdir.parent / "catalogs"
        destination.mkdir(parents=True, exist_ok=True)
        where = "WHERE validation = 'live'"
        if accepted_only:
            where += " AND decision = 'accepted'"
        with self._connect() as con:
            rows = [dict(row) for row in con.execute(
                f"SELECT * FROM candidates {where} ORDER BY source, token"
            ).fetchall()]
        domains = self._catalog_domains()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["source"], []).append(row)
        for source, candidates in grouped.items():
            lines = ["schema_version = 1", f"source = {_toml_string(source)}", ""]
            for candidate in candidates:
                token, config = self._catalog_token(candidate)
                company_id = candidate["company_id"] or f"{source}-{candidate['token']}"
                company = candidate["company_name"] or candidate["token"]
                domain = domains.get(company_id, "unknown")
                validated_at = (candidate["last_validated_at"] or candidate["last_seen_at"]).split("T", 1)[0]
                lines.extend((
                    "[[feed]]",
                    f"key = {_toml_string(token)}",
                    f"company_id = {_toml_string(company_id)}",
                    f"company = {_toml_string(company)}",
                    f"domain = {_toml_string(domain)}",
                    'status = "live"',
                    f"enabled = {'true' if self._adapter_available(source) else 'false'}",
                    f"validated_at = {_toml_string(validated_at)}",
                ))
                if config:
                    lines.append("[feed.config]")
                    lines.extend(f"{key} = {_toml_string(value)}" for key, value in config.items())
                lines.append("")
            (destination / f"{source}.toml").write_text("\n".join(lines), encoding="utf-8")
        return {"files": len(grouped), "feeds": len(rows)}

    @staticmethod
    def _catalog_token(candidate: dict[str, Any]) -> tuple[str, dict[str, str]]:
        if candidate["source"] == "comeet" and "/" in candidate["token"]:
            key, api_token = candidate["token"].split("/", 1)
            return key, {"token": api_token}
        return candidate["token"], {}

    @staticmethod
    def _catalog_domains() -> dict[str, str]:
        try:
            from xjobscore.sources.catalog import entries
            return {
                entry.company_id: entry.domain
                for entry in entries()
                if entry.domain != "unknown"
            }
        except Exception:
            return {}

    @staticmethod
    def _adapter_available(source: str) -> bool:
        try:
            from xjobscore import sources
            return source in sources.SOURCES
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Runs, checkpoints and logs
    # ------------------------------------------------------------------

    def _new_run(self, methods: Iterable[str], crawls: int) -> str:
        selected = tuple(dict.fromkeys(methods))
        unknown = set(selected) - METHODS
        if not selected or unknown:
            detail = ", ".join(sorted(unknown)) if unknown else "at least one method"
            raise TokscanError(f"unknown method(s): {detail}")
        if not 1 <= crawls <= 12:
            raise TokscanError("crawls must be between 1 and 12")
        self.initialize()
        run_id = uuid.uuid4().hex
        artifact = self.workdir / "runs" / run_id
        artifact.mkdir(parents=True)
        checkpoint = {"company_index": 0, "wayback_index": 0, "cc_done": [], "validation_last_id": 0}
        now = utc_now()
        try:
            with self._connect() as con:
                con.execute(
                    """INSERT INTO runs(id, methods_json, config_json, status, phase, checkpoint_json,
                       artifact_dir, pid, created_at, started_at)
                       VALUES (?, ?, ?, 'running', 'starting', ?, ?, ?, ?, ?)""",
                    (run_id, _json(selected), _json({"crawls": crawls}), _json(checkpoint), str(artifact), os.getpid(), now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ActiveRunError("a tokscan run is already active") from exc
        self._run_id = run_id
        self._log("info", "starting", "run started", methods=selected, crawls=crawls)
        return run_id

    async def run(self, methods: Iterable[str], crawls: int = 6) -> str:
        return await self._run(self._new_run(methods, crawls))

    async def resume(self, run_id: str) -> str:
        self.initialize()
        with self._connect() as con:
            run = con.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run is None:
                raise TokscanError(f"run {run_id} does not exist")
            if run["status"] not in {"interrupted", "cancelled", "failed"}:
                raise TokscanError(f"run {run_id} cannot be resumed from {run['status']}")
            try:
                con.execute("UPDATE runs SET status = 'running', phase = 'resuming', pid = ?, error = NULL, finished_at = NULL WHERE id = ?", (os.getpid(), run_id))
            except sqlite3.IntegrityError as exc:
                raise ActiveRunError("a tokscan run is already active") from exc
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
                raise TokscanError(f"run {run_id} does not exist")
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
                for method in run["methods"]:
                    self._check_cancelled()
                    if method == "company":
                        await self._company(run)
                    elif method == "commoncrawl":
                        await self._commoncrawl(run)
                    else:
                        await self._wayback(run)
                self._check_cancelled()
                await self._validate(run)
            self._finish("completed")
            catalog = self.export_catalog()
            self._log("info", "completed", "catalog TOML written", **catalog)
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
            raise TokscanError(f"run {run_id} does not exist")
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
        entry = {"at": utc_now(), "level": level, "phase": phase, "message": message, **details}
        with (artifact / "run.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(_json(entry) + "\n")

    def tail_log(self, run_id: str, offset: int = 0, limit: int = 200) -> tuple[list[dict[str, Any]], int]:
        run = self.status(run_id)
        if run is None:
            raise TokscanError(f"run {run_id} does not exist")
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
                "SELECT validation, count(*) AS n FROM candidates GROUP BY validation"
            ).fetchall()
            evidence = con.execute("SELECT count(*) FROM evidence WHERE run_id = ?", (run_id,)).fetchone()[0]
        return {"evidence": evidence, **{row["validation"]: row["n"] for row in rows}}

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

    async def _request(self, method: str, url: str, *, json_body: dict | None = None) -> httpx.Response | None:
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
                    response = await self._client.request(method, url, json=json_body)
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

    def _record_candidate(
        self, source: str, token: str, method: str, evidence_url: str, *, referrer: str = "",
        company_id: str | None = None, company_name: str | None = None, details: dict[str, Any] | None = None,
    ) -> int:
        assert self._run_id
        rule = BY_SOURCE[source]
        canonical = rule.board_url.format(**rule.urls(token))
        now = utc_now()
        with self._connect() as con:
            # Comeet candidates are promoted after probing from the public
            # locator (slug/board-id) to adapter input (board-id/api-token).
            # Their canonical public board URL remains stable across that
            # promotion, so it is the durable rediscovery key.
            candidate = (
                con.execute("SELECT id FROM candidates WHERE source = ? AND canonical_url = ?", (source, canonical)).fetchone()
                if source == "comeet" else None
            )
            if candidate is None:
                con.execute(
                    """INSERT INTO candidates(source, token, company_id, company_name, canonical_url, first_seen_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source, token) DO UPDATE SET
                         company_id = COALESCE(candidates.company_id, excluded.company_id),
                         company_name = COALESCE(candidates.company_name, excluded.company_name),
                         canonical_url = excluded.canonical_url, last_seen_at = excluded.last_seen_at""",
                    (source, token, company_id, company_name, canonical, now, now),
                )
                candidate = con.execute("SELECT id FROM candidates WHERE source = ? AND token = ?", (source, token)).fetchone()
            else:
                con.execute(
                    """UPDATE candidates
                       SET company_id = COALESCE(company_id, ?), company_name = COALESCE(company_name, ?),
                           last_seen_at = ? WHERE id = ?""",
                    (company_id, company_name, now, candidate["id"]),
                )
            assert candidate is not None
            con.execute(
                """INSERT OR IGNORE INTO evidence(run_id, candidate_id, method, evidence_url, referrer_url, details_json, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (self._run_id, candidate["id"], method, evidence_url, referrer, _json(details or {}), now),
            )
        return int(candidate["id"])

    def _set_validation(self, candidate_id: int, validation: str, job_count: int | None, details: dict[str, Any]) -> None:
        assert self._run_id
        now = utc_now()
        adapter_token = details.pop("_comeet_adapter_token", None)
        with self._connect() as con:
            if adapter_token:
                con.execute("UPDATE candidates SET token = ? WHERE id = ?", (adapter_token, candidate_id))
            con.execute(
                "UPDATE candidates SET validation = ?, job_count = ?, last_validated_at = ? WHERE id = ?",
                (validation, job_count, now, candidate_id),
            )
            candidate = con.execute("SELECT canonical_url FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
            assert candidate is not None
            con.execute(
                """INSERT OR IGNORE INTO evidence(run_id, candidate_id, method, evidence_url, referrer_url, details_json, observed_at)
                   VALUES (?, ?, 'probe', ?, '', ?, ?)""",
                (self._run_id, candidate_id, candidate["canonical_url"], _json(details), now),
            )

    # ------------------------------------------------------------------
    # Company pages and archived company pages
    # ------------------------------------------------------------------

    def _company_seeds(self) -> list[dict[str, str | None]]:
        seeds: dict[str, dict[str, str | None]] = {}
        try:
            from xjobscore.sources.catalog import entries
            for entry in entries():
                seeds.setdefault(entry.domain.lower(), {"domain": entry.domain.lower(), "company_id": entry.company_id, "company_name": entry.company})
        except Exception as exc:
            self._log("warning", "company", "catalog domains unavailable", error=str(exc))
        db_name = os.environ.get("XJOBSCORE_ONESHOT_DB")
        if db_name and Path(db_name).exists():
            try:
                with sqlite3.connect(db_name) as con:
                    for company_id, name, domain in con.execute("SELECT id, display_name, domain FROM companies WHERE domain IS NOT NULL AND domain <> ''"):
                        seeds.setdefault(domain.lower(), {"domain": domain.lower(), "company_id": company_id, "company_name": name})
            except sqlite3.Error as exc:
                self._log("warning", "company", "local company domains unavailable", error=str(exc))
        return [seeds[key] for key in sorted(seeds)]

    async def _company(self, run: dict[str, Any]) -> None:
        self._phase("company")
        seeds = self._company_seeds()
        start = int(run["checkpoint"].get("company_index", 0))
        self._log("info", "company", "scanning company domains", total=len(seeds), start=start)
        for index, seed in enumerate(seeds[start:], start):
            self._check_cancelled()
            root = f"https://{seed['domain']}/"
            response = await self._request("GET", root)
            if response is not None and response.status_code < 400:
                page_url = str(response.url)
                await self._discover_page(response.text, page_url, "company", seed, root)
                pages = self._career_pages(response.text, page_url, seed["domain"] or "")[:3]
                for page in pages:
                    self._check_cancelled()
                    child = await self._request("GET", page)
                    if child is not None and child.status_code < 400:
                        await self._discover_page(child.text, str(child.url), "company", seed, page_url)
            self._checkpoint(company_index=index + 1)
            if (index + 1) % 100 == 0:
                self._log("info", "company", "company progress", completed=index + 1, total=len(seeds))

    async def _discover_page(
        self, page: str, page_url: str, method: str, seed: dict[str, str | None], referrer: str,
    ) -> None:
        for url in self._urls_in_html(page, page_url):
            found = extract(url)
            if found:
                self._record_candidate(*found, method, url, referrer=referrer, company_id=seed.get("company_id"), company_name=seed.get("company_name"))

    @staticmethod
    def _urls_in_html(page: str, base_url: str) -> list[str]:
        values = [*ATTR_URL.findall(page), *ABS_URL.findall(html.unescape(page))]
        seen: set[str] = set()
        out: list[str] = []
        for value in values:
            value = html.unescape(value).strip()
            if not value or value.startswith(("#", "mailto:", "javascript:")):
                continue
            url = urljoin(base_url, value)
            if url.startswith(("http://", "https://")) and url not in seen:
                seen.add(url)
                out.append(url)
        return out

    def _career_pages(self, page: str, base_url: str, domain: str) -> list[str]:
        host = urlsplit(base_url).hostname or ""
        linked = [
            url for url in self._urls_in_html(page, base_url)
            if CAREERS.search(url) and ((urlsplit(url).hostname or "").endswith(domain) or (urlsplit(url).hostname or "") == host)
        ]
        candidates = [*linked, urljoin(base_url, "/careers"), urljoin(base_url, "/jobs")]
        return list(dict.fromkeys(candidates))

    async def _wayback(self, run: dict[str, Any]) -> None:
        self._phase("wayback")
        seeds = self._company_seeds()
        start = int(run["checkpoint"].get("wayback_index", 0))
        self._log("info", "wayback", "scanning archived company pages", total=len(seeds), start=start)
        for index, seed in enumerate(seeds[start:], start):
            self._check_cancelled()
            query = urlencode([
                ("url", f"{seed['domain']}/*"), ("output", "json"),
                ("filter", "statuscode:200"), ("filter", "mimetype:text/html"),
                ("collapse", "urlkey"), ("limit", "1000"),
            ])
            response = await self._request("GET", WAYBACK_CDX + "?" + query)
            if response is not None and response.status_code == 200:
                for timestamp, original in self._wayback_pages(response)[:5]:
                    archived = f"https://web.archive.org/web/{timestamp}id_/{original}"
                    capture = await self._request("GET", archived)
                    if capture is not None and capture.status_code < 400:
                        await self._discover_page(capture.text, str(capture.url), "wayback", seed, original)
            self._checkpoint(wayback_index=index + 1)
            if (index + 1) % 100 == 0:
                self._log("info", "wayback", "wayback progress", completed=index + 1, total=len(seeds))

    @staticmethod
    def _wayback_pages(response: httpx.Response) -> list[tuple[str, str]]:
        try:
            data = response.json()
        except ValueError:
            return []
        if not isinstance(data, list) or len(data) < 2 or not isinstance(data[0], list):
            return []
        names = {name: index for index, name in enumerate(data[0])}
        timestamp, original = names.get("timestamp"), names.get("original")
        if timestamp is None or original is None:
            return []
        pages = []
        for row in data[1:]:
            if not isinstance(row, list) or max(timestamp, original) >= len(row):
                continue
            url = str(row[original])
            if CAREERS.search(url):
                pages.append((str(row[timestamp]), url))
        return pages

    # ------------------------------------------------------------------
    # Common Crawl URL index
    # ------------------------------------------------------------------

    async def _commoncrawl(self, run: dict[str, Any]) -> None:
        self._phase("commoncrawl")
        response = await self._request("GET", COLLINFO)
        if response is None or response.status_code != 200:
            raise TokscanError("unable to retrieve Common Crawl collection list")
        crawls = [row["id"] for row in response.json()[: int(run["config"].get("crawls", 6))]]
        done = set(run["checkpoint"].get("cc_done", []))
        for crawl in crawls:
            manifest = await self._request("GET", CC_PATHS.format(crawl=crawl))
            if manifest is None or manifest.status_code != 200:
                self._log("warning", "commoncrawl", "crawl manifest unavailable", crawl=crawl)
                continue
            try:
                paths = gzip.decompress(manifest.content).decode().splitlines()
            except (OSError, UnicodeDecodeError) as exc:
                self._log("warning", "commoncrawl", "invalid crawl manifest", crawl=crawl, error=str(exc))
                continue
            urls = [CC_CDN + path for path in paths if path.endswith(".parquet") and ("/subset=warc/" in path or "/subset=crawldiagnostics/" in path)]
            for rule in RULES:
                self._check_cancelled()
                marker = f"{crawl}/{rule.source}"
                if marker in done:
                    continue
                if not rule.domains:
                    self._log("info", "commoncrawl", "vendor has no bounded crawl domain", crawl=crawl, source=rule.source)
                    done.add(marker)
                    self._checkpoint(cc_done=sorted(done))
                    continue
                started = time.monotonic()
                try:
                    records = await asyncio.to_thread(self._cc_scan, crawl, rule, urls)
                except Exception as exc:
                    self._log("warning", "commoncrawl", "vendor scan failed", crawl=crawl, source=rule.source, error=str(exc))
                    self._log(
                        "warning", "commoncrawl", "Common Crawl vendor timing", event="operation_timing",
                        operation="commoncrawl_vendor_scan", crawl=crawl, source=rule.source,
                        outcome="error", elapsed_ms=round(1000 * (time.monotonic() - started)),
                    )
                    continue
                for record in records:
                    self._record_candidate(rule.source, record["token"], "commoncrawl", record["evidence_url"], details={"crawl": crawl, "url_count": record["url_count"]})
                done.add(marker)
                self._checkpoint(cc_done=sorted(done))
                self._log("info", "commoncrawl", "vendor scanned", crawl=crawl, source=rule.source, candidates=len(records))
                self._log(
                    "info", "commoncrawl", "Common Crawl vendor timing", event="operation_timing",
                    operation="commoncrawl_vendor_scan", crawl=crawl, source=rule.source, outcome="ok",
                    candidates=len(records), elapsed_ms=round(1000 * (time.monotonic() - started)),
                )

    def _cc_scan(self, crawl: str, rule: Rule, urls: list[str]) -> list[dict[str, Any]]:
        target = self._artifact() / "commoncrawl" / crawl / f"{rule.source}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temp = target.with_suffix(".part")
            temp.unlink(missing_ok=True)
            ranges = " OR ".join(
                f"(url_surtkey >= '{low}' AND url_surtkey < '{high}')" for low, high in map(surt_range, rule.domains)
            )
            stop = ", ".join("'" + value.replace("'", "''") + "'" for value in sorted(STOP))
            files = "[" + ", ".join("'" + url.replace("'", "''") + "'" for url in urls) + "]"
            query = f"""
                SELECT * FROM (
                    SELECT {token_sql(rule)} AS token, count(*) AS url_count, min(url) AS evidence_url
                    FROM read_parquet({files})
                    WHERE ({ranges}) AND ({rule.where_sql})
                    GROUP BY 1
                )
                WHERE token <> ''
                  AND lower(split_part(token, '/', 1)) NOT IN ({stop})
                  AND regexp_matches(token, '{TOKEN_RE.pattern}')
            """
            con = duckdb.connect()
            try:
                con.execute("INSTALL httpfs; LOAD httpfs;")
                for setting in ("SET enable_http_metadata_cache=true", "SET http_retries=8", "SET http_timeout=120000", "SET preserve_insertion_order=false"):
                    try:
                        con.execute(setting)
                    except duckdb.Error:
                        pass
                con.execute(f"COPY ({query}) TO '{str(temp).replace("'", "''")}' (FORMAT PARQUET)")
            finally:
                con.close()
            temp.replace(target)
        con = duckdb.connect()
        try:
            rows = con.execute(f"SELECT token, url_count, evidence_url FROM read_parquet('{str(target).replace("'", "''")}')").fetchall()
        finally:
            con.close()
        return [{"token": row[0], "url_count": int(row[1]), "evidence_url": row[2]} for row in rows]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    async def _validate(self, run: dict[str, Any]) -> None:
        self._phase("validate")
        last_id = int(run["checkpoint"].get("validation_last_id", 0))
        with self._connect() as con:
            candidates = con.execute(
                """SELECT * FROM candidates
                   WHERE id > ? AND decision = 'pending'
                     AND (last_validated_at IS NULL OR last_seen_at > last_validated_at OR validation = 'inconclusive')
                   ORDER BY id""",
                (last_id,),
            ).fetchall()
        self._log("info", "validate", "validating candidates", total=len(candidates))
        for row in candidates:
            self._check_cancelled()
            candidate = dict(row)
            validation, job_count, details = await self._probe(candidate)
            self._set_validation(candidate["id"], validation, job_count, details)
            self._checkpoint(validation_last_id=candidate["id"])
            if candidate["id"] % 100 == 0:
                self._log("info", "validate", "validation progress", candidate_id=candidate["id"])

    async def _probe(self, candidate: dict[str, Any]) -> tuple[str, int | None, dict[str, Any]]:
        rule = BY_SOURCE[candidate["source"]]
        if rule.probe_kind == "comeet":
            return await self._probe_comeet(candidate, rule)
        fields = rule.urls(candidate["token"])
        primary = rule.probe_url if rule.probe_kind != "html" else rule.board_url
        responses: list[tuple[str, int | None]] = []
        response: httpx.Response | None = None
        for template in (primary, *rule.fallbacks):
            endpoint = template.format(**fields)
            response = await self._request(rule.method, endpoint, json_body=rule.body)
            status = response.status_code if response is not None else None
            responses.append((endpoint, status))
            if status not in {404, 410}:
                break
        if response is None:
            return "inconclusive", None, {"responses": responses, "reason": "transport"}
        if all(status in {404, 410} for _, status in responses):
            return "absent", None, {"responses": responses}
        if response.status_code != 200:
            return "inconclusive", None, {"responses": responses, "reason": f"http_{response.status_code}"}
        if rule.probe_kind == "json":
            try:
                count = _job_count(response.json())
            except ValueError:
                return "inconclusive", None, {"responses": responses, "reason": "invalid_json"}
            if count is None:
                return "inconclusive", None, {"responses": responses, "reason": "unrecognized_json"}
            return "live", count, {"responses": responses}
        if rule.probe_kind == "xml":
            count = _xml_job_count(response.text)
            if count is None:
                return "inconclusive", None, {"responses": responses, "reason": "unrecognized_xml"}
            return "live", count, {"responses": responses}
        full_body = response.text.lower()
        body = full_body[:20000]
        if any(marker in body for marker in rule.absent_markers):
            return "absent", None, {"responses": responses, "reason": "absent_marker"}
        if any(marker not in full_body for marker in rule.html_required_markers):
            return "inconclusive", None, {"responses": responses, "reason": "missing_html_marker"}
        if rule.html_any_markers and not any(marker in full_body for marker in rule.html_any_markers):
            return "inconclusive", None, {"responses": responses, "reason": "missing_jobboard_marker"}
        control = await self._html_control(rule, fields)
        fingerprint = _html_fingerprint(body, candidate["token"])
        if control and control == fingerprint:
            return "inconclusive", None, {"responses": responses, "reason": "generic_html"}
        if "<html" not in body and "<!doctype" not in body:
            return "inconclusive", None, {"responses": responses, "reason": "not_html"}
        return "live", None, {"responses": responses, "html_fingerprint": fingerprint}

    async def _probe_comeet(self, candidate: dict[str, Any], rule: Rule) -> tuple[str, int | None, dict[str, Any]]:
        """Resolve Comeet's public board locator into its adapter input.

        Discovery sees ``slug/board-id`` in public URLs.  Comeet's jobs API
        additionally requires a public per-board API token embedded in that
        board page.  A successful probe promotes the candidate token to
        ``board-id/api-token``, which is sufficient to configure the existing
        Comeet adapter (key plus config.token after splitting once on ``/``).
        """
        parts = candidate["token"].split("/", 1)
        if len(parts) != 2:
            return "inconclusive", None, {"responses": [], "reason": "invalid_comeet_token"}
        first, second = parts
        if len(second) >= 16 and second.isalnum():
            board_id, api_token = first, second
            board_response: httpx.Response | None = None
            board_url = candidate["canonical_url"]
        else:
            slug, board_id = first, second
            board_url = rule.board_url.format(**rule.urls(f"{slug}/{board_id}"))
            board_response = await self._request("GET", board_url)
            board_status = board_response.status_code if board_response is not None else None
            if board_response is None:
                return "inconclusive", None, {"responses": [(board_url, board_status)], "reason": "transport"}
            if board_status in {404, 410}:
                return "absent", None, {"responses": [(board_url, board_status)]}
            if board_status != 200:
                return "inconclusive", None, {"responses": [(board_url, board_status)], "reason": f"http_{board_status}"}
            match = COMEET_API_TOKEN.search(board_response.text)
            if match is None:
                return "inconclusive", None, {"responses": [(board_url, board_status)], "reason": "missing_comeet_api_token"}
            api_token = match.group(1)

        endpoint = rule.probe_url.format(**rule.urls(f"x/{board_id}"))
        response = await self._request(
            "GET", endpoint + "?" + urlencode({"token": api_token, "details": "true"}),
        )
        status = response.status_code if response is not None else None
        responses = ([(board_url, board_response.status_code)] if board_response is not None else []) + [(endpoint, status)]
        if response is None:
            return "inconclusive", None, {"responses": responses, "reason": "transport"}
        if status in {404, 410}:
            return "absent", None, {"responses": responses}
        if status != 200:
            return "inconclusive", None, {"responses": responses, "reason": f"http_{status}"}
        try:
            count = _job_count(response.json())
        except ValueError:
            return "inconclusive", None, {"responses": responses, "reason": "invalid_json"}
        if count is None:
            return "inconclusive", None, {"responses": responses, "reason": "unrecognized_json"}
        return "live", count, {"responses": responses, "_comeet_adapter_token": f"{board_id}/{api_token}"}

    async def _html_control(self, rule: Rule, fields: dict[str, str]) -> str | None:
        key = rule.source
        if key in self._controls:
            return self._controls[key]
        control_fields = dict(fields)
        invalid = "tokscan-token-does-not-exist-9f4d2a"
        control_fields["token"] = invalid
        parts = sorted(int(name[1:]) for name in fields if name.startswith("t") and name[1:].isdigit())
        if parts:
            # Composite locators put a regional host in the first component.
            # Preserve that host and invalidate only the actual site/company
            # component, otherwise a generic-page control cannot be fetched.
            control_fields[f"t{parts[-1]}"] = invalid
        endpoint = rule.board_url.format(**control_fields)
        response = await self._request("GET", endpoint)
        value = None
        if response is not None and response.status_code == 200:
            value = _html_fingerprint(response.text[:20000].lower(), control_fields["token"])
        self._controls[key] = value
        return value


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
    if isinstance(meta, dict) and isinstance(meta.get("total"), int):
        return meta["total"]
    return None


def _xml_job_count(text: str) -> int | None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    tags = [element.tag.rsplit("}", 1)[-1].lower() for element in root.iter()]
    if not any(tag in {"rss", "feed", "jobs", "positions", "openings", "workzag-jobs"} for tag in tags):
        return None
    return sum(tag in {"item", "job", "position", "opening"} for tag in tags)


def _html_fingerprint(text: str, token: str) -> str:
    normalized = text.replace(token.lower(), "{token}")
    normalized = re.sub(r"\b[0-9a-f]{8,}\b", "{id}", normalized)
    normalized = re.sub(r"\d+", "{n}", normalized)
    normalized = re.sub(r"\s+", " ", normalized)[:8000]
    return hashlib.sha256(normalized.encode()).hexdigest()


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
