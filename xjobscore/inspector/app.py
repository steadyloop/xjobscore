"""FastAPI entry point for the localhost feed inspector."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
import sqlite3
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from xjobscore.inspector import closures, scheduling, telemetry
from xjobscore.inspector.cache import now_utc
from xjobscore.inspector.policy import (
    FeedNotInPostgres,
    PolicyUnavailable,
    PollNowRefused,
)
from xjobscore.inspector.profile import source_profile, wire_content_type
from xjobscore.inspector.token_sweep import SweepAlreadyRunning, SweepRunner
from xjobscore.inspector.token_heartbeat import (
    HeartbeatAlreadyRunning,
    HeartbeatPaused,
    enqueue_now as token_heartbeat_enqueue,
    heartbeat_snapshot as token_heartbeat_snapshot,
    set_paused as token_heartbeat_set_paused,
)
from xjobscore import logo_harvest, reconcile
from xjobscore.inspector import companies as companies_view
from xjobscore.inspector.tokens import query as tokens_query
from xjobscore.inspector.tokens import recent_checks as tokens_recent_checks
from xjobscore.inspector.tokens import promote_verified as tokens_promote_verified
from xjobscore.inspector.tokens import add_vendors_to_feed as tokens_add_vendors_to_feed
from xjobscore.inspector.tokens import recent_feed_events as tokens_recent_feed_events
from xjobscore.inspector.tokens import set_muted as tokens_set_muted
from xjobscore.inspector.tokens import set_note as tokens_set_note
from xjobscore.inspector.tokens import set_tags as tokens_set_tags
from xjobscore.keyscan_import import import_keyscan_tokens
from starlette.concurrency import run_in_threadpool

from xjobscore.feed_identity import canonical_feed_key
from xjobscore.keyscan_import import resolve_ats as resolve_keyscan_ats
from xjobscore.inspector import keyscan_control
from xjobscore.inspector import keyscan_view
from xjobscore.inspector import validator
from xjobscore.sources import UnknownSource
from xjobscore import activity
from xjobscore import sources as source_registry
from xjobscore.source_policy import source_disabled

STATIC_DIR = Path(__file__).resolve().parent / "static"


class KeyscanRunBody(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    crawl_ids: str | None = None
    sources: str | None = None
    validate_tokens: bool = False
    rescan: bool = False


class PolicyOptions(BaseModel):
    enabled: bool | None = None
    interval_s: int | None = None
    paused: bool | None = None


class ImportOptions(BaseModel):
    enabled: bool = True


class BulkPolicyOptions(BaseModel):
    refs: list[str]
    enabled: bool


class ProviderPauseOptions(BaseModel):
    model_config = {"extra": "forbid"}
    paused: bool


class PollNowOptions(BaseModel):
    source: str | None = None


class ReconcileControlOptions(BaseModel):
    model_config = {"extra": "forbid"}
    running: bool | None = None
    concurrency: int | None = Field(default=None, ge=1, le=32)
    checks_per_second: float | None = Field(default=None, ge=.1, le=20)
    recheck_s: int | None = Field(default=None, ge=60, le=604800)
    retry_s: int | None = Field(default=None, ge=60, le=604800)


class SchedulerControlOptions(BaseModel):
    running: bool
    note: str | None = None


class ProviderGroupOptions(BaseModel):
    # None asks for a new group; an existing id keeps fold state across renames.
    id: int | None = None
    name: str
    sources: list[str] = Field(default_factory=list, max_length=1000)


class ProviderGroupsOptions(BaseModel):
    # The whole board layout, in order: every edit replaces it atomically.
    groups: list[ProviderGroupOptions] = Field(max_length=200)


class TokenHeartbeatControl(BaseModel):
    paused: bool


class TokenSweepStart(BaseModel):
    # None sweeps the whole never-checked backlog.
    limit: int | None = Field(default=None, ge=1)
    ats: list[str] | None = None


class TokenSweepPause(BaseModel):
    paused: bool


class TokenIdentity(BaseModel):
    """One ``(ats, token)`` pair, the natural key of a board."""

    ats: str
    token: str


class TokenEnableBody(BaseModel):
    """Add to feed: exact tokens, whole vendors under the page's view, or both."""

    tokens: list[TokenIdentity] = []
    vendors: list[str] = []
    filter: str = "inactive"
    q: str | None = None
    # None keeps the feed default; bounds match the Feeds page's interval edit.
    interval_s: int | None = Field(default=None, ge=60, le=7 * 86_400)


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


class CompanyVerifyBody(BaseModel):
    """Operator corrections. Omitted means "leave alone"; "" means "clear".

    The two have to stay distinguishable: ``None`` for a field the dialog never
    showed and ``""`` for a box the operator deliberately emptied are different
    instructions, and collapsing them would silently wipe columns.
    """

    name: str | None = None
    domain: str | None = None
    description: str | None = None


class CompanyReviewBody(BaseModel):
    state: Literal["rejected", "pending"]


class CompanyMergeBody(BaseModel):
    """The row that survives. The path's company is the one folded in."""

    target_id: int


class CompanyLogoBody(BaseModel):
    """Fetch against a domain the operator has typed but not yet saved."""

    domain: str | None = None


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


def create_app(*, runtime: Any, token_sweep: Any | None = None) -> FastAPI:
    """Build Inspector around the unified runtime that owns its workers."""
    store = runtime.result_store
    policy_store = runtime.policy
    database = runtime.catalog
    inspector = runtime.inspector
    scheduler_runner = runtime.scheduler
    heartbeat_runner = runtime.heartbeat
    closure_analytics = closures.Analytics()
    # In-process and in-memory by design: a restart just stops it, and the
    # token rows it already wrote are the progress the next Start resumes.
    sweep_runner = token_sweep if token_sweep is not None else SweepRunner(policy_store)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        await runtime.start()
        try:
            yield
        finally:
            await sweep_runner.stop()
            await runtime.stop()

    application = FastAPI(title="xjobscore inspector", lifespan=lifespan)
    application.state.inspector_db = database
    application.state.result_store = store
    application.state.inspector = inspector
    application.state.policy = policy_store
    application.state.scheduler_runner = scheduler_runner
    application.state.token_heartbeat_runner = heartbeat_runner
    application.state.token_sweep = sweep_runner
    application.state.system_runtime = runtime

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
        """Scheduler-page rows: every feed with a row in ``feed``.

        Membership is the row's existence, not ``feed.polling``. The column says
        whether a member is *running*, and the page's Pause/Resume writes it --
        so a held feed has to stay listed or there would be no way to resume it.
        Leaving the fleet is Drop or Mute, which delete the row.

        With PostgreSQL down (policy None) membership is unknowable, so keep
        every row rather than flash an empty page.
        """
        return [
            row
            for row in rows
            if row["feed_ref"] is not None
            and (row["policy"] is None or row["policy"].get("in_postgres"))
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

    @application.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @application.get("/readyz")
    async def readyz() -> Response:
        payload = await runtime.status()
        return JSONResponse(payload, status_code=200 if payload["ready"] else 503)

    @application.get("/api/system")
    async def system_status() -> dict[str, Any]:
        return await runtime.status()

    # ---- System page: live state from process memory, history from PostgreSQL.

    @application.get("/api/system/live")
    async def system_live() -> dict[str, Any]:
        """What the workers are doing right now. Memory only; polled every 2s."""
        busy = activity.INFLIGHT.snapshot()
        names = {
            name for name in set(source_registry.SOURCES) | set(busy)
            if not source_disabled(name)
        }
        rates = getattr(scheduler_runner, "rate_groups", dict)()

        def pool(name: str) -> dict[str, Any]:
            running = busy.get(name, [])
            lanes = source_registry.lanes(name)
            if len(lanes) == 1:
                return {"width": source_registry.width(name), "busy": running}
            # A partitioned vendor runs one pool per lane (`sources.lanes`);
            # each reports its own slots and the budget `Net` charges it.
            split = []
            for lane in lanes:
                group = source_registry.rate_group(lane)
                stats = rates.get(group, {})
                split.append({
                    "lane": lane,
                    "label": lane.partition(":")[2],
                    "width": source_registry.width(lane),
                    "busy": [
                        row for row in running
                        if source_registry.lane_of(name, row["key"]) == lane
                    ],
                    "rate_group": group,
                    "budget": source_registry.rate_budget(lane),
                    "rate": stats.get("rate"),
                    "throttled": stats.get("throttled"),
                })
            return {
                "width": sum(lane["width"] for lane in split),
                "busy": running,
                "lanes": split,
                "partition": {
                    "index": source_registry.partition_index(name),
                    "named": list(source_registry.partitions(name)),
                },
            }

        return {
            "observed_at": now_utc().isoformat(),
            "workers": {name: pool(name) for name in sorted(names)},
            "feeds": activity.POLLS.summary(15),
            "polls_total": activity.POLLS.total,
            "enrichment": runtime.gold_worker.status(),
        }

    @application.get("/api/system/events")
    async def system_events(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
        """Recent system events and the latest completed enrichment, including
        changed feeds enriched immediately and background catch-up runs."""
        pool = await policy_store.pool_or_none()
        if pool is None:
            return {"events": [], "gold_last": None, "telemetry_available": False}
        try:
            async with pool.acquire() as conn:
                events = await conn.fetch(
                    """
                    SELECT id, job_type, identity, state, started_at, finished_at,
                           detail, error
                    FROM system_run
                    WHERE job_type <> 'feed' OR state = 'failed' OR detail ? 'gold'
                    ORDER BY started_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
                gold_last = await conn.fetchrow(
                    """
                    SELECT id, job_type, identity, state, started_at, finished_at,
                           detail, error
                    FROM system_run
                    WHERE state <> 'running' AND finished_at IS NOT NULL
                      AND (job_type = 'gold_catchup'
                           OR (job_type = 'feed' AND detail ? 'gold'))
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                )
        except asyncpg.UndefinedTableError:
            return {"events": [], "gold_last": None, "telemetry_available": False}

        def run_row(row: Any) -> dict[str, Any]:
            return {
                **dict(row),
                "started_at": row["started_at"].isoformat(),
                "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
            }

        return {
            "events": [run_row(row) for row in events],
            "gold_last": run_row(gold_last) if gold_last else None,
            "telemetry_available": True,
        }

    @application.get("/api/system/logs")
    async def system_logs(after: int = Query(0, ge=0)) -> dict[str, Any]:
        """Server log lines newer than ``after`` (a sequence number)."""
        return activity.LOGS.since(after)

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
        return {"sources": [
            row for row in await store.source_stats() if not source_disabled(row["source"])
        ]}

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
    # Reads public.poll_log, which run.py appends to once per completed
    # poll. Every response still has to be correct with the table absent,
    # present but empty, and populated -- see `telemetry_available`.

    async def scheduler_rows(current) -> tuple[list[dict[str, Any]], bool]:
        """Imported feeds enriched with production telemetry and status."""
        pool = await policy_store.pool_or_none()
        daily = rollups = postings = None
        if pool is not None:
            merged, daily, rollups, postings = await asyncio.gather(
                merged_feed_rows(),
                telemetry.feed_daily_counts(pool, now=current),
                telemetry.feed_rollups(pool, now=current),
                telemetry.posting_stats_by_source(pool, now=current),
            )
        else:
            merged = await merged_feed_rows()
        rows = scheduled_rows(merged)
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

    def runner_accepting() -> bool:
        status = scheduler_runner.status()
        return bool(status.get("alive") and status.get("accepting", True))

    @application.get("/api/scheduler/overview")
    async def scheduler_overview() -> dict[str, Any]:
        current = now_utc()
        pool = await policy_store.pool_or_none()
        tiles = None
        if pool is not None:
            merged, tiles = await asyncio.gather(
                merged_feed_rows(), telemetry.global_tiles(pool, now=current)
            )
        else:
            merged = await merged_feed_rows()
        rows = scheduled_rows(merged)
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

    @application.get("/api/reconcile/control")
    async def reconcile_get() -> dict[str, Any]:
        pool = await _public_pool()
        try:
            return await reconcile.snapshot(pool, runtime.reconciler)
        except (asyncpg.PostgresError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @application.post("/api/reconcile/control")
    async def reconcile_set(body: ReconcileControlOptions) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            async with pool.acquire() as conn:
                await reconcile.set_control(conn, body.model_dump(exclude_unset=True, exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (asyncpg.PostgresError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        runtime.reconciler.nudge()
        return await reconcile_get()

    @application.get("/api/scheduler/control")
    async def scheduler_control_get() -> dict[str, Any]:
        return await control_payload()

    @application.post("/api/scheduler/providers/{source}/pause")
    async def scheduler_provider_pause(
        source: str, body: ProviderPauseOptions
    ) -> dict[str, Any]:
        try:
            result = await policy_store.set_provider_paused(source, body.paused)
        except (PolicyUnavailable, ValueError) as exc:
            raise _policy_error(exc) from exc
        if not result["affected"]:
            raise HTTPException(status_code=404, detail="No feeds for this provider")
        if not body.paused:
            scheduler_runner.nudge()
        return result

    @application.post("/api/scheduler/control")
    async def scheduler_control_set(body: SchedulerControlOptions) -> dict[str, Any]:
        try:
            await policy_store.set_scheduler_running(body.running, body.note)
        except (PolicyUnavailable, FeedNotInPostgres, ValueError) as exc:
            raise _policy_error(exc) from exc
        # Pause must wake the scheduler too, so its active lanes stop claiming
        # without waiting for the next periodic tick. In-flight polls finish.
        scheduler_runner.nudge()
        return await control_payload()

    # Board grouping is display state: unavailable (no PostgreSQL, or the
    # tables not migrated yet) reads as "no groups" plus the reason, so the
    # board still renders and only the editing controls switch off.
    @application.get("/api/scheduler/groups")
    async def scheduler_groups() -> dict[str, Any]:
        try:
            groups = await policy_store.provider_groups()
        except PolicyUnavailable as exc:
            return {"available": False, "error": str(exc), "groups": []}
        return {"available": True, "error": None, "groups": groups}

    @application.put("/api/scheduler/groups")
    async def scheduler_groups_set(body: ProviderGroupsOptions) -> dict[str, Any]:
        try:
            groups = await policy_store.set_provider_groups(
                [group.model_dump() for group in body.groups]
            )
        except (PolicyUnavailable, ValueError) as exc:
            raise _policy_error(exc) from exc
        return {"available": True, "error": None, "groups": groups}

    @application.post("/api/gold/dedup")
    async def gold_dedup_start() -> dict[str, Any]:
        pool = await _public_pool()
        queued = await runtime.coordinator.queue_dedup(pool, current=now_utc())
        runtime.gold_worker.nudge()
        return queued

    @application.get("/api/gold/dedup")
    async def gold_dedup_status() -> dict[str, Any]:
        pool = await _public_pool()
        return (await runtime.coordinator.status(pool))["gold"]

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

    @application.post("/api/scheduler/feeds/{feed_ref}/poll-now", status_code=202)
    async def scheduler_poll_now(feed_ref: str) -> dict[str, Any]:
        """Poll one feed now: front of the queue, and wake the runner for it.

        The fetch still happens on the production path -- policy writes the
        queue position, the runner's run.cycle claims and polls it -- so
        leasing, vendor pacing, and poll_log behave exactly as a
        scheduled poll. ``runner_alive`` tells the page whether the poll is
        about to happen in this process or is waiting on another one's tick.
        """
        row = await configured_feed(feed_ref)
        # Checked here rather than in policy.py: aliveness is a property of this
        # process, not of the database. Cheap and DSN-free, so it goes first.
        if not runner_accepting():
            raise _policy_error(
                PollNowRefused(
                    "no scheduler runner is listening, so nothing would poll this "
                    "feed; start the system with `uv run xjobscore`",
                    reason="no_runner",
                )
            )
        try:
            result = await policy_store.poll_now(row["source"], row["key"])
        except (PolicyUnavailable, PollNowRefused, FeedNotInPostgres, ValueError) as exc:
            raise _policy_error(exc) from exc
        scheduler_runner.nudge()
        return {
            **result,
            "queued": True,
            "runner_alive": scheduler_runner.status()["alive"],
        }

    @application.post("/api/scheduler/poll-now", status_code=202)
    async def scheduler_poll_now_all(
        options: PollNowOptions | None = None,
    ) -> dict[str, Any]:
        """Bring every enabled feed forward, optionally limited to one source.

        Keeps ``due_at = now()``: a catalog-wide bump has no front of the queue
        to claim, and backdating thousands of feeds would only reshuffle them
        among themselves. The nudge still applies -- the drain loop should start
        working the backlog now, not on the next tick.
        """
        if not runner_accepting():
            raise _policy_error(
                PollNowRefused(
                    "no unified scheduler is listening",
                    reason="no_runner",
                )
            )
        source = (options or PollNowOptions()).source
        try:
            result = await policy_store.poll_now_all(source)
        except (PolicyUnavailable, PollNowRefused, FeedNotInPostgres, ValueError) as exc:
            raise _policy_error(exc) from exc
        scheduler_runner.nudge()
        return {
            **result,
            "queued": True,
            "runner_alive": scheduler_runner.status()["alive"],
        }

    @application.post("/api/scheduler/feeds/{feed_ref}/fetch", status_code=202)
    async def scheduler_fetch(feed_ref: str) -> dict[str, Any]:
        """Queue one production poll; HTTP requests never execute workers."""
        row = await configured_feed(feed_ref)
        if not runner_accepting():
            raise _policy_error(
                PollNowRefused(
                    "no unified scheduler is listening",
                    reason="no_runner",
                )
            )
        try:
            result = await policy_store.poll_now(row["source"], row["key"])
        except (PolicyUnavailable, PollNowRefused, FeedNotInPostgres, ValueError) as exc:
            raise _policy_error(exc) from exc
        scheduler_runner.nudge()
        return {
            **result,
            "queued": True,
            "runner_alive": scheduler_runner.status()["alive"],
        }

    async def _public_pool():
        """The borrowed public pool, or a 503 naming what is unreachable."""
        pool = await policy_store.pool_or_none()
        if pool is None:
            raise HTTPException(
                status_code=503,
                detail="PostgreSQL unavailable",
            )
        return pool


    async def closure_snapshot(tz: str):
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Unknown timezone") from exc
        pool = await _public_pool()
        try:
            return pool, await closure_analytics.snapshot(pool)
        except asyncpg.UndefinedTableError as exc:
            raise HTTPException(status_code=503, detail="Closure analytics needs the closure and link-check tables.") from exc
        except (asyncpg.PostgresError, OSError) as exc:
            raise HTTPException(status_code=503, detail="Closure analytics is temporarily unavailable. Try again.") from exc

    @application.get("/api/closures")
    async def closure_summary(days: int = Query(7, ge=1, le=90), tz: str = "America/Los_Angeles"):
        _, snapshot = await closure_snapshot(tz)
        return closures.summary(snapshot, days, tz)

    @application.get("/api/closures/postings")
    async def closure_postings(
        days: int = Query(7, ge=1, le=90), tz: str = "America/Los_Angeles",
        view: Literal["closed", "unresolved", "review"] = "closed",
        vendor: str | None = None,
        reason: Literal["ats", "dead", "message", "redirect", "page_unknown", "unknown"] | None = None,
        day: date | None = None, age: int | None = Query(None, ge=0, le=4),
        outcome: Literal["unchecked", "live", "inconclusive", "no_url", "cancelled", "absent", "page_closed"] | None = None,
        q: str = Query("", max_length=200), offset: int = Query(0, ge=0),
        limit: int = Query(25, ge=1, le=100),
    ):
        pool, snapshot = await closure_snapshot(tz)
        try:
            return await closures.postings(pool, snapshot, days=days, tz=tz, view=view,
                                           vendor=vendor, reason=reason, day=day.isoformat() if day else None,
                                           age=age, outcome=outcome, q=q, offset=offset, limit=limit)
        except (asyncpg.PostgresError, OSError) as exc:
            raise HTTPException(status_code=503, detail="Could not read postings. Try again.") from exc

    @application.get("/api/tokens")
    async def tokens(
        filter: str = "inactive",
        limit: int = Query(default=50, ge=1, le=10000),
        offset: int = Query(default=0, ge=0),
        ats: str | None = None,
        groups: bool = False,
        q: str | None = None,
        order: str = "token",
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
                    ats=ats or None,
                    groups=groups,
                    q=q,
                    order=order,
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
        if not body.tokens and not body.vendors:
            raise HTTPException(status_code=400, detail="tokens or vendors must not be empty")
        vendors = set(body.vendors)
        # A token whose vendor is also ticked is already covered by it.
        identities = list(dict.fromkeys(
            (entry.ats, entry.token) for entry in body.tokens if entry.ats not in vendors
        ))
        pool = await _public_pool()
        try:
            added = await tokens_promote_verified(pool, identities, interval_s=body.interval_s)
            if vendors:
                added += await tokens_add_vendors_to_feed(
                    pool, sorted(vendors), filter=body.filter, q=body.q,
                    interval_s=body.interval_s,
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        return {
            "requested": len(identities),
            "vendors": sorted(vendors),
            "imported": added,
            "skipped": max(0, len(identities) - added) if not vendors else 0,
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

    @application.get("/api/tokens/log")
    async def token_log(limit: int = 50) -> dict[str, Any]:
        """The newest liveness probes and the newest feed arrivals/departures.

        Both tails come from ``token_log`` and both panels refresh together, so
        one route serves them rather than making the page poll twice for two
        slices of the same table.

        Degrades the way the scheduler's fetch log does: a missing table or an
        unreachable database reports ``log_available: false`` rather than an
        empty list, because "nothing is recording" and "nothing was checked"
        must not render the same.
        """
        pool = await policy_store.pool_or_none()
        checks = None
        events = None
        if pool is not None:
            checks = await tokens_recent_checks(pool, limit=limit)
            events = await tokens_recent_feed_events(pool, limit=limit)
        return {
            "postgres": policy_store.status(),
            "log_available": checks is not None,
            "checks": checks or [],
            "feed_log_available": events is not None,
            "feed_events": events or [],
        }

    async def sweep_payload() -> dict[str, Any]:
        try:
            backlog = await sweep_runner.backlog()
        except (OSError, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        return {**sweep_runner.snapshot(), "backlog": backlog}

    @application.get("/api/tokens/sweep")
    async def token_sweep_get() -> dict[str, Any]:
        return await sweep_payload()

    @application.post("/api/tokens/sweep/start", status_code=202)
    async def token_sweep_start(body: TokenSweepStart) -> dict[str, Any]:
        await _public_pool()
        try:
            await sweep_runner.start(limit=body.limit, ats=body.ats or None)
        except SweepAlreadyRunning as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (OSError, asyncpg.PostgresError, RuntimeError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        return await sweep_payload()

    @application.post("/api/tokens/sweep/pause")
    async def token_sweep_pause(body: TokenSweepPause) -> dict[str, Any]:
        sweep_runner.pause(body.paused)
        return await sweep_payload()

    @application.post("/api/tokens/sweep/cancel")
    async def token_sweep_cancel() -> dict[str, Any]:
        await sweep_runner.cancel()
        return await sweep_payload()

    @application.get("/api/tokens/heartbeat")
    async def token_heartbeat_get() -> dict[str, Any]:
        return await heartbeat_payload()

    @application.post("/api/tokens/heartbeat/run", status_code=202)
    async def token_heartbeat_run() -> dict[str, Any]:
        pool = await _public_pool()
        try:
            await token_heartbeat_enqueue(pool, now_utc())
        except (HeartbeatAlreadyRunning, HeartbeatPaused) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (OSError, asyncpg.PostgresError, RuntimeError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        heartbeat_runner.nudge()
        return await heartbeat_payload()

    @application.post("/api/tokens/heartbeat/control")
    async def token_heartbeat_control(body: TokenHeartbeatControl) -> dict[str, Any]:
        """The operator's pause/resume, written straight to PostgreSQL.

        Deliberately independent of whether this process runs the loop: the
        switch is shared state, so the button works from any inspector and a
        process with the env var off can still pause the one that does.
        """
        pool = await _public_pool()
        try:
            await token_heartbeat_set_paused(pool, body.paused, current=now_utc())
        except (OSError, asyncpg.PostgresError, RuntimeError) as exc:
            raise HTTPException(
                status_code=503, detail=f"PostgreSQL unavailable: {exc}"
            ) from exc
        if not body.paused:
            # Resume acts now rather than at the next tick.
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
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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

    # --- KeyScan -------------------------------------------------------
    # Read-only views over keyscan's own SQLite file, plus the one write the
    # page can make (the existing keyscan_import bridge). These are `def`, not
    # `async def`, on purpose: every call is blocking sqlite3, so FastAPI must
    # run them in its threadpool rather than on the event loop.

    def _keyscan_read(call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        except keyscan_view.DatabaseMissing as exc:
            # Not an error: an inspector on a machine that has never run a
            # scan is working correctly, it just has nothing to show.
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except sqlite3.Error as exc:
            raise HTTPException(
                status_code=503, detail=f"keyscan database unreadable: {exc}"
            ) from exc

    async def _annotate_in_postgres(rows: list[dict[str, Any]]) -> None:
        """Set `in_postgres` on each row, or None when PostgreSQL is unreachable.

        Names are mapped through the import bridge's own helpers first: keyscan
        stores pre-rename labels and Workday's mixed-case tokens, so a raw
        (ats, token) comparison would report live rows as missing.
        """
        for row in rows:
            row["in_postgres"] = None
        if not rows:
            return
        wanted = []
        for row in rows:
            ats = resolve_keyscan_ats(row["ats"])
            wanted.append((ats, canonical_feed_key(ats, row["key"])))
        try:
            pool = await _public_pool()
            found = await pool.fetch(
                """
                SELECT ats, token FROM token
                 WHERE (ats, token) IN (
                       SELECT * FROM unnest($1::text[], $2::text[]))
                """,
                [pair[0] for pair in wanted],
                [pair[1] for pair in wanted],
            )
        except (OSError, asyncpg.PostgresError, RuntimeError, HTTPException):
            # Degraded, not broken: the page still lists candidates, it just
            # cannot say which are already in the inventory.
            return
        present = {(record["ats"], record["token"]) for record in found}
        for row, pair in zip(rows, wanted):
            row["in_postgres"] = pair in present

    @application.get("/api/keyscan/status")
    def keyscan_status() -> dict[str, Any]:
        run = _keyscan_read(keyscan_view.status)
        if run is not None and run.get("status") == "running":
            # A row can say `running` after its process died (kill -9, power
            # cut). The page needs that distinction to avoid offering a cancel
            # for a run that is already wreckage.
            run["pid_alive"] = keyscan_control.process_alive(run.get("pid"))
        return {"run": run}

    @application.get("/api/keyscan/tally")
    def keyscan_tally() -> dict[str, Any]:
        return _keyscan_read(keyscan_view.tally)

    @application.get("/api/keyscan/coverage")
    def keyscan_coverage() -> dict[str, Any]:
        """The crawls x vendors scan-coverage matrix, computed locally."""
        return _keyscan_read(keyscan_view.coverage)

    @application.get("/api/keyscan/runs")
    def keyscan_runs(limit: int = 20) -> dict[str, Any]:
        return {"runs": _keyscan_read(keyscan_view.runs, limit=limit)}

    @application.get("/api/keyscan/runs/{run_id}/log")
    def keyscan_run_log(run_id: str, offset: int = 0, limit: int = 200) -> dict[str, Any]:
        return _keyscan_read(keyscan_view.run_log, run_id, offset=offset, limit=limit)

    @application.get("/api/keyscan/found")
    def keyscan_found(limit: int = 30) -> dict[str, Any]:
        return {"found": _keyscan_read(keyscan_view.found, limit=limit)}

    @application.get("/api/keyscan/candidates")
    async def keyscan_candidates(
        status: str | None = None,
        source: str | None = None,
        q: str = "",
        imported: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Candidates, annotated with whether each is really in PostgreSQL.

        `imported_at` is only keyscan's record of what the bridge sent, which
        is not the same question as "is this in the inventory now" -- a token
        can reach `token` by another path, and a sent row can be missing. So
        membership is checked against PostgreSQL itself.

        Async, so the blocking SQLite reads go to the threadpool explicitly.
        """
        rows = await run_in_threadpool(
            _keyscan_read,
            keyscan_view.candidates,
            status_filter=status,
            source=source,
            query=q,
            imported=imported,
            limit=limit,
            offset=offset,
        )
        total = await run_in_threadpool(
            _keyscan_read,
            keyscan_view.candidate_count,
            status_filter=status,
            source=source,
            query=q,
            imported=imported,
        )
        await _annotate_in_postgres(rows)
        return {"candidates": rows, "total": total, "offset": offset}

    @application.post("/api/keyscan/run", status_code=202)
    def keyscan_run(body: KeyscanRunBody) -> dict[str, Any]:
        """Launch a detached ``keyscan crawl``.

        Returns as soon as the OS has the process; keyscan writes its own
        ``runs`` row moments later and the page finds it on the next poll.
        """
        try:
            return _keyscan_read(
                keyscan_control.start,
                date_from=body.date_from,
                date_to=body.date_to,
                crawl_ids=body.crawl_ids,
                sources=body.sources,
                validate=body.validate_tokens,
                rescan=body.rescan,
            )
        except keyscan_control.RunAlreadyActive as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except keyscan_control.LaunchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.get("/api/keyscan/crawls")
    async def keyscan_crawls() -> dict[str, Any]:
        """Real Common Crawl snapshots available right now, for the run picker."""
        # Imported here, not at module scope: `keyscan.core` pulls in duckdb,
        # and the read-only page should not pay for that on every inspector
        # start (see keyscan_control.cancel for the same reasoning).
        from keyscan.core import Keyscan, KeyscanError

        scan = Keyscan(keyscan_view.database_path().parent)
        try:
            result = await scan.list_crawls()
        except KeyscanError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        # Which snapshots a scan can plan from footers instead of reading all
        # ~600 files -- the picker marks them, so the cheap ones are visible.
        footers = _keyscan_read(keyscan_view.footer_status)
        for crawl in result["crawls"]:
            crawl["footers"] = footers.get(crawl["id"])
        return result

    @application.post("/api/keyscan/runs/{run_id}/cancel")
    def keyscan_cancel(run_id: str) -> dict[str, Any]:
        """Stop a running scan by writing the row it polls -- no signal."""
        try:
            return _keyscan_read(keyscan_control.cancel, run_id)
        except keyscan_control.LaunchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/keyscan/import")
    async def keyscan_import() -> dict[str, Any]:
        """Bulk-load keyscan's confirmed-live tokens into PostgreSQL.

        Delegates to the same idempotent bridge the CLI uses; unresolvable
        rows are skipped and retried on every future run, so calling this
        twice is cheap and safe.
        """
        pool = await _public_pool()
        path = keyscan_view.database_path()
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"no keyscan database at {path}")
        try:
            result = await import_keyscan_tokens(pool, path)
        except (OSError, sqlite3.Error, asyncpg.PostgresError) as exc:
            raise HTTPException(
                status_code=503, detail=f"import failed: {exc}"
            ) from exc
        return {
            "written": result.written,
            "skipped": result.skipped,
            "by_ats": result.by_ats,
        }

    async def _company_or_404(company_id: int):
        pool = await _public_pool()
        row = await companies_view.get(pool, company_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no company {company_id}")
        return row

    def _company_write_error(
        exc: Exception, owner: dict[str, Any] | None = None
    ) -> HTTPException:
        if isinstance(exc, asyncpg.UniqueViolationError):
            # company.domain is UNIQUE and nothing else on the row is. A
            # collision means this domain already names a different company,
            # which is a merge the operator has to make deliberately -- so the
            # refusal names the other row rather than leaving it to be hunted.
            detail: dict[str, Any] = {
                "message": "that domain already belongs to another company",
                "hint": "domain-taken",
            }
            if owner is not None:
                detail["owner"] = owner
                detail["message"] = (
                    f"{owner['name']} already owns that domain"
                )
            return HTTPException(status_code=409, detail=detail)
        if isinstance(exc, ValueError):
            return HTTPException(status_code=400, detail=str(exc))
        return HTTPException(status_code=503, detail=f"PostgreSQL unavailable: {exc}")

    @application.get("/api/companies")
    async def companies(
        filter: str = "queue",
        order: str = "impact",
        search: str = "",
        limit: int = Query(default=100, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            return {
                "postgres": policy_store.status(),
                **await companies_view.query(
                    pool,
                    filter=filter,
                    order=order,
                    search=search,
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

    @application.get("/api/companies/{company_id}")
    async def company_detail(company_id: int) -> dict[str, Any]:
        return await _company_or_404(company_id)

    @application.post("/api/companies/{company_id}/verify")
    async def company_verify(
        company_id: int, body: CompanyVerifyBody
    ) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            row = await companies_view.verify(
                pool,
                company_id,
                name=body.name,
                domain=body.domain,
                description=body.description,
            )
        except asyncpg.UniqueViolationError as exc:
            owner = await companies_view.owner_of_domain(pool, body.domain)
            raise _company_write_error(exc, owner) from exc
        except (ValueError, OSError, asyncpg.PostgresError) as exc:
            raise _company_write_error(exc) from exc
        if row is None:
            raise HTTPException(status_code=404, detail=f"no company {company_id}")
        return row

    @application.post("/api/companies/{company_id}/merge")
    async def company_merge(
        company_id: int, body: CompanyMergeBody
    ) -> dict[str, Any]:
        """Fold `company_id` into `body.target_id`; the target survives."""
        pool = await _public_pool()
        try:
            row = await companies_view.merge(pool, company_id, body.target_id)
        except (ValueError, OSError, asyncpg.PostgresError) as exc:
            raise _company_write_error(exc) from exc
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"no company {company_id} or {body.target_id}",
            )
        return row

    @application.post("/api/companies/{company_id}/logo")
    async def company_logo_fetch(
        company_id: int, body: CompanyLogoBody
    ) -> dict[str, Any]:
        """Harvest this company's logo from its own site, now."""
        pool = await _public_pool()
        try:
            payload = await logo_harvest.harvest_one(
                pool, company_id, domain=body.domain
            )
        except (OSError, asyncpg.PostgresError) as exc:
            raise _company_write_error(exc) from exc
        if payload is None:
            # Not an error: a domain that ships no usable icon is a real
            # answer, and the row now records that it was asked.
            return {"has_logo": False, "bytes": 0}
        return {"has_logo": True, "bytes": len(payload)}

    @application.get("/api/companies/{company_id}/logo")
    async def company_logo_image(company_id: int) -> Response:
        """Serve the stored logo so the page can show what it just fetched."""
        pool = await _public_pool()
        payload = await pool.fetchval(
            "SELECT bytes FROM company_logo WHERE company_id = $1", company_id
        )
        if not payload:
            raise HTTPException(status_code=404, detail="no logo")
        return Response(content=bytes(payload), media_type="image/png")

    @application.post("/api/companies/{company_id}/review")
    async def company_review(
        company_id: int, body: CompanyReviewBody
    ) -> dict[str, Any]:
        pool = await _public_pool()
        try:
            row = await companies_view.set_review(pool, company_id, state=body.state)
        except (ValueError, OSError, asyncpg.PostgresError) as exc:
            raise _company_write_error(exc) from exc
        if row is None:
            raise HTTPException(status_code=404, detail=f"no company {company_id}")
        return row

    # The System page is the landing page; the Feeds list moved to /feeds.
    @application.get("/")
    async def index():
        return RedirectResponse("/system", status_code=307)

    @application.get("/feeds")
    async def feeds_page():
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/feed")
    async def feed_page():
        return FileResponse(STATIC_DIR / "feed" / "index.html")

    # The Scheduler board now lives on the System page.
    @application.get("/scheduler")
    async def scheduler_page():
        return RedirectResponse("/system", status_code=307)

    @application.get("/system")
    async def system_page():
        return FileResponse(STATIC_DIR / "system" / "index.html")


    @application.get("/tokens")
    async def tokens_page():
        return FileResponse(STATIC_DIR / "tokens" / "index.html")

    @application.get("/keyscan")
    async def keyscan_page():
        return FileResponse(STATIC_DIR / "keyscan" / "index.html")

    @application.get("/companies")
    async def companies_page():
        return FileResponse(STATIC_DIR / "companies" / "index.html")

    @application.get("/closures")
    async def closures_page():
        return FileResponse(STATIC_DIR / "closures" / "index.html")

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return application
