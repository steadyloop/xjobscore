"""Harvest every source adapter once and dump what came back, as JSON.

One file per registered source in `sources.SOURCES`, written to
`xjobscore/raw-inspection/<source>.json` the moment that adapter's own fetch
returns -- the directory fills in progressively rather than all at the end.

This is a lens on the bronze boundary and nothing more: no database, no
`sources.parser`, no adapter touched. What it captures per source is
`Record.envelope` plus `Record.items[0]`, which are precisely the two things
that stop being visible once silver has normalized them away. `envelope` in
particular is carried on every bronze row but belongs to no single item, so a
source that puts the company name only at the response root looks identical to
one that omits it entirely until you read the envelope back.

Feeds come from `sources.catalog`, not from `store` -- the catalog exists to be
usable without Postgres, and that is the whole reason this runner is cheap.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from xjobscore import sources
from xjobscore.net import Net
from xjobscore.records import Feed
from xjobscore.sources import catalog

__all__ = [
    "DEFAULT_OUT",
    "Target",
    "UNRUNNABLE",
    "inspect_all",
    "inspect_one",
    "targets",
]

# Sibling of the package, hyphenated, as requested. Resolved from __file__ so
# the runner writes to the same place whatever the working directory is.
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "raw-inspection"

# Catalog feeds are transient and negative-id'd; this one is synthesized rather
# than read, and nothing here ever persists a feed, so the id is inert.
_SYNTHETIC_ID = 0

# Registered sources the catalog cannot supply an endpoint for. Each still gets
# a file holding the reason instead of a result: an absent file reads as "the
# runner forgot", which is the exact confusion this directory exists to remove.
UNRUNNABLE: dict[str, str] = {
    "icims": (
        "catalogs/icims.toml declares the source but carries zero [[feed]] entries"
    ),
    "workatastartup": (
        "harvest requires captured config['headers'] and config['body'] "
        "(sources/planned/workatastartup.py:22); it cannot be driven unattended"
    ),
}


@dataclass(frozen=True, slots=True)
class Target:
    """One source to exercise, or one reason it cannot be exercised."""

    source: str
    feed: Feed | None = None
    skip: str | None = None


def targets(only: Sequence[str] | None = None) -> list[Target]:
    """Every registered source, paired with a representative feed.

    `catalog.feeds()` is sorted by (source, key), so taking the first entry per
    source is deterministic across runs -- the fixture for `ashby` is always
    the same board, and a diff between two runs is a change in the vendor's
    response rather than a change in which vendor got asked.
    """
    first: dict[str, Feed] = {}
    for feed in catalog.feeds():
        first.setdefault(feed.source, feed)

    # janestreet has no catalog file because it has no key worth cataloguing:
    # its URLs are hardcoded in the adapter and only ever overridden through
    # config, so a bare feed is a complete one.
    first.setdefault(
        "janestreet",
        Feed(id=_SYNTHETIC_ID, source="janestreet", key="janestreet", config={}, state={}),
    )

    wanted = set(only) if only else None
    out: list[Target] = []
    for source in sorted(sources.SOURCES):
        if wanted is not None and source not in wanted:
            continue
        feed = first.get(source)
        if feed is None:
            out.append(Target(source, skip=UNRUNNABLE.get(source, "no live catalog feed")))
        else:
            out.append(Target(source, feed=feed))
    return out


def _write(path: Path, payload: dict) -> None:
    """Serialize atomically: a killed run never leaves half a JSON on disk.

    `default=str` is the same escape hatch silver.py:71 uses. Item payloads are
    already `dict | str` by the time a source returns them (records.py:116), so
    nothing here should need it -- but a source that smuggles a datetime into
    an envelope should produce a readable file, not a traceback after the fetch
    has already been paid for.
    """
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _identity(target: Target, started: float) -> dict:
    feed = target.feed
    assert feed is not None  # only called on the runnable path
    return {
        "source": target.source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(time.monotonic() - started, 3),
        "feed": {
            "id": feed.id,
            "key": feed.key,
            "company_id": feed.company_id,
            "company_name": feed.company_name,
            "config": feed.config,
        },
    }


async def inspect_one(target: Target, net: Net, out_dir: Path, timeout_s: float) -> dict:
    """Harvest one source and write its file before returning.

    The write happens here, inside the task, rather than in the caller after a
    gather -- that is what makes the output directory fill in as each adapter
    finishes instead of all at once at the end.

    Every exception becomes a recorded error, exactly as run.poll:135 does it.
    Six adapters issue one detail request per listed row (json-api-configs.py
    DETAIL_A/DETAIL_B) against a 1 req/s per-host throttle, so `timeout_s` is
    load-bearing: without it a single board can hold the run open for an hour.
    """
    path = out_dir / f"{target.source}.json"

    if target.skip is not None:
        result = {"source": target.source, "ok": False, "skipped": target.skip}
        _write(path, result)
        return result

    started = time.monotonic()
    try:
        harvest = sources.get(target.source)
        record = await asyncio.wait_for(harvest(target.feed, net), timeout_s)
    except Exception as exc:
        result = _identity(target, started) | {
            "ok": False,
            "error": {"class": type(exc).__name__, "detail": str(exc)},
            "envelope": None,
            "item": None,
        }
    else:
        result = _identity(target, started) | {
            "ok": True,
            "http_status": record.http_status,
            "truncated": record.truncated,
            "validators": record.validators,
            "item_count": len(record.items),
            "envelope": record.envelope,
            # items[0] only: a fixture, not an archive. An empty items list is
            # itself the interesting result for several adapters.
            "item": asdict(record.items[0]) if record.items else None,
        }

    _write(path, result)
    return result


async def inspect_all(
    plan: Sequence[Target],
    *,
    out_dir: Path = DEFAULT_OUT,
    user_agent: str = "xjobscore/0.1",
    concurrency: int = 4,
    timeout_s: float = 120.0,
    on_done: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Run the whole plan under one Net, bounded by `concurrency`.

    Distinct sources are distinct hosts, so the per-host token bucket inside
    Net is not the limit here -- the semaphore is, and it is deliberately small
    because this is a debugging tool pointed at other people's boards.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)

    async with Net(user_agent=user_agent) as net:

        async def run_one(target: Target) -> dict:
            async with semaphore:
                result = await inspect_one(target, net, out_dir, timeout_s)
            if on_done is not None:
                on_done(result)
            return result

        return list(await asyncio.gather(*(run_one(t) for t in plan)))
