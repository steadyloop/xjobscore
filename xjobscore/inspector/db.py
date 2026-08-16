"""Database-free feed surface for the standalone inspector."""

from __future__ import annotations

import hashlib
import threading
import time

from xjobscore import sources
from xjobscore.inspector import config
from xjobscore.records import Feed
from xjobscore.sources import docxtract, json_api
from xjobscore.sources.registry.loader import (
    entries as registry_entries,
    feed_ref,
    feeds as registry_feeds,
)


def _id(source: str) -> int:
    # IDs cross the JSON/JavaScript boundary, so keep them within 2**53 - 1.
    value = int.from_bytes(hashlib.sha256(source.encode()).digest()[:8], "big") & ((1 << 53) - 1)
    return -(value or 1)


def _source_feeds() -> list[Feed]:
    """One local feed per adapter whose endpoint is not registry-owned.

    JSON API and docxtract adapters need a provider-specific board key (or a
    full URL). Inventing one from the adapter name produces requests such as
    ``https://teamtailor.teamtailor.com/jobs.rss``: a misleading empty/error
    result instead of a useful configuration error. Those adapters therefore
    always get their feeds from the source registry.
    """
    registry_sources = {entry.source for entry in registry_entries()}
    registry_owned_sources = set(json_api.SPECS) | set(docxtract.SPECS)
    return [
        Feed(
            id=_id(source), source=source, key=source,
            config=config.source_config(source),
        )
        for source in sorted(
            set(sources.SOURCES) - registry_owned_sources - registry_sources
        )
    ]


class CatalogDB:
    """Read-only transient feeds: registry-backed sources and local others.

    Materializing the registry parses ~54k TOML entries (~5s), so the catalog
    is cached and indexed by id and feed_ref. Restarting (or waiting out the
    TTL) picks up registry edits.
    """

    _TTL_S = 300.0

    def __init__(self) -> None:
        self._cached_at = float("-inf")
        self._feeds: list[Feed] = []
        self._by_id: dict[int, Feed] = {}
        self._by_ref: dict[str, Feed] = {}
        self._lock = threading.Lock()
        self._refreshing = False

    def _install(self, feeds: list[Feed]) -> None:
        self._feeds = feeds
        self._by_id = {feed.id: feed for feed in feeds}
        self._by_ref = {feed_ref(feed.source, feed.key): feed for feed in feeds}
        self._cached_at = time.monotonic()

    def _catalog(self) -> list[Feed]:
        """Stale-while-revalidate: only the very first call pays the parse.

        The startup warmup runs this in a worker thread; afterwards a stale
        cache is served immediately while a daemon thread rebuilds, so the
        event loop never blocks on TOML parsing.
        """
        with self._lock:
            if not self._feeds and self._cached_at == float("-inf"):
                self._install([*registry_feeds(), *_source_feeds()])
            elif (
                time.monotonic() - self._cached_at > self._TTL_S
                and not self._refreshing
            ):
                self._refreshing = True
                threading.Thread(target=self._refresh, daemon=True).start()
            return self._feeds

    def _refresh(self) -> None:
        try:
            feeds = [*registry_feeds(), *_source_feeds()]  # parse outside the lock
            with self._lock:
                self._install(feeds)
        finally:
            self._refreshing = False

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def all_feeds(self) -> list[Feed]:
        return list(self._catalog())

    async def get_feed(self, feed_id: int) -> Feed | None:
        self._catalog()
        return self._by_id.get(feed_id)

    async def get_feed_ref(self, ref: str) -> Feed | None:
        self._catalog()
        return self._by_ref.get(ref)
