"""Small async boundary around OneShot's SQLite database.

SQLite work is performed on worker threads so request handlers never block the
event loop.  Connections are short lived, writes are serialized, and callers
that need atomic multi-statement changes can use :meth:`Database.transaction`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from xjobscore.records import Feed

__all__ = ["Database", "DatabaseError", "WriteResult", "utc_now"]

T = TypeVar("T")
Params = Sequence[object] | dict[str, object]


class DatabaseError(RuntimeError):
    """The local database could not be initialized or migrated."""


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Useful, connection-independent metadata from a write."""

    rowcount: int
    lastrowid: int | None


def utc_now() -> str:
    """Return a stable, lexically sortable UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Database:
    """WAL SQLite store with migration and async transaction support.

    The class deliberately does not expose a long-lived connection.  A fresh
    connection per operation avoids cross-thread ownership bugs, while the
    async write lock prevents local writers from needlessly contending.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        migrations_dir: str | Path | None = None,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.path = Path(path) if str(path) != ":memory:" else Path(":memory:")
        self.migrations_dir = (
            Path(migrations_dir)
            if migrations_dir is not None
            else Path(__file__).resolve().parent.parent / "migrations"
        )
        self.busy_timeout_ms = busy_timeout_ms
        self._initialize_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._initialized = False
        self._memory_uri = f"file:oneshot-{id(self)}?mode=memory&cache=shared"
        self._keeper: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        in_memory = self.path == Path(":memory:")
        connection = sqlite3.connect(
            self._memory_uri if in_memory else self.path,
            uri=in_memory,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        return connection

    def _initialize_sync(self) -> sqlite3.Connection | None:
        if self.path != Path(":memory:"):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        keeper = self._connect()
        try:
            keeper.execute("PRAGMA journal_mode = WAL")
            keeper.execute("PRAGMA synchronous = NORMAL")
            keeper.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row[0]
                for row in keeper.execute("SELECT version FROM schema_migrations").fetchall()
            }
            migration_paths = sorted(self.migrations_dir.glob("[0-9][0-9][0-9]_*.sql"))
            if not migration_paths:
                raise DatabaseError(f"no migrations found in {self.migrations_dir}")
            for migration in migration_paths:
                try:
                    version = int(migration.name.split("_", 1)[0])
                except ValueError as exc:
                    raise DatabaseError(f"invalid migration name: {migration.name}") from exc
                if version in applied:
                    continue
                name = migration.name.replace("'", "''")
                applied_at = utc_now().replace("'", "''")
                script = (
                    "BEGIN IMMEDIATE;\n"
                    + migration.read_text(encoding="utf-8")
                    + f"\nINSERT INTO schema_migrations(version, name, applied_at) "
                    f"VALUES ({version}, '{name}', '{applied_at}');\nCOMMIT;"
                )
                try:
                    keeper.executescript(script)
                except sqlite3.Error as exc:
                    if keeper.in_transaction:
                        keeper.execute("ROLLBACK")
                    raise DatabaseError(f"migration {migration.name} failed: {exc}") from exc
            return keeper if self.path == Path(":memory:") else None
        finally:
            if self.path != Path(":memory:"):
                keeper.close()

    async def initialize(self) -> None:
        """Create the database and apply every pending migration once."""
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            self._keeper = await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    async def close(self) -> None:
        """Release the anchor used by a shared in-memory database."""
        keeper, self._keeper = self._keeper, None
        self._initialized = False
        if keeper is not None:
            await asyncio.to_thread(keeper.close)

    async def _ready(self) -> None:
        if not self._initialized:
            await self.initialize()

    def _read_sync(self, sql: str, params: Params, one: bool) -> Any:
        connection = self._connect()
        try:
            cursor = connection.execute(sql, params)
            row = cursor.fetchone() if one else cursor.fetchall()
            if one:
                return dict(row) if row is not None else None
            return [dict(item) for item in row]
        finally:
            connection.close()

    async def fetch_one(self, sql: str, params: Params = ()) -> dict[str, Any] | None:
        """Return one row as a plain dictionary, or ``None``."""
        await self._ready()
        return await asyncio.to_thread(self._read_sync, sql, params, True)

    async def fetch_all(self, sql: str, params: Params = ()) -> list[dict[str, Any]]:
        """Return all rows as connection-independent dictionaries."""
        await self._ready()
        return await asyncio.to_thread(self._read_sync, sql, params, False)

    def _execute_sync(self, sql: str, params: Params) -> WriteResult:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(sql, params)
            result = WriteResult(cursor.rowcount, cursor.lastrowid)
            connection.execute("COMMIT")
            return result
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    async def execute(self, sql: str, params: Params = ()) -> WriteResult:
        """Execute and commit one statement."""
        await self._ready()
        async with self._write_lock:
            return await asyncio.to_thread(self._execute_sync, sql, params)

    def _executemany_sync(self, sql: str, rows: Iterable[Params]) -> WriteResult:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.executemany(sql, rows)
            result = WriteResult(cursor.rowcount, cursor.lastrowid)
            connection.execute("COMMIT")
            return result
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    async def executemany(self, sql: str, rows: Iterable[Params]) -> WriteResult:
        """Execute a batch in one write transaction."""
        await self._ready()
        materialized = list(rows)
        async with self._write_lock:
            return await asyncio.to_thread(self._executemany_sync, sql, materialized)

    def _transaction_sync(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = operation(connection)
            connection.execute("COMMIT")
            return result
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    async def transaction(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """Run a synchronous callback atomically on a worker thread.

        The callback must not retain the connection or return a cursor/Row.
        Plain values and dictionaries are safe return values.
        """
        await self._ready()
        async with self._write_lock:
            return await asyncio.to_thread(self._transaction_sync, operation)

    async def upsert_feed(self, feed: Feed) -> None:
        """Mirror catalog metadata without replacing engine-owned state."""
        import json

        now = utc_now()
        await self.execute(
            """
            INSERT INTO feeds (
                id, source, feed_key, company_id, company_name, config_json,
                state_json, enabled, complete, closes, credentialed,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source = excluded.source,
                feed_key = excluded.feed_key,
                company_id = excluded.company_id,
                company_name = excluded.company_name,
                config_json = excluded.config_json,
                enabled = excluded.enabled,
                complete = excluded.complete,
                closes = excluded.closes,
                credentialed = excluded.credentialed,
                updated_at = excluded.updated_at
            """,
            (
                feed.id, feed.source, feed.key, feed.company_id, feed.company_name,
                json.dumps(feed.config, sort_keys=True, separators=(",", ":")),
                json.dumps(feed.state, sort_keys=True, separators=(",", ":")),
                int(feed.enabled), int(feed.complete), int(feed.closes),
                int(feed.credentialed), now, now,
            ),
        )

    async def upsert_feeds(self, feeds: Iterable[Feed]) -> None:
        """Mirror many catalog feeds in one transaction.

        Catalogs contain thousands of endpoints, so startup must not pay one
        connection and commit per feed.
        """
        import json

        now = utc_now()
        rows = [
            (
                feed.id,
                feed.source,
                feed.key,
                feed.company_id,
                feed.company_name,
                json.dumps(feed.config, sort_keys=True, separators=(",", ":")),
                json.dumps(feed.state, sort_keys=True, separators=(",", ":")),
                int(feed.enabled),
                int(feed.complete),
                int(feed.closes),
                int(feed.credentialed),
                now,
                now,
            )
            for feed in feeds
        ]
        if not rows:
            return

        def mirror(connection: sqlite3.Connection) -> None:
            # A feed removed from the current catalog must not remain as a
            # clickable endpoint that the gateway can no longer resolve.
            connection.execute("UPDATE feeds SET enabled = 0, updated_at = ?", (now,))
            connection.executemany(
                """
                INSERT INTO feeds (
                    id, source, feed_key, company_id, company_name, config_json,
                    state_json, enabled, complete, closes, credentialed,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source = excluded.source,
                    feed_key = excluded.feed_key,
                    company_id = excluded.company_id,
                    company_name = excluded.company_name,
                    config_json = excluded.config_json,
                    enabled = excluded.enabled,
                    complete = excluded.complete,
                    closes = excluded.closes,
                    credentialed = excluded.credentialed,
                    updated_at = excluded.updated_at
                """,
                rows,
            )

        await self.transaction(mirror)
