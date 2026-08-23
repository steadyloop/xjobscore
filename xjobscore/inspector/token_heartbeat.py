"""Persistent, leased token validation and feed promotion heartbeat."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from xjobscore.inspector import validator
from xjobscore.inspector.cache import now_utc
from xjobscore.inspector.tokens import promotion_candidates, promote_verified

logger = logging.getLogger(__name__)

DEFAULT_BATCH = 20
DEFAULT_INTERVAL_S = 6 * 60 * 60
DEFAULT_RETRY_S = 15 * 60
DEFAULT_LEASE_S = 15 * 60
DEFAULT_TICK_S = 60.0


class HeartbeatAlreadyRunning(RuntimeError):
    pass


@dataclass(frozen=True)
class HeartbeatLease:
    hold_until: datetime


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


async def heartbeat_snapshot(
    pool,
    *,
    current: datetime,
    configured: bool,
    offline_after_s: float,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT h.*,
                   (
                       SELECT count(*)::int
                         FROM token AS t
                        WHERE t.status = 'live'
                          AND NOT t.muted
                          AND NOT EXISTS (
                              SELECT 1
                                FROM feed AS f
                               WHERE f.source = t.ats AND f.key = t.token
                          )
                   ) AS eligible
              FROM token_heartbeat AS h
             WHERE h.id
            """
        )
    if row is None:
        raise RuntimeError("token_heartbeat has no singleton row")

    value = dict(row)
    runner_seen_at = value.get("runner_seen_at")
    runner_online = bool(
        runner_seen_at
        and runner_seen_at >= current - timedelta(seconds=offline_after_s)
    )
    running = bool(value.get("hold_until") and value["hold_until"] > current)
    due = value["due_at"] <= current

    if running:
        state = "running"
    elif not runner_online:
        state = "offline"
    elif value["last_outcome"] == "error":
        state = "attention"
    elif due:
        state = "queued"
    elif value["last_outcome"] == "never":
        state = "never"
    else:
        state = "healthy"

    return {
        "state": state,
        "configured": configured,
        "runner_online": runner_online,
        "due_at": _iso(value.get("due_at")),
        "hold_until": _iso(value.get("hold_until")),
        "runner_seen_at": _iso(runner_seen_at),
        "last_started_at": _iso(value.get("last_started_at")),
        "last_completed_at": _iso(value.get("last_completed_at")),
        "last_outcome": value["last_outcome"],
        "eligible": value["eligible"],
        "last_candidates": value["last_candidates"],
        "last_validated": value["last_validated"],
        "last_promoted": value["last_promoted"],
        "last_rejected": value["last_rejected"],
        "last_error": value.get("last_error"),
    }


async def touch_runner(pool, current: datetime) -> None:
    result = await pool.execute(
        "UPDATE token_heartbeat SET runner_seen_at = $1 WHERE id",
        current,
    )
    if result != "UPDATE 1":
        raise RuntimeError("token_heartbeat has no singleton row")


async def claim_due(
    pool,
    *,
    current: datetime,
    lease_seconds: int,
) -> HeartbeatLease | None:
    hold_until = current + timedelta(seconds=lease_seconds)
    row = await pool.fetchrow(
        """
        UPDATE token_heartbeat
           SET hold_until = $2,
               last_started_at = $1,
               last_outcome = 'running',
               last_error = NULL
         WHERE id
           AND due_at <= $1
           AND (hold_until IS NULL OR hold_until <= $1)
     RETURNING hold_until
        """,
        current,
        hold_until,
    )
    return HeartbeatLease(row["hold_until"]) if row else None


async def finish_success(
    pool,
    lease: HeartbeatLease,
    *,
    current: datetime,
    interval_seconds: int,
    candidates: int,
    validated: int,
    promoted: int,
    rejected: int,
) -> bool:
    result = await pool.execute(
        """
        UPDATE token_heartbeat
           SET due_at = $2,
               hold_until = NULL,
               last_completed_at = $1,
               last_outcome = 'success',
               last_candidates = $3,
               last_validated = $4,
               last_promoted = $5,
               last_rejected = $6,
               last_error = NULL
         WHERE id AND hold_until = $7
        """,
        current,
        current + timedelta(seconds=interval_seconds),
        candidates,
        validated,
        promoted,
        rejected,
        lease.hold_until,
    )
    return result == "UPDATE 1"


async def finish_failure(
    pool,
    lease: HeartbeatLease,
    *,
    current: datetime,
    retry_seconds: int,
    error: str,
) -> bool:
    result = await pool.execute(
        """
        UPDATE token_heartbeat
           SET due_at = $2,
               hold_until = NULL,
               last_completed_at = $1,
               last_outcome = 'error',
               last_error = $3
         WHERE id AND hold_until = $4
        """,
        current,
        current + timedelta(seconds=retry_seconds),
        error,
        lease.hold_until,
    )
    return result == "UPDATE 1"


async def enqueue_now(pool, current: datetime) -> None:
    row = await pool.fetchrow(
        """
        UPDATE token_heartbeat
           SET due_at = LEAST(due_at, $1)
         WHERE id
           AND (hold_until IS NULL OR hold_until <= $1)
     RETURNING id
        """,
        current,
    )
    if row is not None:
        return
    running = await pool.fetchval(
        "SELECT hold_until > $1 FROM token_heartbeat WHERE id",
        current,
    )
    if running:
        raise HeartbeatAlreadyRunning("Token heartbeat is already running")
    raise RuntimeError("token_heartbeat has no singleton row")


async def run_claimed_cycle(
    pool,
    lease: HeartbeatLease,
    *,
    batch: int,
    interval_seconds: int,
    retry_seconds: int,
) -> dict[str, int]:
    try:
        identities, _ = await promotion_candidates(pool, limit=batch)
        results = await validator.validate_tokens(identities, pool=pool)
        confirmed_live = [
            (result["ats"], result["token"])
            for result in results
            if result["status"] == "live"
        ]
        promoted = await promote_verified(pool, confirmed_live)
        counts = {
            "candidates": len(identities),
            "validated": len(results),
            "promoted": promoted,
            "rejected": len(results) - len(confirmed_live),
        }
        saved = await finish_success(
            pool,
            lease,
            current=now_utc(),
            interval_seconds=interval_seconds,
            **counts,
        )
        if not saved:
            raise RuntimeError("token heartbeat lease expired before completion")
        return counts
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        try:
            await finish_failure(
                pool,
                lease,
                current=now_utc(),
                retry_seconds=retry_seconds,
                error=error,
            )
        except Exception:
            logger.exception("token heartbeat could not persist its failure")
        raise


class TokenHeartbeatRunner:
    def __init__(
        self,
        policy,
        *,
        enabled: bool,
        tick_s: float = DEFAULT_TICK_S,
        batch: int = DEFAULT_BATCH,
        interval_seconds: int = DEFAULT_INTERVAL_S,
        retry_seconds: int = DEFAULT_RETRY_S,
        lease_seconds: int = DEFAULT_LEASE_S,
    ) -> None:
        self._policy = policy
        self.enabled = enabled
        self.tick_s = tick_s
        self.batch = batch
        self.interval_seconds = interval_seconds
        self.retry_seconds = retry_seconds
        self.lease_seconds = lease_seconds
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="token-heartbeat")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def nudge(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("token heartbeat tick failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.tick_s)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def tick(self) -> dict[str, int] | None:
        pool = await self._policy.pool_or_none()
        if pool is None:
            return None
        current = now_utc()
        await touch_runner(pool, current)
        lease = await claim_due(
            pool,
            current=current,
            lease_seconds=self.lease_seconds,
        )
        if lease is None:
            return None
        return await run_claimed_cycle(
            pool,
            lease,
            batch=self.batch,
            interval_seconds=self.interval_seconds,
            retry_seconds=self.retry_seconds,
        )
