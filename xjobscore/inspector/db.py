"""Database-free feed surface for the standalone inspector."""

from __future__ import annotations

import hashlib

from xjobscore import sources
from xjobscore.inspector import config
from xjobscore.records import Feed
from xjobscore.sources import docxtract, json_api
from xjobscore.sources.catalog import (
    entries as catalog_entries,
    feed_ref,
    feeds as catalog_feeds,
)


def _id(source: str) -> int:
    # IDs cross the JSON/JavaScript boundary, so keep them within 2**53 - 1.
    value = int.from_bytes(hashlib.sha256(source.encode()).digest()[:8], "big") & ((1 << 53) - 1)
    return -(value or 1)


def _source_feeds() -> list[Feed]:
    """One local feed per adapter whose endpoint is not catalog-owned.

    JSON API and docxtract adapters need a provider-specific board key (or a
    full URL). Inventing one from the adapter name produces requests such as
    ``https://teamtailor.teamtailor.com/jobs.rss``: a misleading empty/error
    result instead of a useful configuration error. Those adapters therefore
    always get their feeds from the source catalog.
    """
    catalog_sources = {entry.source for entry in catalog_entries()}
    catalog_owned_sources = set(json_api.SPECS) | set(docxtract.SPECS)
    return [
        Feed(
            id=_id(source), source=source, key=source,
            config=config.source_config(source), state={},
            credentialed=source == "workatastartup",
        )
        for source in sorted(
            set(sources.SOURCES) - catalog_owned_sources - catalog_sources
        )
    ]


class CatalogDB:
    """Read-only transient feeds: catalog-backed ATS, local other sources."""

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def all_feeds(self) -> list[Feed]:
        return [*catalog_feeds(), *_source_feeds()]

    async def get_feed(self, feed_id: int) -> Feed | None:
        return next((feed for feed in [*catalog_feeds(), *_source_feeds()] if feed.id == feed_id), None)

    async def get_feed_ref(self, ref: str) -> Feed | None:
        return next(
            (
                feed
                for feed in [*catalog_feeds(), *_source_feeds()]
                if feed_ref(feed.source, feed.key) == ref
            ),
            None,
        )
