"""Schema-aligned scheduler, fetch, bronze, and silver pass.

One invocation runs one cycle and exits. Scheduling repetition is deliberately
left to cron or a launch agent rather than an internal sleep loop.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from xjobscore import gold, probe, router, silver, sources, store, work
from xjobscore.net import DEFAULT_USER_AGENT, Net
from xjobscore.records import Feed
from xjobscore.work import Outcome

if TYPE_CHECKING:
    from asyncpg import Pool

logger = logging.getLogger(__name__)

__all__ = [
    "Config",
    "cycle",
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
    batch: int = 20
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
    parser.add_argument("--batch", type=int)
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
        help="probe postings missing from a complete poll and close the dead ones",
    )
    parser.add_argument(
        "--enrich",
        nargs="?",
        const="all",
        choices=("all", *(step.name for step in gold.steps())),
        metavar="NAME",
        help="run all enrichments, or one named Gold step",
    )
    args = parser.parse_args(argv)

    dsn = args.dsn or os.environ.get("XJOBSCORE_DSN") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise ValueError("no dsn: pass --dsn or set XJOBSCORE_DSN/DATABASE_URL")
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
    return Config(
        dsn=dsn,
        user_agent=args.user_agent or os.environ.get("XJOBSCORE_USER_AGENT") or DEFAULT_USER_AGENT,
        batch=args.batch if args.batch is not None else int(os.environ.get("XJOBSCORE_BATCH", "20")),
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


def _probe_url(feed: Feed) -> str | None:
    """This feed's per-posting probe URL with `{id}` still in it, if it has one.

    A vendor with no probe endpoint is not a failure -- it is a vendor whose
    postings we cannot ask about, and the honest response is to leave its rows
    alone rather than infer closure from absence.
    """
    try:
        adapter = sources.json_api.get_adapter(feed.source)
    except KeyError:
        return None
    template = (adapter.get("probe") or {}).get("url")
    if not template:
        return None
    # The same context the list and detail legs build, so a vendor keyed on
    # {tenant}/{site} rather than {key} works without a second code path.
    parts = adapter.get("key_parts", ())
    context = {"key": feed.key}
    if parts:
        context.update(zip(parts, feed.key.split("/", len(parts) - 1)))
    context.update(feed.config or {})
    for token, value in context.items():
        if isinstance(value, str):
            template = template.replace("{" + token + "}", value)
    return template


async def _finish_failure(
    pool: Pool,
    feed: Feed,
    now: datetime,
    outcome: Outcome,
    *,
    new_postings: int = 0,
) -> None:
    """Release a failed claim and record the attempt.

    ``new_postings`` is zero for a genuine error, which never reached silver. A
    truncated fetch did reach it and may have created rows, so the partial path
    passes its real count rather than pretending the poll produced nothing.
    """
    due_at = work.next_due_at(feed.interval_s, feed.strikes, outcome, now)
    async with pool.acquire() as conn:
        if await store.finish_schedule_failure(conn, feed, now=now, due_at=due_at):
            await store.append_poll_observation(
                conn,
                feed_id=feed.id,
                polled_at=now,
                status=outcome,
                new_postings=new_postings,
            )
        else:
            logger.warning("lost schedule lease while failing %s", store.source_instance(feed))


async def poll(pool: Pool, net: Net, feed: Feed, now: datetime) -> Outcome:
    """Fetch one claimed feed and carry a changed snapshot through silver."""
    source = store.source_instance(feed)
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
    except Exception:
        logger.exception("harvest failed source=%s", source)
        await _finish_failure(pool, feed, now, "error")
        return "error"

    if record.http_status == 304 or record.count_unchanged:
        due_at = work.next_due_at(feed.interval_s, feed.strikes, "not_modified", now)
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
                await store.append_poll_observation(
                    conn,
                    feed_id=feed.id,
                    polled_at=now,
                    status="not_modified",
                    new_postings=0,
                )
        if not ok:
            logger.warning("lost schedule lease after 304 source=%s", source)
        return "not_modified"

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
            )
            # "ok", not "not_modified": the fetch returned a full body and only
            # the snapshot hash matched. Keep the status matching what poll()
            # returns for this path.
            if ok:
                await store.append_poll_observation(
                    conn,
                    feed_id=feed.id,
                    polled_at=now,
                    status="ok",
                    new_postings=0,
                )
        if not ok:
            logger.warning("lost schedule lease after unchanged poll source=%s", source)
        return "ok"

    try:
        async with pool.acquire() as conn, conn.transaction():
            stored = await router.persist(conn, feed, prepared)
        async with pool.acquire() as conn, conn.transaction():
            counts = await silver.process(
                conn, feed, stored, record.envelope, seen_at=now
            )
    except Exception:
        logger.exception("parse/persist failed source=%s", source)
        await _finish_failure(pool, feed, now, "error")
        return "error"

    if counts.unresolved or counts.unsupported or counts.secondhand:
        logger.info(
            "silver skips source=%s unresolved=%d unsupported=%d secondhand=%d",
            source,
            counts.unresolved,
            counts.unsupported,
            counts.secondhand,
        )

    outcome: Outcome = "partial" if record.truncated else "ok"
    if outcome == "partial":
        await _finish_failure(pool, feed, now, outcome, new_postings=counts.created)
        return outcome

    due_at = work.next_due_at(feed.interval_s, feed.strikes, "ok", now)
    async with pool.acquire() as conn:
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
        )
        # Only when the lease held: a reclaimed worker polled this feed too and
        # records its own row, and two rows for one poll double-count lambda.
        if ok:
            await store.append_poll_observation(
                conn,
                feed_id=feed.id,
                polled_at=now,
                status="ok",
                new_postings=counts.created,
            )
    if not ok:
        logger.warning("lost schedule lease after success source=%s", source)
    return "ok"


async def cycle(
    pool: Pool,
    net: Net,
    cfg: Config,
    now: datetime,
) -> int:
    """Claim due feeds and poll them.

    Feed records are already complete: PostgreSQL owns their adapter
    configuration, company identity, and source metadata.
    """
    async with pool.acquire() as conn:
        if cfg.dry_run:
            claimed = await store.due_feeds(conn, now, cfg.batch)
        else:
            claimed = await store.claim_due_feeds(
                conn, now, cfg.batch, lease_seconds=cfg.lease_seconds
            )
    if cfg.dry_run:
        # Deliberately cold, not `sources.reusing`: a dry run exists to prove
        # a feed still fetches end to end, and revision reuse would silently
        # skip most of that proof on a warm cache -- the opposite of what
        # "dry run" is for here.
        await asyncio.gather(*(sources.get(feed.source)(feed, net) for feed in claimed))
    else:
        await asyncio.gather(*(poll(pool, net, feed, now) for feed in claimed))
    return len(claimed)


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
        outcome = await poll(pool, net, claimed, claimed_at)
        return outcome

    outcomes: list[str] = []

    async def worker() -> None:
        while candidate := await next_feed():
            outcomes.append(await one(candidate))

    await asyncio.gather(*(worker() for _ in range(min(cfg.concurrency, len(candidates)))))
    return FetchAllResult(
        eligible=len(candidates),
        polled=sum(outcome in {"ok", "not_modified"} for outcome in outcomes),
        skipped=outcomes.count("skipped"),
        errors=outcomes.count("error"),
        partial=outcomes.count("partial"),
    )


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    candidates: int = 0
    closed: int = 0
    live: int = 0
    inconclusive: int = 0
    unprobeable: int = 0  # feeds whose adapter declares no probe endpoint


async def reconcile(pool: Pool, net: Net, cfg: Config) -> ReconcileResult:
    """Probe postings that stopped appearing, and close the ones that are gone.

    Absence is the trigger and the origin's answer is the evidence. The two are
    kept apart on purpose: a truncated fetch, a dead token, or a board that
    briefly empties makes every posting in a source look absent at once, and
    closing on that alone would manufacture closures by the hundred. Because the
    probe is authoritative, the trigger is allowed to be cheap -- a posting
    hidden by a bad fetch answers 200 and is left alone.

    Only feeds whose adapter declares a `probe` endpoint participate. A vendor
    we cannot ask is counted and skipped, never guessed at.
    """
    async with pool.acquire() as conn:
        feeds = await store.replay_feeds(conn)

    result = ReconcileResult()
    for feed in feeds:
        template = _probe_url(feed)
        if template is None:
            result = replace(result, unprobeable=result.unprobeable + 1)
            continue
        source = store.source_instance(feed)
        async with pool.acquire() as conn:
            absent = await store.absent_postings(conn, feed, source)
        if not absent:
            continue

        urls = [template.replace("{id}", row["source_key"]) for row in absent]
        probed_at = datetime.now(timezone.utc)
        results = await probe.probe_many(net, urls, concurrency=cfg.concurrency)
        logger.info(
            "reconcile source=%s absent=%d absent_hits=%d",
            source,
            len(absent),
            sum(one.outcome == "absent" for one in results),
        )
        if cfg.dry_run:
            result = replace(result, candidates=result.candidates + len(absent))
            continue

        checked = [row["id"] for row in absent]
        async with pool.acquire() as conn, conn.transaction():
            # Every probe moves link_checked_at, whatever it found: the column
            # means "when we last looked". Doing it in one statement first also
            # means an inconclusive probe is not silently re-spent next pass.
            await store.mark_checked(conn, checked, checked_at=probed_at)
            for row, one in zip(absent, results):
                if one.outcome != "absent":
                    continue
                # Bronze first, so the closure is replayable: content
                # addressing means re-probing an already-tombstoned posting
                # inserts nothing, and a parser fix re-derives the closure
                # without re-fetching a URL that is already gone.
                data = router.wrap(probe.tombstone(one, probed_at=probed_at))
                await store.save_raw(
                    conn,
                    source=source,
                    source_key=row["source_key"],
                    data=data,
                    data_hash=router.encoded_hash(data),
                )
                await store.close_posting_by_key(
                    conn,
                    source=source,
                    source_key=row["source_key"],
                    closed_at=probed_at,
                )
        result = replace(
            result,
            candidates=result.candidates + len(absent),
            closed=result.closed + sum(one.outcome == "absent" for one in results),
            live=result.live + sum(one.outcome == "live" for one in results),
            inconclusive=result.inconclusive
            + sum(one.outcome == "inconclusive" for one in results),
        )
    return result


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
    """Run the selected enrichments, isolating each in its own transaction."""
    results: dict[str, gold.Counts | Exception] = {}
    selected = None if cfg.enrich == "all" else cfg.enrich
    for step in gold.steps(selected):
        try:
            async with pool.acquire() as conn, conn.transaction():
                results[step.name] = await step.rebuild(conn, dry_run=cfg.dry_run)
        except Exception as exc:  # one step's failure must not sink the layer
            logger.exception("enrichment failed name=%s", step.name)
            results[step.name] = exc
    return results


async def main(argv: Sequence[str] | None = None) -> int:
    cfg = load_config(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(level=os.environ.get("XJOBSCORE_LOG_LEVEL", "INFO"))
    pool = await store.connect(cfg.dsn)
    try:
        if cfg.fetch_all:
            async with Net(user_agent=cfg.user_agent) as net:
                result = await fetch_all(pool, net, cfg)
            print(
                f"eligible={result.eligible} polled={result.polled} "
                f"partial={result.partial} errors={result.errors} skipped={result.skipped}"
                f"{' (dry run, nothing written)' if cfg.dry_run else ''}"
            )
            return 1 if result.errors else 0
        if cfg.reconcile:
            async with Net(user_agent=cfg.user_agent) as net:
                outcome = await reconcile(pool, net, cfg)
            print(
                f"candidates={outcome.candidates} closed={outcome.closed} "
                f"live={outcome.live} inconclusive={outcome.inconclusive} "
                f"unprobeable={outcome.unprobeable}"
                f"{' (dry run, nothing written)' if cfg.dry_run else ''}"
            )
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
        # One cycle, then exit. This CLI entry point is not a daemon: repeating
        # it here is a job for cron or a launch agent, which restart cleanly,
        # log per run, and cannot wedge a long-lived process holding a pool and
        # a batch of feed leases. `cycle()` itself has no such assumption baked
        # in, though: `inspector.runner.SchedulerRunner` calls it on a tick from
        # inside the inspector's own long-lived process, reusing one `Net`
        # across calls.
        async with Net(user_agent=cfg.user_agent) as net:
            polled = await cycle(pool, net, cfg, datetime.now(timezone.utc))
            print(f"polled={polled}{' (dry run, nothing written)' if cfg.dry_run else ''}")
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
