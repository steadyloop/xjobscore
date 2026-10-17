"""The Scheduler board, built once and shared by every tab.

Building the board means one row per feed -- 112k rows, ~2 s CPU and ~300 MB
of Python objects -- on the event loop the scheduler polls from. Each tab
asked for it every 30-60 s and each request rebuilt it, so a few open tabs
kept the process at ~90% CPU. Now a build runs at most once per TTL, only
while someone is looking, and the rows are dropped as soon as they are
serialized: what stays is one gzip blob per vendor (~14 MB in all, untracked
by the GC), each vendor's per-status rollups, and a small search index.
"""

from __future__ import annotations

import asyncio
import gzip
from array import array
from bisect import bisect_right
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import orjson

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


# Status lines a vendor's pull-down shows, worst first. `unknown` covers a
# degraded PostgreSQL; `disabled` is listed by scheduling but never produced.
STATES = ("backing_off", "stale", "never_polled", "paused", "healthy", "unknown")


def _bucket() -> dict[str, Any]:
    return {
        "count": 0,
        "interval_min": None,
        "interval_max": None,
        "due_min": None,
        "last_min": None,
        "last_max": None,
        "jobs": None,
        "errors": None,
    }


def _low(current: Any, value: Any) -> Any:
    return value if current is None or (value is not None and value < current) else current


def _high(current: Any, value: Any) -> Any:
    return value if current is None or (value is not None and value > current) else current


def _add(bucket: dict[str, Any], row: dict[str, Any], telemetry: bool) -> None:
    policy = row.get("policy") or {}
    bucket["count"] += 1
    interval = policy.get("interval_s")
    bucket["interval_min"] = _low(bucket["interval_min"], interval)
    bucket["interval_max"] = _high(bucket["interval_max"], interval)
    # ISO-8601 UTC strings from one formatter compare in time order.
    if policy.get("enabled") is not False:
        bucket["due_min"] = _low(bucket["due_min"], policy.get("due_at"))
    bucket["last_min"] = _low(bucket["last_min"], policy.get("last_poll_at"))
    bucket["last_max"] = _high(bucket["last_max"], policy.get("last_poll_at"))
    if telemetry:
        spark = row.get("spark") or []
        bucket["jobs"] = (bucket["jobs"] or 0) + (spark[-1] if spark else 0)
        errors = (row.get("telemetry") or {}).get("error_count_7d") or 0
        bucket["errors"] = (bucket["errors"] or 0) + errors


def _state(row: dict[str, Any]) -> str:
    return row["sched"]["status"]["state"]


def _find(body: bytes, ref: str) -> dict[str, Any] | None:
    start = body.find(b'{"feed_ref":"' + ref.encode() + b'"')
    if start < 0:
        return None
    end = body.find(_ROW_START, start)
    if end < 0:
        end = body.rindex(b"]")
    return orjson.loads(body[start:end])


@dataclass(frozen=True, slots=True)
class Board:
    """Every scheduled feed, one gzip blob per vendor, plus what the page
    shows before anyone opens a vendor: counts and per-status rollups.

    Rows stay in `scheduler_rows` order (worst status first), so a status's
    page is a slice of its vendor's list. The search index is each row's
    lowercase "company key", newline-joined, so a fleet search scans bytes
    and parses only the vendors that match.
    """

    by_source: dict[str, bytes]
    summary: dict[str, dict[str, Any]]
    search_text: dict[str, bytes]
    line_starts: dict[str, array]
    feed_count: int
    counts: dict[str, int]
    telemetry_available: bool

    @classmethod
    def build(cls, rows: list[dict[str, Any]], *, telemetry_available: bool) -> "Board":
        grouped: dict[str, list[dict[str, Any]]] = {}
        counts: dict[str, int] = {}
        for row in rows:
            grouped.setdefault(row["source"], []).append(row)
            state = _state(row)
            counts[state] = counts.get(state, 0) + 1
        by_source, summary, search_text, line_starts = {}, {}, {}, {}
        for source, members in grouped.items():
            buckets: dict[str, dict[str, Any]] = {}
            text = bytearray()
            starts = array("I")
            for row in members:
                state = _state(row)
                _add(buckets.setdefault(state, _bucket()), row, telemetry_available)
                starts.append(len(text))
                label = f"{row.get('company_name') or ''} {row['key']}"
                text += label.casefold().replace("\n", " ").encode() + b"\n"
            by_source[source] = gzip.compress(orjson.dumps(members), compresslevel=1, mtime=0)
            summary[source] = {
                "counts": {state: bucket["count"] for state, bucket in buckets.items()},
                "buckets": [
                    {"state": state, **buckets[state]} for state in STATES if state in buckets
                ],
            }
            search_text[source] = bytes(text)
            line_starts[source] = starts
        return cls(
            by_source, summary, search_text, line_starts,
            len(rows), counts, telemetry_available,
        )

    def rows(self, source: str) -> list[dict[str, Any]] | None:
        """One vendor's rows, parsed; None for a vendor not on the board."""
        blob = self.by_source.get(source)
        return None if blob is None else orjson.loads(gzip.decompress(blob))

    def row(self, ref: str, source: str | None = None) -> dict[str, Any] | None:
        """One feed's row, or None. With `source`, only that vendor is read."""
        if not _REF.fullmatch(ref):
            return None
        sources = [source] if source is not None else list(self.by_source)
        for name in sources:
            blob = self.by_source.get(name)
            if blob is not None and (found := _find(gzip.decompress(blob), ref)) is not None:
                return found
        return None

    def search(self, query: str, limit: int) -> dict[str, tuple[int, list[int]]]:
        """Vendors whose rows contain `query`: (match count, first row indexes)."""
        needle = query.casefold().encode()
        found: dict[str, tuple[int, list[int]]] = {}
        if not needle:
            return found
        for source, text in self.search_text.items():
            starts = self.line_starts[source]
            lines: list[int] = []
            seen = -1
            position = text.find(needle)
            while position >= 0:
                line = bisect_right(starts, position) - 1
                if line != seen:
                    lines.append(line)
                    seen = line
                position = text.find(needle, starts[line + 1] if line + 1 < len(starts) else len(text))
            if lines:
                found[source] = (len(lines), lines[:limit])
        return found
