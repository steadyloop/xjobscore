"""Feed surface for the inspector: PostgreSQL when reachable, TOML otherwise."""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any

from xjobscore import sources
from xjobscore.inspector import config
from xjobscore.records import Feed
from xjobscore.sources import docxtract, json_api
from xjobscore.registry_loader import (
    _feed_id,
    entries as registry_entries,
    feed_ref,
    feeds as registry_feeds,
    source_types,
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


# `feed` is the operator's inventory; `token` is where a feed's company lives
# (they are joined loosely on (ats, token) = (source, key), with no foreign
# key). This is the same join store._FEED_SELECT uses, so the inspector resolves
# a feed exactly the way the pipeline does.
_PG_FEEDS = """
    SELECT f.source, f.key, f.enabled, f.interval_s, f.etag, f.res_hash,
           f.strikes, f.due_at, f.hold_until,
           t.company_id, c.name AS company_name
    FROM feed f
    LEFT JOIN token t ON t.ats = f.source AND t.token = f.key
    LEFT JOIN company c ON c.id = t.company_id
    ORDER BY f.source, f.key
"""


class CatalogDB:
    """Read-only feeds, preferring what PostgreSQL actually polls.

    PostgreSQL owns feed inventory; the TOML registry is the fallback for a
    standalone inspector with no database, and is still the only source of
    adapter `config`, so a row present in both is merged rather than replaced.
    Materializing the registry parses ~54k TOML entries (~5s), so both catalogs
    are cached and indexed by id and feed_ref.

    Feed ids stay `_feed_id(source, key)` in both modes rather than becoming
    PostgreSQL's `feed.id`. They are an inspector-internal handle -- every
    mutating route addresses a feed by (source, key) -- so deriving them keeps
    a URL meaning the same thing whether or not the database is up.
    """

    _TTL_S = 300.0

    def __init__(self, policy: Any | None = None) -> None:
        self._cached_at = float("-inf")
        self._feeds: list[Feed] = []
        self._by_id: dict[int, Feed] = {}
        self._by_ref: dict[str, Feed] = {}
        self._lock = threading.Lock()
        self._refreshing = False
        self._policy = policy
        self._pg: list[Feed] | None = None
        self._pg_cached_at = float("-inf")

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

    def invalidate(self) -> None:
        """Force the next ``all_feeds()`` call to re-query PostgreSQL.

        Called after a mutation (e.g. deleting a feed) that must be visible
        immediately rather than waiting out ``_TTL_S``. Leaves ``_pg`` itself
        alone so a re-query that fails right after still falls back to the
        last-known list, same as ordinary TTL expiry.
        """
        self._pg_cached_at = float("-inf")

    async def _postgres_feeds(self) -> list[Feed] | None:
        """Feeds as PostgreSQL sees them, or None when it is unreachable."""
        if self._policy is None:
            return None
        if self._pg is not None and time.monotonic() - self._pg_cached_at <= self._TTL_S:
            return self._pg
        pool = await self._policy.pool_or_none()
        if pool is None:
            return None
        try:
            rows = await pool.fetch(_PG_FEEDS)
        except Exception:
            # Degraded mode is the whole point of the fallback: a database that
            # is up but mid-migration must not empty the Feeds page.
            return self._pg
        # `config`, `description` and `implemented` have no columns -- they are
        # registry-owned adapter parameters -- so they are carried over from the
        # TOML entry of the same identity when one exists.
        declared = {(entry.source, entry.key): entry for entry in registry_entries()}
        types = source_types()
        feeds = []
        for row in rows:
            entry = declared.get((row["source"], row["key"]))
            feeds.append(
                Feed(
                    id=_feed_id(row["source"], row["key"]),
                    source=row["source"],
                    key=row["key"],
                    config=entry.config if entry else {},
                    enabled=row["enabled"],
                    company_id=row["company_id"],
                    company_name=row["company_name"],
                    description=entry.description if entry else "",
                    implemented=entry.implemented if entry else True,
                    source_type=types.get(row["source"], "Other"),
                    etag=row["etag"],
                    res_hash=row["res_hash"],
                    interval_s=row["interval_s"],
                    strikes=row["strikes"],
                    due_at=row["due_at"],
                    hold_until=row["hold_until"],
                )
            )
        self._pg = feeds
        self._pg_cached_at = time.monotonic()
        return feeds

    async def _resolve(self) -> list[Feed]:
        postgres = await self._postgres_feeds()
        if postgres is None:
            return list(self._catalog())
        # Adapters whose endpoint is not registry-owned have no feed row and
        # never will; they are local to the inspector in either mode.
        return [*postgres, *_source_feeds()]

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def all_feeds(self) -> list[Feed]:
        return await self._resolve()

    async def get_feed(self, feed_id: int) -> Feed | None:
        return next((f for f in await self._resolve() if f.id == feed_id), None)

    async def get_feed_ref(self, ref: str) -> Feed | None:
        return next(
            (f for f in await self._resolve() if feed_ref(f.source, f.key) == ref), None
        )
