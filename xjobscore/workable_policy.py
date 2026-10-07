"""Workable verification pacing and cooldown shared by local processes.

Polling keeps its existing pace. Every client sharing this SQLite file honors
the same cooldown; verification starts are spaced by at least 2.5 seconds.
Multiple machines must mount the same state only on a filesystem supporting
SQLite locking, or coordinate through a separate distributed rate service.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
import math
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

import httpx

VERIFICATION_INTERVAL_S = 2.5
DEFAULT_COOLDOWN_S = 900.0


class CoolingDown(Exception):
    def __init__(self, remaining: float):
        self.remaining = max(1, math.ceil(remaining))
        super().__init__(f"Workable cooldown: {self.remaining}s remaining")


def matches(url: str) -> bool:
    return urlsplit(url if "://" in url else "https://" + url).hostname == "apply.workable.com"


def verification(url: str, params=None) -> bool:
    query = parse_qs(urlsplit(url).query)
    details = (params or {}).get("details", query.get("details", [""])[-1])
    return matches(url) and str(details).lower() != "true"


class SharedPolicy:
    def __init__(self, path: Path, *, interval: float = VERIFICATION_INTERVAL_S):
        self.path = path
        self.interval = interval

    def _cooldown_until(self) -> float:
        # Scheduling reads must not create policy state or reserve a request.
        if not self.path.is_file():
            return 0.0
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='policy'").fetchone():
                return 0.0
            row = db.execute("SELECT hold_until FROM policy WHERE id=1").fetchone()
            return float(row[0]) if row else 0.0

    async def cooldown_until(self) -> float:
        return await asyncio.to_thread(self._cooldown_until)

    def _transaction(self, *, validation: bool, cooldown: float | None = None) -> float:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY, next_start REAL NOT NULL, hold_until REAL NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO policy VALUES (1, 0, 0)")
            next_start, hold = db.execute("SELECT next_start, hold_until FROM policy WHERE id=1").fetchone()
            now = time.time()
            if cooldown is not None:
                db.execute("UPDATE policy SET hold_until=? WHERE id=1", (max(hold, now + cooldown),))
                return 0.0
            if hold > now:
                raise CoolingDown(hold - now)
            if not validation:
                return 0.0
            if next_start > now:
                return next_start - now
            # Claim only when ready, never reserve future starts that can
            # bunch up after cancellation or an event-loop delay.
            db.execute("UPDATE policy SET next_start=? WHERE id=1", (now + self.interval,))
            return 0.0

    async def acquire(self, *, validation: bool) -> float:
        started = time.monotonic()
        while True:
            wait = await asyncio.to_thread(self._transaction, validation=validation)
            if wait <= 0:
                return time.monotonic() - started
            await asyncio.sleep(wait)

    async def hold(self, seconds: float) -> None:
        await asyncio.to_thread(self._transaction, validation=False, cooldown=seconds)


def policy() -> SharedPolicy:
    configured = os.environ.get("XJOBSCORE_WORKABLE_STATE")
    path = Path(configured) if configured else Path.home() / ".cache/xjobscore/workable-policy.sqlite3"
    return SharedPolicy(path)


async def cooldown_until() -> float:
    """Persisted cooldown deadline (Unix seconds), or zero if none exists."""
    return await policy().cooldown_until()


async def before_request(url: str, *, validation: bool) -> float:
    if not matches(url):
        return 0.0
    return await policy().acquire(validation=validation)


async def note_response(response: httpx.Response) -> bool:
    if response.status_code != 429 or not matches(str(response.url)):
        return False
    # Imported lazily: Net also imports this module.
    from .net import _retry_after_seconds
    delay = _retry_after_seconds(response.headers.get("retry-after"))
    await policy().hold(delay if delay is not None else DEFAULT_COOLDOWN_S)
    return True


def deferred_response(url: str, error: CoolingDown) -> httpx.Response:
    return httpx.Response(429, headers={"retry-after": str(error.remaining)},
                          request=httpx.Request("GET", url))
