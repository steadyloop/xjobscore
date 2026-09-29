"""Incremental Gold coordination and durable unified-runtime telemetry."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from asyncpg.exceptions import DeadlockDetectedError

from xjobscore import gold
from xjobscore.records import Feed
from xjobscore.silver import ChangeSet

logger = logging.getLogger(__name__)

INCREMENTAL_STEPS = ("job_type", "skills", "degrees", "summaries")
DEDUP_DEBOUNCE_S = 30
DEDUP_MAX_DELAY_S = 5 * 60
DEDUP_LEASE_S = 60 * 60
# Feed workers write job_posting while dedup runs. Dedup only writes what
# changed, so a collision is rare; when Postgres picks dedup as the deadlock
# victim, the whole (atomic) rebuild is simply run again.
DEDUP_ATTEMPTS = 3
DEDUP_RETRY_S = 60
RUN_RETENTION_DAYS = 30


@dataclass(frozen=True, slots=True)
class GoldResult:
    counts: dict[str, gold.Counts] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return bool(self.errors)

    def detail(self) -> dict[str, Any]:
        return {
            "counts": {
                name: {
                    "groups": value.groups,
                    "postings": value.postings,
                    "skipped_keys": value.skipped_keys,
                    "skipped_postings": value.skipped_postings,
                }
                for name, value in self.counts.items()
            },
            "errors": self.errors,
        }


class SystemRuns:
    """Small durable job ledger used by the Inspector's system view."""

    async def begin(
        self, pool: Any, job_type: str, identity: str | None = None
    ) -> int:
        return int(
            await pool.fetchval(
                """
                INSERT INTO system_run (job_type, identity)
                VALUES ($1, $2) RETURNING id
                """,
                job_type,
                identity,
            )
        )

    async def finish(
        self,
        pool: Any,
        run_id: int,
        *,
        state: str,
        detail: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        await pool.execute(
            """
            UPDATE system_run
            SET state = $2,
                finished_at = now(),
                detail = detail || $3::jsonb,
                error = $4
            WHERE id = $1 AND state = 'running'
            """,
            run_id,
            state,
            detail or {},
            error,
        )

    async def startup(self, pool: Any) -> None:
        await pool.execute(
            """
            UPDATE system_run
            SET state = 'interrupted', finished_at = now(),
                error = COALESCE(error, 'system stopped before this run completed')
            WHERE state = 'running'
            """
        )
        await self.prune(pool)

    async def prune(self, pool: Any) -> None:
        await pool.execute(
            "DELETE FROM system_run WHERE started_at < now() - make_interval(days => $1)",
            RUN_RETENTION_DAYS,
        )


class GoldCoordinator:
    """Runs posting-local Gold immediately and global Gold at checkpoints."""

    def __init__(self, runs: SystemRuns | None = None, *, batch_size: int = 1_000) -> None:
        self.runs = runs or SystemRuns()
        self.batch_size = batch_size
        self._backlog: dict[str, int] = {}
        self._wake_callback: Callable[[], None] | None = None

    def set_wake_callback(self, callback: Callable[[], None]) -> None:
        self._wake_callback = callback

    def notify_changed(self) -> None:
        if self._wake_callback is not None:
            try:
                self._wake_callback()
            except Exception:
                logger.exception("could not wake Gold worker")

    def catchup_may_remain(self) -> bool:
        return any(count >= self.batch_size for count in self._backlog.values())

    async def begin_feed(self, pool: Any, feed: Feed) -> int:
        return await self.runs.begin(pool, "feed", f"{feed.source}:{feed.key}")

    async def finish_feed(
        self,
        pool: Any,
        run_id: int,
        *,
        outcome: str,
        changes: ChangeSet,
        gold_result: GoldResult | None = None,
        stages: dict[str, int] | None = None,
        counts: dict[str, Any] | None = None,
        error_stage: str | None = None,
        error: str | None = None,
    ) -> None:
        if error is not None:
            state = "failed"
        elif gold_result is not None and gold_result.degraded:
            state = "degraded"
        else:
            state = "complete"
        detail: dict[str, Any] = {
            "outcome": outcome,
            "changed_postings": len(changes.posting_ids),
            "affected_companies": len(changes.company_ids),
            "stage_ms": stages or {},
            "counts": counts or {},
        }
        if gold_result is not None:
            detail["gold"] = gold_result.detail()
        if error_stage:
            detail["error_stage"] = error_stage
        await self.runs.finish(pool, run_id, state=state, detail=detail, error=error)

    async def mark_changed(
        self, conn: Any, changes: ChangeSet, *, current: datetime
    ) -> None:
        if not changes.posting_ids:
            return
        await conn.execute(
            """
            UPDATE gold_control
            SET generation = generation + 1,
                dirty_at = COALESCE(dirty_at, $1),
                not_before = $1 + ($2 * interval '1 second')
            WHERE id
            """,
            current,
            DEDUP_DEBOUNCE_S,
        )

    async def queue_dedup(self, pool: Any, *, current: datetime) -> dict[str, Any]:
        """Request a checkpoint through the same leased path as automatic work."""
        row = await pool.fetchrow(
            """
            UPDATE gold_control
            SET generation = generation + 1,
                dirty_at = COALESCE(dirty_at, $1),
                not_before = $1
            WHERE id
            RETURNING generation, processed_generation
            """,
            current,
        )
        if row is None:
            raise RuntimeError("gold_control has no singleton row")
        self.notify_changed()
        return {
            "state": "queued",
            "generation": int(row["generation"]),
            "processed_generation": int(row["processed_generation"]),
        }

    async def enrich_changed(self, pool: Any, changes: ChangeSet) -> GoldResult:
        if not changes.posting_ids:
            return GoldResult()
        counts: dict[str, gold.Counts] = {}
        errors: dict[str, str] = {}
        ids = sorted(changes.posting_ids)
        companies = sorted(changes.company_ids)
        for name in INCREMENTAL_STEPS:
            try:
                async with pool.acquire() as conn, conn.transaction():
                    counts[name] = await gold.apply_incremental(
                        conn, name, ids, companies
                    )
            except Exception as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"
                logger.exception("incremental Gold failed step=%s", name)
        return GoldResult(counts=counts, errors=errors)

    async def catch_up_once(
        self,
        pool: Any,
        *,
        accepting: Callable[[], bool] | None = None,
    ) -> GoldResult | None:
        stale: dict[str, list[int]] = {}
        async with pool.acquire() as conn:
            for name in INCREMENTAL_STEPS:
                if accepting is not None and not accepting():
                    return None
                stale[name] = await gold.stale_posting_ids(
                    conn, name, limit=self.batch_size
                )
        self._backlog = {name: len(ids) for name, ids in stale.items()}
        if not any(stale.values()):
            return None

        run_id = await self.runs.begin(pool, "gold_catchup")
        counts: dict[str, gold.Counts] = {}
        errors: dict[str, str] = {}
        interrupted = False
        for name, ids in stale.items():
            if accepting is not None and not accepting():
                interrupted = True
                break
            if not ids:
                continue
            try:
                async with pool.acquire() as conn, conn.transaction():
                    counts[name] = await gold.apply_incremental(conn, name, ids)
            except Exception as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"
                logger.exception("Gold catch-up failed step=%s", name)
        result = GoldResult(counts=counts, errors=errors)
        await self.runs.finish(
            pool,
            run_id,
            state=(
                "interrupted"
                if interrupted
                else "degraded" if result.degraded else "complete"
            ),
            detail={"gold": result.detail()},
            error=(
                "system shutdown interrupted the catch-up batch"
                if interrupted
                else "; ".join(errors.values()) or None
            ),
        )
        return result

    async def dedup_once(
        self, pool: Any, *, queue_drained: bool, current: datetime | None = None
    ) -> gold.Counts | None:
        current = current or datetime.now(timezone.utc)
        hold_until = current + timedelta(seconds=DEDUP_LEASE_S)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE gold_control
                SET hold_until = $2, last_started_at = $1,
                    last_outcome = 'running', last_error = NULL
                WHERE id
                  AND generation > processed_generation
                  AND (hold_until IS NULL OR hold_until <= $1)
                  AND (
                    ($3 AND not_before <= $1)
                    OR dirty_at <= $1 - ($4 * interval '1 second')
                  )
                RETURNING generation
                """,
                current,
                hold_until,
                queue_drained,
                DEDUP_MAX_DELAY_S,
            )
        if row is None:
            return None

        generation = int(row["generation"])
        run_id = await self.runs.begin(pool, "dedup", str(generation))
        try:
            (step,) = gold.steps("dedup")
            for attempt in range(1, DEDUP_ATTEMPTS + 1):
                try:
                    async with pool.acquire() as conn, conn.transaction():
                        counts = await step.rebuild(conn)
                    break
                except DeadlockDetectedError:
                    if attempt == DEDUP_ATTEMPTS:
                        raise
                    logger.warning(
                        "dedup deadlocked with a posting write; retrying attempt=%d generation=%d",
                        attempt + 1,
                        generation,
                    )
            finished = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE gold_control
                    SET processed_generation = GREATEST(processed_generation, $2),
                        dirty_at = CASE WHEN generation <= $2 THEN NULL ELSE dirty_at END,
                        not_before = CASE WHEN generation <= $2 THEN NULL ELSE not_before END,
                        hold_until = NULL,
                        last_completed_at = $1,
                        last_outcome = 'success', last_error = NULL
                    WHERE id AND hold_until = $3
                    """,
                    finished,
                    generation,
                    hold_until,
                )
            await self.runs.finish(
                pool,
                run_id,
                state="complete",
                detail={
                    "generation": generation,
                    "groups": counts.groups,
                    "postings": counts.postings,
                },
            )
            return counts
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE gold_control
                    SET hold_until = NULL, last_completed_at = now(),
                        last_outcome = 'error', last_error = $1
                    WHERE id AND hold_until = $2
                    """,
                    error,
                    hold_until,
                )
            await self.runs.finish(
                pool, run_id, state="failed", error=error,
                detail={"generation": generation},
            )
            logger.exception("dedup checkpoint failed generation=%d", generation)
            return None

    async def next_dedup_delay(
        self,
        pool: Any,
        *,
        queue_drained: bool,
        current: datetime | None = None,
    ) -> float | None:
        """Seconds until the next durable dedup deadline, if one exists."""
        current = current or datetime.now(timezone.utc)
        row = await pool.fetchrow(
            """
            SELECT generation, processed_generation, dirty_at, not_before,
                   hold_until, last_completed_at, last_outcome
            FROM gold_control WHERE id
            """
        )
        if row is None or row["generation"] <= row["processed_generation"]:
            return None
        if row["hold_until"] is not None and row["hold_until"] > current:
            target = row["hold_until"]
        elif row["dirty_at"] is None:
            target = current
        else:
            target = row["dirty_at"] + timedelta(seconds=DEDUP_MAX_DELAY_S)
            if queue_drained and row["not_before"] is not None:
                target = min(target, row["not_before"])
        if row["last_outcome"] == "error" and row["last_completed_at"] is not None:
            target = max(
                target,
                row["last_completed_at"] + timedelta(seconds=DEDUP_RETRY_S),
            )
        return max(0.0, (target - current).total_seconds())

    async def status(self, pool: Any) -> dict[str, Any]:
        async with pool.acquire() as conn:
            control = await conn.fetchrow("SELECT * FROM gold_control WHERE id")
            recent = await conn.fetch(
                """
                SELECT id, job_type, identity, state, started_at, finished_at,
                       detail, error
                FROM system_run ORDER BY started_at DESC LIMIT 20
                """
            )
        value = dict(control) if control else {}
        for key, item in tuple(value.items()):
            if isinstance(item, datetime):
                value[key] = item.isoformat()
        return {
            "gold": value,
            # Bounded counts from the most recent catch-up scan. A value equal
            # to batch_size means "at least this many", without making every
            # dashboard refresh scan the posting table.
            "backlog": self._backlog,
            "backlog_batch_size": self.batch_size,
            "recent_runs": [
                {
                    **dict(row),
                    "started_at": row["started_at"].isoformat(),
                    "finished_at": row["finished_at"].isoformat()
                    if row["finished_at"]
                    else None,
                }
                for row in recent
            ],
        }
