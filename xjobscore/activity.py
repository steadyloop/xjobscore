"""Process-local live telemetry for the System page.

Live state, not history: what the workers are doing right now, what the last
few minutes of polling produced, and the recent server log. All of it lives in
this process's memory because the workers and the API that reads it share one
process (``uv run xjobscore``; the startup advisory lock keeps it to one per
database). History stays in PostgreSQL -- ``poll_log`` and ``system_run``.

Everything here resets on restart, and the readers say so: the poll window
reports how much of its span it has actually observed.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from xjobscore.records import Feed

MINUTES_KEPT = 60
LOG_LINES_KEPT = 500
# Loggers whose lines are noise on the page: httpx logs every vendor request
# (the live fetch log already shows each poll), and uvicorn.access logs the
# page's own refreshes.
QUIET_LOGGERS = ("httpx", "uvicorn.access")


class InFlight:
    """Feeds a worker has claimed and not yet finished polling."""

    def __init__(self) -> None:
        # (source, key) is the feed's identity; ids are not unique across the
        # scripted feeds the cycle tests build.
        self._running: dict[tuple[str, str], float] = {}

    def start(self, feed: Feed) -> None:
        self._running[(feed.source, feed.key)] = time.time()

    def finish(self, feed: Feed) -> None:
        self._running.pop((feed.source, feed.key), None)

    def snapshot(self, now: float | None = None) -> dict[str, list[dict[str, Any]]]:
        """In-flight feeds by source, longest-running first."""
        now = time.time() if now is None else now
        by_source: dict[str, list[dict[str, Any]]] = {}
        for (source, key), started in self._running.items():
            by_source.setdefault(source, []).append(
                {"key": key, "age_s": max(0, int(now - started))}
            )
        for rows in by_source.values():
            rows.sort(key=lambda row: -row["age_s"])
        return by_source


class _Minute:
    __slots__ = ("minute", "polls", "new_postings", "partial", "errors", "feeds_with_new")

    def __init__(self, minute: int) -> None:
        self.minute = minute
        self.polls = 0
        self.new_postings = 0
        self.partial = 0
        self.errors = 0
        self.feeds_with_new: set[int] = set()


class PollWindow:
    """Per-minute counts of recorded polls -- one per ``poll_log`` row."""

    def __init__(self) -> None:
        self.started = time.time()
        self._minutes: deque[_Minute] = deque(maxlen=MINUTES_KEPT)
        # Every poll recorded since start; readers diff it to get a live rate.
        self.total = 0

    def reset(self) -> None:
        self.started = time.time()
        self._minutes.clear()
        self.total = 0

    def record(
        self, feed_id: int, status: str, new_postings: int, now: float | None = None
    ) -> None:
        now = time.time() if now is None else now
        minute = int(now // 60)
        if not self._minutes or self._minutes[-1].minute != minute:
            self._minutes.append(_Minute(minute))
        bucket = self._minutes[-1]
        self.total += 1
        bucket.polls += 1
        bucket.new_postings += new_postings
        if status == "partial":
            bucket.partial += 1
        elif status == "error":
            bucket.errors += 1
        if new_postings > 0:
            bucket.feeds_with_new.add(feed_id)

    def summary(self, minutes: int = 15, now: float | None = None) -> dict[str, Any]:
        """Totals over the last ``minutes``, or since start when that is shorter."""
        now = time.time() if now is None else now
        oldest = int(now // 60) - minutes + 1
        buckets = [bucket for bucket in self._minutes if bucket.minute >= oldest]
        feeds_with_new: set[int] = set()
        for bucket in buckets:
            feeds_with_new |= bucket.feeds_with_new
        span_s = minutes * 60
        observed_s = int(now - self.started)
        return {
            "window_s": min(span_s, observed_s),
            "since_start": observed_s < span_s,
            "polled": sum(bucket.polls for bucket in buckets),
            "with_new": len(feeds_with_new),
            "new_postings": sum(bucket.new_postings for bucket in buckets),
            "partial": sum(bucket.partial for bucket in buckets),
            "errors": sum(bucket.errors for bucket in buckets),
        }


class LogBuffer(logging.Handler):
    """The last few hundred log records, minus the per-request noise."""

    def __init__(self, capacity: int = LOG_LINES_KEPT) -> None:
        super().__init__(level=logging.INFO)
        self._lines: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._seq = 0
        # Sequence numbers restart with the process; the boot id tells a reader
        # that its cursor belongs to a previous run.
        self.boot = f"{time.time():.6f}"
        # Handlers may be called from threads (uvicorn, asyncpg callbacks);
        # the sequence number must stay strictly increasing.
        self._guard = threading.Lock()
        self.setFormatter(logging.Formatter())

    def filter(self, record: logging.LogRecord) -> bool:
        return not any(
            record.name == name or record.name.startswith(name + ".")
            for name in QUIET_LOGGERS
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            exc = self.formatter.formatException(record.exc_info) if record.exc_info else None
        except Exception:
            self.handleError(record)
            return
        with self._guard:
            self._seq += 1
            self._lines.append(
                {
                    "seq": self._seq,
                    "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                    "level": record.levelname,
                    "name": record.name,
                    "msg": message,
                    "exc": exc,
                }
            )

    def since(self, seq: int = 0) -> dict[str, Any]:
        with self._guard:
            lines = [line for line in self._lines if line["seq"] > seq]
            return {"boot": self.boot, "lines": lines, "last_seq": self._seq}


INFLIGHT = InFlight()
POLLS = PollWindow()
LOGS = LogBuffer()
