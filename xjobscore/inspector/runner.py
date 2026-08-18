"""Supervises the real pipeline (``run.cycle``) from inside the inspector process.

This is the one place the inspector crosses from *describing* production
polling into *doing* it. Everything else in the inspector -- ``service.py``'s
``fetch_once``, the ``inspector`` schema -- is deliberately side-effect-free;
``SchedulerRunner`` is not. It calls the exact ``run.cycle`` (and therefore
``run.poll``) path cron would, against the exact ``public`` tables:
``raw_posting``, ``job_posting``, ``poll_observation``, and the runtime feed
columns run.py's workers own. See ``inspector/__init__.py`` and
``inspector/README.md`` for the boundary this redraws.

There are two entry points into that path. The tick loop below runs
``run.cycle`` on a schedule; ``poll_feed_now`` runs ``run.poll`` for one feed
on demand -- the Scheduler page's Fetch-all, where the browser is the
scheduler. The on-demand path shares this runner's ``Net`` (so per-host pacing
covers both) and registry cache, and deliberately ignores both gates: it needs
neither ``config.runner_enabled()`` nor ``scheduler_control.running``, because
the operator driving it *is* the schedule.

Two independent switches gate it:

- ``config.runner_enabled()`` (env var, read once at process start) -- whether
  this inspector process is *allowed* to run the loop at all. Off by default,
  so every dev laptop and the whole test suite stay inert without anyone
  having to remember to pause a database flag.
- ``scheduler_control.running`` (PostgreSQL, read fresh every tick) -- the
  operator's pause/resume, driven from the Scheduler page. Persistent so a
  process restart cannot silently undo a deliberate pause, and defaults to
  false so a fresh database polls nothing until asked.

A process with the env var off still reads and writes the DB flag through the
API (so the Scheduler page's button always works), it just never acts on it --
``status()["alive"]`` is how the page tells "paused" apart from "nothing is
listening".
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from xjobscore import feader, gold, run, store
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

# How long a parsed registry snapshot (index + source types) stays valid
# before the next tick reparses it off-thread. Mirrors db.CatalogDB's TTL for
# the same underlying data; a registry edit picked up within five minutes is
# plenty responsive for a background poller.
_REGISTRY_TTL_S = 300.0

# Backoff ceiling after consecutive tick failures, so a wedged database (or a
# bug) does not turn into a tight error loop.
_MAX_BACKOFF_S = 300.0


@dataclass
class _RunnerStatus:
    alive: bool = False
    last_cycle_at: datetime | None = None
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
        batch: int,
        lease_seconds: int,
    ) -> None:
        self._policy = policy
        self._enabled = enabled
        self._cfg = Config(
            dsn="",  # unused: cycle() takes the pool directly, not cfg.dsn
            user_agent=user_agent,
            batch=batch,
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
        self._registry_index: dict[tuple[str, str], object] | None = None
        self._registry_types: dict[str, str] | None = None
        self._registry_built_at = float("-inf")
        self._registry_lock = asyncio.Lock()

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

    async def _refresh_registry(self, pool: Any) -> None:
        # Locked because poll_feed_now fans out: the first bulk-fetch click
        # lands ~8 concurrent calls on a cold cache, and without the lock each
        # would reparse the registry. The loop alone never raced this.
        async with self._registry_lock:
            if (
                self._registry_index is not None
                and time.monotonic() - self._registry_built_at < _REGISTRY_TTL_S
            ):
                return
            # Parsing ~2600 feeds and every catalog is real CPU work; keep it off
            # the event loop so a concurrent HTTP request to the inspector is never
            # blocked behind it.
            index, types = await asyncio.to_thread(self._build_registry)
            # run.main() always syncs the registry into `public` before every
            # cycle (run.py:361) -- that write is what makes the company_id values
            # _hydrate() stamps onto each Feed actually exist as rows in
            # `company`. Skipping it here would hand job_posting a company_id the
            # database has never heard of and job_posting_company_id_fkey would
            # reject the insert. Tied to the same TTL as the in-memory parse so
            # the two never disagree.
            async with pool.acquire() as conn:
                await feader.feed(conn)
            self._registry_index = index
            self._registry_types = types
            self._registry_built_at = time.monotonic()

    @staticmethod
    def _build_registry() -> tuple[dict[tuple[str, str], object], dict[str, str]]:
        return run._registry_index(), run.source_types()

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
        await self._refresh_registry(pool)
        # Drain the backlog: a full batch means more feeds were due than this
        # cycle claimed, so run again immediately rather than waiting a full
        # tick to make progress. An empty or partial batch means the queue is
        # caught up.
        while True:
            polled = await run.cycle(
                pool,
                net,
                self._cfg,
                now_utc(),
                index=self._registry_index,
                types=self._registry_types,
            )
            self._status.last_cycle_at = now_utc()
            self._status.last_polled = polled
            if polled < self._cfg.batch:
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
        await self._refresh_registry(pool)
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
        feed = run._hydrate(feed, self._registry_index, self._registry_types)
        outcome = await run.poll(pool, net, feed, now)
        async with pool.acquire() as conn:
            # run.poll stamps polled_at with the exact `now` it was handed on
            # every terminal path; a lost lease writes no row, so this reads
            # back None rather than crashing.
            new_postings = await conn.fetchval(
                "SELECT new_postings FROM poll_observation"
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
            SELECT f.enabled,
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
