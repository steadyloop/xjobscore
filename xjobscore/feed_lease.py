"""Keep a claimed feed owned while a long network harvest is in progress."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, TypeVar

from xjobscore import store
from xjobscore.records import Feed

if TYPE_CHECKING:
    from asyncpg import Pool

T = TypeVar('T')


class FeedLeaseLost(RuntimeError):
    pass


class FeedLease:
    def __init__(self, pool: Pool, feed: Feed, claimed_at: datetime):
        self.pool = pool
        self.feed = feed
        self.duration = max(60, (feed.hold_until - claimed_at).total_seconds()) if feed.hold_until else 0
        self.interval = min(300, self.duration / 3) if self.duration else 0

    async def _renew(self, stopped: asyncio.Event) -> None:
        while not stopped.is_set():
            try:
                await asyncio.wait_for(stopped.wait(), timeout=self.interval)
                return
            except TimeoutError:
                pass
            now = datetime.now(timezone.utc)
            until = now + timedelta(seconds=self.duration)
            async with self.pool.acquire() as conn:
                owned = await store.renew_feed_lease(conn, self.feed, now=now, until=until)
            if not owned:
                raise FeedLeaseLost(f'Feed lease lost: {self.feed.source}:{self.feed.key}')
            self.feed = replace(self.feed, hold_until=until)

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self.feed.hold_until is None:
            return await operation()
        stopped = asyncio.Event()
        harvest = asyncio.create_task(operation())
        renewal = asyncio.create_task(self._renew(stopped))
        try:
            await asyncio.wait({harvest, renewal}, return_when=asyncio.FIRST_COMPLETED)
            if renewal.done():
                # Ownership loss or a failed renewal stops further requests.
                await renewal
            return await harvest
        finally:
            stopped.set()
            if not harvest.done():
                harvest.cancel()
            await asyncio.gather(harvest, return_exceptions=True)
            # Do not cancel an UPDATE in flight: its commit may already have
            # happened. Settle it and retain the new token before finalization.
            # The store query has a ten-second timeout.
            await renewal
