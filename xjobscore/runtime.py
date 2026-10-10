"""The one long-lived xjobscore process: scheduler, workers, and Console."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

from xjobscore import coverage_snapshot, publish, reconcile, run, workable_policy
from xjobscore.console import config
from xjobscore.console.cache import now_utc
from xjobscore.console.db import CatalogDB
from xjobscore.console.persistence import ResultStore
from xjobscore.console.policy import PolicyStore
from xjobscore.console.service import ConsoleService
from xjobscore.console.token_heartbeat import (
    TokenHeartbeatRunner,
    heartbeat_snapshot,
)
from xjobscore.net import DEFAULT_USER_AGENT, Net
from xjobscore.pipeline import GoldCoordinator, SystemRuns

logger = logging.getLogger(__name__)

# ASCII "XJOBSCORE", within PostgreSQL's signed-bigint advisory-lock range.
SYSTEM_LOCK_ID = 0x584A4F4253434F52
REQUIRED_TABLES = ("scheduler_control", "gold_control", "system_run", "reconcile_queue", "reconcile_control")
# Global dedup is off: each rebuild loads every posting into this process and
# ran back to back on 2026-10-05, starving the feed workers' event loop.
# Postings still mark gold_control dirty, so re-enabling resumes from there.
DEDUP_ENABLED = False


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    dsn: str
    console_store_dsn: str
    console_schema: str
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
    # Unset disables the Neon publisher; the harvest runs the same either way.
    publish_dsn: str | None = None
    frontend_dsn: str | None = None
    publish_interval_s: float = 3600.0
    publish_seen_drift_s: float = float(publish.DEFAULT_SEEN_DRIFT_S)

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
            "XJOBSCORE_PUBLISH_INTERVAL_S": self.publish_interval_s,
            "XJOBSCORE_PUBLISH_SEEN_DRIFT_S": self.publish_seen_drift_s,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        dsn = config.dsn()
        return cls(
            dsn=dsn,
            console_store_dsn=config.persistence_dsn(),
            console_schema=config.persistence_schema(),
            host=os.environ.get("XJOBSCORE_HOST", "127.0.0.1"),
            port=int(os.environ.get("XJOBSCORE_PORT", "8765")),
            user_agent=os.environ.get("XJOBSCORE_USER_AGENT", DEFAULT_USER_AGENT),
            tick_s=float(os.environ.get("XJOBSCORE_TICK_S", "60")),
            cycle_budget_s=float(os.environ.get("XJOBSCORE_CYCLE_BUDGET_S", "30")),
            lease_seconds=int(os.environ.get("XJOBSCORE_LEASE_SECONDS", "3600")),
            shutdown_grace_s=float(os.environ.get("XJOBSCORE_SHUTDOWN_GRACE_S", "30")),
            heartbeat_tick_s=float(os.environ.get("XJOBSCORE_HEARTBEAT_TICK_S", "60")),
            heartbeat_batch=int(os.environ.get("XJOBSCORE_HEARTBEAT_BATCH", "20")),
            heartbeat_interval_s=int(os.environ.get("XJOBSCORE_HEARTBEAT_INTERVAL_S", "21600")),
            heartbeat_retry_s=int(os.environ.get("XJOBSCORE_HEARTBEAT_RETRY_S", "900")),
            heartbeat_lease_s=int(os.environ.get("XJOBSCORE_HEARTBEAT_LEASE_S", "900")),
            publish_dsn=os.environ.get("PUBLISH_DATABASE_URL") or None,
            frontend_dsn=os.environ.get("FRONTEND_DATABASE_URL") or None,
            publish_interval_s=float(os.environ.get("XJOBSCORE_PUBLISH_INTERVAL_S", "3600")),
            publish_seen_drift_s=float(os.environ.get("XJOBSCORE_PUBLISH_SEEN_DRIFT_S", str(publish.DEFAULT_SEEN_DRIFT_S))),
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
        net: Net | None = None,
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
        self._net = net
        self._owns_net = net is None
        self._stopping = False
        self._last_cycle_at: datetime | None = None
        self._last_polled: int | None = None
        self._consecutive_errors = 0
        self._last_error: str | None = None
        self._last_pruned_at: datetime | None = None
        self._workable_cooldown_until = 0.0
        # One lane per vendor with due work, or per partition for a vendor
        # whose limiter counts per partition (`sources.lanes`; Workday: per
        # data center), each `run.drain_source`. Lanes run
        # beside the tick, never inside it: the tick only starts lanes for due
        # vendors without one, so no vendor ever waits on another's slowest poll.
        self._lanes: dict[str, asyncio.Task[int]] = {}
        # Cleared when the scheduler is paused or ownership is lost; lanes stop
        # claiming at their next feed. In-flight polls finish under their lease.
        self._scheduling = False
        self._polled_since_tick = 0
        self._polled_since_refresh = 0
        self._last_refresh: float = float("-inf")

    def _accepting(self) -> bool:
        return self._scheduling and not self._stopping

    def _start_lane(self, pool: Any, source: str) -> None:
        async def lane() -> int:
            polled = await run.drain_source(
                pool,
                self._net,
                self._cfg,
                source,
                coordinator=self._coordinator,
                accepting=self._accepting,
            )
            self._polled_since_tick += polled
            self._polled_since_refresh += polled
            return polled

        task = asyncio.create_task(lane(), name=f"xjobscore-lane-{source}")
        task.add_done_callback(self._lane_done)
        self._lanes[source] = task

    def _lane_done(self, task: asyncio.Task[int]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # The next tick restarts the lane; one vendor's failure is its own.
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.error("scheduler lane failed: %s", task.get_name(), exc_info=exc)

    def rate_groups(self) -> dict[str, dict[str, Any]]:
        """`Net.rate_groups` of the polling client; {} before it opens."""
        return self._net.rate_groups() if self._net is not None else {}

    def status(self) -> dict[str, Any]:
        return {
            "alive": self._task is not None and not self._task.done(),
            "accepting": not self._stopping,
            "lanes": sorted(
                source for source, task in self._lanes.items() if not task.done()
            ),
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
        if self._owns_net:
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
            # Lanes stop claiming at once (`_accepting`); give in-flight polls
            # the same grace the tick had, then cancel what is left.
            lanes = [task for task in self._lanes.values() if not task.done()]
            if lanes:
                _, pending = await asyncio.wait(lanes, timeout=self._shutdown_grace_s)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            self._lanes.clear()
            # Gold the lanes handed off (`GoldCoordinator.defer`) gets the same
            # grace; anything cancelled is repaired by catch-up.
            await self._coordinator.drain(self._shutdown_grace_s)
            self._task = None
            if self._owns_net and self._net is not None:
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
            try:
                await self._ownership_check()
            except BaseException:
                self._scheduling = False
                raise
        pool = await self._policy.pool_or_none()
        if pool is None or self._net is None:
            self._scheduling = False
            raise RuntimeError("PostgreSQL or network client unavailable")

        current = now_utc()
        if (
            self._last_pruned_at is None
            or (current - self._last_pruned_at).total_seconds() >= 86_400
        ):
            await self._coordinator.runs.prune(pool)
            self._last_pruned_at = current

        control = await self._policy.scheduler_control()
        self._scheduling = bool(control is not None and control.get("running"))
        if self._scheduling and not self._stopping:
            hold_until = await workable_policy.cooldown_until()
            if hold_until > current.timestamp() and hold_until != self._workable_cooldown_until:
                logger.warning(
                    "Workable rate-limited; polling deferred until %s. Other sources continue.",
                    datetime.fromtimestamp(hold_until).astimezone().isoformat(timespec="seconds"),
                )
            self._workable_cooldown_until = hold_until
            async with pool.acquire() as conn:
                due = await run.due_lanes(conn, current)
            for source in due:
                lane = self._lanes.get(source)
                if lane is None or lane.done():
                    self._start_lane(pool, source)

        self._last_cycle_at = now_utc()
        self._last_polled = self._polled_since_tick
        self._polled_since_tick = 0

        # `cycle` refreshed this once per round; lanes have no rounds, so the
        # read model refreshes on the same cadence by the clock instead.
        loop = asyncio.get_running_loop()
        if (
            self._polled_since_refresh
            and loop.time() - self._last_refresh >= self._cfg.cycle_budget_s
        ):
            self._polled_since_refresh = 0
            self._last_refresh = loop.time()
            try:
                await coverage_snapshot.refresh(pool)
            except Exception:
                logger.exception("coverage snapshot refresh failed")


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
        self._next_check_at: datetime | None = None
        self._checking = False
        self._consecutive_errors = 0
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        return {
            "alive": self._task is not None and not self._task.done(),
            "accepting": not self._stopping,
            "last_tick_at": self._last_tick_at.isoformat()
            if self._last_tick_at
            else None,
            "next_check_at": self._next_check_at.isoformat()
            if self._next_check_at
            else None,
            "checking": self._checking,
            "consecutive_errors": self._consecutive_errors,
            "last_error": self._last_error,
        }

    def nudge(self) -> None:
        if not self._stopping:
            self._wake.set()
            if self._task is not None and not self._task.done():
                self._next_check_at = now_utc()

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
            self._next_check_at = None
            self._checking = False

    async def _loop(self) -> None:
        while not self._stopping:
            self._next_check_at = None
            self._checking = True
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
            finally:
                self._checking = False

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
            self._next_check_at = now_utc() + timedelta(seconds=delay)
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
        if not DEDUP_ENABLED:
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


class NeonPublisher:
    """Push the serving cohort's changes to thin Neon on a fixed interval."""

    def __init__(
        self,
        policy: PolicyStore,
        *,
        target_dsn: str | None,
        local_target_dsn: str | None = None,
        interval_s: float,
        seen_drift_s: float,
        shutdown_grace_s: float,
        ownership_check: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._policy = policy
        self._target_dsn = target_dsn
        self._local_target_dsn = local_target_dsn
        self._target_status: dict[str, Any] = {}
        self._interval_s = interval_s
        self._seen_drift_s = seen_drift_s
        self._shutdown_grace_s = shutdown_grace_s
        self._ownership_check = ownership_check
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._last_published_at: datetime | None = None
        self._last_counts: publish.PublishCounts | None = None
        self._last_duration_s: float | None = None
        self._consecutive_errors = 0
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._target_dsn or self._local_target_dsn)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "targets": self._target_status,
            "alive": self._task is not None and not self._task.done(),
            "interval_seconds": self._interval_s,
            "last_published_at": self._last_published_at.isoformat()
            if self._last_published_at
            else None,
            "last_counts": {
                field: getattr(self._last_counts, field)
                for field in publish.PublishCounts.__slots__
            }
            if self._last_counts
            else None,
            "last_duration_s": self._last_duration_s,
            "consecutive_errors": self._consecutive_errors,
            "last_error": self._last_error,
        }

    def nudge(self) -> None:
        if not self._stopping:
            self._wake.set()

    async def start(self) -> None:
        if self._task is not None or not self.enabled:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="xjobscore-publish")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping = True
        self._wake.set()
        try:
            await asyncio.wait_for(self._task, timeout=self._shutdown_grace_s)
        except asyncio.TimeoutError:
            # Cancelling mid-write rolls the Neon transaction back; the next
            # run resends whatever this one did not commit.
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
                logger.exception("Neon publish failed")

            if self._stopping:
                break
            # A failed run (Neon waking, network blip) retries well before the
            # next scheduled one, backing off up to the interval itself.
            delay = (
                min(self._interval_s, 60.0 * (2 ** (self._consecutive_errors - 1)))
                if self._consecutive_errors
                else self._interval_s
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
        if pool is None or not self.enabled:
            raise RuntimeError("PostgreSQL unavailable")

        started = asyncio.get_running_loop().time()
        if self._local_target_dsn:
            targets = {"local": self._local_target_dsn}
            if self._target_dsn:
                targets["neon"] = self._target_dsn
            results = await publish.publish_targets(
                pool, targets, seen_drift_s=self._seen_drift_s
            )
            for name, result in results.items():
                self._target_status[name] = (
                    {"error": f"{type(result).__name__}: {result}"}
                    if isinstance(result, BaseException)
                    else {"last_counts": {field: getattr(result, field) for field in publish.PublishCounts.__slots__}, "error": None}
                )
            failures = [r for r in results.values() if isinstance(r, BaseException)]
            if failures:
                raise RuntimeError("Frontend publication failed: " + "; ".join(str(r) for r in failures))
            counts = results.get("neon", results["local"])
        else:
            async with pool.acquire() as source:
                counts = await publish.publish_to(
                    source, self._target_dsn, seen_drift_s=self._seen_drift_s
                )
        self._last_duration_s = round(asyncio.get_running_loop().time() - started, 2)
        self._last_counts = counts
        self._last_published_at = now_utc()
        logger.info(
            "Neon publish %s in %.1fs",
            publish.format_counts(counts),
            self._last_duration_s,
        )


class UnifiedRuntime:
    """Composition root shared by Uvicorn's lifespan and Console routes."""

    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings
        self.net = Net(user_agent=settings.user_agent)
        self.policy = PolicyStore(settings.dsn)
        self.catalog = CatalogDB(self.policy)
        self.result_store = ResultStore(
            settings.console_store_dsn, schema=settings.console_schema
        )
        self.console = ConsoleService(
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
        self.reconciler = reconcile.Worker(self.policy, self.net,
            shutdown_grace_s=settings.shutdown_grace_s, ownership_check=self._assert_system_lock)
        self.scheduler = SystemScheduler(
            self.policy,
            self.coordinator,
            user_agent=settings.user_agent,
            tick_s=settings.tick_s,
            cycle_budget_s=settings.cycle_budget_s,
            lease_seconds=settings.lease_seconds,
            net=self.net,
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
        self.publisher = NeonPublisher(
            self.policy,
            target_dsn=settings.publish_dsn,
            local_target_dsn=settings.frontend_dsn,
            interval_s=settings.publish_interval_s,
            seen_drift_s=settings.publish_seen_drift_s,
            shutdown_grace_s=settings.shutdown_grace_s,
            ownership_check=self._assert_system_lock,
        )
        self._lock_conn: asyncpg.Connection | None = None
        # One asyncpg connection runs one query at a time; workers ping it concurrently.
        self._lock_conn_guard = asyncio.Lock()
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
                    f"{joined}; migrate the database to match schema.sql"
                )
            owned = await conn.fetchval("SELECT pg_try_advisory_lock($1)", SYSTEM_LOCK_ID)
            if not owned:
                raise RuntimeError(
                    "another xjobscore system already owns this database; "
                    "stop the existing instance (Ctrl+C in its terminal) before restarting"
                )
        except Exception:
            await conn.close()
            raise
        self._lock_conn = conn

    async def _assert_system_lock(self) -> None:
        async with self._lock_conn_guard:
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
            await self.net.__aenter__()
            await self.scheduler.start()
            await self.reconciler.start()
            await self.gold_worker.start()
            await self.heartbeat.start()
            await self.publisher.start()
            self.started = True
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        await asyncio.gather(
            self.heartbeat.stop(grace_s=self.settings.shutdown_grace_s),
            self.scheduler.stop(),
            self.reconciler.stop(),
            self.gold_worker.stop(),
            self.publisher.stop(),
        )
        await self.net.__aexit__(None, None, None)
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
        reconciliation = None
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
                reconciliation = await reconcile.snapshot(pool, self.reconciler)
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
            and self.reconciler.status()["alive"]
            and (not self.publisher.enabled or self.publisher.status()["alive"])
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
            "publish": self.publisher.status(),
            "reconcile": reconciliation,
            "status_error": status_error,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
