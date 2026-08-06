"""Adapter boundary used by OneShot manual fetches.

The gateway deliberately knows only the source registry.  It owns one shared
``Net`` instance and never imports an individual vendor adapter.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from xjobscore import sources
from xjobscore.inspector.db import CatalogDB
from xjobscore.net import Net
from xjobscore.records import Canonical, Feed, Record

__all__ = ["AdapterGateway", "GatewayResult", "ParserState"]

ParserState = Literal["available", "missing", "error"]


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
        catalog: CatalogDB | None = None,
        net: Net | None = None,
        *,
        user_agent: str = "xjobscore-oneshot/1",
    ) -> None:
        self.catalog = catalog or CatalogDB()
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
