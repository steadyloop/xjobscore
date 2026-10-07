"""Refresh missing/ATS-closed signals for a snapshot of selected jobs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from xjobscore import reconcile, publish, router, sources, store
from xjobscore.net import DEFAULT_USER_AGENT, Net
from xjobscore.records import LINK_DEAD, PAGE_CLOSED, Posting, Unparseable
from xjobscore.run import _load_dotenv


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("oneshot", "all"), default="oneshot")
    parser.add_argument("--source", action="append", default=[], help="vendor; repeatable")
    parser.add_argument("--feed", action="append", default=[], help="source:key; repeatable")
    parser.add_argument("--job-id", action="append", type=positive, default=[])
    parser.add_argument("--limit", type=positive, help="maximum jobs, ordered by ID")
    parser.add_argument("--concurrency", type=positive, default=8)
    parser.add_argument("--include-paused", action="store_true", help="also check paused/disabled feeds")
    parser.add_argument("--apply", action="store_true", help="fetch and write; otherwise preview only")
    parser.add_argument("--dsn", help="override DATABASE_URL")
    return parser.parse_args(argv)


async def select_jobs(conn, args):
    # Reuse the publisher's membership rules without its content fingerprint.
    cohort = "SELECT j.id\n" + publish.COHORT_SQL[publish.COHORT_SQL.index("    FROM job_posting j"):]
    membership = f"j.id IN ({cohort})" if args.scope == "oneshot" else "TRUE"
    return await conn.fetch(
        f"""SELECT j.id, j.source, j.source_key, f.id AS feed_id,
                   (f.polling AND f.paused_at IS NULL) AS enabled
            FROM job_posting j
            LEFT JOIN feed f ON j.source = f.source || ':' || f.key
            WHERE {membership}
              AND (cardinality($1::text[]) = 0 OR split_part(j.source, ':', 1) = ANY($1))
              AND (cardinality($2::text[]) = 0 OR j.source = ANY($2))
              AND (cardinality($3::bigint[]) = 0 OR j.id = ANY($3))
            ORDER BY j.id LIMIT $4""",
        args.source, args.feed, args.job_id, args.limit,
    )


async def fresh_listing(feed, net):
    feed = replace(feed, etag=None, last_modified=None, res_hash=None,
                   list_total=None, last_full_poll_at=None)
    if feed.source in sources.json_api.SPECS:
        return await sources.json_api.harvest(
            feed.source, feed, net,
            options=sources.json_api.JsonApiHarvestOptions(force_full=True),
        )
    return await sources.get(feed.source)(feed, net)


async def check_feed(pool, net, feed, jobs, *, include_paused=False):
    source = store.source_instance(feed)
    lease = datetime.now(timezone.utc) + timedelta(hours=1)
    async with pool.acquire() as conn:
        claimed = await conn.fetchval(
            """UPDATE feed SET hold_until = $2 WHERE id = $1
               AND (hold_until IS NULL OR hold_until <= now())
               AND ($3 OR (polling AND paused_at IS NULL)) RETURNING id""",
            feed.id, lease, include_paused,
        )
    if claimed is None:
        return {"feed": source, "unchecked": len(jobs), "reason": "paused or leased"}
    try:
        record = await fresh_listing(feed, net)
        if (record.truncated or (record.result_window_truncated and not record.full_listing)
                or record.count_unchanged
                or record.http_status == 304 or (record.http_status or 200) >= 400):
            return {"feed": source, "unchecked": len(jobs), "reason": "incomplete listing"}
        observed_at = datetime.now(timezone.utc)
        selected = {job["source_key"] for job in jobs}
        items = [item for item in record.items if item.external_id in selected]
        listed = {item.external_id for item in record.items}
        closed = set()
        unparseable = 0
        parse = sources.parser(feed.source)
        for item in items:
            try:
                postings = parse(feed, item, record.envelope)
            except Unparseable:
                unparseable += 1
                continue
            for posting in postings:
                if (isinstance(posting, Posting) and posting.fidelity == "origin"
                        and posting.external_id in selected
                        and posting.closed_evidence not in {LINK_DEAD, PAGE_CLOSED}
                        and (posting.source_active is False or posting.state == "closed")):
                    closed.add(posting.external_id)
        async with pool.acquire() as conn, conn.transaction():
            owns_lease = await conn.fetchval(
                """SELECT id FROM feed WHERE id = $1 AND hold_until = $2
                   AND hold_until > now() FOR UPDATE""", feed.id, lease,
            )
            if owns_lease is None:
                return {"feed": source, "unchecked": len(jobs), "reason": "lease expired"}
            await router.persist(conn, feed, router.prepare(feed, replace(record, items=items)))
            await conn.execute(
                """UPDATE job_posting SET
                   missing_since = CASE WHEN source_key = ANY($3::text[]) THEN NULL
                                        ELSE COALESCE(missing_since, $5) END,
                   ats_closed_at = CASE WHEN source_key = ANY($4::text[])
                                        THEN COALESCE(ats_closed_at, $5) ELSE ats_closed_at END,
                   status = CASE WHEN source_key = ANY($4::text[]) THEN 'closed' ELSE status END,
                   last_seen_at = CASE WHEN source_key = ANY($3::text[])
                                       THEN GREATEST(last_seen_at, $5) ELSE last_seen_at END
                   WHERE source = $1 AND id = ANY($2::bigint[])""",
                source, [job["id"] for job in jobs], list(listed), list(closed), observed_at,
            )
            await reconcile.enqueue(conn, [job["id"] for job in jobs])
            # A subsequent cached poll must not restore membership from an older snapshot.
            # Leave last_change_at alone: this run did not refresh off-scope jobs.
            await conn.execute(
                """UPDATE feed SET etag = NULL, last_modified = NULL, res_hash = NULL,
                   list_total = NULL, last_full_poll_at = NULL WHERE id = $1""", feed.id,
            )
        return {"feed": source, "checked": len(jobs), "missing": len(selected - listed),
                "ats_closed": len(closed), "unparseable": unparseable}
    except Exception as error:
        return {"feed": source, "unchecked": len(jobs), "reason": type(error).__name__}
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE feed SET hold_until = NULL WHERE id = $1 AND hold_until = $2",
                feed.id, lease,
            )


async def run(args):
    pool = await store.connect(args.dsn or os.environ["DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            jobs = await select_jobs(conn, args)
            feeds = {feed.id: feed for feed in await store.all_feeds(conn)}
        groups = defaultdict(list)
        skipped = defaultdict(int)
        for job in jobs:
            if job["feed_id"] not in feeds:
                skipped["no_feed"] += 1
            elif not args.include_paused and not job["enabled"]:
                skipped["paused"] += 1
            else:
                groups[job["feed_id"]].append(job)
        summary = {"scope": args.scope, "selected": len(jobs), "feeds": len(groups),
                   "jobs_to_check": sum(map(len, groups.values())), **skipped}
        print(json.dumps({"preview": summary}), flush=True)
        if not args.apply:
            return 0
        pending = iter(groups.items())
        totals = defaultdict(int, unchecked=sum(skipped.values()))
        async with Net(user_agent=os.environ.get("XJOBSCORE_USER_AGENT") or DEFAULT_USER_AGENT) as net:
            async def worker():
                for feed_id, selected in pending:
                    result = await check_feed(pool, net, feeds[feed_id], selected,
                                              include_paused=args.include_paused)
                    for key in ("checked", "unchecked", "missing", "ats_closed", "unparseable"):
                        totals[key] += result.get(key, 0)
                    print(json.dumps(result), flush=True)
            await asyncio.gather(*(worker() for _ in range(min(args.concurrency, len(groups)))))
        print(json.dumps({"summary": dict(totals)}), flush=True)
        return int(totals["unchecked"] > 0 or totals["unparseable"] > 0)
    finally:
        await pool.close()


def main(argv=None):
    _load_dotenv()
    args = arguments(argv)
    if not (args.dsn or os.environ.get("DATABASE_URL")):
        raise SystemExit("Set DATABASE_URL or pass --dsn")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
