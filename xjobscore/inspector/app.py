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
from xjobscore.inspector.runner import GoldJob, SchedulerRunner
from xjobscore.inspector.service import InspectorService
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


class DuplicateReviewBody(BaseModel):
    decision: Literal["correct", "incorrect", "needs_review", "hidden"] | None = None
    note: str | None = None


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
    gold_job: Any | None = None,
) -> FastAPI:
    """Build an inspector app, optionally with injected test collaborators."""

    database = catalog if catalog is not None else CatalogDB()
    store = store if store is not None else ResultStore(
        config.persistence_dsn(), schema=config.persistence_schema()
    )
    policy_store = policy if policy is not None else PolicyStore(config.dsn_or_none())
    inspector = InspectorService(
        database,
        user_agent=user_agent or config.user_agent(),
        result_store=store,
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
    dedup_job = gold_job if gold_job is not None else GoldJob(policy_store)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        await database.connect()
        try:
            await store.initialize()
            await policy_store.start()
            await scheduler_runner.start()
            yield
        finally:
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

        ``feed.enabled`` *is* the membership test. The Feeds page owns it end to
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
    ) -> dict[str, Any]:
        snapshot = await inspector.get_result_ref(feed_ref)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No result for this feed")
        end = min(offset + limit, snapshot.item_count)
        return {
            "result": snapshot.summary,
            "network": snapshot.network,
            "envelope": snapshot.envelope,
            "items": [snapshot.item_summary(i) for i in range(offset, end)],
            "offset": offset,
            "limit": limit,
            "has_more": end < snapshot.item_count,
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

    # --- Scheduler: production poll telemetry ------------------------------
    # Reads public.poll_observation, which run.py appends to once per completed
    # poll. Every response still has to be correct with the table absent,
    # present but empty, and populated -- see `telemetry_available`.

    async def scheduler_rows(current) -> tuple[list[dict[str, Any]], bool]:
        """Imported feeds, each enriched with poll telemetry and its estimates.

        Returns the rows plus whether poll_observation exists at all. Two passes
        over the rows are needed: the per-source prior is the median λ of that
        source's own feeds, so every raw λ has to be known before any of them can
        be shrunk.
        """
        rows = scheduled_rows(await merged_feed_rows())
        pool = await policy_store.pool_or_none()
        daily = rollups = postings = None
        if pool is not None:
            daily = await telemetry.feed_daily_counts(pool, now=current)
            rollups = await telemetry.feed_rollups(pool, now=current)
            postings = await telemetry.posting_stats_by_source(pool, now=current)
        available = daily is not None and rollups is not None

        raw_by_source: dict[str, list[float | None]] = {}
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
            row["_lambda_raw"] = scheduling.ewma_lambda(spark)
            raw_by_source.setdefault(row["source"], []).append(row["_lambda_raw"])

        priors = scheduling.source_priors(raw_by_source)
        for row in rows:
            tel = row["telemetry"]
            prior = scheduling.prior_for(row["source"], priors)
            lam = scheduling.shrunk_lambda(
                row.pop("_lambda_raw"),
                (tel or {}).get("observed_days") or 0,
                prior,
            )
            interval_s = (row.get("policy") or {}).get("interval_s")
            ideal_s = scheduling.ideal_interval_s(lam)
            row["sched"] = {
                "lambda": lam,
                "prior": prior,
                "ideal_interval_s": ideal_s,
                "verdict": scheduling.interval_verdict(interval_s, ideal_s),
                "requests_per_day": scheduling.requests_per_day(interval_s),
                "status": scheduling.feed_status(row.get("policy"), tel, current),
            }

        # Busiest first; unknown rates sort last rather than as zero.
        rows.sort(
            key=lambda row: (
                row["sched"]["lambda"] is None,
                -(row["sched"]["lambda"] or 0.0),
                row["source"],
                row["key"],
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
        rows, available = await scheduler_rows(current)
        pool = await policy_store.pool_or_none()
        tiles = (
            await telemetry.global_tiles(pool, now=current)
            if pool is not None
            else None
        )
        counts: dict[str, int] = {}
        for row in rows:
            state = row["sched"]["status"]["state"]
            counts[state] = counts.get(state, 0) + 1
        return {
            "postgres": policy_store.status(),
            "telemetry_available": available,
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

    async def _postings_pool():
        """The borrowed public pool, or a 503 naming what is unreachable."""
        pool = await policy_store.pool_or_none()
        if pool is None:
            raise HTTPException(
                status_code=503,
                detail="PostgreSQL unavailable; postings read public.job_posting",
            )
        return pool

    @application.get("/api/postings")
    async def postings(
        source: str | None = None,
        status: str = "active",
        label_state: str = "all",
        search: str = "",
        intern_signal: bool = False,
        order: str = "newest",
        seed: str = "",
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        pool = await _postings_pool()
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
        pool = await _postings_pool()
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
        pool = await _postings_pool()
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
        pool = await _postings_pool()
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

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return application


app = create_app()


def service() -> InspectorService:
    """Compatibility accessor for code that imports the default app service."""
    return app.state.inspector
