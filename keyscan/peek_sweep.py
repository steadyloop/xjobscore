"""Bulk `peek`: (ats, key) rows in, one JSONL line of titles each out.

Resumable -- every completed board is appended immediately and skipped on a
rerun, so an interrupted sweep costs only the boards it had not reached.
Concurrency is per host, not global: one slow vendor must not stall the rest
and must not be hammered either.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .peek import HEADERS, peek
from .rules import BY_SOURCE


# Host per vendor, resolved once: the gate is keyed by host so that vendors
# sharing one (or a vendor keyed under two labels) share a single semaphore.
HOSTS = {ats: urlparse(rule.probe_url).netloc for ats, rule in BY_SOURCE.items()}


def _done(out: Path) -> set[tuple[str, str]]:
    """The (ats, key) pairs already written, so a rerun skips them."""
    seen = set()
    if out.exists():
        # Split on newline alone: `splitlines()` also breaks on U+2028 and
        # friends, which real job titles contain, and a board whose row was
        # torn in half never registers as done and is refetched every run.
        for line in out.read_text().split("\n"):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            seen.add((row["ats"], row["key"]))
    return seen


async def sweep(
    pairs, out: Path, *, per_host: int = 4, timeout: float = 20.0,
    on_row=None,
) -> int:
    """Peek every pair not already in ``out``. Returns the number fetched."""
    todo = [p for p in pairs if p not in _done(out)]
    gates: dict[str, asyncio.Semaphore] = defaultdict(
        lambda: asyncio.Semaphore(per_host)
    )
    lock = asyncio.Lock()
    fetched = 0

    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers=HEADERS,
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
    ) as client:
        with out.open("a") as sink:

            async def one(ats: str, key: str) -> None:
                nonlocal fetched
                async with gates[HOSTS.get(ats, ats)]:
                    try:
                        row = {"ats": ats, "key": key,
                               "titles": await peek(ats, key, client)}
                    except (httpx.HTTPError, KeyError, ValueError) as exc:
                        row = {"ats": ats, "key": key, "titles": [],
                               "error": type(exc).__name__}
                async with lock:
                    sink.write(json.dumps(row, ensure_ascii=False) + "\n")
                    sink.flush()
                    fetched += 1
                    if on_row:
                        on_row(fetched, len(todo), row)

            await asyncio.gather(*(one(*p) for p in todo))
    return fetched
