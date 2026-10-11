"""The Scheduler board, built once and shared by every tab.

Building the board means one row per feed -- 112k rows, ~2 s CPU and ~300 MB
of Python objects -- on the event loop the scheduler polls from. Each tab
asked for it every 30-60 s and each request rebuilt it, so a few open tabs
kept the process at ~90% CPU. Now a build runs at most once per TTL, only
while someone is looking, and the rows are dropped as soon as they are
serialized: what stays is one gzip blob (~14 MB, untracked by the GC).
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import orjson
from fastapi import Request
from fastapi.responses import Response

logger = logging.getLogger(__name__)

T = TypeVar("T")

_REF = re.compile(r"[0-9a-f]{64}")
# Every row opens with its feed_ref (service.feed_rows builds it first, and
# orjson keeps insertion order). JSON escapes quotes inside strings, so this
# byte sequence can only ever be a row boundary.
_ROW_START = b',{"feed_ref":"'


class Shared(Generic[T]):
    """One value per `ttl_s`; concurrent callers share a single load.

    `invalidate` makes the next `get` load afresh, and a load that started
    before it is never stored -- so a write followed by a read never sees
    pre-write data. `serve_stale` answers with the previous value while the
    next one loads in the background, for reads too slow to wait on.
    """

    def __init__(
        self, load: Callable[[], Awaitable[T]], *, ttl_s: float, serve_stale: bool = False
    ) -> None:
        self._load = load
        self._ttl_s = ttl_s
        self._serve_stale = serve_stale
        self._value: T | None = None
        self._loaded_at = float("-inf")
        self._generation = 0
        self._task: asyncio.Task[T] | None = None
        self._task_generation = -1

    def invalidate(self) -> None:
        self._generation += 1
        self._loaded_at = float("-inf")

    async def get(self) -> T:
        if time.monotonic() - self._loaded_at < self._ttl_s:
            return self._value  # type: ignore[return-value]
        if self._task is None or self._task_generation != self._generation:
            self._task_generation = self._generation
            self._task = asyncio.create_task(self._refresh(self._generation))
            self._task.add_done_callback(self._log_failure)
        if self._serve_stale and self._value is not None:
            return self._value
        # Shielded: one client disconnecting must not cancel everyone's load.
        return await asyncio.shield(self._task)

    async def _refresh(self, generation: int) -> T:
        try:
            value = await self._load()
            if generation == self._generation:
                self._value, self._loaded_at = value, time.monotonic()
            return value
        finally:
            if self._task_generation == generation:
                self._task = None

    @staticmethod
    def _log_failure(task: asyncio.Task[Any]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.warning("shared load failed: %s", task.exception())


@dataclass(frozen=True, slots=True)
class Board:
    """The `/api/scheduler/feeds` body, gzipped, plus the overview's counts."""

    gz: bytes
    feed_count: int
    counts: dict[str, int]
    telemetry_available: bool

    @classmethod
    def build(cls, payload: dict[str, Any], counts: dict[str, int]) -> "Board":
        """`payload` must end with its "feeds" list, which `row` relies on."""
        assert list(payload)[-1] == "feeds"
        body = orjson.dumps(payload)
        return cls(
            gzip.compress(body, compresslevel=1, mtime=0),
            len(payload["feeds"]),
            counts,
            bool(payload["telemetry_available"]),
        )

    def row(self, ref: str) -> dict[str, Any] | None:
        """One feed's row, or None. ~0.07 s for 112k feeds; nothing is kept."""
        if not _REF.fullmatch(ref):
            return None
        body = gzip.decompress(self.gz)
        start = body.find(b'{"feed_ref":"' + ref.encode() + b'"')
        if start < 0:
            return None
        end = body.find(_ROW_START, start)
        if end < 0:
            end = body.rindex(b"]")
        return orjson.loads(body[start:end])

    def response(self, request: Request) -> Response:
        if "gzip" in request.headers.get("accept-encoding", ""):
            return Response(
                self.gz,
                media_type="application/json",
                headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"},
            )
        return Response(gzip.decompress(self.gz), media_type="application/json")
