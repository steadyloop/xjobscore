"""SQLite persistence for inspector-only fetch results."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from xjobscore.inspector.cache import RunSnapshot


class ResultStore:
    """A small file-backed store containing the latest result per feed.

    The database is independent of PostgreSQL and the production schema. Each
    write replaces the previous result for the same (source, feed_id), so
    persistence grows with the number of feeds rather than every click.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS inspector_run (
                result_id TEXT PRIMARY KEY,
                feed_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                feed_key TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                elapsed_ms REAL NOT NULL,
                status TEXT NOT NULL,
                http_status INTEGER,
                item_count INTEGER NOT NULL,
                raw_size_bytes INTEGER NOT NULL,
                truncated INTEGER NOT NULL,
                force_full INTEGER NOT NULL,
                error_class TEXT,
                error_detail TEXT,
                size_bytes INTEGER NOT NULL,
                envelope_json TEXT NOT NULL,
                network_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE (source, feed_id)
            );
            CREATE TABLE IF NOT EXISTS inspector_item (
                result_id TEXT NOT NULL REFERENCES inspector_run(result_id) ON DELETE CASCADE,
                item_index INTEGER NOT NULL,
                external_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                raw_document_json TEXT NOT NULL,
                PRIMARY KEY (result_id, item_index)
            );
            CREATE INDEX IF NOT EXISTS inspector_run_source_idx
                ON inspector_run (source, finished_at DESC);
            """
        )
        columns = {column[1] for column in conn.execute("PRAGMA table_info(inspector_run)")}
        if "network_json" not in columns:
            conn.execute(
                "ALTER TABLE inspector_run ADD COLUMN network_json TEXT NOT NULL DEFAULT '{}'"
            )
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def save(self, snapshot: RunSnapshot) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_sync, snapshot)

    def _save_sync(self, snapshot: RunSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM inspector_run WHERE source = ? AND feed_id = ?",
                (snapshot.source, snapshot.feed_id),
            )
            conn.execute(
                """
                INSERT INTO inspector_run (
                    result_id, feed_id, source, feed_key, started_at, finished_at,
                    elapsed_ms, status, http_status, item_count, raw_size_bytes,
                    truncated, force_full, error_class, error_detail, size_bytes,
                    envelope_json, network_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.result_id,
                    snapshot.feed_id,
                    snapshot.source,
                    snapshot.key,
                    snapshot.started_at.isoformat(),
                    snapshot.finished_at.isoformat(),
                    snapshot.elapsed_ms,
                    snapshot.status,
                    snapshot.http_status,
                    snapshot.item_count,
                    snapshot.raw_size_bytes,
                    int(snapshot.truncated),
                    int(snapshot.force_full),
                    snapshot.error_class,
                    snapshot.error_detail,
                    snapshot.size_bytes,
                    json.dumps(snapshot.envelope, ensure_ascii=False, default=str),
                    json.dumps(snapshot.network, ensure_ascii=False, default=str),
                ),
            )
            conn.executemany(
                """
                INSERT INTO inspector_item
                    (result_id, item_index, external_id, kind, payload_json, raw_document_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot.result_id,
                        index,
                        item["external_id"],
                        item["kind"],
                        json.dumps(item["payload"], ensure_ascii=False, default=str),
                        json.dumps(item["raw_document"], ensure_ascii=False, default=str),
                    )
                    for index, item in enumerate(snapshot.items)
                ],
            )

    async def load(self) -> list[RunSnapshot]:
        async with self._lock:
            return await asyncio.to_thread(self._load_sync)

    async def load_feed(self, feed_id: int) -> RunSnapshot | None:
        async with self._lock:
            snapshots = await asyncio.to_thread(self._load_sync, feed_id)
            return snapshots[0] if snapshots else None

    async def summary_groups(self) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._summary_groups_sync)

    def _summary_groups_sync(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT result_id, feed_id, source, feed_key, started_at, finished_at,
                       elapsed_ms, status, http_status, item_count, raw_size_bytes,
                       truncated, force_full, error_class, error_detail, size_bytes
                       , network_json
                FROM inspector_run ORDER BY source, finished_at DESC
                """
            ).fetchall()
        columns = (
            "result_id", "feed_id", "source", "key", "started_at", "finished_at",
            "elapsed_ms", "status", "http_status", "item_count", "raw_size_bytes",
            "truncated", "force_full", "error_class", "error_detail", "size_bytes",
            "network_json",
        )
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            summary = dict(zip(columns, row))
            summary["started_at"] = datetime.fromisoformat(summary["started_at"]).isoformat()
            summary["finished_at"] = datetime.fromisoformat(summary["finished_at"]).isoformat()
            summary["truncated"] = bool(summary["truncated"])
            summary["force_full"] = bool(summary["force_full"])
            summary["network"] = json.loads(summary.pop("network_json") or "{}")
            groups.setdefault(summary["source"], []).append(summary)
        return [{"source": source, "results": results} for source, results in groups.items()]

    def _load_sync(self, feed_id: int | None = None) -> list[RunSnapshot]:
        with self._connect() as conn:
            if feed_id is None:
                runs = conn.execute(
                    "SELECT * FROM inspector_run ORDER BY source, finished_at DESC"
                ).fetchall()
            else:
                runs = conn.execute(
                    "SELECT * FROM inspector_run WHERE feed_id = ?",
                    (feed_id,),
                ).fetchall()
            columns = [column[1] for column in conn.execute("PRAGMA table_info(inspector_run)")]
            run_rows = [dict(zip(columns, row)) for row in runs]
            snapshots: list[RunSnapshot] = []
            for row in run_rows:
                item_rows = conn.execute(
                    """
                    SELECT item_index, external_id, kind, payload_json, raw_document_json
                    FROM inspector_item WHERE result_id = ? ORDER BY item_index
                    """,
                    (row["result_id"],),
                ).fetchall()
                items = tuple(
                    {
                        "external_id": item[1],
                        "kind": item[2],
                        "payload": json.loads(item[3]),
                        "raw_document": json.loads(item[4]),
                    }
                    for item in item_rows
                )
                snapshots.append(
                    RunSnapshot(
                        result_id=row["result_id"],
                        feed_id=row["feed_id"],
                        source=row["source"],
                        key=row["feed_key"],
                        started_at=datetime.fromisoformat(row["started_at"]),
                        finished_at=datetime.fromisoformat(row["finished_at"]),
                        elapsed_ms=row["elapsed_ms"],
                        status=row["status"],
                        http_status=row["http_status"],
                        item_count=row["item_count"],
                        raw_size_bytes=row["raw_size_bytes"],
                        truncated=bool(row["truncated"]),
                        force_full=bool(row["force_full"]),
                        error_class=row["error_class"],
                        error_detail=row["error_detail"],
                        items=items,
                        envelope=json.loads(row["envelope_json"]),
                        size_bytes=row["size_bytes"],
                        network=json.loads(row.get("network_json") or "{}"),
                    )
                )
            return snapshots
