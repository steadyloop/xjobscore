"""Temporary compatibility helpers for isolated legacy tests.

Production uses :mod:`xjobscore.runtime`; Inspector never constructs an active
``SchedulerRunner`` and web requests never call ``poll_feed_now``. The class is
retained only so focused tests and old imports fail gradually during the
one-release command transition. ``GoldJob`` serves the same compatibility role
for the old in-process dedup control; the unified runtime queues dedup through
:class:`xjobscore.pipeline.GoldCoordinator` instead.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from xjobscore import gold, run, store
from xjobscore.inspector.cache import now_utc
from xjobscore.inspector.policy import (
    FeedNotInPostgres,
    PolicyStore,
    PolicyUnavailable,
    PollNowRefused,
)
from xjobscore.net import Net
from xjobscore.run import Config

logger = logging.getLogger(__name__)

# Backoff ceiling after consecutive tick failures, so a wedged database (or a
# bug) does not turn into a tight error loop.
_MAX_BACKOFF_S = 300.0


@dataclass
class _RunnerStatus:
    alive: bool = False
    last_cycle_at: datetime | None = None
    # Feeds polled by the most recent cycle -- one time-budgeted cycle, not
    # the whole drain, which may take several.
    last_polled: int | None = None
    consecutive_errors: int = 0
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "alive": self.alive,
            "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
            "last_polled": self.last_polled,
            "consecutive_errors": self.consecutive_errors,
            "last_error": self.last_error,
        }


class SchedulerRunner:
    """Owns one background task that calls ``run.cycle`` on a tick.

    Started and stopped from the inspector's FastAPI lifespan, exactly like
    ``PolicyStore``. Only meant to be started once per process.
    """

    def __init__(
        self,
        policy: PolicyStore,
        *,
        enabled: bool,
        user_agent: str,
        tick_s: float,
        cycle_budget_s: float,
        lease_seconds: int,
    ) -> None:
        self._policy = policy
        self._enabled = enabled
        self._cfg = Config(
            dsn="",  # unused: cycle() takes the pool directly, not cfg.dsn
            user_agent=user_agent,
            cycle_budget_s=cycle_budget_s,
            lease_seconds=lease_seconds,
        )
        self._tick_s = tick_s
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._status = _RunnerStatus()
        # One Net for the loop and every poll_feed_now call: per-host pacing
        # (net.py _HostPace) lives inside a Net instance, so splitting them
        # would let 8 concurrent on-demand fetches sidestep the budget the
        # loop is pacing against.
        self._net: Net | None = None
        self._net_lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        return self._status.as_dict()

    def nudge(self) -> None:
        """Cut the current sleep short so a queued express poll runs now.

        The Scheduler page's "Poll now" writes a front-of-queue ``due_at`` and
        then calls this; without it the operator waits out a whole ``tick_s``
        (60s by default) for a button labelled *now*. Deliberately also
        short-circuits the consecutive-error backoff -- an operator asking for
        a poll is a better signal than the last tick's exception.

        In-process only: it wakes the runner living in the same process as the
        API, which is the pairing ``create_app`` builds. Under several uvicorn
        workers the express ``due_at`` is still correct and whichever process
        holds the running loop still claims the feed on its next tick; only the
        sub-second latency is lost. A no-op when this process's runner is
        disabled -- the flag is written and nothing here pretends otherwise.
        """
        self._wake.set()

    async def start(self) -> None:
        if not self._enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="scheduler-runner")
        self._status.alive = True

    async def stop(self) -> None:
        # The lifespan calls this unconditionally, and poll_feed_now may have
        # opened the shared Net on a runner whose loop never started -- so the
        # net teardown cannot hide behind the task check.
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            self._status.alive = False
        async with self._net_lock:
            if self._net is not None:
                await self._net.__aexit__(None, None, None)
                self._net = None

    async def _ensure_net(self) -> Net:
        async with self._net_lock:
            if self._net is None:
                self._net = await Net(user_agent=self._cfg.user_agent).__aenter__()
            return self._net

    async def _loop(self) -> None:
        # The shared Net outlives the loop on purpose: stop() closes it, so a
        # poll_feed_now racing shutdown never holds a client the loop tore down.
        net = await self._ensure_net()
        while True:
            try:
                await self._tick(net)
                self._status.consecutive_errors = 0
                self._status.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a tick failure must not kill the runner
                self._status.consecutive_errors += 1
                self._status.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("scheduler runner tick failed")

            delay = self._tick_s
            if self._status.consecutive_errors:
                delay = min(
                    _MAX_BACKOFF_S, self._tick_s * (2 ** self._status.consecutive_errors)
                )
            # Sleep, but interruptibly: nudge() lands the next tick at once
            # instead of after a full interval. Clearing *after* the wait
            # means a nudge that arrives mid-tick is not swallowed by the
            # tick already in flight -- it earns the following one, which
            # is the tick that can actually see the new due_at.
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def _tick(self, net: Net) -> None:
        control = await self._policy.scheduler_control()
        if control is None or not control.get("running"):
            return
        pool = await self._policy.pool_or_none()
        if pool is None:
            return
        # Drain the backlog: a cycle that spent its whole time budget while
        # some vendor still had feeds due reports drained=False, so run again
        # immediately rather than waiting a full tick to make progress. The
        # control flag is re-read between cycles so an operator's pause lands
        # within one budget rather than one whole drain.
        while True:
            result = await run.cycle(pool, net, self._cfg, now_utc())
            self._status.last_cycle_at = now_utc()
            self._status.last_polled = result.polled
            if result.drained:
                return
            control = await self._policy.scheduler_control()
            if control is None or not control.get("running"):
                return

    async def poll_feed_now(self, source: str, key: str) -> dict[str, Any]:
        """Claim, hydrate, and poll one feed through ``run.poll``, right now.

        The Scheduler page's Fetch-all: the browser drives one of these per
        feed, so unlike ``poll_now`` nothing is queued and no loop needs to be
        listening -- not ``config.runner_enabled()``, not
        ``scheduler_control.running``. Per-feed policy still holds: a paused,
        disabled, or already-claimed feed refuses instead of polling.
        """
        pool = await self._policy.pool_or_none()
        if pool is None:
            raise PolicyUnavailable("PostgreSQL unavailable")
        net = await self._ensure_net()
        now = now_utc()
        async with pool.acquire() as conn:
            feed = await store.claim_feed(
                conn,
                source=source,
                key=key,
                now=now,
                lease_seconds=self._cfg.lease_seconds,
            )
            if feed is None:
                await self._refuse_unclaimable(conn, source, key)
        outcome = await run.poll(pool, net, feed, now)
        async with pool.acquire() as conn:
            # run.poll stamps polled_at with the exact `now` it was handed on
            # every terminal path; a lost lease writes no row, so this reads
            # back None rather than crashing.
            new_postings = await conn.fetchval(
                "SELECT new_postings FROM poll_log"
                " WHERE feed_id = $1 AND polled_at = $2",
                feed.id,
                now,
            )
        return {"status": outcome, "feed_id": feed.id, "new_postings": new_postings}

    @staticmethod
    async def _refuse_unclaimable(conn: Any, source: str, key: str) -> None:
        """Turn a failed claim into the specific refusal. Always raises.

        Same diagnostic read and vocabulary as ``PolicyStore.poll_now``
        (policy.py), so the Scheduler page keys one set of reasons.
        """
        row = await conn.fetchrow(
            """
            SELECT f.polling AS enabled,
                   f.paused_at,
                   f.hold_until > now() AS held
            FROM feed f
            WHERE f.source = $1 AND f.key = $2
            """,
            source,
            key,
        )
        if row is None:
            raise FeedNotInPostgres(f"{source}:{key}")
        if not row["enabled"]:
            raise PollNowRefused(
                f"{source}:{key} is disabled; enable it before polling",
                reason="feed_disabled",
            )
        if row["paused_at"] is not None:
            raise PollNowRefused(
                f"{source}:{key} is paused; resume polling before polling it now",
                reason="feed_paused",
            )
        # held, or a pure race lost between the claim and this read: either
        # way somebody else has the lease.
        raise PollNowRefused(
            f"{source}:{key} is already being polled",
            reason="already_polling",
        )


class GoldJob:
    """Single-flight background rebuild of the cross-posting dedup groups.

    Manual and explicit by design: ``store.replace_dup_groups`` is a
    destructive full rebuild of ``job_posting_dup_group``
    (store.py:519), and ``store.dedup_candidates`` loads every ``job_posting``
    row (store.py:507) -- both too heavy to run on every scheduler tick, and
    not safe to run as a plain request handler. The Postings page's "Rebuild
    duplicate groups" button starts one and polls ``state`` until terminal.
    """

    def __init__(self, policy: PolicyStore) -> None:
        self._policy = policy
        self.state = "idle"  # 'idle' | 'running' | 'done' | 'failed'
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.counts: dict[str, int] | None = None
        self.error: str | None = None
        self._task: asyncio.Task | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "counts": self.counts,
            "error": self.error,
        }

    def start(self) -> None:
        """Kick off a rebuild. Raises RuntimeError if one is already running."""
        if self.state == "running":
            raise RuntimeError("dedup rebuild already running")
        self.state = "running"
        self.started_at = now_utc()
        self.finished_at = None
        self.counts = None
        self.error = None
        self._task = asyncio.create_task(self._run(), name="gold-dedup-rebuild")

    async def _run(self) -> None:
        try:
            pool = await self._policy.pool_or_none()
            if pool is None:
                raise RuntimeError("PostgreSQL unavailable")
            (step,) = gold.steps("dedup")
            async with pool.acquire() as conn, conn.transaction():
                counts = await step.rebuild(conn)
            self.counts = {
                "groups": counts.groups,
                "postings": counts.postings,
                "skipped_keys": counts.skipped_keys,
                "skipped_postings": counts.skipped_postings,
            }
            self.state = "done"
        except asyncio.CancelledError:
            self.state = "failed"
            self.error = "cancelled"
            raise
        except Exception as exc:
            self.state = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
            logger.exception("gold dedup rebuild failed")
        finally:
            self.finished_at = now_utc()
