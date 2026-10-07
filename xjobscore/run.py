"""Pipeline primitives and explicit repair/debug commands.

The normal long-lived process is :mod:`xjobscore.runtime`. This module keeps
the proven fetch/Bronze/Silver implementation it composes, plus deliberately
finite maintenance operations such as replay, reconciliation, and dry runs.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg

from xjobscore import activity, coverage_snapshot, gold, router, silver, sources, store, work, workable_policy
from xjobscore.net import DEFAULT_USER_AGENT, Net
from xjobscore.records import Feed
from xjobscore.work import Outcome

if TYPE_CHECKING:
    from asyncpg import Connection, Pool
    from xjobscore.pipeline import GoldCoordinator, GoldResult

logger = logging.getLogger(__name__)

__all__ = [
    "Config",
    "CycleResult",
    "cycle",
    "drain_source",
    "due_lanes",
    "fetch_all",
    "load_config",
    "main",
    "parse_all",
    "poll",
    "reconcile",
    "run_enrichments",
]


@dataclass(frozen=True, slots=True)
class Config:
    dsn: str
    user_agent: str
    # Seconds a cycle keeps *claiming* feeds. Not a feed count: a batch of 20
    # was 20 slots blocked on their slowest member, and 20 of anything is a
    # different amount of work per vendor (0.17s for a Greenhouse board,
    # 11s for a Workday one).
    cycle_budget_s: float = 30.0
    dry_run: bool = False
    parse: bool = False
    fetch_all: bool = False
    concurrency: int = 8
    lease_seconds: int = 3600
    enrich: str | None = None
    reconcile: bool = False


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _load_dotenv() -> None:
    """Read KEY=VALUE from ``.env`` so a bare invocation finds the DSN.

    Deliberately duplicated from ``inspector/config.py`` rather than imported:
    the core pipeline does not depend on the inspector, and this is ten lines.
    ``setdefault`` semantics mean a real environment variable always wins, so
    running under a process manager that already exports the DSN is unaffected.
    """
    repo_root = Path(__file__).resolve().parent.parent
    for candidate in (Path.cwd() / ".env", repo_root / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def load_config(argv: Sequence[str]) -> Config:
    _load_dotenv()
    parser = argparse.ArgumentParser(prog="xjobscore")
    parser.add_argument("--dsn")
    parser.add_argument("--user-agent")
    parser.add_argument(
        "--cycle-budget-s",
        type=float,
        help="seconds to keep claiming due feeds; in-flight polls finish; default: 30",
    )
    parser.add_argument("--lease-seconds", type=int)
    parser.add_argument("--dry-run", action="store_true", default=None)
    parser.add_argument("--parse", action="store_true", default=None)
    parser.add_argument(
        "--fetch-all",
        action="store_true",
        default=None,
        help="poll every currently claimable feed, ignoring its due time",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        help="maximum simultaneous polls for --fetch-all; default: 8",
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        default=None,
        help="queue missing postings for the independent link-check worker",
    )
    parser.add_argument(
        "--enrich",
        nargs="?",
        const="all",
        choices=("all", *(step.name for step in gold.steps())),
        metavar="NAME",
        help="run posting enrichments (excluding dedup), or one named Gold step",
    )
    args = parser.parse_args(argv)

    dsn = args.dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        raise ValueError("no dsn: pass --dsn or set DATABASE_URL")
    enrich = args.enrich or os.environ.get("XJOBSCORE_ENRICH") or None
    known_enrichments = {"all", *(step.name for step in gold.steps())}
    if enrich is not None and enrich not in known_enrichments:
        choices = ", ".join(step.name for step in gold.steps())
        parser.error(f"unknown enrichment {enrich!r}; choose {choices}")
    concurrency = (
        args.concurrency
        if args.concurrency is not None
        else int(os.environ.get("XJOBSCORE_FETCH_ALL_CONCURRENCY", "8"))
    )
    if concurrency < 1:
        parser.error("--concurrency must be at least 1")
    cycle_budget_s = (
        args.cycle_budget_s
        if args.cycle_budget_s is not None
        else float(os.environ.get("XJOBSCORE_CYCLE_BUDGET_S", "30"))
    )
    if cycle_budget_s <= 0:
        parser.error("--cycle-budget-s must be positive")
    return Config(
        dsn=dsn,
        user_agent=args.user_agent or os.environ.get("XJOBSCORE_USER_AGENT") or DEFAULT_USER_AGENT,
        cycle_budget_s=cycle_budget_s,
        lease_seconds=args.lease_seconds
        if args.lease_seconds is not None
        else int(os.environ.get("XJOBSCORE_LEASE_SECONDS", "3600")),
        dry_run=args.dry_run if args.dry_run is not None else _env_bool("XJOBSCORE_DRY_RUN", False),
        parse=args.parse if args.parse is not None else _env_bool("XJOBSCORE_PARSE", False),
        fetch_all=args.fetch_all
        if args.fetch_all is not None
        else _env_bool("XJOBSCORE_FETCH_ALL", False),
        concurrency=concurrency,
        enrich=enrich,
        reconcile=args.reconcile
        if args.reconcile is not None
        else _env_bool("XJOBSCORE_RECONCILE", False),
    )


def _status_of(exc: BaseException) -> int | None:
    """The HTTP status a failure carried, if it carried one.

    ``NetError`` is the only exception that knows one, and it is ``None`` there
    too whenever the request never got an answer. Read defensively: a source is
    free to raise anything at all.
    """
    status = getattr(exc, "status", None)
    return status if isinstance(status, int) else None


async def _append_poll_log(conn: asyncpg.Connection, **row) -> None:
    """Write the poll's ``poll_log`` row and count it in the live window, so
    the System page's numbers match the table row for row. ``row`` is passed
    through untouched: it is exactly ``store.append_poll_log``'s keywords."""
    await store.append_poll_log(conn, **row)
    activity.POLLS.record(row["feed_id"], row["status"], row.get("new_postings", 0))


async def _finish_failure(
    pool: Pool,
    feed: Feed,
    now: datetime,
    outcome: Outcome,
    *,
    new_postings: int = 0,
    status_code: int | None = None,
) -> None:
    """Release a failed claim and record the attempt.

    ``new_postings`` is zero for a genuine error, which never reached silver. A
    truncated fetch did reach it and may have created rows, so the partial path
    passes its real count rather than pretending the poll produced nothing.

    ``status_code`` is whatever HTTP status the failure carried, and ``None``
    when it carried none -- a transport failure, or a fault raised before the
    request. Diagnostic only: `outcome` alone still decides strikes and backoff.
    """
    due_at = work.next_due_at(feed.interval_s, feed.strikes, outcome, now)
    async with pool.acquire() as conn:
        if await store.finish_schedule_failure(conn, feed, now=now, due_at=due_at):
            await _append_poll_log(
                conn,
                feed_id=feed.id,
                polled_at=now,
                status=outcome,
                new_postings=new_postings,
                status_code=status_code,
            )
            # Checked only on a 404, and only after the row above is written,
            # so this poll is itself the third strike rather than a prelude to
            # one. A 404 from a job board is not a transient fault the way a
            # 429 or a 503 is: the board slug does not resolve, and retrying it
            # daily forever is the state this replaces.
            if status_code == 404:
                # Guarded for the same reason the prior-item read above is:
                # this is new surface a working poll never depended on. A fault
                # here (a DB hiccup, not a data problem) should cost the drop,
                # not turn an ordinary failed poll into an unhandled exception
                # that takes the rest of the cycle with it. The feed keeps its
                # strike and its backoff either way, so the next poll retries
                # the drop with nothing lost.
                try:
                    await _drop_if_gone(conn, feed)
                except Exception:
                    logger.exception(
                        "gone-check failed source=%s", store.source_instance(feed)
                    )
        else:
            logger.warning("lost schedule lease while failing %s", store.source_instance(feed))


async def _drop_if_gone(conn: Connection, feed: Feed) -> bool:
    """Drop a feed whose board has answered 404 ``GONE_STRIKES`` times running.

    Dropped, not muted: the `feed` row goes and `poll_log` cascades with it,
    while `token`, `company`, bronze, and postings all survive. The
    `feed_exited` row `store.drop_feed` writes to `token_log` is the only trace
    that outlives the delete, so the count is logged here where it is still
    known.
    """
    strikes = await store.gone_strikes(conn, feed.id)
    if strikes < store.GONE_STRIKES:
        return False
    dropped = await store.drop_feed(conn, feed.source, feed.key)
    if dropped is None:
        return False
    logger.warning(
        "dropped %s after %d consecutive 404s", store.source_instance(feed), strikes
    )
    return True


async def poll(
    pool: Pool,
    net: Net,
    feed: Feed,
    now: datetime,
    *,
    coordinator: GoldCoordinator | None = None,
) -> Outcome:
    """Fetch one claimed feed and carry a changed snapshot through silver."""
    source = store.source_instance(feed)
    changes = silver.ChangeSet()
    stage_ms: dict[str, int] = {}
    stage_counts: dict[str, object] = {}
    system_run_id: int | None = None
    if coordinator is not None:
        try:
            system_run_id = await coordinator.begin_feed(pool, feed)
        except Exception:
            # Telemetry is evidence about work, never a prerequisite for it.
            logger.exception("could not start system run source=%s", source)

    async def finish_system_run(
        outcome: Outcome,
        *,
        gold_result: GoldResult | None = None,
        error_stage: str | None = None,
        error: str | None = None,
    ) -> Outcome:
        if coordinator is not None and system_run_id is not None:
            try:
                await coordinator.finish_feed(
                    pool,
                    system_run_id,
                    outcome=outcome,
                    changes=changes,
                    gold_result=gold_result,
                    stages=stage_ms,
                    counts=stage_counts,
                    error_stage=error_stage,
                    error=error,
                )
            except Exception:
                logger.exception("could not finish system run source=%s", source)
        return outcome
    fetch_started = time.monotonic()
    prior = None
    if feed.source in sources.REVISION_FIELDS:
        # Read-then-harvest, not inside harvest: the source boundary bars
        # harvest from touching the database (sources/__init__.py), so the one
        # caller that already holds `pool` loads what a stale-safe detail
        # request could skip and hands it in as data, not a capability.
        #
        # A separate try, not folded into the harvest one below: this read is
        # new surface a working poll never depended on before. A failure here
        # (a transient DB hiccup, not a data problem -- `router.prior_items`
        # already treats a malformed row as a miss) should cost a fully-cold
        # harvest, not the whole poll.
        try:
            async with pool.acquire() as conn:
                prior = await router.prior_items(conn, source)
        except Exception:
            logger.exception("prior-item read failed source=%s", source)
    try:
        record = await sources.reusing(feed.source, prior, now=now)(feed, net)
    except Exception as exc:
        stage_ms["fetch"] = round((time.monotonic() - fetch_started) * 1000)
        status = _status_of(exc)
        if status == 404:
            # A 404 board is an answer, not a hiccup: the vendor deleted it,
            # and there is no fault here to read a traceback for. Recorded the
            # same as any failure -- `_drop_if_gone` is what acts on it, after
            # GONE_STRIKES consecutive answers -- but logged as one line, so a
            # real harvest bug still stands out in the log.
            logger.warning("board gone (404) source=%s", source)
        elif status == 429:
            # A vendor refusal (including a locally enforced cooldown) is an
            # expected response. Keep the failure record without a traceback.
            logger.warning("rate limited (429) source=%s: %s", source, exc)
        else:
            logger.exception("harvest failed source=%s", source)
        await _finish_failure(pool, feed, now, "error", status_code=status)
        return await finish_system_run(
            "error", error_stage="fetch", error=f"{type(exc).__name__}: {exc}"
        )
    stage_ms["fetch"] = round((time.monotonic() - fetch_started) * 1000)
    stage_counts["fetch"] = {
        "items": len(record.items),
        "http_status": record.http_status,
        "truncated": record.truncated,
        "reused_items": record.reused_item_count,
        "detail_fetched": record.detail_fetched_count,
    }

    if record.http_status == 304 or record.count_unchanged:
        due_at = work.next_due_at(feed.interval_s, feed.strikes, "unchanged", now)
        async with pool.acquire() as conn, conn.transaction():
            await store.touch_source_postings(conn, source, now, feed_id=feed.id)
            ok = await store.finish_schedule_success(
                conn,
                feed,
                now=now,
                due_at=due_at,
                etag=record.etag if record.etag is not None else feed.etag,
                res_hash=None,
                changed=False,
                last_modified=record.last_modified if record.last_modified is not None else feed.last_modified,
            )
            if ok:
                await _append_poll_log(
                    conn,
                    feed_id=feed.id,
                    polled_at=now,
                    status="unchanged",
                    new_postings=0,
                )
        if not ok:
            logger.warning("lost schedule lease after 304 source=%s", source)
        return await finish_system_run("unchanged")

    prepared = router.prepare(feed, record)
    response_hash = router.snapshot_hash(prepared)
    changed = response_hash != feed.res_hash

    if not changed and not record.truncated:
        due_at = work.next_due_at(feed.interval_s, feed.strikes, "ok", now)
        async with pool.acquire() as conn, conn.transaction():
            await store.touch_source_postings(conn, source, now, feed_id=feed.id)
            ok = await store.finish_schedule_success(
                conn,
                feed,
                now=now,
                due_at=due_at,
                etag=record.etag,
                res_hash=response_hash,
                changed=False,
                list_total=record.list_total,
                full_listing=record.full_listing,
                last_modified=record.last_modified,
                reused_detail=record.reused_item_count > 0,
            )
            # "ok", not "unchanged": the content is indeed unchanged, but
            # "unchanged" is reserved for the 304 path where no body crossed
            # the wire. Here the vendor sent a full body and only the snapshot
            # hash matched, so the fetch cost is the same as any "ok" poll.
            # Keep the status matching what poll() returns for this path.
            if ok:
                await _append_poll_log(
                    conn,
                    feed_id=feed.id,
                    polled_at=now,
                    status="ok",
                    new_postings=0,
                )
                # This is still a complete listing. The scoped touch leaves
                # previously absent postings alone; record any missing stamp
                # that has not landed yet, even when the snapshot is stable.
                await store.mark_missing_postings(
                    conn, source, now, listed_keys=[item.external_id for item in record.items]
                )
        if not ok:
            logger.warning("lost schedule lease after unchanged poll source=%s", source)
        return await finish_system_run("ok")

    bronze_started = time.monotonic()
    try:
        async with pool.acquire() as conn, conn.transaction():
            stored = await router.persist(conn, feed, prepared)
    except Exception as exc:
        stage_ms["bronze"] = round((time.monotonic() - bronze_started) * 1000)
        logger.exception("bronze persist failed source=%s", source)
        await _finish_failure(pool, feed, now, "error")
        return await finish_system_run(
            "error", error_stage="bronze", error=f"{type(exc).__name__}: {exc}"
        )
    stage_ms["bronze"] = round((time.monotonic() - bronze_started) * 1000)
    stage_counts["bronze"] = {
        "rows": len(stored),
        "inserted": sum(item.created for item in stored),
    }

    silver_started = time.monotonic()
    try:
        async with pool.acquire() as conn, conn.transaction():
            counts = await silver.process(
                conn, feed, stored, record.envelope, seen_at=now, changes=changes
            )
            changes.posting_ids.update(counts.changed_posting_ids)
            changes.company_ids.update(counts.affected_company_ids)
            if coordinator is not None:
                await coordinator.mark_changed(conn, changes, current=now)
    except Exception as exc:
        stage_ms["silver"] = round((time.monotonic() - silver_started) * 1000)
        # No status_code: the fetch already succeeded, so the HTTP leg is not
        # what failed here and recording its 200 would misdirect the reader.
        logger.exception("silver projection failed source=%s", source)
        await _finish_failure(pool, feed, now, "error")
        return await finish_system_run(
            "error", error_stage="silver",
            error=f"{type(exc).__name__}: {exc}",
        )
    stage_ms["silver"] = round((time.monotonic() - silver_started) * 1000)
    stage_counts["silver"] = {
        "postings": counts.postings,
        "created": counts.created,
        "unchanged": counts.unchanged,
        "unresolved": counts.unresolved,
        "unsupported": counts.unsupported,
        "secondhand": counts.secondhand,
        "unparseable": counts.unparseable,
        "minted": counts.minted,
    }

    if counts.unresolved or counts.unsupported or counts.secondhand or counts.unparseable:
        logger.info(
            "silver skips source=%s unresolved=%d unsupported=%d secondhand=%d "
            "unparseable=%d",
            source,
            counts.unresolved,
            counts.unsupported,
            counts.secondhand,
            counts.unparseable,
        )

    outcome: Outcome = "partial" if record.truncated else "ok"

    async def enrich_changed() -> GoldResult | None:
        if coordinator is None or not changes.posting_ids:
            return None
        # The source lease is released before this starts. Version stamps make
        # a crash here recoverable by catch-up without refetching the source.
        gold_started = time.monotonic()
        try:
            return await coordinator.enrich_changed(pool, changes)
        finally:
            stage_ms["gold"] = round((time.monotonic() - gold_started) * 1000)
            coordinator.notify_changed()

    async def finish_with_gold(outcome: Outcome) -> Outcome:
        if coordinator is None or not changes.posting_ids:
            return await finish_system_run(outcome)

        async def gold_then_finish() -> None:
            gold_result = await enrich_changed()
            await finish_system_run(outcome, gold_result=gold_result)

        # Handed off, not awaited: the worker goes straight back to its
        # vendor's queue (see pipeline.DEFERRED_GOLD_CONCURRENCY), and the
        # feed's system run closes when its Gold does.
        await coordinator.defer(gold_then_finish)
        return outcome

    if outcome == "partial":
        await _finish_failure(
            pool, feed, now, outcome,
            new_postings=counts.created,
            status_code=record.http_status,
        )
        return await finish_with_gold(outcome)

    due_at = work.next_due_at(feed.interval_s, feed.strikes, "ok", now)
    # Commit the snapshot, successful observation, and disappearance markers
    # together. If stamping fails, the next claim must retry the full poll.
    async with pool.acquire() as conn, conn.transaction():
        ok = await store.finish_schedule_success(
            conn,
            feed,
            now=now,
            due_at=due_at,
            etag=record.etag,
            res_hash=response_hash,
            changed=changed,
            list_total=record.list_total,
            full_listing=record.full_listing,
            last_modified=record.last_modified,
            reused_detail=record.reused_item_count > 0,
            # Unresolved postings were dropped, not stored. Keeping the
            # snapshot would let the next poll skip silver and drop them
            # forever, even once the company becomes resolvable.
            forget_snapshot=counts.unresolved > 0,
        )
        # Only when the lease held: a reclaimed worker polled this feed too and
        # records its own row, and two rows for one poll double-count lambda.
        if ok:
            await _append_poll_log(
                conn,
                feed_id=feed.id,
                polled_at=now,
                status="ok",
                new_postings=counts.created,
            )
            # A whole listing was just projected (truncated fetches took the
            # partial branch above), so anything it did not list is missing.
            await store.mark_missing_postings(
                conn, source, now, listed_keys=[item.external_id for item in record.items]
            )
    if not ok:
        logger.warning("lost schedule lease after success source=%s", source)
    return await finish_with_gold("ok")


@dataclass(frozen=True, slots=True)
class CycleResult:
    polled: int = 0
    # False when some vendor still had feeds due when the budget ran out, which
    # is the caller's signal to run another cycle now rather than wait a tick.
    drained: bool = True
    by_source: dict[str, int] = field(default_factory=dict)


async def _poll_tracked(
    pool: Pool,
    net: Net,
    feed: Feed,
    claimed_at: datetime,
    coordinator: GoldCoordinator | None,
) -> None:
    """`poll`, visible to the System page's worker slots while it runs."""
    activity.INFLIGHT.start(feed)
    try:
        if coordinator is None:
            await poll(pool, net, feed, claimed_at)
        else:
            await poll(pool, net, feed, claimed_at, coordinator=coordinator)
    finally:
        activity.INFLIGHT.finish(feed)


async def _source_cooling(source: str) -> bool:
    return source == "workable" and await workable_policy.cooldown_until() > time.time()


async def due_lanes(conn: Connection, now: datetime) -> list[str]:
    """Every lane (`sources.lanes`) with at least one claimable feed."""
    due = []
    for source in await store.due_sources(conn, now):
        if await _source_cooling(source):
            continue
        if not sources.partitions(source):
            due.append(source)
            continue
        values = await store.due_key_parts(conn, now, source, sources.partition_index(source))
        due.extend(sorted({sources.partition_lane(source, value) for value in values}))
    return due


async def drain_source(
    pool: Pool,
    net: Net,
    cfg: Config,
    source: str,
    *,
    coordinator: GoldCoordinator | None = None,
    accepting: Callable[[], bool] | None = None,
) -> int:
    """Poll one lane until its due queue is empty. Returns feeds polled.

    `source` is a lane name (`sources.lanes`): a vendor, or one partition of a
    vendor whose limiter counts per partition (`workday:wd1`).

    The long-running scheduler's unit of work: one lane per vendor, started
    and awaited by nobody else. `cycle` gathers every vendor's workers and so
    cannot start the next round until the slowest in-flight poll anywhere
    finishes -- a single 10-minute Avature poll left Lever idle 96% of six
    hours. A lane has no time budget for the same reason: the only thing that
    ends it is its own empty queue, or `accepting` turning false.
    """
    polled = 0
    vendor, part = sources.lane_filter(source)

    async def worker() -> None:
        nonlocal polled
        while accepting is None or accepting():
            # A sibling poll or another local client can open the shared hold
            # mid-lane. Stop before claiming another feed or adding a strike.
            if await _source_cooling(vendor):
                return
            claimed_at = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                claimed = await store.claim_due_feeds(
                    conn, claimed_at, 1, lease_seconds=cfg.lease_seconds,
                    source=vendor, **({"part": part} if part else {}),
                )
            if not claimed:
                return
            polled += 1
            await _poll_tracked(pool, net, claimed[0], claimed_at, coordinator)

    await asyncio.gather(*(worker() for _ in range(sources.width(source))))
    return polled


async def cycle(
    pool: Pool,
    net: Net,
    cfg: Config,
    now: datetime,
    *,
    coordinator: GoldCoordinator | None = None,
    accepting: Callable[[], bool] | None = None,
) -> CycleResult:
    """Poll every due feed, one worker pool per vendor, within a time budget.

    Each source gets `sources.width(source)` workers, and each worker claims
    ONE due feed of its own source, polls it, and immediately goes back for
    another. Nobody waits on a sibling: a batch-and-gather finishes when its
    slowest member does, so one 11-second Workday poll used to leave nineteen
    Greenhouse slots idle. Per-vendor queues are also what stops a vendor that
    cannot keep up from monopolising the run -- falling behind makes its
    `due_at` older, and one global `ORDER BY due_at` rewards exactly that.

    The budget stops *claiming* only; a poll already in flight runs to
    completion under its lease. Feed records are complete when claimed:
    PostgreSQL owns their adapter configuration, company identity, and source
    metadata.
    """
    deadline = time.monotonic() + cfg.cycle_budget_s
    async with pool.acquire() as conn:
        due = await due_lanes(conn, now)
        # A dry run takes no lease, so nothing would stop a refill loop
        # reselecting the same rows forever: snapshot each queue instead and
        # let the budget bound the work.
        queues = {}
        if cfg.dry_run:
            for lane in due:
                vendor, part = sources.lane_filter(lane)
                queues[lane] = await store.due_feeds(
                    conn, now, source=vendor, **({"part": part} if part else {})
                )

    polled = dict.fromkeys(due, 0)
    exhausted: set[str] = set()

    async def claim_one(source: str, claimed_at: datetime) -> Feed | None:
        vendor, part = sources.lane_filter(source)
        if await _source_cooling(vendor):
            return None
        if cfg.dry_run:
            return queues[source].pop(0) if queues[source] else None
        async with pool.acquire() as conn:
            claimed = await store.claim_due_feeds(
                conn, claimed_at, 1, lease_seconds=cfg.lease_seconds,
                source=vendor, **({"part": part} if part else {}),
            )
        return claimed[0] if claimed else None

    async def worker(source: str) -> None:
        while time.monotonic() < deadline and (
            accepting is None or accepting()
        ):
            # A fresh `now` per claim, not the cycle's: every poll stamps
            # `poll_log.polled_at` and computes `next_due_at` from the time it
            # was claimed, and a 30-second cycle would otherwise backdate them
            # all to its own start.
            claimed_at = datetime.now(timezone.utc)
            feed = await claim_one(source, claimed_at)
            if feed is None:
                exhausted.add(source)
                return
            polled[source] += 1
            if cfg.dry_run:
                # Deliberately cold, not `sources.reusing`: a dry run exists to
                # prove a feed still fetches end to end, and revision reuse
                # would silently skip most of that proof on a warm cache -- the
                # opposite of what "dry run" is for here. Caught per feed so one
                # dead board cannot end the whole proof, as in `fetch_all`.
                try:
                    await sources.get(feed.source)(feed, net)
                except Exception:
                    logger.exception(
                        "dry-run harvest failed source=%s", store.source_instance(feed)
                    )
            else:
                await _poll_tracked(pool, net, feed, claimed_at, coordinator)

    await asyncio.gather(
        *(worker(source) for source in due for _ in range(sources.width(source)))
    )

    if not cfg.dry_run:
        # One read-model refresh per cycle, never one per browser view or feed.
        # It is presentation state, so a failure is logged without turning
        # already-completed harvests into a failed polling cycle.
        try:
            await coverage_snapshot.refresh(pool)
        except Exception:
            logger.exception("coverage snapshot refresh failed")

    by_source = {source: count for source, count in polled.items() if count}
    result = CycleResult(
        polled=sum(polled.values()),
        # Vacuously true when nothing was due at all.
        drained=exhausted == set(due),
        by_source=by_source,
    )
    logger.info(
        "cycle polled=%d drained=%s %s",
        result.polled,
        result.drained,
        " ".join(f"{source}={count}" for source, count in sorted(by_source.items())),
    )
    return result


@dataclass(frozen=True, slots=True)
class FetchAllResult:
    eligible: int = 0
    polled: int = 0
    skipped: int = 0
    errors: int = 0
    partial: int = 0


async def fetch_all(
    pool: Pool,
    net: Net,
    cfg: Config,
    *,
    coordinator: GoldCoordinator | None = None,
) -> FetchAllResult:
    """Poll every currently claimable feed with bounded shared concurrency.

    This is the command-line counterpart to the inspector's Fetch all.  The
    inventory intentionally ignores schedules, but every live task claims its
    individual feed immediately before polling so it cannot override a pause,
    disable, or concurrent worker lease.
    """
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        candidates = await store.fetchable_feeds(conn, now)

    cursor = 0
    cursor_lock = asyncio.Lock()

    async def next_feed() -> Feed | None:
        nonlocal cursor
        async with cursor_lock:
            if cursor == len(candidates):
                return None
            feed = candidates[cursor]
            cursor += 1
            return feed

    async def one(candidate: Feed) -> str:
        if await _source_cooling(candidate.source):
            return "skipped"
        if cfg.dry_run:
            # Cold on purpose -- see the matching comment in `cycle`.
            try:
                await sources.get(candidate.source)(candidate, net)
            except Exception:
                logger.exception("fetch-all harvest failed source=%s", store.source_instance(candidate))
                return "error"
            return "ok"

        claimed_at = datetime.now(timezone.utc)
        async with pool.acquire() as conn:
            claimed = await store.claim_feed(
                conn,
                source=candidate.source,
                key=candidate.key,
                now=claimed_at,
                lease_seconds=cfg.lease_seconds,
            )
        if claimed is None:
            return "skipped"
        if coordinator is None:
            outcome = await poll(pool, net, claimed, claimed_at)
        else:
            outcome = await poll(
                pool, net, claimed, claimed_at, coordinator=coordinator
            )
        return outcome

    outcomes: list[str] = []

    async def worker() -> None:
        while candidate := await next_feed():
            outcomes.append(await one(candidate))

    await asyncio.gather(*(worker() for _ in range(min(cfg.concurrency, len(candidates)))))
    if coordinator is not None:
        # Polls hand their Gold off rather than awaiting it; a one-shot
        # command must not exit with that work still queued.
        await coordinator.drain()
    return FetchAllResult(
        eligible=len(candidates),
        polled=sum(outcome in {"ok", "unchanged"} for outcome in outcomes),
        skipped=outcomes.count("skipped"),
        errors=outcomes.count("error"),
        partial=outcomes.count("partial"),
    )


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    candidates: int = 0
    queued: int = 0


async def reconcile(pool: Pool, net: Net | None, cfg: Config) -> ReconcileResult:
    """Queue missing jobs; the independently controlled worker checks links."""
    from xjobscore.reconcile import enqueue
    async with pool.acquire() as conn, conn.transaction():
        candidates = await enqueue(conn, dry_run=True)
        queued = 0 if cfg.dry_run else await enqueue(conn, priority=1)
    return ReconcileResult(candidates=candidates, queued=queued)


async def parse_all(pool: Pool, cfg: Config) -> list[tuple[Feed, silver.Counts]]:
    async with pool.acquire() as conn:
        feeds = await store.replay_feeds(conn)
        # Snapshot once for the whole run rather than a per-posting alias
        # query: nothing writes aliases along this path, unlike the poll loop,
        # which stays on the per-call lookup.
        aliases = await store.load_alias_map(conn)
    results = []
    seen_at = datetime.now(timezone.utc)
    for feed in feeds:
        async with pool.acquire() as conn, conn.transaction():
            counts = await silver.replay(
                conn,
                feed,
                seen_at=seen_at,
                dry_run=cfg.dry_run,
                aliases=aliases,
            )
        results.append((feed, counts))
    return results


async def run_enrichments(
    pool: Pool, cfg: Config
) -> dict[str, gold.Counts | Exception]:
    """Run selected enrichments, retrying transient PostgreSQL deadlocks."""
    results: dict[str, gold.Counts | Exception] = {}
    selected = None if cfg.enrich == "all" else cfg.enrich
    steps = gold.steps(selected)
    if cfg.enrich == "all":
        # Bare --enrich is the routine per-posting enrichment path. Dedup is a
        # destructive whole-table rebuild and remains available only when it
        # is named explicitly with --enrich dedup.
        steps = tuple(step for step in steps if step.name != "dedup")
    for step in steps:
        try:
            attempt = 0
            while True:
                try:
                    async with pool.acquire() as conn:
                        if step.atomic:
                            async with conn.transaction():
                                outcome = await step.rebuild(conn, dry_run=cfg.dry_run)
                        else:
                            # Each executemany is its own transaction. This keeps
                            # row locks scoped to one batch instead of the entire
                            # posting table scan.
                            outcome = await step.rebuild(conn, dry_run=cfg.dry_run)
                    results[step.name] = outcome
                    break
                except asyncpg.DeadlockDetectedError:
                    attempt += 1
                    delay = min(0.05 * 2 ** (attempt - 1), 2.0)
                    logger.warning(
                        "enrichment deadlocked; retrying name=%s attempt=%d delay=%.2fs",
                        step.name,
                        attempt,
                        delay,
                    )
                    await asyncio.sleep(delay)
        except Exception as exc:  # one step's failure must not sink the layer
            logger.exception("enrichment failed name=%s", step.name)
            results[step.name] = exc
    return results


async def main(argv: Sequence[str] | None = None) -> int:
    cfg = load_config(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(level=os.environ.get("XJOBSCORE_LOG_LEVEL", "INFO"))
    if not any((cfg.fetch_all, cfg.reconcile, cfg.parse, cfg.enrich, cfg.dry_run)):
        print(
            "No maintenance operation selected. Start the complete system with "
            "`uv run xjobscore`; use --help for repair/debug operations.",
            file=sys.stderr,
        )
        return 2
    pool = await store.connect(cfg.dsn)
    try:
        if cfg.fetch_all:
            from xjobscore.pipeline import GoldCoordinator

            async with Net(user_agent=cfg.user_agent) as net:
                result = await fetch_all(
                    pool,
                    net,
                    cfg,
                    coordinator=None if cfg.dry_run else GoldCoordinator(),
                )
            print(
                f"eligible={result.eligible} polled={result.polled} "
                f"partial={result.partial} errors={result.errors} skipped={result.skipped}"
                f"{' (dry run, nothing written)' if cfg.dry_run else ''}"
            )
            return 1 if result.errors else 0
        if cfg.reconcile:
            outcome = await reconcile(pool, None, cfg)
            print(f"eligible={outcome.candidates} queued={outcome.queued}"
                  f"{' (dry run, nothing written)' if cfg.dry_run else ''}")
            return 0
        if cfg.parse:
            for feed, counts in await parse_all(pool, cfg):
                print(
                    f"{feed.source}:{feed.key} postings={counts.postings} "
                    f"created={counts.created} "
                    f"unchanged={counts.unchanged} unresolved={counts.unresolved} "
                    f"unsupported={counts.unsupported} secondhand={counts.secondhand}"
                )
            return 0
        if cfg.enrich:
            failed = False
            for name, outcome in (await run_enrichments(pool, cfg)).items():
                if isinstance(outcome, Exception):
                    failed = True
                    print(f"{name} failed: {outcome}")
                    continue
                print(
                    f"{name} groups={outcome.groups} postings={outcome.postings} "
                    f"skipped_keys={outcome.skipped_keys} "
                    f"skipped_postings={outcome.skipped_postings}"
                    f"{' (dry run, nothing written)' if cfg.dry_run else ''}"
                )
            return 1 if failed else 0
        # The only flagless operation left in this maintenance CLI is a dry
        # fetch proof. Production polling belongs to the unified runtime.
        async with Net(user_agent=cfg.user_agent) as net:
            result = await cycle(pool, net, cfg, datetime.now(timezone.utc))
            per_source = " ".join(
                f"{source}={count}" for source, count in sorted(result.by_source.items())
            )
            print(
                f"polled={result.polled} drained={result.drained}"
                f"{' ' + per_source if per_source else ''}"
                f"{' (dry run, nothing written)' if cfg.dry_run else ''}"
            )
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
