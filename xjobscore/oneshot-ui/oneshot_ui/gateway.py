"""Adapter boundary used by OneShot manual fetches.

The gateway deliberately knows only the source registry.  It owns one shared
``Net`` instance and never imports an individual vendor adapter.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

import asyncpg

from xjobscore import sources
from xjobscore.net import Net
from xjobscore.records import Canonical, Feed, Record

__all__ = ["AdapterGateway", "GatewayResult", "ParserState"]

ParserState = Literal["available", "missing", "error"]


class SimpleCatalog:
    """Feed catalog backed by PostgreSQL."""

    def __init__(self, dsn: str = "postgresql://xjobscore:password@localhost:5432/xjobscore"):
        self.dsn = dsn
        self.conn: asyncpg.Connection | None = None

    async def connect(self) -> None:
        if self.conn is None:
            self.conn = await asyncpg.connect(self.dsn)

    async def disconnect(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def all_feeds(self) -> list[Feed]:
        if self.conn is None:
            return []
        rows = await self.conn.fetch("SELECT id, source, key, enabled FROM feed ORDER BY source, key")
        return [
            Feed(id=row["id"], source=row["source"], key=row["key"],
                 config={}, state={}, enabled=row["enabled"],
                 complete=False, closes=False, credentialed=False,
                 cadence_s=900, next_poll_at=None)
            for row in rows
        ]

    async def get_feed(self, feed_id: int) -> Feed | None:
        if self.conn is None:
            return None
        row = await self.conn.fetchrow("SELECT id, source, key, enabled FROM feed WHERE id = $1", feed_id)
        if row is None:
            return None
        return Feed(id=row["id"], source=row["source"], key=row["key"],
                   config={}, state={}, enabled=row["enabled"],
                   complete=False, closes=False, credentialed=False,
                   cadence_s=900, next_poll_at=None)

    async def get_feed_ref(self, ref: str) -> Feed | None:
        if self.conn is None:
            return None
        try:
            feed_id = int(ref)
            return await self.get_feed(feed_id)
        except ValueError:
            return None

    async def source_coverage_rows(self) -> list[dict[str, object]]:
        """Return source-level coverage from the PostgreSQL source of truth."""
        if self.conn is None:
            return []
        rows = await self.conn.fetch(
            """
            WITH feed_stats AS (
                SELECT
                    f.source,
                    COUNT(*) FILTER (WHERE f.enabled) AS board_count,
                    COUNT(*) FILTER (
                        WHERE f.enabled AND s.last_poll_at IS NOT NULL
                    ) AS polled_count,
                    COUNT(*) FILTER (
                        WHERE f.enabled AND (
                            s.disabled_at IS NOT NULL
                            OR COALESCE(s.strikes, 0) > 0
                            OR (
                                s.due_at < now()
                                AND (s.hold_until IS NULL OR s.hold_until < now())
                            )
                        )
                    ) AS attention_count,
                    MAX(s.last_poll_at) FILTER (WHERE f.enabled) AS last_poll_at
                FROM feed f
                LEFT JOIN schedule s ON s.feed_id = f.id
                GROUP BY f.source
            ),
            posting_stats AS (
                SELECT
                    source,
                    COUNT(*) FILTER (WHERE status = 'active') AS coverage,
                    COUNT(*) FILTER (
                        WHERE first_seen_at >= date_trunc('day', now())
                    ) AS added_today
                FROM job_posting
                GROUP BY source
            )
            SELECT
                f.source,
                f.board_count,
                f.polled_count,
                f.attention_count,
                f.last_poll_at,
                COALESCE(p.coverage, 0) AS coverage,
                COALESCE(p.added_today, 0) AS added_today
            FROM feed_stats f
            LEFT JOIN posting_stats p ON p.source = f.source
            ORDER BY COALESCE(p.coverage, 0) DESC, f.source
            """
        )
        return [dict(row) for row in rows]


@dataclass(frozen=True, slots=True)
class GatewayResult:
    """One source response plus its best-effort canonical parse."""

    feed: Feed
    record: Record
    canonical: tuple[Canonical, ...]
    parser_state: ParserState
    parser_error: str | None = None


class AdapterGateway:
    """Catalog-backed access to registered harvesters and parsers.

    ``connect`` opens the process-wide HTTP pool but performs no requests.
    Fetches occur only through :meth:`fetch`, with a feed chosen by the caller.
    """

    def __init__(
        self,
        catalog: SimpleCatalog | None = None,
        net: Net | None = None,
        *,
        user_agent: str = "xjobscore-oneshot/1",
    ) -> None:
        self.catalog = catalog or SimpleCatalog()
        self.net = net or Net(user_agent=user_agent)
        self._connected = False
        self._lifecycle_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open dependencies without harvesting any feed."""
        async with self._lifecycle_lock:
            if self._connected:
                return
            await self.catalog.connect()
            try:
                await self.net.__aenter__()
            except BaseException:
                await self.catalog.disconnect()
                raise
            self._connected = True

    async def disconnect(self) -> None:
        """Close the shared HTTP pool and catalog."""
        async with self._lifecycle_lock:
            if not self._connected:
                return
            try:
                await self.net.__aexit__(None, None, None)
            finally:
                await self.catalog.disconnect()
                self._connected = False

    async def all_feeds(self) -> list[Feed]:
        """Return metadata only; this never contacts a vendor."""
        return await self.catalog.all_feeds()

    async def get_feed(self, feed_id: int) -> Feed | None:
        """Resolve one catalog feed by its Python-safe integer ID."""
        return await self.catalog.get_feed(feed_id)

    async def get_feed_ref(self, ref: str) -> Feed | None:
        """Resolve one catalog feed by its browser-safe reference."""
        return await self.catalog.get_feed_ref(ref)

    async def source_coverage_rows(self) -> list[dict[str, object]]:
        """Read source-level coverage without contacting any vendor."""
        return await self.catalog.source_coverage_rows()

    async def fetch(self, feed: Feed) -> GatewayResult:
        """Harvest exactly ``feed`` and parse its items when a parser exists."""
        if not self._connected:
            raise RuntimeError("AdapterGateway.connect() must be called before fetch()")
        harvest = sources.get(feed.source)
        record = await harvest(feed, self.net)
        return self.parse(feed, record)

    @staticmethod
    def parse(feed: Feed, record: Record) -> GatewayResult:
        """Parse a captured record without making a network request."""
        try:
            parse = sources.parser(feed.source)
        except sources.UnknownSource:
            return GatewayResult(feed, record, (), "missing", "parser is not registered")

        canonical: list[Canonical] = []
        try:
            for item in record.items:
                canonical.extend(parse(feed, item, record.envelope))
        except Exception as exc:
            return GatewayResult(feed, record, tuple(canonical), "error", str(exc))
        return GatewayResult(feed, record, tuple(canonical), "available")

    async def __aenter__(self) -> AdapterGateway:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()
