"""Config and the loop. The only module aware of all the others.

Everything else in the tree is deliberately ignorant: a source knows `net` and
`records`, `work` knows nothing at all, `store` knows SQL. This file is where
those become a system, and it is the only place a new wire may be run between
two modules.

One cycle: take the due feeds, harvest each concurrently, route what came back,
reschedule, and reconcile the ones the operator flagged as complete listings.
A feed that raises does not abort the cycle -- it becomes a poll_run row with
status 'error' and a backed-off next_poll_at, which is the difference between
one dead board and a stalled engine.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from xjobscore import router, silver, sources, store, work
from xjobscore.net import Net
from xjobscore.records import Feed
from xjobscore.work import Outcome

if TYPE_CHECKING:
    from asyncpg import Connection

__all__ = ["Config", "cycle", "load_config", "main", "parse_all", "poll"]

# How long the continuous loop naps between cycles when nothing forces --once.
# Not a Config field: the loop cadence is an operational knob, not part of the
# per-run policy the rest of the module reasons about.
_LOOP_INTERVAL_S = 60.0


@dataclass(frozen=True, slots=True)
class Config:
    """Everything the loop needs, resolved once at startup."""

    dsn: str
    user_agent: str
    batch: int = 20  # feeds harvested concurrently per cycle
    once: bool = False  # one cycle then exit, for cron and for tests
    dry_run: bool = False  # harvest and route, write nothing
    parse: bool = False  # run silver.replay over every feed instead of polling


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def load_config(argv: Sequence[str]) -> Config:
    """Flags over environment over defaults. No config file, no framework."""
    parser = argparse.ArgumentParser(prog="xjobscore")
    parser.add_argument("--dsn", default=None)
    parser.add_argument("--user-agent", default=None)
    parser.add_argument("--batch", type=int, default=None)
    parser.add_argument("--once", action="store_true", default=None)
    parser.add_argument("--dry-run", action="store_true", default=None)
    parser.add_argument("--parse", action="store_true", default=None)
    args = parser.parse_args(argv)

    dsn = args.dsn or os.environ.get("XJOBSCORE_DSN")
    if not dsn:
        raise ValueError("no dsn: pass --dsn or set XJOBSCORE_DSN")

    user_agent = args.user_agent or os.environ.get("XJOBSCORE_USER_AGENT") or "xjobscore/0.1"
    batch = args.batch if args.batch is not None else int(os.environ.get("XJOBSCORE_BATCH", "20"))
    once = args.once if args.once is not None else _env_bool("XJOBSCORE_ONCE", False)
    dry_run = args.dry_run if args.dry_run is not None else _env_bool("XJOBSCORE_DRY_RUN", False)
    parse = args.parse if args.parse is not None else _env_bool("XJOBSCORE_PARSE", False)

    return Config(
        dsn=dsn, user_agent=user_agent, batch=batch, once=once, dry_run=dry_run, parse=parse
    )


async def poll(conn: Connection, net: Net, feed: Feed, now: datetime) -> Outcome:
    """Harvest one feed and land what it returns.

        store.start_run
          -> sources.get(feed.source)(feed, net)
          -> router.route(conn, feed, record, run_id)
          -> work.next_state / work.next_poll_at -> store.set_feed_state / set_next_poll
          -> reconcile.run(conn, feed, run_id)   if work.should_reconcile
          -> store.finish_run

    Every exception is caught here and becomes Outcome 'error' plus a finished
    poll_run carrying error_class and error_detail. Nothing propagates: the
    caller is a gather over the whole batch.

    Rescheduling happens on every path including the failing one, or a feed
    that errors stays due forever and is retried in a hot loop.
    """
    run_id = await store.start_run(conn, feed.id)

    outcome: Outcome
    error_class: str | None = None
    error_detail: str | None = None
    record: Record | None = None
    items_listed = 0
    items_new = 0
    items_changed = 0

    try:
        harvest = sources.get(feed.source)
        record = await harvest(feed, net)
        items_listed = len(record.items)

        # Bronze only: record.items land in raw_document via router.route.
        # Parsing a raw item into Posting/Target/Label/Fact (silver) is a
        # separate, not-yet-designed downstream layer. Routed before the
        # outcome branch so a route failure falls into the except below and
        # becomes outcome 'error' -- a poll that could not persist is not
        # 'ok', even if the fetch itself succeeded.
        counts = await router.route(conn, feed, record, run_id)
        items_new = counts.new
        items_changed = counts.changed

        if record.http_status == 304:
            outcome = "not_modified"
        elif record.truncated:
            outcome = "partial"
        else:
            outcome = "ok"
    except Exception as exc:
        outcome = "error"
        error_class = type(exc).__name__
        error_detail = str(exc)

    # record.validators carries etag/last_modified back out of the source, so
    # a conditional GET that matched actually shortens the next poll's body.
    new_state = work.next_state(feed.state, outcome, record.validators if record else {})
    await store.set_feed_state(conn, feed.id, new_state)

    when = work.next_poll_at(feed, outcome, now)
    await store.set_next_poll(conn, feed.id, when)

    if work.should_reconcile(feed, outcome):
        # TODO(reconcile): reconcile.run(conn, feed, run_id) belongs here --
        # it does not exist yet. It would close postings that
        # store.open_postings_missed(conn, feed.id, run_id) returns.
        pass

    await store.finish_run(
        conn,
        run_id,
        status=outcome if outcome != "not_modified" else "ok",
        http_status=record.http_status if record else None,
        items_listed=items_listed,
        items_new=items_new,
        items_changed=items_changed,
        error_class=error_class,
        error_detail=error_detail,
    )
    return outcome


async def cycle(conn: Connection, net: Net, cfg: Config, now: datetime) -> int:
    """One pass over the due feeds. Returns how many were polled.

    Concurrency is bounded by cfg.batch rather than by the number of due feeds,
    and `net` rate-limits per host underneath, so a batch that happens to be
    fifty feeds on one ATS still goes out politely.
    """
    feeds = await store.due_feeds(conn, now, cfg.batch)
    if not feeds:
        return 0

    semaphore = asyncio.Semaphore(cfg.batch)

    async def run_one(feed: Feed) -> None:
        async with semaphore:
            if cfg.dry_run:
                # "harvest and route, write nothing" -- poll() always writes
                # (that is its whole job), so a dry run calls harvest directly
                # and skips poll()/store entirely rather than threading a flag
                # through poll()'s fixed signature.
                try:
                    await sources.get(feed.source)(feed, net)
                except Exception:
                    pass
            else:
                await poll(conn, net, feed, now)

    await asyncio.gather(*(run_one(feed) for feed in feeds))
    return len(feeds)


async def parse_all(conn: Connection, cfg: Config) -> list[tuple[Feed, silver.Counts]]:
    """Replay every feed's current bronze into silver. Not part of `cycle` --
    see silver.py's own docstring for why this runs on a separate schedule
    from the poll rather than inline with `harvest`.
    """
    feeds = await store.all_feeds(conn)
    results = []
    for feed in feeds:
        counts = await silver.replay(conn, feed, dry_run=cfg.dry_run)
        results.append((feed, counts))
    return results


async def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    cfg = load_config(argv if argv is not None else sys.argv[1:])

    conn = await store.connect(cfg.dsn)
    try:
        if cfg.parse:
            for feed, counts in await parse_all(conn, cfg):
                print(
                    f"{feed.source}:{feed.key} "
                    f"postings={counts.postings} unchanged={counts.unchanged} "
                    f"unresolved={counts.unresolved} targets={counts.targets} "
                    f"dropped={counts.dropped}"
                )
            return 0

        async with Net(user_agent=cfg.user_agent) as net:
            await cycle(conn, net, cfg, datetime.now(timezone.utc))
            while not cfg.once:
                await asyncio.sleep(_LOOP_INTERVAL_S)
                await cycle(conn, net, cfg, datetime.now(timezone.utc))
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":  # python -m xjobscore.run --once
    import asyncio
    import sys

    sys.exit(asyncio.run(main(sys.argv[1:])))
