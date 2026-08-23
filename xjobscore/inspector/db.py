"""PostgreSQL-backed feed surface for the inspector."""

from __future__ import annotations

import time
from typing import Any

from xjobscore.feed_identity import feed_ref
from xjobscore.records import Feed

__all__ = ["CatalogDB", "feed_ref"]


# `feed` owns the endpoint and its runtime metadata. `token` owns company
# identity. The feed foreign key makes that one relationship mandatory.
_PG_FEEDS = """
    SELECT f.id, f.source, f.key, f.polling AS enabled, f.config,
           f.type, c.description,
           f.interval_s, f.etag, f.res_hash, f.strikes, f.due_at, f.hold_until,
           t.company_id,
           c.name AS company_name
    FROM feed f
    LEFT JOIN token t ON t.ats = f.source AND t.token = f.key
    LEFT JOIN company c ON c.id = t.company_id
    ORDER BY f.source, f.key
"""


def _feed(row: Any) -> Feed:
    return Feed(
        id=row["id"],
        source=row["source"],
        key=row["key"],
        config=dict(row["config"] or {}),
        enabled=row["enabled"],
        company_id=row["company_id"],
        company_name=row["company_name"],
        description=row["description"] or "",
        type=row["type"] or "ATS",
        etag=row["etag"],
        res_hash=row["res_hash"],
        interval_s=row["interval_s"],
        strikes=row["strikes"],
        due_at=row["due_at"],
        hold_until=row["hold_until"],
    )


class CatalogDB:
    """Read feed inventory exclusively from PostgreSQL.

    There is deliberately no local catalog or adapter-generated fallback. A
    feed is an operator-created database row, so an unavailable database means
    the inspector has no feed inventory to display or fetch.
    """

    _TTL_S = 5.0

    def __init__(self, policy: Any | None = None) -> None:
        self._policy = policy
        self._feeds: list[Feed] = []
        self._cached_at = float("-inf")

    def invalidate(self) -> None:
        self._cached_at = float("-inf")

    async def _resolve(self) -> list[Feed]:
        if self._policy is None:
            return []
        if time.monotonic() - self._cached_at <= self._TTL_S:
            return self._feeds
        pool = await self._policy.pool_or_none()
        if pool is None:
            return []
        rows = await pool.fetch(_PG_FEEDS)
        self._feeds = [_feed(row) for row in rows]
        self._cached_at = time.monotonic()
        return self._feeds

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def all_feeds(self) -> list[Feed]:
        return list(await self._resolve())

    async def get_feed(self, feed_id: int) -> Feed | None:
        return next((feed for feed in await self._resolve() if feed.id == feed_id), None)

    async def get_feed_ref(self, ref: str) -> Feed | None:
        return next(
            (feed for feed in await self._resolve() if feed_ref(feed.source, feed.key) == ref),
            None,
        )
