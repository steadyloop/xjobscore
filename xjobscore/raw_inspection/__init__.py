"""Harvest every source adapter once and dump what came back, as JSON.

One file per registered source in `sources.SOURCES`, written to
`xjobscore/raw_inspection/harvested/<source>.json` the moment that adapter's
own fetch returns -- the directory fills in progressively rather than all at
the end. What it captures per source is `Record.envelope` plus
`Record.items[0]`, which are precisely the two things that stop being visible
once silver has normalized them away.

A second pass runs `sources.parser(source)` -- sync and pure, no net, no DB,
per its own contract -- directly on that same harvested item, and writes
`xjobscore/raw_inspection/parsed/<source>.json`. This is the vendor-shape
mapping only: `normalize_title` and company-alias resolution happen later, in
`silver.replay` against a live connection, so what lands here is the `Posting`
(or `Target`/`Label`/`Fact`) exactly as the source module built it -- title
un-normalized, `company_raw` unresolved. No adapter is touched by either pass.

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
from xjobscore.records import Feed, Item
from xjobscore.sources import catalog

__all__ = [
    "DEFAULT_HARVESTED",
    "DEFAULT_PARSED",
    "Target",
    "UNRUNNABLE",
    "inspect_all",
    "inspect_one",
    "targets",
]

# Both are siblings of this package's own files, resolved from __file__ so the
# runner writes to the same place whatever the working directory is.
_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_HARVESTED = _PACKAGE_DIR / "harvested"
DEFAULT_PARSED = _PACKAGE_DIR / "parsed"

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


def _parse_result(target: Target, harvested: dict, item: Item | None, envelope: dict) -> dict:
    """Run the registered parser on one harvested item, DB-free.

    `sources.parser` raises `UnknownSource` for the one harvest-only source
    (`workatastartup`); everything else that reaches this function already has
    a harvest to explain first, via `harvested`.
    """
    base = {"source": target.source}

    if not harvested["ok"]:
        reason = harvested.get("skipped") or (harvested.get("error") or {}).get("class")
        return base | {"ok": False, "skipped": f"harvest did not succeed: {reason}"}
    if item is None:
        return base | {"ok": False, "skipped": "no item harvested to parse"}

    try:
        parse = sources.parser(target.source)
    except sources.UnknownSource:
        return base | {"ok": False, "skipped": f"no parser registered for '{target.source}'"}

    try:
        canonicals = parse(target.feed, item, envelope)
    except Exception as exc:
        return base | {"ok": False, "error": {"class": type(exc).__name__, "detail": str(exc)}}

    return base | {
        "ok": True,
        "canonical_count": len(canonicals),
        # tagged with its dataclass name -- Canonical is a bare union
        # (records.py:243) with no discriminant field of its own.
        "canonicals": [{"type": type(c).__name__, **asdict(c)} for c in canonicals],
    }


async def inspect_one(
    target: Target,
    net: Net,
    harvested_dir: Path,
    parsed_dir: Path | None,
    timeout_s: float,
) -> dict:
    """Harvest one source and write its file before returning.

    The write happens here, inside the task, rather than in the caller after a
    gather -- that is what makes the output directory fill in as each adapter
    finishes instead of all at once at the end. Parsing (when `parsed_dir` is
    given) runs in the same task, directly on the in-memory `Item` -- no
    round-trip through the harvested JSON.

    Every exception becomes a recorded error, exactly as run.poll:135 does it.
    Six adapters issue one detail request per listed row (json-api-configs.py
    DETAIL_A/DETAIL_B) against a 1 req/s per-host throttle, so `timeout_s` is
    load-bearing: without it a single board can hold the run open for an hour.
    """
    harvested_path = harvested_dir / f"{target.source}.json"

    if target.skip is not None:
        harvested = {"source": target.source, "ok": False, "skipped": target.skip}
        _write(harvested_path, harvested)
        if parsed_dir is not None:
            _write(parsed_dir / f"{target.source}.json", _parse_result(target, harvested, None, {}))
        return harvested

    started = time.monotonic()
    item: Item | None = None
    envelope: dict = {}
    try:
        harvest = sources.get(target.source)
        record = await asyncio.wait_for(harvest(target.feed, net), timeout_s)
    except Exception as exc:
        harvested = _identity(target, started) | {
            "ok": False,
            "error": {"class": type(exc).__name__, "detail": str(exc)},
            "envelope": None,
            "item": None,
        }
    else:
        item = record.items[0] if record.items else None
        envelope = record.envelope
        harvested = _identity(target, started) | {
            "ok": True,
            "http_status": record.http_status,
            "truncated": record.truncated,
            "validators": record.validators,
            "item_count": len(record.items),
            "envelope": envelope,
            # items[0] only: a fixture, not an archive. An empty items list is
            # itself the interesting result for several adapters.
            "item": asdict(item) if item is not None else None,
        }

    _write(harvested_path, harvested)
    if parsed_dir is not None:
        _write(parsed_dir / f"{target.source}.json", _parse_result(target, harvested, item, envelope))
    return harvested


async def inspect_all(
    plan: Sequence[Target],
    *,
    harvested_dir: Path = DEFAULT_HARVESTED,
    parsed_dir: Path | None = DEFAULT_PARSED,
    user_agent: str = "xjobscore/0.1",
    concurrency: int = 4,
    timeout_s: float = 300.0,
    on_done: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Run the whole plan under one Net, bounded by `concurrency`.

    Distinct sources are distinct hosts, so the per-host token bucket inside
    Net is not the limit here -- the semaphore is, and it is deliberately small
    because this is a debugging tool pointed at other people's boards. Pass
    `parsed_dir=None` to skip the parse pass entirely.
    """
    harvested_dir.mkdir(parents=True, exist_ok=True)
    if parsed_dir is not None:
        parsed_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)

    async with Net(user_agent=user_agent) as net:

        async def run_one(target: Target) -> dict:
            async with semaphore:
                result = await inspect_one(target, net, harvested_dir, parsed_dir, timeout_s)
            if on_done is not None:
                on_done(result)
            return result

        return list(await asyncio.gather(*(run_one(t) for t in plan)))
