"""FastAPI entry point for the localhost feed inspector."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from xjobscore.inspector import config, scheduling, telemetry
from xjobscore.inspector.cache import now_utc
from xjobscore.inspector.db import CatalogDB
from xjobscore.inspector.duplicate_groups import current_fingerprint
from xjobscore.inspector.duplicate_groups import query as duplicate_group_query
from xjobscore.inspector import fields as fields_recon
from xjobscore.inspector import mapping as field_mapping
from xjobscore.inspector.persistence import ResultStore
from xjobscore.inspector.policy import (
    FeedNotInPostgres,
    PolicyStore,
    PolicyUnavailable,
    PollNowRefused,
)
from xjobscore.inspector.postings import detail as posting_detail
from xjobscore.inspector.postings import query as posting_query
from xjobscore.inspector.profile import source_profile, wire_content_type
from xjobscore.inspector.query import format_error as query_format_error
from xjobscore.inspector.query import run as query_run_sql
from xjobscore.inspector.query import schema as query_schema_sql
from xjobscore.inspector.review import detail as review_detail
from xjobscore.inspector.review import query as review_query
from xjobscore.inspector.review import set_labels as review_set_labels
from xjobscore.inspector.runner import GoldJob, SchedulerRunner
from xjobscore.inspector.service import InspectorService
from xjobscore.inspector.token_heartbeat import (
    HeartbeatAlreadyRunning,
    TokenHeartbeatRunner,
    enqueue_now as token_heartbeat_enqueue,
    heartbeat_snapshot as token_heartbeat_snapshot,
)
from xjobscore.inspector.tokens import query as tokens_query
from xjobscore.inspector.tokens import promote_verified as tokens_promote_verified
from xjobscore.inspector.tokens import set_muted as tokens_set_muted
from xjobscore.inspector.tokens import set_note as tokens_set_note
from xjobscore.inspector.tokens import set_tags as tokens_set_tags
from xjobscore.inspector import validator
from xjobscore.sources import UnknownSource

STATIC_DIR = Path(__file__).resolve().parent / "static"


class PolicyOptions(BaseModel):
    enabled: bool | None = None
    interval_s: int | None = None
    paused: bool | None = None


class ImportOptions(BaseModel):
    enabled: bool = True


class BulkPolicyOptions(BaseModel):
    refs: list[str]
    enabled: bool


class PollNowOptions(BaseModel):
    source: str | None = None


class SchedulerControlOptions(BaseModel):
    running: bool
    note: str | None = None


class PostingLabel(BaseModel):
    """One label upsert; ``label: null`` clears the row's label instead."""

    source: str
    source_key: str
    label: Literal["relevant", "irrelevant", "unsure"] | None = None
    note: str | None = None
    title_snapshot: str | None = None
    company_snapshot: str | None = None


class PostingLabelsBody(BaseModel):
    labels: list[PostingLabel]


class ReviewLabel(BaseModel):
    """One opus-bucket decision for a single ``job_posting`` row."""

    id: int
    label: Literal["tech", "non-tech", "ambiguous", "other"]


class ReviewLabelsBody(BaseModel):
    labels: list[ReviewLabel]


class DuplicateReviewBody(BaseModel):
    decision: Literal["correct", "incorrect", "needs_review", "hidden"] | None = None
    note: str | None = None


class QueryBody(BaseModel):
    sql: str


class TokenIdentity(BaseModel):
    """One ``(ats, token)`` pair, the natural key of a board."""

    ats: str
    token: str


class TokenEnableBody(BaseModel):
    tokens: list[TokenIdentity]


class TokenValidateBody(BaseModel):
    """Validate specific tokens, or every token stored for one vendor."""
    tokens: list[TokenIdentity] | None = None
    # Unfiltered on purpose -- the server does not know which filter the page
    # is showing, so a caller that means "the rows on screen" must name them
    # in ``tokens``. Either way the batch cap below applies.
    vendor: str | None = None


class TokenNoteBody(TokenIdentity):
    """The operator note attached to a token; null or blank clears it."""

    note: str | None = None


class TokenTagsBody(TokenIdentity):
    """Compact operator tags; an empty list clears them."""

    tags: list[str]


class TokenMutedBody(TokenIdentity):
    muted: bool


def _policy_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PollNowRefused):
        # Same 409 shape as an unimported feed, but the hint names what to fix
        # rather than always saying "import" -- these refusals are the
        # scheduler being paused, the feed being off, or a poll already running.
        return HTTPException(
            status_code=409,
            detail={"message": str(exc), "hint": exc.reason},
        )
    if isinstance(exc, FeedNotInPostgres):
        return HTTPException(
            status_code=409,
            detail={"message": str(exc), "hint": "import"},
        )
    if isinstance(exc, PolicyUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def create_app(
    *,
    catalog: Any | None = None,
    store: Any | None = None,
    user_agent: str | None = None,
    policy: Any | None = None,
    runner: Any | None = None,
    token_runner: Any | None = None,
    gold_job: Any | None = None,
) -> FastAPI:
    """Build an inspector app, optionally with injected test collaborators."""

    store = store if store is not None else ResultStore(
        config.persistence_dsn(), schema=config.persistence_schema()
    )
    policy_store = policy if policy is not None else PolicyStore(config.dsn_or_none())
    # The catalog borrows the policy pool rather than opening its own. Feed
    # inventory is PostgreSQL-only.
    database = catalog if catalog is not None else CatalogDB(policy_store)
    inspector = InspectorService(
        database,
        user_agent=user_agent or config.user_agent(),
        result_store=store,
        company_store=policy_store,
    )
    # Off by default (config.runner_enabled()): every test process and every
    # dev laptop's inspector must stay inert unless someone opts in. See
    # runner.py's module docstring for the full two-switch gate.
    scheduler_runner = runner if runner is not None else SchedulerRunner(
        policy_store,
        enabled=config.runner_enabled(),
        user_agent=user_agent or config.user_agent(),
        tick_s=config.runner_tick_s(),
        batch=config.runner_batch(),
        lease_seconds=config.runner_lease_s(),
    )
    heartbeat_runner = token_runner if token_runner is not None else TokenHeartbeatRunner(
        policy_store,
        enabled=config.token_heartbeat_enabled(),
        tick_s=config.token_heartbeat_tick_s(),
        batch=config.token_heartbeat_batch(),
        interval_seconds=config.token_heartbeat_interval_s(),
        retry_seconds=config.token_heartbeat_retry_s(),
        lease_seconds=config.token_heartbeat_lease_s(),
    )
    dedup_job = gold_job if gold_job is not None else GoldJob(policy_store)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        await database.connect()
        try:
            await store.initialize()
            await policy_store.start()
            await scheduler_runner.start()
            await heartbeat_runner.start()
            yield
        finally:
            await heartbeat_runner.stop()
            await scheduler_runner.stop()
            await policy_store.close()
            await store.close()
            await database.disconnect()

    application = FastAPI(title="xjobscore inspector", lifespan=lifespan)
    application.state.inspector_db = database
    application.state.result_store = store
    application.state.inspector = inspector
    application.state.policy = policy_store
    application.state.scheduler_runner = scheduler_runner
    application.state.token_heartbeat_runner = heartbeat_runner
    application.state.gold_job = dedup_job

    async def merged_feed_rows() -> list[dict[str, Any]]:
        rows = await inspector.feed_rows()
        policies = await policy_store.snapshot()
        for row in rows:
            ref = row["feed_ref"]
            if policies is None:
                row["policy"] = None
            elif ref is None:
                row["policy"] = {"in_postgres": False}
            else:
                pg = policies.get((row["source"], row["key"]))
                row["policy"] = (
                    {"in_postgres": True, **pg}
                    if pg is not None
                    else {"in_postgres": False}
                )
        return rows

    def scheduled_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Scheduler-page rows: feeds production is actually polling.

        ``feed.polling`` *is* the membership test. The Feeds page owns it end to
        end -- its toggle imports the feed on first flip and switches it off
        afterwards -- so enabling a feed there is what puts it on the Scheduler,
        and disabling it is what takes it off. Nothing on the Scheduler widens
        or narrows that set.

        A paused feed (``feed.paused_at`` set, ``enabled`` still true)
        deliberately stays visible. That is the entire point of pausing rather
        than disabling: holding a feed's polling should not cost you the ability
        to see it, read its history, or resume it.

        With PostgreSQL down (policy None) membership is unknowable, so keep
        every row rather than flash an empty page.
        """
        return [
            row
            for row in rows
            if row["feed_ref"] is not None
            and (
                row["policy"] is None
                or (row["policy"].get("in_postgres") and row["policy"].get("enabled"))
            )
        ]

    async def configured_feed(ref: str) -> dict[str, Any]:
        row = next(
            (row for row in await merged_feed_rows() if row["feed_ref"] == ref),
            None,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Feed not found")
        return row

    @application.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "postgres": policy_store.status(),
        }

    @application.get("/api/feeds")
    async def feeds() -> dict[str, Any]:
        return {
            "postgres": policy_store.status(),
            "feeds": await merged_feed_rows(),
        }

    @application.get("/api/feeds/{feed_ref}")
    async def feed(feed_ref: str) -> dict[str, Any]:
        return {"feed": await configured_feed(feed_ref)}

    @application.post("/api/feeds/{feed_ref}/fetch")
    async def fetch(feed_ref: str) -> dict[str, Any]:
        try:
            snapshot = await inspector.fetch_once_ref(feed_ref)
        except UnknownSource as exc:
            raise HTTPException(
                status_code=422,
                detail=f"No registered harvester for source: {exc.args[0]}",
            ) from None
        except KeyError:
            raise HTTPException(status_code=404, detail="Feed not found") from None
        return snapshot.summary

    @application.get("/api/results")
    async def result_groups() -> dict[str, Any]:
        return {"sources": await inspector.source_groups()}

    @application.get("/api/results/{feed_ref}")
    async def result(
        feed_ref: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
        parsed_only: bool = Query(default=False),
    ) -> dict[str, Any]:
        snapshot = await inspector.get_result_ref(feed_ref)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No result for this feed")
        # parse_items is always derived in full by get_result_ref (see
        # service._with_parse) regardless of how many rows a caller later
        # pages through, so filtering against it here is safe even for a
        # feed the size of Simplify's.
        indices = (
            [i for i, row in enumerate(snapshot.parse_items) if row.get("canonicals")]
            if parsed_only
            else range(snapshot.item_count)
        )
        total = len(indices) if parsed_only else snapshot.item_count
        end = min(offset + limit, total)
        return {
            "result": snapshot.summary,
            "network": snapshot.network,
            "envelope": snapshot.envelope,
            "items": [snapshot.item_summary(i) for i in indices[offset:end]],
            "offset": offset,
            "limit": limit,
            "has_more": end < total,
            "total_count": total,
            "profile": source_profile(snapshot.source),
            "wire_content_type": wire_content_type(snapshot.network),
        }

    @application.get("/api/results/{feed_ref}/items/{item_index}")
    async def item(feed_ref: str, item_index: int) -> dict[str, Any]:
        snapshot = await inspector.get_result_ref(feed_ref)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No result for this feed")
        if item_index < 0 or item_index >= snapshot.item_count:
            raise HTTPException(status_code=404, detail="Item not found")
        return snapshot.item_detail(item_index)

    @application.get("/api/results/{feed_ref}/envelope")
    async def envelope(feed_ref: str) -> dict[str, Any]:
        snapshot = await inspector.get_result_ref(feed_ref)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No result for this feed")
        return snapshot.envelope_detail()

    @application.get("/api/results/{feed_ref}/parsed")
    async def parsed(
        feed_ref: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        snapshot = await inspector.get_result_ref(feed_ref)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No result for this feed")
        end = min(offset + limit, len(snapshot.parse_items))
        items = []
        for row in snapshot.parse_items[offset:end]:
            summary = {key: value for key, value in row.items() if key != "canonicals"}
            summary["canonical_count"] = len(row.get("canonicals", ()))
            items.append(summary)
        return {
            "status": snapshot.parse_status,
            "summary": snapshot.parse_summary,
            "items": items,
            "offset": offset,
            "limit": limit,
            "has_more": end < len(snapshot.parse_items),
        }

    @application.get("/api/results/{feed_ref}/parsed/{item_index}")
    async def parsed_item(feed_ref: str, item_index: int) -> dict[str, Any]:
        snapshot = await inspector.get_result_ref(feed_ref)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No result for this feed")
        if item_index < 0 or item_index >= len(snapshot.parse_items):
            raise HTTPException(status_code=404, detail="Parsed item not found")
        return snapshot.parse_items[item_index]

    @application.get("/api/results/{feed_ref}/network")
    async def result_network(feed_ref: str) -> dict[str, Any]:
        snapshot = await inspector.get_result_ref(feed_ref)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No result for this feed")
        return snapshot.network

    @application.get("/api/feeds/{feed_ref}/runs")
    async def runs(
        feed_ref: str,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        return {"runs": await store.run_history(feed_ref, limit)}

    @application.get("/api/runs/{result_id}/network")
    async def run_network(result_id: str) -> dict[str, Any]:
        network = await store.run_network(result_id)
        if network is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return network

    @application.get("/api/stats/sources")
    async def source_stats() -> dict[str, Any]:
        return {"sources": await store.source_stats()}

    @application.post("/api/feeds/{feed_ref}/policy")
    async def update_feed_policy(
        feed_ref: str, body: PolicyOptions
    ) -> dict[str, Any]:
        if body.enabled is None and body.interval_s is None and body.paused is None:
            raise HTTPException(status_code=400, detail="No policy change requested")
        row = await configured_feed(feed_ref)
        result: dict[str, Any] = {"feed_ref": feed_ref}
        try:
            if body.enabled is not None:
                result.update(
                    await policy_store.set_enabled(
                        row["source"], row["key"], body.enabled
                    )
                )
            if body.interval_s is not None:
                result.update(
                    await policy_store.set_interval(
                        row["source"], row["key"], body.interval_s
                    )
                )
            if body.paused is not None:
                result.update(
                    await policy_store.set_paused(
                        row["source"], row["key"], body.paused
                    )
                )
        except (PolicyUnavailable, FeedNotInPostgres, ValueError) as exc:
            raise _policy_error(exc) from exc
        return result

    @application.post("/api/feeds/bulk-policy")
    async def update_feeds_policy(body: BulkPolicyOptions) -> dict[str, Any]:
        """Enable or disable many feeds at once, importing on enable.

        One resolve pass over the merged rows, then one round trip -- the
        per-vendor switch on the Feeds page covers 2,591 feeds for Ashby, which
        is far past what per-feed POSTs can carry. Unknown refs are reported
        rather than 404ing the whole batch, so a stale tab cannot make an
        otherwise valid flip fail.
        """
        if not body.refs:
            raise HTTPException(status_code=400, detail="No feeds requested")
        by_ref = {row["feed_ref"]: row for row in await merged_feed_rows()}
        identities = [
            (by_ref[ref]["source"], by_ref[ref]["key"])
            for ref in body.refs
            if ref in by_ref
        ]
        unknown = [ref for ref in body.refs if ref not in by_ref]
        if not identities:
            raise HTTPException(status_code=404, detail="No such feeds")
        try:
            result = await policy_store.set_feeds_enabled(identities, body.enabled)
        except (PolicyUnavailable, FeedNotInPostgres, ValueError) as exc:
            raise _policy_error(exc) from exc
        return {**result, "unknown": unknown}

    @application.post("/api/feeds/{feed_ref}/import")
    async def import_feed(
        feed_ref: str, body: ImportOptions | None = None
    ) -> dict[str, Any]:
        row = await configured_feed(feed_ref)
        options = body or ImportOptions()
        try:
            return await policy_store.import_feed(
                row["source"], row["key"], options.enabled
            )
        except (PolicyUnavailable, FeedNotInPostgres, ValueError) as exc:
            raise _policy_error(exc) from exc

    @application.delete("/api/feeds/{feed_ref}")
    async def drop_feed(feed_ref: str) -> dict[str, Any]:
        """Drop a feed without muting its discovery token."""
        row = await configured_feed(feed_ref)
        try:
            result = await policy_store.drop_feed(row["source"], row["key"])
        except (PolicyUnavailable, FeedNotInPostgres, ValueError) as exc:
            raise _policy_error(exc) from exc
        invalidate = getattr(database, "invalidate", None)
        if invalidate is not None:
            invalidate()
        return {"feed_ref": feed_ref, **result}

    # --- Scheduler: production poll telemetry ------------------------------
    # Reads public.poll_observation, which run.py appends to once per completed
    # poll. Every response still has to be correct with the table absent,
    # present but empty, and populated -- see `telemetry_available`.

    async def scheduler_rows(current) -> tuple[list[dict[str, Any]], bool]:
        """Imported feeds enriched with production telemetry and status."""
        rows = scheduled_rows(await merged_feed_rows())
        pool = await policy_store.pool_or_none()
        daily = rollups = postings = None
        if pool is not None:
            daily = await telemetry.feed_daily_counts(pool, now=current)
            rollups = await telemetry.feed_rollups(pool, now=current)
            postings = await telemetry.posting_stats_by_source(pool, now=current)
        available = daily is not None and rollups is not None

        for row in rows:
            feed_id = (row.get("policy") or {}).get("feed_id")
            spark = (daily or {}).get(feed_id) if feed_id is not None else None
            row["spark"] = spark
            row["telemetry"] = (
                (rollups or {}).get(feed_id) if feed_id is not None else None
            )
            # job_posting keys on the source namespace, not feed_id -- see
            # telemetry.posting_stats_by_source.
            row["postings"] = (postings or {}).get(f"{row['source']}:{row['key']}")
            interval_s = (row.get("policy") or {}).get("interval_s")
            row["sched"] = {
                "requests_per_day": scheduling.requests_per_day(interval_s),
                "status": scheduling.feed_status(row.get("policy"), current),
            }

        priority = {
            "backing_off": 0,
            "stale": 1,
            "never_polled": 2,
            "healthy": 3,
            "paused": 4,
            "disabled": 5,
            "unknown": 6,
        }
        rows.sort(
            key=lambda row: (
                priority[row["sched"]["status"]["state"]],
                (row.get("policy") or {}).get("last_poll_at") or "",
                row["source"],
                (row.get("company_name") or row["key"]).casefold(),
            )
        )
        return rows, available

    async def control_payload() -> dict[str, Any]:
        return {
            "control": await policy_store.scheduler_control(),
            "runner": scheduler_runner.status(),
        }

    @application.get("/api/scheduler/overview")
    async def scheduler_overview() -> dict[str, Any]:
        current = now_utc()
        rows = scheduled_rows(await merged_feed_rows())
        pool = await policy_store.pool_or_none()
        tiles = (
            await telemetry.global_tiles(pool, now=current)
            if pool is not None
            else None
        )
        counts: dict[str, int] = {}
        for row in rows:
            state = scheduling.feed_status(row.get("policy"), current)["state"]
            counts[state] = counts.get(state, 0) + 1
        return {
            "postgres": policy_store.status(),
            "telemetry_available": tiles is not None,
            "feed_count": len(rows),
            "tiles": tiles,
            "counts_by_status": counts,
            **await control_payload(),
        }

    @application.get("/api/scheduler/control")
    async def scheduler_control_get() -> dict[str, Any]:
        return await control_payload()

    @application.post("/api/scheduler/control")
    async def scheduler_control_set(body: SchedulerControlOptions) -> dict[str, Any]:
        try:
            await policy_store.set_scheduler_running(body.running, body.note)
        except (PolicyUnavailable, FeedNotInPostgres, ValueError) as exc:
            raise _policy_error(exc) from exc
        return await control_payload()

    @application.post("/api/gold/dedup")
    async def gold_dedup_start() -> dict[str, Any]:
        try:
            dedup_job.start()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return dedup_job.as_dict()

    @application.get("/api/gold/dedup")
    async def gold_dedup_status() -> dict[str, Any]:
        return dedup_job.as_dict()

    @application.get("/api/scheduler/feeds")
    async def scheduler_feeds(source: str | None = None) -> dict[str, Any]:
        rows, available = await scheduler_rows(now_utc())
        if source is not None:
            rows = [row for row in rows if row["source"] == source]
        return {
            "postgres": policy_store.status(),
            "telemetry_available": available,
            "feeds": rows,
        }

    @application.get("/api/scheduler/feeds/{feed_ref}")
    async def scheduler_feed(feed_ref: str) -> dict[str, Any]:
        rows, available = await scheduler_rows(now_utc())
        row = next((item for item in rows if item["feed_ref"] == feed_ref), None)
        if row is None:
            raise HTTPException(status_code=404, detail="Feed not found")
        feed_id = (row.get("policy") or {}).get("feed_id")
        pool = await policy_store.pool_or_none()
        events = None
        if pool is not None and feed_id is not None:
            events = await telemetry.feed_events(pool, feed_id)
        return {
            "postgres": policy_store.status(),
            "telemetry_available": available,
            "feed": row,
            "events": events or [],
        }

    @application.get("/api/scheduler/events")
    async def scheduler_events(limit: int = 50) -> dict[str, Any]:
        """Latest polls across the whole fleet -- the live fetch log."""
        pool = await policy_store.pool_or_none()
        events = None
        if pool is not None:
            events = await telemetry.recent_events(pool, limit=max(1, min(limit, 200)))
        return {
            "postgres": policy_store.status(),
            "telemetry_available": events is not None,
            "events": events or [],
        }

    @application.post("/api/scheduler/feeds/{feed_ref}/poll-now")
    async def scheduler_poll_now(feed_ref: str) -> dict[str, Any]:
        """Poll one feed now: front of the queue, and wake the runner for it.

        The fetch still happens on the production path -- policy writes the
        queue position, the runner's run.cycle claims and polls it -- so
        leasing, vendor pacing, and poll_observation behave exactly as a
        scheduled poll. ``runner_alive`` tells the page whether the poll is
        about to happen in this process or is waiting on another one's tick.
        """
        row = await configured_feed(feed_ref)
        # Checked here rather than in policy.py: aliveness is a property of this
        # process, not of the database. Cheap and DSN-free, so it goes first.
        if not scheduler_runner.status()["alive"]:
            raise _policy_error(
                PollNowRefused(
                    "no scheduler runner is listening, so nothing would poll this "
                    "feed; start one with XJOBSCORE_INSPECTOR_RUNNER=1",
                    reason="no_runner",
                )
            )
        try:
            result = await policy_store.poll_now(row["source"], row["key"])
        except (PolicyUnavailable, PollNowRefused, FeedNotInPostgres, ValueError) as exc:
            raise _policy_error(exc) from exc
        scheduler_runner.nudge()
        return {**result, "runner_alive": scheduler_runner.status()["alive"]}

    @application.post("/api/scheduler/poll-now")
    async def scheduler_poll_now_all(
        options: PollNowOptions | None = None,
    ) -> dict[str, Any]:
        """Bring every enabled feed forward, optionally limited to one source.

        Keeps ``due_at = now()``: a catalog-wide bump has no front of the queue
        to claim, and backdating thousands of feeds would only reshuffle them
        among themselves. The nudge still applies -- the drain loop should start
        working the backlog now, not on the next tick.
        """
        source = (options or PollNowOptions()).source
        try:
            result = await policy_store.poll_now_all(source)
        except (PolicyUnavailable, PollNowRefused, FeedNotInPostgres, ValueError) as exc:
            raise _policy_error(exc) from exc
        scheduler_runner.nudge()
        return {**result, "runner_alive": scheduler_runner.status()["alive"]}

    @application.post("/api/scheduler/feeds/{feed_ref}/fetch")
    async def scheduler_fetch(feed_ref: str) -> dict[str, Any]:
        """Fetch one feed through the production path, driven by the browser.

        Unlike poll-now this does not queue: it claims a lease, runs run.poll,
        and answers when the poll has finished -- raw_posting, job_posting,
        poll_observation and all. Needs neither the runner loop nor
        scheduler_control.running, because the caller is the scheduler here.
        A harvest failure is not an HTTP error: it answers 200 with
        ``status: "error"``, the same vocabulary poll_observation records.
        """
        row = await configured_feed(feed_ref)
        try:
            return await scheduler_runner.poll_feed_now(row["source"], row["key"])
        except (PolicyUnavailable, PollNowRefused, FeedNotInPostgres, ValueError) as exc:
            raise _policy_error(exc) from exc

    async def _public_pool():
        """The borrowed public pool, or a 503 naming what is unreachable."""
        pool = await policy_store.pool_or_none()
        if pool is None:
            raise HTTPException(
                status_code=503,
                detail="PostgreSQL unavailable",
            )
        return pool

    @application.get("/api/postings")
    async def postings(
        source: str | None = None,
        status: str = "active",
        label_state: str = "all",
        search: str = "",
        search_field: str = "all",
        intern_signal: bool = False,
        order: str = "newest",
        seed: str = "",
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            labels = await store.posting_labels()
            return {
                "postgres": policy_store.status(),
                **await posting_query(
                    pool,
                    labels,
                    source=source,
                    status=status,
                    label_state=label_state,
                    search=search,
                    search_field=search_field,
                    intern_signal=intern_signal,
                    order=order,
                    seed=seed,
                    limit=limit,
                    offset=offset,
                ),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc

    @application.post("/api/postings/labels")
    async def posting_labels_write(body: PostingLabelsBody) -> dict[str, Any]:
        if not body.labels:
            raise HTTPException(status_code=400, detail="labels must not be empty")
        upserts = [entry.model_dump() for entry in body.labels if entry.label]
        clears = [
            (entry.source, entry.source_key)
            for entry in body.labels
            if entry.label is None
        ]
        try:
            written = await store.set_posting_labels(upserts)
            cleared = await store.delete_posting_labels(clears)
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"Inspector database unavailable: {exc}"
            ) from exc
        return {"written": written, "cleared": cleared}

    @application.get("/api/postings/{posting_id}")
    async def posting_item(posting_id: int) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            labels = await store.posting_labels()
            value = await posting_detail(pool, labels, posting_id)
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        if value is None:
            raise HTTPException(status_code=404, detail="Posting not found")
        return value

    @application.get("/api/review")
    async def review(
        source: str | None = None,
        label: str = "all",
        state: str = "unreviewed",
        search: str = "",
        order: str = "newest",
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            return {
                "postgres": policy_store.status(),
                **await review_query(
                    pool,
                    source=source,
                    label=label,
                    state=state,
                    search=search,
                    order=order,
                    limit=limit,
                    offset=offset,
                ),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc

    @application.post("/api/review/labels")
    async def review_labels_write(body: ReviewLabelsBody) -> dict[str, Any]:
        if not body.labels:
            raise HTTPException(status_code=400, detail="labels must not be empty")
        pool = await _public_pool()
        try:
            written = await review_set_labels(
                pool, [entry.model_dump() for entry in body.labels]
            )
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        return {"written": written}

    @application.get("/api/review/{posting_id}")
    async def review_item(posting_id: int) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            value = await review_detail(pool, posting_id)
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        if value is None:
            raise HTTPException(status_code=404, detail="Posting not found")
        return value

    @application.get("/api/duplicate-groups")
    async def duplicate_groups(
        source: str | None = None,
        review_state: str = "unreviewed",
        search: str = "",
        intern_signal: bool = False,
        order: str = "newest",
        seed: str = "",
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            reviews = await store.duplicate_group_reviews()
            return {
                "postgres": policy_store.status(),
                **await duplicate_group_query(
                    pool,
                    reviews,
                    source=source,
                    review_state=review_state,
                    search=search,
                    intern_signal=intern_signal,
                    order=order,
                    seed=seed,
                    limit=limit,
                    offset=offset,
                ),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"Duplicate review unavailable: {exc}"
            ) from exc

    @application.post("/api/duplicate-groups/{group_id}/review")
    async def duplicate_group_review(
        group_id: UUID, body: DuplicateReviewBody
    ) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            fingerprint = await current_fingerprint(pool, group_id)
            if fingerprint is None:
                raise HTTPException(status_code=404, detail="Duplicate group not found")
            note = body.note.strip() if body.note and body.note.strip() else None
            if body.decision is None:
                await store.delete_duplicate_group_review(
                    group_id=str(group_id), fingerprint=fingerprint
                )
                review = None
            else:
                await store.set_duplicate_group_review(
                    group_id=str(group_id),
                    fingerprint=fingerprint,
                    decision=body.decision,
                    note=note,
                )
                review = {"decision": body.decision, "note": note}
            return {
                "group_id": str(group_id),
                "fingerprint": fingerprint,
                "review": review,
            }
        except HTTPException:
            raise
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"Duplicate review unavailable: {exc}"
            ) from exc

    @application.post("/api/query")
    async def query_run(body: QueryBody) -> dict[str, Any]:
        """Run one read-only SQL statement against the pipeline database.

        POST, not GET with a query string: SQL does not belong in a URL, and
        the answer must not be cached. See ``query.run`` for the three layers
        that keep this from ever writing.
        """
        pool = await _public_pool()
        try:
            return await query_run_sql(pool, body.sql)
        except ValueError as exc:
            # The admin's SQL failed the allowlist -- not a database problem.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except asyncpg.PostgresError as exc:
            # Syntax error, the READ ONLY transaction refusing a write,
            # statement_timeout firing -- all the admin's SQL being wrong,
            # unlike the postings routes above where a PostgresError means
            # the database itself is unhealthy.
            raise HTTPException(status_code=400, detail=query_format_error(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc

    @application.get("/api/query/schema")
    async def query_schema() -> dict[str, Any]:
        pool = await _public_pool()
        try:
            return await query_schema_sql(pool)
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc

    # ------ fields (raw_posting field recon)
    #
    # Read-only throughout, and split by cost on purpose: /survey is a
    # sampled census that answers in ~2s, /values is an exact full scan of
    # one chosen path. See fields.py for the measurements behind the split.

    @application.get("/api/fields/adapters")
    async def fields_adapters(force: bool = False) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            return await fields_recon.adapters(pool, force=force)
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc

    @application.get("/api/fields/feeds")
    async def fields_feeds(adapter: str) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            return await fields_recon.feeds(pool, adapter)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc

    @application.get("/api/fields/mapping")
    async def fields_mapping(adapter: str) -> dict[str, Any]:
        """How the adapter's config reads its payload. No database at all."""
        try:
            fields_recon._validate_adapter(adapter)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**field_mapping.adapter_map(adapter), "domains": field_mapping.domains()}

    @application.get("/api/fields/survey")
    async def fields_survey(
        adapter: str,
        feed: str | None = None,
        sample: int = fields_recon.DEFAULT_SAMPLE,
        depth: int = Query(default=fields_recon.DEFAULT_DEPTH, ge=1, le=fields_recon.MAX_DEPTH),
        seed: str = "",
    ) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            result = await fields_recon.survey(
                pool, adapter, feed=feed, sample=sample, depth=depth, seed=seed
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        # Annotated here rather than in fields.py so the SQL layer stays
        # ignorant of the source configs, and so a config that fails to
        # introspect can never break the census itself.
        annotations = field_mapping.annotate(
            [row["path"] for row in result["paths"]], adapter
        )
        for row in result["paths"]:
            row["mapped"] = annotations.get(row["path"], {"fields": [], "inherited": None})
        return result

    @application.get("/api/fields/values")
    async def fields_values(
        adapter: str,
        path: str,
        feed: str | None = None,
        limit: int = Query(default=fields_recon.VALUE_LIMIT, ge=1, le=fields_recon.MAX_VALUE_LIMIT),
    ) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            result = await fields_recon.values(
                pool, adapter, path, feed=feed, limit=limit
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc

        annotation = field_mapping.annotate([path], adapter)[path]
        chains = [field_mapping.chain(name) for name in annotation["fields"]]
        # The collapse preview only runs for a field whose narrowing is a
        # pure function of the value alone; everything else reports the chain
        # without pretending to evaluate it.
        collapsed: dict[str, dict[str, str | None]] = {}
        observed = [entry["value"] for entry in result["values"]]
        for name in annotation["fields"]:
            preview = field_mapping.preview(name, observed)
            if preview:
                collapsed[name] = preview
        return {**result, "mapped": annotation, "chains": chains, "collapsed": collapsed}

    @application.get("/api/fields/examples")
    async def fields_examples(
        adapter: str,
        path: str,
        value: str | None = None,
        feed: str | None = None,
        limit: int = Query(default=fields_recon.EXAMPLE_LIMIT, ge=1, le=fields_recon.EXAMPLE_LIMIT),
    ) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            return await fields_recon.examples(
                pool, adapter, path, value, feed=feed, limit=limit
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc

    # --- Tokens: the discovery inventory and its promotion into feed -------
    # public.token holds every board key ever seen; public.feed is the curated
    # set the scheduler polls. Promotion is the one write, and it creates
    # candidates -- enabling them stays a Feeds page decision.

    @application.get("/api/tokens")
    async def tokens(
        filter: str = "inactive",
        limit: int = Query(default=50, ge=1, le=10000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            return {
                "postgres": policy_store.status(),
                **await tokens_query(
                    pool,
                    filter=filter,
                    limit=limit,
                    offset=offset,
                ),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc

    @application.post("/api/tokens/enable")
    async def enable_tokens(body: TokenEnableBody) -> dict[str, Any]:
        if not body.tokens:
            raise HTTPException(status_code=400, detail="tokens must not be empty")
        identities = list(
            dict.fromkeys((entry.ats, entry.token) for entry in body.tokens)
        )
        pool = await _public_pool()
        try:
            promoted = await tokens_promote_verified(pool, identities)
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        return {
            "requested": len(identities),
            "imported": promoted,
            "skipped": len(identities) - promoted,
            "enabled": True,
        }

    async def heartbeat_payload() -> dict[str, Any]:
        pool = await _public_pool()
        try:
            return {
                "postgres": policy_store.status(),
                **await token_heartbeat_snapshot(
                    pool,
                    current=now_utc(),
                    configured=heartbeat_runner.enabled,
                    offline_after_s=max(180, heartbeat_runner.tick_s * 3),
                ),
            }
        except (OSError, asyncpg.PostgresError, RuntimeError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc

    @application.get("/api/tokens/heartbeat")
    async def token_heartbeat_get() -> dict[str, Any]:
        return await heartbeat_payload()

    @application.post("/api/tokens/heartbeat/run", status_code=202)
    async def token_heartbeat_run() -> dict[str, Any]:
        pool = await _public_pool()
        try:
            await token_heartbeat_enqueue(pool, now_utc())
        except HeartbeatAlreadyRunning as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (OSError, asyncpg.PostgresError, RuntimeError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        heartbeat_runner.nudge()
        return await heartbeat_payload()

    @application.put("/api/tokens/note")
    async def set_token_note(body: TokenNoteBody) -> dict[str, Any]:
        """Save (or clear) the operator note for one existing token."""
        pool = await _public_pool()
        note = body.note.strip() if body.note and body.note.strip() else None
        try:
            saved = await tokens_set_note(
                pool, ats=body.ats, token=body.token, note=note
            )
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        if saved is None:
            raise HTTPException(status_code=404, detail="Token not found")
        return saved

    @application.put("/api/tokens/tags")
    async def set_token_tags(body: TokenTagsBody) -> dict[str, Any]:
        pool = await _public_pool()
        tags = list(dict.fromkeys(tag.strip() for tag in body.tags if tag.strip()))
        try:
            saved = await tokens_set_tags(
                pool, ats=body.ats, token=body.token, tags=tags
            )
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        if saved is None:
            raise HTTPException(status_code=404, detail="Token not found")
        return saved

    @application.put("/api/tokens/muted")
    async def set_token_muted(body: TokenMutedBody) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            saved = await tokens_set_muted(
                pool,
                ats=body.ats,
                token=body.token,
                muted=body.muted,
            )
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        if saved is None:
            raise HTTPException(status_code=404, detail="Token not found")
        if saved["feed_dropped"]:
            invalidate = getattr(database, "invalidate", None)
            if invalidate is not None:
                invalidate()
        return saved

    @application.post("/api/tokens/validate")
    async def validate_tokens_endpoint(body: TokenValidateBody) -> dict[str, Any]:
        """Validate one or more tokens and update their status in the database."""
        pool = await _public_pool()
        identities = []
        if body.tokens:
            identities = [(entry.ats, entry.token) for entry in body.tokens]
        elif body.vendor:
            try:
                records = await pool.fetch(
                    "SELECT DISTINCT ats, token FROM token WHERE ats = $1 ORDER BY token",
                    body.vendor,
                )
                identities = [(r["ats"], r["token"]) for r in records]
            except (OSError, asyncpg.PostgresError) as exc:
                raise HTTPException(
                    status_code=503, detail=f"PostgreSQL unavailable: {exc}"
                ) from exc
        else:
            raise HTTPException(status_code=400, detail="provide tokens or vendor")

        if len(identities) > validator.MAX_BATCH:
            # Better a refusal the caller can see than a request that runs for
            # minutes and dies in a proxy with the work half done.
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{len(identities)} tokens is more than one request can "
                    f"validate (max {validator.MAX_BATCH}) -- send them in batches"
                ),
            )

        try:
            results = await validator.validate_tokens(identities, pool=pool)
            return {
                "total": len(identities),
                "results": results,
            }
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc

    @application.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/feed")
    async def feed_page():
        return FileResponse(STATIC_DIR / "feed" / "index.html")

    @application.get("/scheduler")
    async def scheduler_page():
        return FileResponse(STATIC_DIR / "scheduler" / "index.html")

    @application.get("/postings")
    async def postings_page():
        return FileResponse(STATIC_DIR / "postings" / "index.html")

    @application.get("/review")
    async def review_page():
        return FileResponse(STATIC_DIR / "review" / "index.html")

    @application.get("/query")
    async def query_page():
        return FileResponse(STATIC_DIR / "query" / "index.html")

    @application.get("/fields")
    async def fields_page():
        return FileResponse(STATIC_DIR / "fields" / "index.html")

    @application.get("/tokens")
    async def tokens_page():
        return FileResponse(STATIC_DIR / "tokens" / "index.html")

    @application.get("/regex")
    async def regex_page():
        return FileResponse(STATIC_DIR / "regex" / "index.html")

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return application


app = create_app()


def service() -> InspectorService:
    """Compatibility accessor for code that imports the default app service."""
    return app.state.inspector
