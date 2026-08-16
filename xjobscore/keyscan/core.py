"""The complete standalone keyscan workflow.

It deliberately owns its own SQLite file and artifacts. Nothing in this
module writes the application database or source registry.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import html
from itertools import zip_longest
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import tomllib
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
USER_AGENT = "xjobscore-keyscan/1.0 (+local token discovery)"
REGISTRY_CATALOG_DIR = Path(__file__).resolve().parents[1] / "sources" / "registry" / "ats-catalog"
# "validate" discovers nothing.  It exists so a backlog of already-discovered
# tokens can be drained on its own, without waiting behind a fresh crawl.
METHODS = frozenset({"company", "commoncrawl", "wayback", "validate"})
# Probes run concurrently with discovery.  `_request` holds a global semaphore
# of four, and discovery issues one request at a time, so three validators
# saturate the budget without ever starving the crawl of its slot.
VALIDATION_WORKERS = 3
LIST_KEYS = ("jobs", "offers", "data", "content", "jobPostings", "results", "result", "positions", "items")
TOTAL_KEYS = ("total", "totalFound", "totalCount", "count")
NAME_KEYS = ("name", "companyName", "organizationName")
GENERIC_NAMES = frozenset({"apply", "careers", "career", "jobs", "job board", "open positions", "positions"})
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
    if row is None:
        return None
    result = dict(row)
    if result.get("company_id") is not None:
        result["company_id"] = int(result["company_id"])
    return result


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
    ) -> None:
        self.workdir = Path(workdir) if workdir else Path(__file__).with_name("work")
        self.db_path = self.workdir / "keyscan.sqlite"
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
        # Set only while a run streams validation; `_record_candidate` uses its
        # presence to decide whether a newly seen token is probed immediately.
        self._validation_queue: asyncio.Queue[int] | None = None
        self._validating: set[int] = set()
        self._validated_count = 0

    # ------------------------------------------------------------------
    # Storage and public inspection
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        if self._initialized:
            return
        self.workdir.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            # A polling reader must not block behind the writing scan, and the
            # daemon serves reads from a separate connection to the same file.
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
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS keyscan_one_active
                    ON runs(status) WHERE status = 'running';
                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    token TEXT NOT NULL,
                    company_id INTEGER,
                    company_name TEXT,
                    canonical_url TEXT NOT NULL,
                    validation TEXT NOT NULL DEFAULT 'unvalidated'
                        CHECK (validation IN ('unvalidated','live','absent','inconclusive')),
                    decision TEXT NOT NULL DEFAULT 'pending'
                        CHECK (decision IN ('pending','accepted','rejected')),
                    job_count INTEGER,
                    -- Monotonic stamp bumped on every validation.  Candidate
                    -- ids are assigned at discovery and go live minutes later,
                    -- so they cannot serve as a poll cursor; this can.
                    validated_seq INTEGER,
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
                CREATE INDEX IF NOT EXISTS keyscan_candidates_review
                    ON candidates(validation, decision, source, id);
                CREATE INDEX IF NOT EXISTS keyscan_evidence_candidate
                    ON evidence(candidate_id, observed_at DESC);
                """
            )
            # `CREATE TABLE IF NOT EXISTS` leaves an existing work database on
            # the old shape, so the poll cursor is added explicitly.
            columns = {row["name"] for row in con.execute("PRAGMA table_info(candidates)")}
            if "validated_seq" not in columns:
                con.execute("ALTER TABLE candidates ADD COLUMN validated_seq INTEGER")
                # Backfill in id order so a poller starting at zero receives
                # the tokens validated before the upgrade rather than only
                # those validated after it.
                con.execute(
                    """WITH ordered AS (
                           SELECT id, ROW_NUMBER() OVER (ORDER BY id) AS seq
                           FROM candidates WHERE last_validated_at IS NOT NULL
                       )
                       UPDATE candidates
                       SET validated_seq = (SELECT seq FROM ordered WHERE ordered.id = candidates.id)
                       WHERE last_validated_at IS NOT NULL"""
                )
            con.execute(
                "CREATE INDEX IF NOT EXISTS keyscan_candidates_seq ON candidates(validated_seq)"
            )
            # A database written before the tokscan -> keyscan rename carries the
            # old index names; the `keyscan_` ones above already replace them.
            for stale in (
                "tokscan_one_active",
                "tokscan_candidates_review",
                "tokscan_evidence_candidate",
                "tokscan_candidates_seq",
            ):
                con.execute(f"DROP INDEX IF EXISTS {stale}")
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
        return [_row(row) for row in rows if row is not None]

    def candidates_since(
        self, *, since_seq: int = 0, validation: str | None = "live",
        source: str | None = None, limit: int = 500,
    ) -> dict[str, Any]:
        """Validated candidates after `since_seq`, for incremental polling.

        Ordering by `validated_seq` rather than `id` is what makes the cursor
        correct: a token is inserted during discovery but only becomes live
        once probed, so an id-ordered poller would step past it forever.
        """
        self.initialize()
        cursor = max(0, since_seq)
        clauses = ["validated_seq IS NOT NULL", "validated_seq > ?"]
        params: list[Any] = [cursor]
        for column, value in (("validation", validation), ("source", source)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        capped = max(1, min(limit, 1000))
        params.append(capped)
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM candidates WHERE " + " AND ".join(clauses)
                + " ORDER BY validated_seq LIMIT ?",
                params,
            ).fetchall()
        candidates = [_row(row) for row in rows if row is not None]
        return {
            "candidates": candidates,
            "next_seq": candidates[-1]["validated_seq"] if candidates else cursor,
            "count": len(candidates),
            # A full page means the cursor has not caught up; poll again now
            # rather than waiting out the interval.
            "more": len(candidates) == capped,
        }

    async def robots(self, sources: Iterable[str] | None = None) -> list[dict[str, str]]:
        """Report whether shared ATS hosts explicitly block Common Crawl."""
        selected = tuple(dict.fromkeys(sources or ()))
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

    def decide(self, candidate_id: int, decision: str) -> dict[str, Any]:
        if decision not in {"accepted", "rejected"}:
            raise KeyscanError("decision must be accepted or rejected")
        self.initialize()
        with self._connect() as con:
            row = con.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
            if row is None:
                raise KeyscanError(f"candidate {candidate_id} does not exist")
            if row["decision"] != "pending":
                raise KeyscanError(f"candidate {candidate_id} is already {row['decision']}")
            if decision == "accepted" and row["validation"] != "live":
                raise KeyscanError("only live candidates may be accepted")
            con.execute("UPDATE candidates SET decision = ? WHERE id = ?", (decision, candidate_id))
            changed = con.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        assert changed is not None
        result = _row(changed)
        assert result is not None
        return result

    def export(self, path: Path | str, *, accepted_only: bool = True) -> int:
        self.initialize()
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        where = "WHERE decision = 'accepted'" if accepted_only else ""
        with self._connect() as con, destination.open("w", encoding="utf-8") as handle:
            rows = con.execute(f"SELECT * FROM candidates {where} ORDER BY source, token").fetchall()
            for row in rows:
                candidate = _row(row)
                assert candidate is not None
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

    def export_catalog(
        self, directory: Path | str | None = None, *, accepted_only: bool = False,
        candidates: bool = False,
    ) -> dict[str, int]:
        """Update registry ATS catalogs from validated or raw Keyscan candidates.

        The default destination is the application's canonical
        ``sources/registry/ats-catalog`` directory. Existing feed entries are
        preserved. Normal exports append only live candidates with a known
        registry company; candidate exports append unresolved, disabled rows.
        Passing an explicit directory retains the standalone export behavior
        used by tests and one-off JSON/TOML handoffs.
        """
        self.initialize()
        destination = Path(directory) if directory else REGISTRY_CATALOG_DIR
        destination.mkdir(parents=True, exist_ok=True)
        where = "" if candidates else "WHERE validation = 'live'"
        if accepted_only:
            where += "WHERE decision = 'accepted'" if not where else " AND decision = 'accepted'"
        with self._connect() as con:
            rows = [
                candidate
                for row in con.execute(
                    f"SELECT * FROM candidates {where} ORDER BY source, token"
                ).fetchall()
                if (candidate := _row(row)) is not None
            ]
        domains = self._registry_domains()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["source"], []).append(row)
        if directory is None:
            return self._update_registry_catalogs(
                destination, grouped, self._registry_company_info(), raw_candidates=candidates,
            )

        written = 0
        written_files = 0
        for source, candidate_rows in grouped.items():
            lines = ["schema_version = 1", f"source = {_toml_string(source)}", ""]
            for candidate in candidate_rows:
                company_id = candidate["company_id"]
                if company_id is None and not candidates:
                    continue
                if candidates and company_id is None:
                    lines.extend(self._candidate_catalog_feed_block(candidate).splitlines())
                    lines.append("")
                    written += 1
                    continue
                token, config = self._catalog_token(candidate)
                company = candidate["company_name"] or candidate["token"]
                domain = domains.get(company_id, "unknown")
                validated_at = (candidate["last_validated_at"] or candidate["last_seen_at"]).split("T", 1)[0]
                runnable = self._adapter_available(source) and self._catalog_ready(source, config)
                lines.extend((
                    "[[feed]]",
                    f"key = {_toml_string(token)}",
                    # The registry no longer carries an enabled flag, so a token we
                    # cannot actually run is exported unverified: loader.feeds()
                    # skips it exactly as the old enabled = false did.
                    f"verified = {'true' if runnable else 'false'}",
                    f"verified_at = {_toml_string(validated_at)}",
                    f"company_id = {company_id}",
                    f"company_name = {_toml_string(company)}",
                    f"domain = {_toml_string(domain)}",
                ))
                if config:
                    lines.append("[feed.config]")
                    lines.extend(f"{key} = {_toml_string(value)}" for key, value in config.items())
                lines.append("")
                written += 1
            if len(lines) == 3:
                continue
            (destination / f"{source}.toml").write_text("\n".join(lines), encoding="utf-8")
            written_files += 1
        return {"files": written_files, "feeds": written}

    def export_live_toml(self, path: Path | str) -> int:
        """Write a portable snapshot of every currently live token as TOML.

        Unlike ``export_catalog``, this does not require a candidate to be
        matched to a company in the application registry.  It is intended for
        handoff and inspection of Keyscan's validated output.
        """
        self.initialize()
        destination = Path(path)
        with self._connect() as con:
            rows = con.execute(
                """SELECT source, token, canonical_url, company_name, job_count,
                          last_validated_at, validated_seq
                     FROM candidates
                    WHERE validation = 'live'
                    ORDER BY source, token"""
            ).fetchall()

        lines = [
            "schema_version = 1",
            f"generated_at = {_toml_string(utc_now())}",
            'validation = "live"',
            "",
        ]
        for row in rows:
            lines.extend((
                "[[token]]",
                f"source = {_toml_string(row['source'])}",
                f"key = {_toml_string(row['token'])}",
                f"canonical_url = {_toml_string(row['canonical_url'])}",
                f"validated_at = {_toml_string(row['last_validated_at'])}",
                f"validated_seq = {row['validated_seq']}",
            ))
            if row["company_name"]:
                lines.append(f"company_name = {_toml_string(row['company_name'])}")
            if row["job_count"] is not None:
                lines.append(f"job_count = {row['job_count']}")
            lines.append("")

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\n".join(lines), encoding="utf-8")
        return len(rows)

    def _update_registry_catalogs(
        self, destination: Path, grouped: dict[str, list[dict[str, Any]]],
        registry_info: dict[int, tuple[str, str]], *, raw_candidates: bool = False,
    ) -> dict[str, int]:
        """Append new valid feeds without replacing operator-owned catalogs."""
        added = 0
        skipped = 0
        touched: set[Path] = set()
        for source, candidate_rows in grouped.items():
            path = destination / f"{source}.toml"
            if not path.exists():
                # A catalog must also be referenced by feeds.toml. Creating an
                # orphan file here would make registry.loader reject the whole
                # registry, so leave unregistered sources for manual review.
                skipped += len(candidate_rows)
                continue
            try:
                document = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise KeyscanError(f"cannot read registry catalog {path}: {exc}") from exc
            existing = document.get("feed", [])
            if not isinstance(existing, list):
                raise KeyscanError(f"registry catalog {path}: feed must be an array of tables")
            existing_keys = {
                row.get("key") for row in existing
                if isinstance(row, dict) and isinstance(row.get("key"), str)
            }
            blocks: list[str] = []
            for candidate in candidate_rows:
                if raw_candidates:
                    token = candidate["token"]
                    if token in existing_keys:
                        continue
                    blocks.append(self._candidate_catalog_feed_block(candidate))
                    existing_keys.add(token)
                    continue
                company_id = candidate.get("company_id")
                if not company_id or company_id not in registry_info:
                    skipped += 1
                    continue
                token, config = self._catalog_token({
                    **candidate,
                    "company_name": candidate.get("company_name") or registry_info[company_id][0],
                })
                if token in existing_keys:
                    continue
                company, domain = registry_info[company_id]
                blocks.append(self._catalog_feed_block(
                    source, token, company_id, company, domain, candidate, config,
                ))
                existing_keys.add(token)
            if not blocks:
                continue
            text = path.read_text(encoding="utf-8")
            separator = "" if text.endswith("\n") else "\n"
            path.write_text(text + separator + "\n" + "\n\n".join(blocks) + "\n", encoding="utf-8")
            touched.add(path)
            added += len(blocks)
        return {"files": len(touched), "feeds": added, "skipped": skipped}

    @staticmethod
    def _candidate_catalog_feed_block(candidate: dict[str, Any]) -> str:
        """Encode an unresolved discovery without making it a runnable feed."""
        return "\n".join((
            "[[feed]]",
            f"key = {_toml_string(candidate['token'])}",
            "verified = false",
            'verified_at = ""',
            "company_id = 0",
            'company_name = ""',
            'domain = ""',
        ))

    @staticmethod
    def _catalog_feed_block(
        source: str, token: str, company_id: int, company: str, domain: str,
        candidate: dict[str, Any], config: dict[str, str],
    ) -> str:
        validated_at = (candidate["last_validated_at"] or candidate["last_seen_at"]).split("T", 1)[0]
        runnable = Keyscan._adapter_available(source) and Keyscan._catalog_ready(source, config)
        lines = [
            "[[feed]]",
            f"key = {_toml_string(token)}",
            # See export_catalog: unverified stands in for the retired enabled flag.
            f"verified = {'true' if runnable else 'false'}",
            f"verified_at = {_toml_string(validated_at)}",
            f"company_id = {company_id}",
            f"company_name = {_toml_string(company)}",
            f"domain = {_toml_string(domain)}",
        ]
        if config:
            lines.append("")
            lines.append("[feed.config]")
            lines.extend(f"{key} = {_toml_string(value)}" for key, value in config.items())
        return "\n".join(lines)

    @staticmethod
    def _catalog_token(candidate: dict[str, Any]) -> tuple[str, dict[str, str]]:
        source = candidate["source"]
        token = candidate["token"]
        company_name = candidate.get("company_name")
        if source == "comeet" and "/" in token:
            key, api_token = token.split("/", 1)
            return key, {"token": api_token}
        config: dict[str, str] = {}
        if source in {"adp", "bullhorn", "eightfold", "gem", "oracle", "ukg"} and company_name:
            config["company_raw"] = company_name
        if source == "eightfold" and "/" in token:
            host, domain = token.split("/", 1)
            config.update(host=host, domain=domain, query="")
            return host, config
        if source == "oleeo" and "/" in token:
            tenant, board = token.split("/", 1)
            if board != "1":
                config["url"] = f"https://{tenant}.tal.net/candidate/jobboard/vacancy/{board}/feed"
            return tenant, config
        return token, config

    @staticmethod
    def _catalog_ready(source: str, config: dict[str, str]) -> bool:
        rule = BY_SOURCE.get(source)
        return rule is not None and all(config.get(key) is not None for key in rule.catalog_requires)

    @staticmethod
    def _registry_domains() -> dict[int, str]:
        try:
            from xjobscore.sources.registry.loader import companies, entries
            result = {
                company.id: (company.domain if company.domain != "unknown" else "unknown")
                for company in companies()
            }
            result.update({
                entry.company_id: entry.domain
                for entry in entries()
                if entry.domain != "unknown"
            })
            return result
        except Exception:
            return {}

    @staticmethod
    def _registry_company_info() -> dict[int, tuple[str, str]]:
        try:
            from xjobscore.sources.registry.loader import companies
            return {
                company.id: (company.name, company.domain)
                for company in companies()
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

    def _new_run(
        self, methods: Iterable[str], crawls: int, sources: Iterable[str] | None = None,
    ) -> str:
        selected = tuple(dict.fromkeys(methods))
        unknown = set(selected) - METHODS
        if not selected or unknown:
            detail = ", ".join(sorted(unknown)) if unknown else "at least one method"
            raise KeyscanError(f"unknown method(s): {detail}")
        if not 1 <= crawls <= 12:
            raise KeyscanError("crawls must be between 1 and 12")
        selected_sources = tuple(dict.fromkeys(sources or ()))
        unknown_sources = set(selected_sources) - BY_SOURCE.keys()
        if unknown_sources:
            raise KeyscanError(f"unknown source(s): {', '.join(sorted(unknown_sources))}")
        self.initialize()
        run_id = uuid.uuid4().hex
        artifact = self.workdir / "runs" / run_id
        artifact.mkdir(parents=True)
        checkpoint = {"company_index": 0, "wayback_index": 0, "cc_done": []}
        now = utc_now()
        try:
            with self._connect() as con:
                con.execute(
                    """INSERT INTO runs(id, methods_json, config_json, status, phase, checkpoint_json,
                       artifact_dir, pid, created_at, started_at)
                       VALUES (?, ?, ?, 'running', 'starting', ?, ?, ?, ?, ?)""",
                    (run_id, _json(selected), _json({"crawls": crawls, "sources": selected_sources}), _json(checkpoint), str(artifact), os.getpid(), now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ActiveRunError("a keyscan run is already active") from exc
        self._run_id = run_id
        self._log(
            "info", "starting", "run started", methods=selected, crawls=crawls,
            sources=selected_sources or "all",
        )
        return run_id

    async def run(
        self, methods: Iterable[str], crawls: int = 6, sources: Iterable[str] | None = None,
    ) -> str:
        return await self._run(self._new_run(methods, crawls, sources))

    def begin(
        self, methods: Iterable[str], crawls: int = 6, sources: Iterable[str] | None = None,
    ) -> str:
        """Register a run without executing it, then hand the id to `execute`.

        The daemon needs the split so that a rejected request — bad methods, or
        a run already active — fails the HTTP call instead of a background task.
        """
        return self._new_run(methods, crawls, sources)

    async def execute(self, run_id: str) -> str:
        return await self._run(run_id)

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
                queue: asyncio.Queue[int] = asyncio.Queue()
                self._validation_queue = queue
                self._validating.clear()
                self._validated_count = 0
                workers = [
                    asyncio.create_task(self._validation_worker(run))
                    for _ in range(VALIDATION_WORKERS)
                ]
                try:
                    for method in run["methods"]:
                        self._check_cancelled()
                        if method == "company":
                            await self._company(run)
                        elif method == "commoncrawl":
                            await self._commoncrawl(run)
                        elif method == "wayback":
                            await self._wayback(run)
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
        entry = {"at": utc_now(), "level": level, "phase": phase, "message": message, **details}
        with (artifact / "run.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(_json(entry) + "\n")

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
        company_id: int | None = None, company_name: str | None = None, details: dict[str, Any] | None = None,
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
        candidate_id = int(candidate["id"])
        if self._validation_queue is not None:
            self._validation_queue.put_nowait(candidate_id)
        return candidate_id

    def _set_validation(self, candidate_id: int, validation: str, job_count: int | None, details: dict[str, Any]) -> None:
        assert self._run_id
        now = utc_now()
        adapter_token = details.pop("_comeet_adapter_token", None)
        company_name = details.get("company_name")
        with self._connect() as con:
            if adapter_token:
                con.execute("UPDATE candidates SET token = ? WHERE id = ?", (adapter_token, candidate_id))
            con.execute(
                """UPDATE candidates
                   SET validation = ?, job_count = ?, last_validated_at = ?,
                       company_name = COALESCE(company_name, ?),
                       validated_seq = (SELECT COALESCE(MAX(validated_seq), 0) + 1 FROM candidates)
                   WHERE id = ?""",
                (validation, job_count, now, company_name, candidate_id),
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

    def _company_seeds(self) -> list[dict[str, Any]]:
        seeds: dict[str, dict[str, Any]] = {}
        try:
            from xjobscore.sources.registry.loader import entries
            for entry in entries():
                seeds.setdefault(entry.domain.lower(), {"domain": entry.domain.lower(), "company_id": entry.company_id, "company_name": entry.company_name})
        except Exception as exc:
            self._log("warning", "company", "registry domains unavailable", error=str(exc))
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
        selected_sources = self._selected_sources(run)
        start = int(run["checkpoint"].get("company_index", 0))
        self._log("info", "company", "scanning company domains", total=len(seeds), start=start)
        for index, seed in enumerate(seeds[start:], start):
            self._check_cancelled()
            root = f"https://{seed['domain']}/"
            response = await self._request("GET", root)
            if response is not None and response.status_code < 400:
                page_url = str(response.url)
                await self._discover_page(
                    response.text, page_url, "company", seed, root, selected_sources,
                )
                pages = self._career_pages(response.text, page_url, seed["domain"] or "")[:3]
                for page in pages:
                    self._check_cancelled()
                    child = await self._request("GET", page)
                    if child is not None and child.status_code < 400:
                        await self._discover_page(
                            child.text, str(child.url), "company", seed, page_url, selected_sources,
                        )
            self._checkpoint(company_index=index + 1)
            if (index + 1) % 100 == 0:
                self._log("info", "company", "company progress", completed=index + 1, total=len(seeds))

    async def _discover_page(
        self, page: str, page_url: str, method: str, seed: dict[str, str | None], referrer: str,
        selected_sources: set[str] | None = None,
    ) -> None:
        for url in self._urls_in_html(page, page_url):
            found = extract(url)
            if found and (selected_sources is None or found[0] in selected_sources):
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
        selected_sources = self._selected_sources(run)
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
                        await self._discover_page(
                            capture.text, str(capture.url), "wayback", seed, original, selected_sources,
                        )
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
            raise KeyscanError("unable to retrieve Common Crawl collection list")
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
            selected_sources = self._selected_sources(run)
            rules = [rule for rule in RULES if selected_sources is None or rule.source in selected_sources]
            for rule in rules:
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
            candidate_id = await self._validation_queue.get()
            try:
                # A cancelled run still drains, so `queue.join()` cannot hang.
                if self._cancelled or candidate_id in self._validating:
                    continue
                candidate = self._pending_validation(candidate_id, selected_sources)
                if candidate is None:
                    continue
                self._validating.add(candidate_id)
                try:
                    validation, job_count, details = await self._probe(candidate)
                    self._set_validation(candidate_id, validation, job_count, details)
                finally:
                    self._validating.discard(candidate_id)
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
                    candidate_id=candidate_id, error=f"{type(exc).__name__}: {exc}",
                )
            finally:
                self._validation_queue.task_done()

    def _pending_validation(
        self, candidate_id: int, selected_sources: set[str] | None,
    ) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM candidates
                   WHERE id = ? AND decision = 'pending'
                     AND (last_validated_at IS NULL OR last_seen_at > last_validated_at
                          OR validation = 'inconclusive')""",
                (candidate_id,),
            ).fetchone()
        candidate = _row(row)
        if candidate is None:
            return None
        if selected_sources is not None and candidate["source"] not in selected_sources:
            return None
        return candidate

    async def _validate(self, run: dict[str, Any]) -> None:
        """Sweep whatever streaming validation did not already cover.

        There is deliberately no cursor here.  Ids are assigned during
        discovery but validated out of order, so `id > last` would skip
        candidates; the freshness predicate alone is exact, and it makes both
        a resumed run and a `--methods validate` backlog drain pick up exactly
        the unvalidated remainder.
        """
        self._phase("validate")
        with self._connect() as con:
            candidates = con.execute(
                """SELECT id, source FROM candidates
                   WHERE decision = 'pending'
                     AND (last_validated_at IS NULL OR last_seen_at > last_validated_at OR validation = 'inconclusive')
                   ORDER BY id""",
            ).fetchall()
        selected_sources = self._selected_sources(run)
        # Discovery records a vendor's tokens consecutively, so probing in id
        # order would park every worker on one host behind `_request`'s
        # per-host lock.  Round-robin across sources keeps them on different
        # hosts and lets the pool actually run concurrently.
        by_source: dict[str, list[int]] = {}
        for row in candidates:
            if selected_sources is None or row["source"] in selected_sources:
                by_source.setdefault(row["source"], []).append(int(row["id"]))
        pending = [
            candidate_id
            for group in zip_longest(*by_source.values())
            for candidate_id in group
            if candidate_id is not None
        ]
        self._log(
            "info", "validate", "validating candidates",
            total=len(pending), sources=len(by_source),
        )
        if self._validation_queue is None:
            # No worker pool (a direct call outside `_run`): probe serially.
            for candidate_id in pending:
                self._check_cancelled()
                candidate = self._pending_validation(candidate_id, selected_sources)
                if candidate is None:
                    continue
                validation, job_count, details = await self._probe(candidate)
                self._set_validation(candidate_id, validation, job_count, details)
            return
        # Reuse the streaming workers so a large backlog drains concurrently
        # instead of queueing behind one per-host lock.
        for candidate_id in pending:
            self._check_cancelled()
            self._validation_queue.put_nowait(candidate_id)

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
            response = await self._request(
                rule.method, endpoint, json_body=rule.body, headers=rule.headers,
            )
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
                payload = response.json()
            except ValueError:
                return "inconclusive", None, {"responses": responses, "reason": "invalid_json"}
            count = _count_at_path(payload, rule.count_path) if rule.count_path else _job_count(payload)
            if count is None:
                return "inconclusive", None, {"responses": responses, "reason": "unrecognized_json"}
            details: dict[str, Any] = {"responses": responses}
            company_name = _company_name(payload)
            if company_name:
                details.update(company_name=company_name, company_name_source="probe_json")
            elif rule.name_url:
                name, name_responses = await self._probe_name(rule, fields)
                details["name_responses"] = name_responses
                if name:
                    details.update(company_name=name, company_name_source="metadata_json")
            return "live", count, details
        if rule.probe_kind == "xml":
            if any(marker not in response.text for marker in rule.xml_required_markers):
                return "inconclusive", None, {"responses": responses, "reason": "missing_xml_marker"}
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
        details = {"responses": responses, "html_fingerprint": fingerprint}
        company_name = _html_company_name(response.text)
        if company_name:
            details.update(company_name=company_name, company_name_source="probe_html")
        return "live", None, details

    async def _probe_name(
        self, rule: Rule, fields: dict[str, str],
    ) -> tuple[str | None, list[tuple[str, int | None]]]:
        responses: list[tuple[str, int | None]] = []
        for template in (rule.name_url, *rule.name_fallbacks):
            endpoint = template.format(**fields)
            response = await self._request("GET", endpoint)
            status = response.status_code if response is not None else None
            responses.append((endpoint, status))
            if response is None or status in {404, 410}:
                continue
            if status != 200:
                break
            try:
                name = _company_name(response.json())
            except ValueError:
                name = None
            if name:
                return name, responses
        return None, responses

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
            payload = response.json()
        except ValueError:
            return "inconclusive", None, {"responses": responses, "reason": "invalid_json"}
        count = _job_count(payload)
        if count is None:
            return "inconclusive", None, {"responses": responses, "reason": "unrecognized_json"}
        details: dict[str, Any] = {
            "responses": responses, "_comeet_adapter_token": f"{board_id}/{api_token}",
        }
        company_name = _company_name(payload)
        if company_name:
            details.update(company_name=company_name, company_name_source="probe_json")
        return "live", count, details

    async def _html_control(self, rule: Rule, fields: dict[str, str]) -> str | None:
        key = rule.source
        if key in self._controls:
            return self._controls[key]
        control_fields = dict(fields)
        invalid = "keyscan-token-does-not-exist-9f4d2a"
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
