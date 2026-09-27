"""The one long-lived xjobscore process: scheduler, workers, and Inspector."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

from xjobscore import run
from xjobscore.inspector import config
from xjobscore.inspector.cache import now_utc
from xjobscore.inspector.db import CatalogDB
from xjobscore.inspector.persistence import ResultStore
from xjobscore.inspector.policy import PolicyStore
from xjobscore.inspector.service import InspectorService
from xjobscore.inspector.token_heartbeat import (
    TokenHeartbeatRunner,
    heartbeat_snapshot,
)
from xjobscore.net import DEFAULT_USER_AGENT, Net
from xjobscore.pipeline import GoldCoordinator, SystemRuns

logger = logging.getLogger(__name__)

# ASCII "XJOBSCORE", within PostgreSQL's signed-bigint advisory-lock range.
SYSTEM_LOCK_ID = 0x584A4F4253434F52
REQUIRED_TABLES = ("scheduler_control", "gold_control", "system_run")


def _env(name: str, legacy: str | None, default: str) -> str:
    value = os.environ.get(name)
    if value is not None:
        return value
    if legacy and legacy in os.environ:
        logger.warning("%s is deprecated; use %s", legacy, name)
        return os.environ[legacy]
    return default


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    dsn: str
    inspector_store_dsn: str
    inspector_schema: str
    host: str = "127.0.0.1"
    port: int = 8765
    user_agent: str = DEFAULT_USER_AGENT
    tick_s: float = 60.0
    cycle_budget_s: float = 30.0
    lease_seconds: int = 3600
    shutdown_grace_s: float = 30.0
    heartbeat_tick_s: float = 60.0
    heartbeat_batch: int = 20
    heartbeat_interval_s: int = 21_600
    heartbeat_retry_s: int = 900
    heartbeat_lease_s: int = 900

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65_535:
            raise ValueError("XJOBSCORE_PORT must be between 1 and 65535")
        positive = {
            "XJOBSCORE_TICK_S": self.tick_s,
            "XJOBSCORE_CYCLE_BUDGET_S": self.cycle_budget_s,
            "XJOBSCORE_LEASE_SECONDS": self.lease_seconds,
            "XJOBSCORE_SHUTDOWN_GRACE_S": self.shutdown_grace_s,
            "XJOBSCORE_HEARTBEAT_TICK_S": self.heartbeat_tick_s,
            "XJOBSCORE_HEARTBEAT_BATCH": self.heartbeat_batch,
            "XJOBSCORE_HEARTBEAT_INTERVAL_S": self.heartbeat_interval_s,
            "XJOBSCORE_HEARTBEAT_RETRY_S": self.heartbeat_retry_s,
            "XJOBSCORE_HEARTBEAT_LEASE_S": self.heartbeat_lease_s,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        if "XJOBSCORE_INSPECTOR_RUNNER" in os.environ:
            logger.warning(
                "XJOBSCORE_INSPECTOR_RUNNER was removed and is ignored; "
                "use the persistent Inspector pause/resume control"
            )
        dsn = config.dsn()
        return cls(
            dsn=dsn,
            inspector_store_dsn=config.persistence_dsn(),
            inspector_schema=config.persistence_schema(),
            host=_env("XJOBSCORE_HOST", None, "127.0.0.1"),
            port=int(_env("XJOBSCORE_PORT", "XJOBSCORE_INSPECTOR_PORT", "8765")),
            user_agent=_env(
                "XJOBSCORE_USER_AGENT",
                "XJOBSCORE_INSPECTOR_USER_AGENT",
                DEFAULT_USER_AGENT,
            ),
            tick_s=float(
                _env("XJOBSCORE_TICK_S", "XJOBSCORE_INSPECTOR_RUNNER_TICK_S", "60")
            ),
            cycle_budget_s=float(
                _env(
                    "XJOBSCORE_CYCLE_BUDGET_S",
                    "XJOBSCORE_INSPECTOR_RUNNER_BUDGET_S",
                    "30",
                )
            ),
            lease_seconds=int(
                _env(
                    "XJOBSCORE_LEASE_SECONDS",
                    "XJOBSCORE_INSPECTOR_RUNNER_LEASE_S",
                    "3600",
                )
            ),
            shutdown_grace_s=float(_env("XJOBSCORE_SHUTDOWN_GRACE_S", None, "30")),
            heartbeat_tick_s=float(
                _env(
                    "XJOBSCORE_HEARTBEAT_TICK_S",
                    "XJOBSCORE_INSPECTOR_TOKEN_HEARTBEAT_TICK_S",
                    "60",
                )
            ),
            heartbeat_batch=int(
                _env(
                    "XJOBSCORE_HEARTBEAT_BATCH",
                    "XJOBSCORE_INSPECTOR_TOKEN_HEARTBEAT_BATCH",
                    "20",
                )
            ),
            heartbeat_interval_s=int(
                _env(
                    "XJOBSCORE_HEARTBEAT_INTERVAL_S",
                    "XJOBSCORE_INSPECTOR_TOKEN_HEARTBEAT_INTERVAL_S",
                    "21600",
                )
            ),
            heartbeat_retry_s=int(
                _env(
                    "XJOBSCORE_HEARTBEAT_RETRY_S",
                    "XJOBSCORE_INSPECTOR_TOKEN_HEARTBEAT_RETRY_S",
                    "900",
                )
            ),
            heartbeat_lease_s=int(
                _env(
                    "XJOBSCORE_HEARTBEAT_LEASE_S",
                    "XJOBSCORE_INSPECTOR_TOKEN_HEARTBEAT_LEASE_S",
                    "900",
                )
            ),
        )


class SystemScheduler:
    """Supervise feed polling plus Gold repair/checkpoint work."""

    def __init__(
        self,
        policy: PolicyStore,
        coordinator: GoldCoordinator,
        *,
        user_agent: str,
        tick_s: float,
        cycle_budget_s: float,
        lease_seconds: int,
        shutdown_grace_s: float,
        ownership_check: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._policy = policy
        self._coordinator = coordinator
        self._cfg = run.Config(
            dsn="",
            user_agent=user_agent,
            cycle_budget_s=cycle_budget_s,
            lease_seconds=lease_seconds,
        )
        self._tick_s = tick_s
        self._shutdown_grace_s = shutdown_grace_s
        self._ownership_check = ownership_check
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._net: Net | None = None
        self._stopping = False
        self._last_cycle_at: datetime | None = None
        self._last_polled: int | None = None
        self._consecutive_errors = 0
        self._last_error: str | None = None
        self._last_pruned_at: datetime | None = None

    def status(self) -> dict[str, Any]:
        return {
            "alive": self._task is not None and not self._task.done(),
            "accepting": not self._stopping,
            "last_cycle_at": self._last_cycle_at.isoformat()
            if self._last_cycle_at
            else None,
            "last_polled": self._last_polled,
            "consecutive_errors": self._consecutive_errors,
            "last_error": self._last_error,
        }

    def nudge(self) -> None:
        if not self._stopping:
            self._wake.set()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._net = await Net(user_agent=self._cfg.user_agent).__aenter__()
        self._task = asyncio.create_task(self._loop(), name="xjobscore-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping = True
        self._wake.set()
        try:
            await asyncio.wait_for(self._task, timeout=self._shutdown_grace_s)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        finally:
            self._task = None
            if self._net is not None:
                await self._net.__aexit__(None, None, None)
                self._net = None

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.tick()
                self._consecutive_errors = 0
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._consecutive_errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("unified scheduler tick failed")

            if self._stopping:
                break
            delay = min(
                300.0,
                self._tick_s * (2 ** self._consecutive_errors)
                if self._consecutive_errors
                else self._tick_s,
            )
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def tick(self) -> None:
        if self._ownership_check is not None:
            await self._ownership_check()
        pool = await self._policy.pool_or_none()
        if pool is None or self._net is None:
            raise RuntimeError("PostgreSQL or network client unavailable")

        current = now_utc()
        if (
            self._last_pruned_at is None
            or (current - self._last_pruned_at).total_seconds() >= 86_400
        ):
            await self._coordinator.runs.prune(pool)
            self._last_pruned_at = current

        control = await self._policy.scheduler_control()
        queue_drained = False
        if control is not None and control.get("running"):
            while not self._stopping:
                result = await run.cycle(
                    pool,
                    self._net,
                    self._cfg,
                    now_utc(),
                    coordinator=self._coordinator,
                    accepting=lambda: not self._stopping,
                )
                self._last_cycle_at = now_utc()
                self._last_polled = result.polled
                queue_drained = result.drained
                if self._stopping:
                    break
                if queue_drained:
                    break
                control = await self._policy.scheduler_control()
                if control is None or not control.get("running"):
                    break


class GoldWorker:
    """Independently repair stale Gold rows and checkpoint global dedup."""

    def __init__(
        self,
        policy: PolicyStore,
        coordinator: GoldCoordinator,
        *,
        tick_s: float,
        shutdown_grace_s: float,
        ownership_check: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._policy = policy
        self._coordinator = coordinator
        self._tick_s = tick_s
        self._shutdown_grace_s = shutdown_grace_s
        self._ownership_check = ownership_check
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._next_delay_s: float | None = None
        self._last_tick_at: datetime | None = None
        self._consecutive_errors = 0
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        return {
            "alive": self._task is not None and not self._task.done(),
            "accepting": not self._stopping,
            "last_tick_at": self._last_tick_at.isoformat()
            if self._last_tick_at
            else None,
            "consecutive_errors": self._consecutive_errors,
            "last_error": self._last_error,
        }

    def nudge(self) -> None:
        if not self._stopping:
            self._wake.set()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="xjobscore-gold")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping = True
        self._wake.set()
        try:
            await asyncio.wait_for(self._task, timeout=self._shutdown_grace_s)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        finally:
            self._task = None

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.tick()
                self._consecutive_errors = 0
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._consecutive_errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("Gold worker tick failed")

            if self._stopping:
                break
            delay = min(
                300.0,
                self._tick_s * (2 ** self._consecutive_errors)
                if self._consecutive_errors
                else self._tick_s,
            )
            if not self._consecutive_errors and self._next_delay_s is not None:
                delay = min(delay, max(0.1, self._next_delay_s))
            self._next_delay_s = None
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def tick(self) -> None:
        if self._ownership_check is not None:
            await self._ownership_check()
        pool = await self._policy.pool_or_none()
        if pool is None:
            raise RuntimeError("PostgreSQL unavailable")

        catchup = await self._coordinator.catch_up_once(
            pool, accepting=lambda: not self._stopping
        )
        self._last_tick_at = now_utc()
        if self._stopping:
            return

        queue_drained = bool(
            await pool.fetchval(
                """
                SELECT
                  NOT (SELECT running FROM scheduler_control WHERE id)
                  OR NOT EXISTS (
                      SELECT 1 FROM feed
                      WHERE polling AND paused_at IS NULL AND due_at <= now()
                  )
                """
            )
        )
        await self._coordinator.dedup_once(
            pool, queue_drained=queue_drained
        )
        self._next_delay_s = await self._coordinator.next_dedup_delay(
            pool, queue_drained=queue_drained
        )
        if (
            catchup is not None
            and not catchup.degraded
            and self._coordinator.catchup_may_remain()
        ):
            self._next_delay_s = 0.1


class UnifiedRuntime:
    """Composition root shared by Uvicorn's lifespan and Inspector routes."""

    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings
        self.policy = PolicyStore(settings.dsn)
        self.catalog = CatalogDB(self.policy)
        self.result_store = ResultStore(
            settings.inspector_store_dsn, schema=settings.inspector_schema
        )
        self.inspector = InspectorService(
            self.catalog,
            user_agent=settings.user_agent,
            result_store=self.result_store,
            company_store=self.policy,
        )
        self.runs = SystemRuns()
        self.coordinator = GoldCoordinator(self.runs)
        self.gold_worker = GoldWorker(
            self.policy,
            self.coordinator,
            tick_s=settings.tick_s,
            shutdown_grace_s=settings.shutdown_grace_s,
            ownership_check=self._assert_system_lock,
        )
        self.coordinator.set_wake_callback(self.gold_worker.nudge)
        self.scheduler = SystemScheduler(
            self.policy,
            self.coordinator,
            user_agent=settings.user_agent,
            tick_s=settings.tick_s,
            cycle_budget_s=settings.cycle_budget_s,
            lease_seconds=settings.lease_seconds,
            shutdown_grace_s=settings.shutdown_grace_s,
            ownership_check=self._assert_system_lock,
        )
        self.heartbeat = TokenHeartbeatRunner(
            self.policy,
            enabled=True,
            tick_s=settings.heartbeat_tick_s,
            batch=settings.heartbeat_batch,
            interval_seconds=settings.heartbeat_interval_s,
            retry_seconds=settings.heartbeat_retry_s,
            lease_seconds=settings.heartbeat_lease_s,
            run_store=self.runs,
            ownership_check=self._assert_system_lock,
        )
        self._lock_conn: asyncpg.Connection | None = None
        self.started = False

    async def _claim_process_lock(self) -> None:
        conn = await asyncpg.connect(self.settings.dsn)
        try:
            missing = [
                name
                for name in REQUIRED_TABLES
                if await conn.fetchval("SELECT to_regclass($1)", f"public.{name}") is None
            ]
            if missing:
                joined = ", ".join(missing)
                raise RuntimeError(
                    "unified runtime schema is missing "
                    f"{joined}; apply xjobscore/migrations/20260923_unified_runtime.sql"
                )
            owned = await conn.fetchval("SELECT pg_try_advisory_lock($1)", SYSTEM_LOCK_ID)
            if not owned:
                raise RuntimeError(
                    "another xjobscore system already owns this database"
                )
        except Exception:
            await conn.close()
            raise
        self._lock_conn = conn

    async def _assert_system_lock(self) -> None:
        if self._lock_conn is None or self._lock_conn.is_closed():
            raise RuntimeError("xjobscore system advisory lock connection was lost")
        # Advisory locks are session-scoped. A successful ping proves the
        # session that owns it is still the one supervising workers.
        await self._lock_conn.fetchval("SELECT 1")

    async def start(self) -> None:
        if self.started:
            return
        await self._claim_process_lock()
        try:
            await self.policy.start()
            pool = await self.policy.pool_or_none()
            if pool is None:
                raise RuntimeError("could not connect to PostgreSQL")
            await self.runs.startup(pool)
            await self.result_store.initialize()
            await self.catalog.connect()
            await self.scheduler.start()
            await self.gold_worker.start()
            await self.heartbeat.start()
            self.started = True
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        await asyncio.gather(
            self.heartbeat.stop(grace_s=self.settings.shutdown_grace_s),
            self.scheduler.stop(),
            self.gold_worker.stop(),
        )
        await self.catalog.disconnect()
        await self.result_store.close()
        await self.policy.close()
        if self._lock_conn is not None:
            try:
                if not self._lock_conn.is_closed():
                    await self._lock_conn.execute(
                        "SELECT pg_advisory_unlock($1)", SYSTEM_LOCK_ID
                    )
            except Exception:
                logger.warning("system advisory-lock connection was already lost")
            finally:
                if not self._lock_conn.is_closed():
                    await self._lock_conn.close()
                self._lock_conn = None
        self.started = False

    async def status(self) -> dict[str, Any]:
        pool = await self.policy.pool_or_none()
        pipeline: dict[str, Any] | None = None
        heartbeat: dict[str, Any] | None = None
        scheduler = self.scheduler.status()
        control = None
        status_errors: list[str] = []
        lock_owned = False
        try:
            await self._assert_system_lock()
            lock_owned = True
        except Exception as exc:
            status_errors.append(f"{type(exc).__name__}: {exc}")
        if pool is not None:
            try:
                control = await self.policy.scheduler_control()
                pipeline = await self.coordinator.status(pool)
                pipeline["worker"] = self.gold_worker.status()
                runtime_counts = await pool.fetchrow(
                    """
                    SELECT
                      (SELECT count(*)::int FROM feed
                       WHERE polling AND due_at <= now()
                         AND (hold_until IS NULL OR hold_until <= now())) AS due_feeds,
                      (SELECT count(*)::int FROM feed
                       WHERE hold_until > now()) AS active_feeds,
                      (SELECT COALESCE(sum(new_postings), 0)::int FROM poll_log
                       WHERE polled_at >= date_trunc('day', now())) AS output_today,
                      (SELECT count(*)::int FROM system_run
                       WHERE state = 'running') AS active_runs
                    """
                )
                pipeline["active_feeds"] = runtime_counts["active_feeds"]
                pipeline["active_runs"] = runtime_counts["active_runs"]
                pipeline["output_today"] = runtime_counts["output_today"]
                scheduler = {
                    **scheduler,
                    "control": control,
                    "cadence_seconds": self.settings.tick_s,
                    "due_feeds": runtime_counts["due_feeds"],
                }
                heartbeat = await heartbeat_snapshot(
                    pool,
                    current=now_utc(),
                    configured=True,
                    offline_after_s=max(180, self.settings.heartbeat_tick_s * 3),
                )
                heartbeat["cadence_seconds"] = self.settings.heartbeat_interval_s
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                status_errors.append(error)
                logger.warning("could not read unified runtime status: %s", error)
        else:
            status_errors.append("PostgreSQL unavailable")
        status_error = "; ".join(status_errors) or None
        workers_supervised = (
            scheduler["alive"]
            and self.gold_worker.status()["alive"]
            and self.heartbeat.alive
        )
        return {
            "ready": self.started
            and lock_owned
            and workers_supervised
            and status_error is None,
            "lock_owned": lock_owned,
            "postgres": self.policy.status(),
            "scheduler": scheduler,
            "pipeline": pipeline,
            "heartbeat": heartbeat,
            "status_error": status_error,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
