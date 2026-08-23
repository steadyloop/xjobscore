"""PostgreSQL policy reads and writes -- the inspector's only PG coupling.

The inspector may WRITE four operator-owned things: ``feed.polling``,
``feed.interval_s``, ``feed.paused_at``, and ``scheduler_control.running``
(plus explicit feed import and drop). The runtime columns on ``feed``
-- etag, res_hash, strikes, hold_until -- belong to the production worker and
are read here for display only. Writing them would corrupt run.py's change
detection and 304 handling.

``due_at`` is the documented exception, written only by ``poll_now``. It is a
queue *position*, not derived state: run.py reads it to decide what to claim
next and overwrites it unconditionally when the poll finishes. Moving it earlier
asks for a feed to be picked up sooner and can lose nothing, because the
conditional-request validators it would need to corrupt (``etag``, ``res_hash``)
are untouched -- a poll brought forward still sends If-None-Match and still
costs one 304. ``hold_until`` is deliberately left alone so a feed a worker has
already claimed is not yanked out from under it.

``poll_now`` writes that position *behind* the whole backlog rather than at
``now()`` -- see ``_EXPRESS_BACKDATE`` -- and refuses outright when the poll it
promises could not happen, so the Scheduler page never reports a queued poll
that nothing will ever claim.

This class stays policy-only: it reads and writes the switch, never the
polling loop itself. ``inspector.runner.SchedulerRunner`` is the collaborator
that actually calls ``run.cycle`` from inside the inspector process, gated by
this switch and by its own ``config.runner_enabled()`` check -- see that
module's docstring for the two-level gate. That runner is the one place the
inspector's PG coupling widens past policy: through its tick loop and its
on-demand ``poll_feed_now`` (the Scheduler page's Fetch-all), it runs run.py's
own worker path against ``raw_posting``, ``job_posting``,
``poll_observation``, and the runtime ``feed`` columns this file otherwise
treats as read-only.

When no DSN is configured or PostgreSQL is unreachable the inspector runs in
degraded mode: ``snapshot()`` returns None and mutations raise
PolicyUnavailable, which the API maps to 503.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from xjobscore import store

MIN_INTERVAL_S = 60
MAX_INTERVAL_S = 7 * 86_400

# How far back ``poll_now`` backdates ``due_at``.
#
# The obvious value -- ``now()`` -- is wrong, and wrong in the one direction
# that matters. ``store.claim_due_feeds`` picks work with ``ORDER BY s.due_at``
# *ascending* (store.py:117), so a feed stamped ``now()`` sorts behind every
# feed that was already overdue: on any backlog "poll now" pushed the feed to
# the back of the queue it claimed to jump. Backdating past the oldest possible
# real due_at puts it genuinely first. A decade clears any plausible backlog --
# even a catalog left paused for months -- while still leaving later clicks a
# marginally larger due_at than earlier ones, so several express feeds keep
# click order among themselves instead of falling back on feed_id.
#
# The sentinel never persists: however the poll ends,
# ``store.finish_schedule_success``/``_failure`` overwrite due_at with
# ``work.next_due_at(...)`` (store.py:214), so it lives only for the seconds
# between the click and the claim.
_EXPRESS_BACKDATE = timedelta(days=3650)


class PolicyUnavailable(RuntimeError):
    """PostgreSQL is not configured or not reachable."""


class FeedNotInPostgres(KeyError):
    """The (source, key) pair has no feed row; import it first."""


class PollNowRefused(RuntimeError):
    """An immediate poll was asked for and provably cannot happen.

    Every case here is one where the old code wrote a new ``due_at``, answered
    200, and left the operator watching a queue position that nothing would
    ever claim -- a disabled feed, a paused scheduler, a feed a worker already
    holds. ``reason`` is the machine-readable tag the Scheduler page keys its
    message off; ``str(exc)`` is the sentence a human reads.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class PolicyStore:
    def __init__(self, dsn: str | None) -> None:
        self._dsn = dsn
        self._pool: Any | None = None
        self._error: str | None = None if dsn else "no DSN configured"
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        return {
            "connected": self._pool is not None,
            "dsn_configured": bool(self._dsn),
            "error": self._error,
        }

    async def start(self) -> None:
        """Try the first connection; failure leaves the app in degraded mode."""
        try:
            await self._ensure_pool()
        except PolicyUnavailable:
            pass

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _ensure_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        if not self._dsn:
            raise PolicyUnavailable("no DSN configured")
        async with self._lock:
            if self._pool is None:
                try:
                    self._pool = await store.connect(self._dsn)
                except Exception as exc:
                    self._error = str(exc)
                    raise PolicyUnavailable(str(exc)) from exc
                self._error = None
        return self._pool

    async def pool_or_none(self) -> Any | None:
        """The live ``public`` pool, or None when degraded.

        Read-only companions (``telemetry.py``) borrow this instead of opening a
        second pool to the same database, so connection limits and degraded-mode
        semantics stay owned in one place.
        """
        try:
            return await self._ensure_pool()
        except PolicyUnavailable:
            return None

    async def snapshot(self) -> dict[tuple[str, str], dict[str, Any]] | None:
        """Policy + display state keyed by (source, key); None when degraded.

        Queried fresh on every call -- no in-memory caching.
        """
        try:
            pool = await self._ensure_pool()
        except PolicyUnavailable:
            return None
        try:
            async with pool.acquire() as conn:
                # Read-only display projection; store.py has no reader that
                # exposes the worker-owned last_* columns.
                rows = await conn.fetch(
                    """
                    SELECT f.id, f.source, f.key, f.polling AS enabled,
                           f.interval_s, f.strikes, f.due_at, f.hold_until,
                           f.paused_at, f.last_poll_at, f.last_change_at,
                           f.etag IS NOT NULL AS has_etag
                    FROM feed f
                    """
                )
        except Exception as exc:
            self._error = str(exc)
            return None
        return {
            (row["source"], row["key"]): {
                "feed_id": row["id"],
                "enabled": row["enabled"],
                "interval_s": row["interval_s"],
                "strikes": row["strikes"],
                "due_at": row["due_at"].isoformat() if row["due_at"] else None,
                "hold_until": row["hold_until"].isoformat() if row["hold_until"] else None,
                "paused_at": row["paused_at"].isoformat() if row["paused_at"] else None,
                "last_poll_at": row["last_poll_at"].isoformat() if row["last_poll_at"] else None,
                "last_change_at": row["last_change_at"].isoformat()
                if row["last_change_at"]
                else None,
                "has_etag": row["has_etag"],
            }
            for row in rows
        }

    async def company_alias_index(self) -> dict[str, tuple[int, str]]:
        """Snapshot PostgreSQL aliases for one synchronous parse preview."""
        pool = await self.pool_or_none()
        if pool is None:
            return {}
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT a.alias_norm, c.id AS company_id, c.name AS company_name
                    FROM alias a
                    JOIN company c ON c.id = a.company_id
                    """
                )
        except Exception as exc:
            self._error = str(exc)
            return {}
        return {
            row["alias_norm"]: (int(row["company_id"]), row["company_name"])
            for row in rows
        }

    async def company_descriptions(self, company_ids: set[int]) -> dict[int, str]:
        """Read non-empty company descriptions from PostgreSQL."""
        if not company_ids:
            return {}
        pool = await self.pool_or_none()
        if pool is None:
            return {}
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, description
                    FROM company
                    WHERE description IS NOT NULL AND description <> ''
                      AND id = ANY($1::bigint[])
                    """,
                    sorted(company_ids),
                )
        except Exception as exc:
            self._error = str(exc)
            return {}
        return {int(row["id"]): row["description"] for row in rows}

    async def set_enabled(self, source: str, key: str, enabled: bool) -> dict[str, Any]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            feed_id = await store.set_feed_enabled(
                conn, source=source, key=key, enabled=enabled
            )
        if feed_id is None:
            raise FeedNotInPostgres(f"{source}:{key}")
        return {"feed_id": feed_id, "enabled": enabled}

    async def set_feeds_enabled(
        self, identities: list[tuple[str, str]], enabled: bool
    ) -> dict[str, Any]:
        """Flip a whole vendor's feeds in one statement pair.

        The per-vendor switch can cover thousands of feeds (2,591 for Ashby), so
        this deliberately does not loop over :meth:`set_enabled` /
        :meth:`import_feed`: that would be one round trip and one pool acquire
        per feed. Duplicate identities are collapsed first because ``ON CONFLICT
        DO UPDATE`` cannot touch the same row twice in one statement.
        """
        deduped = list(dict.fromkeys(identities))
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                affected = await store.set_feeds_enabled(
                    conn, deduped, enabled=enabled
                )
        return {"enabled": enabled, "requested": len(deduped), "affected": affected}

    async def set_interval(self, source: str, key: str, interval_s: int) -> dict[str, Any]:
        if not MIN_INTERVAL_S <= interval_s <= MAX_INTERVAL_S:
            raise ValueError(
                f"interval_s must be within [{MIN_INTERVAL_S}, {MAX_INTERVAL_S}]"
            )
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            feed_id = await conn.fetchval(
                "SELECT id FROM feed WHERE source = $1 AND key = $2", source, key
            )
            if feed_id is None:
                raise FeedNotInPostgres(f"{source}:{key}")
            updated = await store.set_schedule_interval(conn, feed_id, interval_s)
        if not updated:
            raise FeedNotInPostgres(f"{source}:{key}")
        return {"feed_id": feed_id, "interval_s": interval_s}

    async def set_paused(self, source: str, key: str, paused: bool) -> dict[str, Any]:
        """Hold or release production polling for one feed.

        Distinct from ``set_enabled``: a held feed stays enabled, so it keeps its
        place in every listing and its history stays legible. Only the queue
        readers skip it.
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            feed_id = await conn.fetchval(
                "SELECT id FROM feed WHERE source = $1 AND key = $2", source, key
            )
            if feed_id is None:
                raise FeedNotInPostgres(f"{source}:{key}")
            updated = await store.set_schedule_paused(conn, feed_id, paused)
        if not updated:
            raise FeedNotInPostgres(f"{source}:{key}")
        return {"feed_id": feed_id, "paused": paused}

    async def poll_now(self, source: str, key: str) -> dict[str, Any]:
        """Put one feed at the front of the production queue, to be polled now.

        Checks every condition ``store.claim_due_feeds`` will check, before
        writing anything, and refuses with the specific reason when one fails.
        The alternative -- write and hope -- is what made this button report
        success for polls that could not happen.

        What it deliberately does *not* touch: ``hold_until``, so a claim a
        worker already holds is never yanked out from under it (that case
        refuses instead); and ``strikes``, because a successful poll zeroes
        them anyway (store.py:214) and a failed one has earned the backoff.
        """
        # Read before write: a paused scheduler means the express due_at would
        # sit unclaimed until someone resumes, which is exactly the silent
        # no-op this refuses to perform.
        control = await self.scheduler_control()
        if control is None:
            raise PolicyUnavailable(self._error or "scheduler_control unavailable")
        if not control.get("running"):
            raise PollNowRefused(
                "the scheduler is paused; resume it before polling a feed",
                reason="scheduler_paused",
            )

        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT f.id, f.polling AS enabled,
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
                # claim_due_feeds filters on f.polling (store.py:109), so this
                # used to answer 200 and then never poll.
                raise PollNowRefused(
                    f"{source}:{key} is disabled; enable it before polling",
                    reason="feed_disabled",
                )
            if row["paused_at"] is not None:
                raise PollNowRefused(
                    f"{source}:{key} is paused; resume polling before polling it now",
                    reason="feed_paused",
                )
            if row["held"]:
                raise PollNowRefused(
                    f"{source}:{key} is already being polled",
                    reason="already_polling",
                )
            feed_id = row["id"]
            updated = await conn.fetchval(
                """
                UPDATE feed SET due_at = now() - $2::interval
                WHERE id = $1 AND paused_at IS NULL
                RETURNING due_at
                """,
                feed_id,
                _EXPRESS_BACKDATE,
            )
        if updated is None:
            # Lost a race with a pause between the read and the write.
            raise PollNowRefused(
                f"{source}:{key} is paused; resume polling before polling it now",
                reason="feed_paused",
            )
        return {"feed_id": feed_id, "due_at": updated.isoformat(), "express": True}

    async def poll_now_all(self, source: str | None = None) -> dict[str, Any]:
        """Bring every enabled feed forward, optionally limited to one source.

        Only feeds that are not already due are touched, so the count returned
        reflects work actually created rather than rows rewritten.
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            updated = await conn.fetchval(
                """
                WITH bumped AS (
                    UPDATE feed f SET due_at = now()
                    WHERE f.polling
                      AND f.paused_at IS NULL
                      AND f.due_at > now()
                      AND ($1::text IS NULL OR f.source = $1)
                    RETURNING 1
                )
                SELECT count(*)::int FROM bumped
                """,
                source,
            )
        return {"source": source, "updated": int(updated or 0)}

    async def import_feed(self, source: str, key: str, enabled: bool = True) -> dict[str, Any]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            feed_id = await store.import_feed(
                conn, source=source, key=key, enabled=enabled
            )
        return {"feed_id": feed_id, "enabled": enabled}

    async def drop_feed(self, source: str, key: str) -> dict[str, Any]:
        """Remove an imported feed while leaving its token unmuted.

        The database cascade removes poll observations with the feed. The token
        row is intentionally untouched, so a later explicit import or token
        heartbeat can bring the feed back.
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            feed_id = await conn.fetchval(
                "DELETE FROM feed WHERE source = $1 AND key = $2 RETURNING id",
                source,
                key,
            )
        if feed_id is None:
            raise FeedNotInPostgres(f"{source}:{key}")
        return {"feed_id": int(feed_id), "dropped": True}

    async def import_feeds(
        self, identities: list[tuple[str, str]], *, enabled: bool = False
    ) -> dict[str, Any]:
        """Enable many tokens into ``feed`` in one statement.

        The Tokens page's backlog is hundreds of rows at a time, so this does
        not loop over :meth:`import_feed` -- that would be one round trip and
        one pool acquire per token. Duplicate identities are collapsed first
        because ``ON CONFLICT DO UPDATE`` cannot touch the same row twice in
        one statement, the same constraint :meth:`set_feeds_enabled` works
        around.

        A token that already has a feed row -- enabled or disabled -- is left
        exactly as it is; ``enabled`` only ever applies to a genuinely new
        row. That inertness is what makes sending the same token twice safe:
        there is no path here that flips an existing row's switch, so the
        caller never needs to check "is this already there" itself.
        """
        deduped = list(dict.fromkeys(identities))
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                imported, existing = await store.import_feeds(
                    conn, deduped, enabled=enabled
                )
        return {
            "requested": len(deduped),
            "imported": imported,
            "existing": existing,
            "enabled": enabled,
        }

    async def scheduler_control(self) -> dict[str, Any] | None:
        """The global run/pause switch; None when degraded, matching snapshot().

        Read fresh every call, same as snapshot() -- a runner ticking every
        minute or so has no reason to cache this and every reason not to.
        """
        try:
            pool = await self._ensure_pool()
        except PolicyUnavailable:
            return None
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT running, changed_at, note FROM scheduler_control WHERE id"
                )
        except Exception as exc:
            self._error = str(exc)
            return None
        if row is None:
            # scheduler_control ships with one seeded row; a live database that
            # predates this table has none. Treat that the same as "paused"
            # rather than raising, so an un-migrated database degrades safely
            # instead of 500ing every scheduler-page load.
            return {"running": False, "changed_at": None, "note": None}
        return {
            "running": row["running"],
            "changed_at": row["changed_at"].isoformat() if row["changed_at"] else None,
            "note": row["note"],
        }

    async def set_scheduler_running(
        self, running: bool, note: str | None = None
    ) -> dict[str, Any]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE scheduler_control
                SET running = $1, changed_at = now(), note = $2
                WHERE id
                RETURNING running, changed_at
                """,
                running,
                note,
            )
        if row is None:
            raise PolicyUnavailable(
                "scheduler_control has no row -- apply the pending DDL "
                "(see doc/psql-commands.md)"
            )
        return {"running": row["running"], "changed_at": row["changed_at"].isoformat()}
