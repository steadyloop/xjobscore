"""PostgreSQL mechanics for the schema.sql-backed core pipeline."""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import cache
import re
from typing import TYPE_CHECKING, Any
import unicodedata

import asyncpg
import orjson

from xjobscore.feed_identity import canonical_feed_identity
from xjobscore.records import Feed
from xjobscore.source_policy import require_source_allowed

if TYPE_CHECKING:
    from asyncpg import Connection, Pool

__all__ = [
    "alias_candidates",
    "all_feeds",
    "append_poll_log",
    "claim_due_feeds",
    "claim_feed",
    "due_key_parts",
    "due_sources",
    "close_posting_by_key",
    "connect",
    "dedup_candidates",
    "dedup_descriptions",
    "due_feeds",
    "ensure_token_company",
    "company_identity_key",
    "fill_company",
    "fetchable_feeds",
    "finish_schedule_failure",
    "finish_schedule_success",
    "import_feed",
    "import_feeds",
    "latest_raw",
    "load_alias_map",
    "mark_checked",
    "mint_company",
    "normalize_domain",
    "raw_candidates",
    "replace_dup_groups",
    "rekey_company_identity",
    "replay_feeds",
    "renew_feed_lease",
    "resolve_company",
    "resolve_or_mint",
    "save_raw",
    "set_feed_enabled",
    "set_schedule_interval",
    "set_schedule_paused",
    "source_instance",
    "token_slug",
    "touch_source_postings",
    "upsert_job_posting",
    "upsert_keyscan_token",
    "write_company_aliases",
]


def _encode_jsonb(value: Any) -> bytes:
    # jsonb's binary wire format is a version byte (always 1) followed by the
    # UTF-8 JSON text -- see PostgreSQL's jsonb_send/jsonb_recv. Binary format
    # skips the str round-trip format="text" would otherwise force on every
    # encode and decode.
    return b"\x01" + orjson.dumps(value)


def _decode_jsonb(data: bytes) -> Any:
    return orjson.loads(data[1:])


async def _install_jsonb_codec(conn: Connection) -> None:
    await conn.set_type_codec(
        "jsonb",
        encoder=_encode_jsonb,
        decoder=_decode_jsonb,
        schema="pg_catalog",
        format="binary",
    )


async def connect(dsn: str, *, search_path: str | None = None) -> Pool:
    """Create the pool used by concurrent poll tasks.

    ``search_path`` scopes every connection in the pool to one schema. Production
    leaves it unset and gets ``public``; the integration test points it at a
    throwaway schema so it can exercise the real claim/poll path without
    touching a developer's actual feeds.
    """
    return await asyncpg.create_pool(
        dsn,
        min_size=1,
        # Sized for concurrent vendor lanes (greenhouse 30, ashby 44, lever 40
        # workers, plus ~25 single-width vendors) holding a connection only for
        # claim, bronze, silver and finish. PostgreSQL allows 100.
        max_size=40,
        init=_install_jsonb_codec,
        **({"server_settings": {"search_path": search_path}} if search_path else {}),
    )


def source_instance(feed: Feed) -> str:
    """Return the collision-safe posting namespace documented by schema.sql."""
    source, key = canonical_feed_identity(feed.source, feed.key)
    return f"{source}:{key}"


def _feed_from_row(row: asyncpg.Record) -> Feed:
    return Feed(
        id=row["id"],
        source=row["source"],
        key=row["key"],
        config=dict(row.get("config") or {}),
        enabled=row["enabled"],
        company_id=row.get("company_id"),
        company_name=row.get("company_name"),
        company_needs_fill=row.get("company_needs_fill", True),
        description=row.get("description") or "",
        type=row.get("type") or "ATS",
        etag=row.get("etag"),
        last_modified=row.get("last_modified"),
        res_hash=row.get("res_hash"),
        list_total=row.get("list_total"),
        last_full_poll_at=row.get("last_full_poll_at"),
        interval_s=int(row.get("interval_s") or 900),
        strikes=int(row.get("strikes") or 0),
        due_at=row.get("due_at"),
        hold_until=row.get("hold_until"),
    )


_FEED_SELECT = """
    SELECT
        f.id, f.source, f.key, f.polling AS enabled, t.config,
        f.type, c.description,
        f.interval_s, f.etag, f.last_modified, f.res_hash, f.list_total, f.last_full_poll_at,
        f.strikes, f.due_at, f.hold_until,
        t.company_id,
        c.name AS company_name,
        -- False once there is nothing left for silver to fill: see fill_company.
        (c.id IS NULL OR (c.reviewed = 'pending'
                          AND (c.domain IS NULL OR c.description IS NULL)
                          AND NOT c.tags ? 'fill_checked_at')) AS company_needs_fill
    FROM feed f
    LEFT JOIN token t ON t.ats = f.source AND t.token = f.key
    LEFT JOIN company c ON c.id = t.company_id
"""


# A filter on one '/'-separated part of `feed.key`: (1-based part index,
# values, exclude). `(2, ("wd1",), False)` keeps Workday keys whose data
# center is wd1; `(2, ("wd1", "wd3"), True)` keeps every other data center.
KeyPart = tuple[int, tuple[str, ...], bool]


def _key_part_clause(part: KeyPart | None, first: int) -> tuple[str, list[Any]]:
    """SQL for `part` with its parameters numbered from `$first`."""
    if part is None:
        return "", []
    index, values, exclude = part
    test = "<> ALL" if exclude else "= ANY"
    return (
        f"AND split_part(f.key, '/', ${first}) {test}(${first + 1}::text[])",
        [index, list(values)],
    )


async def claim_due_feeds(
    conn: Connection,
    now: datetime,
    limit: int,
    *,
    lease_seconds: int = 3600,
    source: str | None = None,
    part: KeyPart | None = None,
) -> list[Feed]:
    """Lease due feed rows atomically for horizontally safe workers.

    `source` restricts the claim to one vendor, which is how `run.cycle`'s
    per-vendor worker pools each take the next feed of their own queue without
    a slow vendor's backlog crowding out a fast one. Spelled as a conditional
    clause rather than `($4 IS NULL OR f.source = $4)` so the unfiltered claim
    still plans on `feed_due_idx` and the filtered one on `feed_source_due_idx`.
    `part` narrows that vendor's queue to one lane (`sources.lane_filter`).
    """
    lease_until = now + timedelta(seconds=max(60, lease_seconds))
    source_clause = "AND f.source = $4" if source is not None else ""
    part_clause, part_args = _key_part_clause(part, 5 if source is not None else 4)
    rows = await conn.fetch(
        f"""
        WITH picked AS (
            SELECT f.id
            FROM feed f
            WHERE f.polling
              AND f.paused_at IS NULL
              AND f.due_at <= $1
              AND (f.hold_until IS NULL OR f.hold_until <= $1)
              {source_clause}
              {part_clause}
            -- Oldest due first, within the vendor as well as across all of
            -- them: policy.poll_now backdates due_at by 3650 days so an
            -- express poll sorts to the front, and it must still do so here.
            ORDER BY f.due_at, f.id
            FOR UPDATE OF f SKIP LOCKED
            LIMIT $2
        ), claimed AS (
            UPDATE feed f
            SET hold_until = $3
            FROM picked p
            WHERE f.id = p.id
            RETURNING f.id, f.hold_until
        )
        SELECT
            f.id, f.source, f.key, f.polling AS enabled, t.config,
            f.type, c.description,
            f.interval_s, f.etag, f.last_modified, f.res_hash, f.list_total, f.last_full_poll_at,
            f.strikes, f.due_at,
            -- The lease must come from the CTE. A data-modifying
            -- CTE's writes are invisible to the rest of the same query, so
            -- `f.hold_until` here is the *pre-claim* value -- NULL on a feed
            -- nobody has polled. finish_schedule_*() then guards on
            -- `hold_until = $7`, which never matches NULL, so every completed
            -- poll reported a lost lease and skipped its poll_log row.
            x.hold_until,
            t.company_id,
            c.name AS company_name
        FROM claimed x
        JOIN feed f ON f.id = x.id
        LEFT JOIN token t ON t.ats = f.source AND t.token = f.key
        LEFT JOIN company c ON c.id = t.company_id
        ORDER BY f.due_at, f.id
        """,
        now,
        max(1, limit),
        lease_until,
        *([source] if source is not None else []),
        *part_args,
    )
    return [_feed_from_row(row) for row in rows]


async def claim_feed(
    conn: Connection,
    *,
    source: str,
    key: str,
    now: datetime,
    lease_seconds: int = 3600,
) -> Feed | None:
    """Lease one feed for an on-demand poll, regardless of due_at.

    Same eligibility as claim_due_feeds minus the due check: enabled, unpaused,
    and not already held. None means the feed is unknown or not claimable; the
    caller diagnoses which.
    """
    lease_until = now + timedelta(seconds=max(60, lease_seconds))
    row = await conn.fetchrow(
        """
        WITH picked AS (
            SELECT f.id
            FROM feed f
            WHERE f.source = $1
              AND f.key = $2
              AND f.polling
              AND f.paused_at IS NULL
              AND (f.hold_until IS NULL OR f.hold_until <= $3)
            FOR UPDATE OF f SKIP LOCKED
        ), claimed AS (
            UPDATE feed f
            SET hold_until = $4
            FROM picked p
            WHERE f.id = p.id
            RETURNING f.id, f.hold_until
        )
        SELECT
            f.id, f.source, f.key, f.polling AS enabled, t.config,
            f.type, c.description,
            f.interval_s, f.etag, f.last_modified, f.res_hash, f.list_total, f.last_full_poll_at,
            f.strikes, f.due_at,
            -- The lease must come from the CTE: a data-modifying
            -- CTE's writes are invisible to the rest of the same query, so
            -- `f.hold_until` would be the pre-claim value and finish_schedule_*'s
            -- `hold_until = $N` guard would never match. Same trap as
            -- claim_due_feeds above.
            x.hold_until,
            t.company_id,
            c.name AS company_name
        FROM claimed x
        JOIN feed f ON f.id = x.id
        LEFT JOIN token t ON t.ats = f.source AND t.token = f.key
        LEFT JOIN company c ON c.id = t.company_id
        """,
        source,
        key,
        now,
        lease_until,
    )
    return _feed_from_row(row) if row else None


async def all_feeds(conn: Connection) -> list[Feed]:
    rows = await conn.fetch(_FEED_SELECT + " ORDER BY f.source, f.key")
    return [_feed_from_row(row) for row in rows]


async def due_sources(conn: Connection, now: datetime) -> list[str]:
    """Every source with at least one claimable feed right now.

    `run.cycle` spawns a worker pool per name returned and nothing at all for
    the rest, so this is deliberately the same predicate `claim_due_feeds`
    uses -- a source listed here must have something for its workers to claim.
    """
    rows = await conn.fetch(
        """
        SELECT DISTINCT f.source
        FROM feed f
        WHERE f.polling
          AND f.paused_at IS NULL
          AND f.due_at <= $1
          AND (f.hold_until IS NULL OR f.hold_until <= $1)
        ORDER BY f.source
        """,
        now,
    )
    return [row["source"] for row in rows]


async def due_key_parts(
    conn: Connection, now: datetime, source: str, index: int
) -> list[str]:
    """Distinct values of key part `index` among `source`'s claimable feeds.

    `run.due_lanes` turns these into lanes, so the predicate is the same one
    `claim_due_feeds` and `due_sources` use.
    """
    rows = await conn.fetch(
        """
        SELECT DISTINCT split_part(f.key, '/', $3) AS part
        FROM feed f
        WHERE f.polling
          AND f.paused_at IS NULL
          AND f.due_at <= $1
          AND (f.hold_until IS NULL OR f.hold_until <= $1)
          AND f.source = $2
        ORDER BY part
        """,
        now,
        source,
        index,
    )
    return [row["part"] for row in rows]


async def due_feeds(
    conn: Connection,
    now: datetime,
    limit: int | None = None,
    *,
    source: str | None = None,
    part: KeyPart | None = None,
) -> list[Feed]:
    """Read the claimable queue without taking leases, for dry runs.

    `limit=None` reads the whole queue: a dry run takes no lease, so there is
    nothing to stop a refill loop reselecting the same rows forever. `cycle`
    snapshots this once per source instead and lets its time budget bound the
    work.
    """
    source_clause = "AND f.source = $2" if source is not None else ""
    part_clause, part_args = _key_part_clause(part, 2 + (source is not None))
    limit_clause = (
        "" if limit is None else f"LIMIT ${2 + (source is not None) + len(part_args)}"
    )
    rows = await conn.fetch(
        _FEED_SELECT
        + f"""
        WHERE f.polling
          AND f.paused_at IS NULL
          AND f.due_at <= $1
          AND (f.hold_until IS NULL OR f.hold_until <= $1)
          {source_clause}
          {part_clause}
        ORDER BY f.due_at, f.id
        {limit_clause}
        """,
        now,
        *([source] if source is not None else []),
        *part_args,
        *([max(1, limit)] if limit is not None else []),
    )
    return [_feed_from_row(row) for row in rows]


async def fetchable_feeds(conn: Connection, now: datetime) -> list[Feed]:
    """List feeds an explicit fetch-all sweep may attempt.

    Unlike ``due_feeds``, this deliberately ignores ``due_at``.  It otherwise
    observes the same operator and lease boundaries as ``claim_feed``.  A
    caller must still claim each row before a live poll: another worker may
    change that state between this initial inventory read and its turn.
    """
    rows = await conn.fetch(
        _FEED_SELECT
        + """
        WHERE f.polling
          AND f.paused_at IS NULL
          AND (f.hold_until IS NULL OR f.hold_until <= $1)
        ORDER BY f.source, f.key
        """,
        now,
    )
    return [_feed_from_row(row) for row in rows]


async def replay_feeds(conn: Connection) -> list[Feed]:
    """Return only feed instances that currently have replayable bronze."""
    rows = await conn.fetch(
        _FEED_SELECT
        + """
        WHERE EXISTS (
            SELECT 1 FROM raw_posting r
            WHERE r.source = f.source || ':' || f.key
        )
        ORDER BY f.source, f.key
        """
    )
    return [_feed_from_row(row) for row in rows]


async def renew_feed_lease(
    conn: Connection, feed: Feed, *, now: datetime, until: datetime,
) -> bool:
    """Extend only a still-live claim owned by this poll."""
    result = await conn.execute(
        """
        UPDATE feed SET hold_until = $3
        WHERE id = $1 AND hold_until = $2 AND hold_until > $4
        """,
        feed.id, feed.hold_until, until, now,
        timeout=10,
    )
    return result == "UPDATE 1"


async def finish_schedule_success(
    conn: Connection,
    feed: Feed,
    *,
    now: datetime,
    due_at: datetime,
    etag: str | None,
    res_hash: str | None,
    changed: bool,
    list_total: int | None = None,
    full_listing: bool = False,
    last_modified: str | None = None,
    forget_snapshot: bool = False,
    reused_detail: bool = False,
) -> bool:
    """Complete a claimed poll without overwriting a reclaimed worker's lease.

    ``forget_snapshot`` clears etag, last_modified, and res_hash so the next
    poll cannot short-circuit (304 or matching hash) past silver. Used when
    silver left postings unresolved: a later poll may be able to resolve them.

    A supplied ``list_total`` records the unfiltered board's observed total,
    even when a search accelerates the subsequent retrieval. Only a full
    listing that also fetched every detail fresh (``reused_detail`` false) advances
    ``last_full_poll_at``. That column is the clock `sources.reusing` bounds
    detail reuse by; a count-probe source enumerates its whole board on most
    polls, so advancing it on every listing kept reuse "fresh" forever and a
    description-only edit was never refetched.
    """
    result = await conn.execute(
        """
        UPDATE feed SET
            etag = CASE WHEN $11 THEN NULL ELSE $3 END,
            res_hash = CASE WHEN $11 THEN NULL ELSE COALESCE($4, res_hash) END,
            strikes = 0,
            due_at = $5,
            hold_until = NULL,
            last_poll_at = $2,
            last_change_at = CASE WHEN $6 THEN $2 ELSE last_change_at END,
            list_total = COALESCE($8::integer, list_total),
            last_full_poll_at = CASE WHEN $9 AND NOT $12 THEN $2 ELSE last_full_poll_at END,
            last_modified = CASE WHEN $11 THEN NULL ELSE $10 END
        WHERE id = $1 AND hold_until = $7
        """,
        feed.id,
        now,
        etag,
        res_hash,
        due_at,
        changed,
        feed.hold_until,
        list_total,
        full_listing,
        last_modified,
        forget_snapshot,
        reused_detail,
    )
    return result == "UPDATE 1"


async def finish_schedule_failure(
    conn: Connection,
    feed: Feed,
    *,
    now: datetime,
    due_at: datetime,
    etag: str | None = None,
) -> bool:
    """Release a failed claim, retaining bronze and the prior response hash."""
    result = await conn.execute(
        """
        UPDATE feed SET
            etag = COALESCE($3, etag),
            strikes = LEAST(strikes::integer + 1, 32767)::smallint,
            due_at = $4,
            hold_until = NULL,
            last_poll_at = $2
        WHERE id = $1 AND hold_until = $5
        """,
        feed.id,
        now,
        etag,
        due_at,
        feed.hold_until,
    )
    return result == "UPDATE 1"


async def append_poll_log(
    conn: Connection,
    *,
    feed_id: int,
    polled_at: datetime,
    status: str,
    new_postings: int = 0,
    status_code: int | None = None,
) -> None:
    """Record what one completed poll did -- the Scheduler page's only input.

    Append-only and never updated: the row is the fact that a poll happened, so
    a later poll adds a row rather than revising this one. ``status`` mirrors
    ``work.Outcome``; ``new_postings`` counts first sightings only, never
    re-seen postings whose content merely changed.

    ``status_code`` is the HTTP status behind that outcome, and ``None`` when
    there is none to record: a transport failure, or a fault raised before the
    request went out. It explains a row, never routes one -- ``status`` remains
    the only value the scheduler reads.
    """
    await conn.execute(
        """
        INSERT INTO poll_log (feed_id, polled_at, status, new_postings, status_code)
        VALUES ($1, $2, $3, $4, $5)
        """,
        feed_id,
        polled_at,
        status,
        new_postings,
        status_code,
    )


# Hoisted so both the console's manual drop and the scheduler's automatic one
# run this exact statement rather than two copies of it. The log row goes in the
# same CTE as the delete, and `token_log` has no FK, so it is the only trace of
# the feed that survives.
DROP_FEED_SQL = """
WITH dropped AS (
    DELETE FROM feed
     WHERE source = $1 AND key = $2
 RETURNING id, source, key
), logged AS (
    INSERT INTO token_log (checked_at, ats, token, status)
    SELECT now(), source, key, 'feed_exited' FROM dropped
)
SELECT id FROM dropped
"""

# Consecutive 404s that justify dropping a feed rather than retrying it forever.
GONE_STRIKES = 3


async def gone_strikes(conn: Connection, feed_id: int) -> int:
    """Count 404s recorded for this feed since its last successful poll.

    Reads `poll_log` rather than a column of its own: `status_code` is already
    the durable record of what a failure carried, so nothing new has to be
    stored to answer this.

    An `ok` or `unchanged` poll resets the count -- the board answered, so
    whatever was wrong is over. A failure that carried NO status does NOT
    reset it: that is a transport failure, the shared blips that hit every
    vendor at once, and letting one of those rescue a genuinely dead board is
    the whole reason this counts 404s instead of `feed.strikes`.
    """
    return await conn.fetchval(
        """
        SELECT count(*) FROM poll_log
         WHERE feed_id = $1
           AND status_code = 404
           AND polled_at > COALESCE(
               (SELECT max(polled_at) FROM poll_log
                 WHERE feed_id = $1 AND status IN ('ok', 'unchanged')),
               '-infinity'::timestamptz)
        """,
        feed_id,
    )


async def drop_feed(conn: Connection, source: str, key: str) -> int | None:
    """Delete a feed, leaving its token, company, bronze, and postings intact.

    Returns the dropped feed's id, or None when no such feed was there. The
    `token` row is deliberately untouched, so a later explicit import or a
    token heartbeat can bring the feed back.
    """
    return await conn.fetchval(DROP_FEED_SQL, source, key)


async def save_raw(
    conn: Connection,
    *,
    source: str,
    source_key: str,
    data: Any,
    data_hash: str,
) -> tuple[int, bool]:
    """Persist one immutable bronze revision and return its durable id."""
    row = await conn.fetchrow(
        """
        INSERT INTO raw_posting(source, source_key, data, data_hash)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (source, source_key, data_hash) DO NOTHING
        RETURNING id
        """,
        source,
        source_key,
        data,
        data_hash,
    )
    if row is not None:
        return row["id"], True
    raw_id = await conn.fetchval(
        "SELECT id FROM raw_posting WHERE source = $1 AND source_key = $2 AND data_hash = $3",
        source,
        source_key,
        data_hash,
    )
    return int(raw_id), False


async def raw_candidates(
    conn: Connection, source: str, keys: list[str], hashes: list[str]
) -> list[asyncpg.Record]:
    """Find exact hashes first, otherwise the latest raw for hash-rule compatibility."""
    return await conn.fetch(
        """
        SELECT wanted.source_key, wanted.data_hash AS requested_hash,
               COALESCE(stored.id, prior.id) AS id,
               COALESCE(stored.data_hash, prior.data_hash) AS data_hash,
               prior.data
        FROM unnest($2::text[], $3::text[]) AS wanted(source_key, data_hash)
        LEFT JOIN raw_posting stored ON stored.source = $1
            AND stored.source_key = wanted.source_key AND stored.data_hash = wanted.data_hash
        LEFT JOIN LATERAL (
            SELECT id, data_hash, data FROM raw_posting
            WHERE stored.id IS NULL AND source = $1 AND source_key = wanted.source_key
            ORDER BY fetched_at DESC, id DESC LIMIT 1
        ) prior ON true
        """,
        source, keys, hashes,
    )


async def latest_raw(conn: Connection, source: str) -> list[asyncpg.Record]:
    """Return the newest bronze revision per source-local posting identity."""
    return await conn.fetch(
        """
        SELECT DISTINCT ON (source_key) id, source_key, data, data_hash, fetched_at
        FROM raw_posting
        WHERE source = $1
        ORDER BY source_key, fetched_at DESC, id DESC
        """,
        source,
    )


async def set_feed_enabled(
    conn: Connection, *, source: str, key: str, enabled: bool
) -> int | None:
    """Operator on/off switch for one feed; None when the feed is unknown."""
    source, key = canonical_feed_identity(source, key)
    if enabled:
        require_source_allowed(source)
    return await conn.fetchval(
        "UPDATE feed SET polling = $3 WHERE source = $1 AND key = $2 RETURNING id",
        source,
        key,
        enabled,
    )


async def set_feeds_enabled(
    conn: Connection, identities: list[tuple[str, str]], *, enabled: bool
) -> int:
    """Operator on/off switch for many feeds at once; returns rows affected.

    Enabling imports any feed PostgreSQL has never seen, because the vendor
    switch on the Feeds page is useless otherwise: a catalog-only source like
    Ashby has no rows to flip until the first import. The conflict branch writes
    ``polling`` through because this explicit operator action must resurrect a
    feed that was previously switched off.

    Disabling never inserts: a feed absent from PostgreSQL is already off, so
    only the enable branch can log ``feed_entered`` -- and only for the rows
    ``xmax = 0`` marks as genuinely new. Flipping ``polling`` on an existing
    feed is not an entry; the row was already in the table.
    """
    if not identities:
        return 0
    identities = list(
        dict.fromkeys(canonical_feed_identity(*row) for row in identities)
    )
    if enabled:
        for source, _ in identities:
            require_source_allowed(source)
    sources = [source for source, _ in identities]
    keys = [key for _, key in identities]
    if not enabled:
        result = await conn.execute(
            """
            UPDATE feed SET polling = false
            WHERE (source, key) IN (SELECT * FROM unnest($1::text[], $2::text[]))
            """,
            sources,
            keys,
        )
        return int(result.rsplit(" ", 1)[-1])
    rows = await conn.fetch(
        """
        WITH upserted AS (
            INSERT INTO feed(source, key, polling, interval_s)
            SELECT source, key, true, CASE WHEN source = 'workable' THEN 43200 ELSE 900 END
            FROM unnest($1::text[], $2::text[]) AS incoming(source, key)
            ON CONFLICT (source, key) DO UPDATE SET polling = true
            RETURNING id, source, key, (xmax = 0) AS inserted
        ), logged AS (
            INSERT INTO token_log (checked_at, ats, token, status)
            SELECT now(), source, key, 'feed_entered' FROM upserted WHERE inserted
        )
        SELECT id FROM upserted
        """,
        sources,
        keys,
    )
    return len(rows)


async def set_schedule_interval(
    conn: Connection, feed_id: int, interval_s: int
) -> bool:
    """Operator cadence change. Runtime schedule state is left untouched."""
    result = await conn.execute(
        "UPDATE feed SET interval_s = $2 WHERE id = $1",
        feed_id,
        interval_s,
    )
    return result == "UPDATE 1"


async def set_schedule_paused(conn: Connection, feed_id: int, paused: bool) -> bool:
    """Hold or release one feed's polling without touching ``feed.polling``.

    A held feed is skipped by both queue readers while staying enabled, so it
    keeps its place in every listing rather than disappearing the way disabling
    it would. ``due_at`` is deliberately untouched: it goes stale in the past for
    the duration of the hold, which makes releasing one immediately due.
    """
    result = await conn.execute(
        """
        UPDATE feed
        SET paused_at = CASE WHEN $2 THEN now() ELSE NULL END
        WHERE id = $1
        """,
        feed_id,
        paused,
    )
    return result == "UPDATE 1"


def alias_candidates(raw: str) -> list[str]:
    """Normalized alias keys for ``raw``, most specific first.

    Order is resolution order: an exact casefolded match beats the slug, which
    beats the slug with a trailing legal suffix stripped.
    """
    exact = unicodedata.normalize("NFKC", raw).strip().casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", exact).strip("-")
    without_suffix = re.sub(
        r"-(?:inc|incorporated|llc|ltd|limited|corp|corporation|co|company)$",
        "",
        slug,
    )
    return list(dict.fromkeys(value for value in (exact, slug, without_suffix) if value))


async def load_alias_map(conn: Connection) -> dict[str, int]:
    """Snapshot the whole alias table for in-memory company resolution.

    A few thousand rows. Trading one query per posting for one query per run is
    only sound while nothing writes aliases mid-pass, as in the parse path.
    """
    rows = await conn.fetch("SELECT alias_norm, company_id FROM company_alias")
    return {row["alias_norm"]: row["company_id"] for row in rows}


async def resolve_company(
    conn: Connection, raw: str, *, aliases: dict[str, int] | None = None
) -> int | None:
    candidates = alias_candidates(raw)
    if aliases is not None:
        # Authoritative: the map is the entire table, so a miss here is a miss
        # in the database. Falling back to a query would reintroduce the very
        # per-posting round trip the map exists to remove.
        for candidate in candidates:
            company_id = aliases.get(candidate)
            if company_id is not None:
                return company_id
        return None
    row = await conn.fetchrow(
        """
        SELECT company_id
        FROM company_alias
        WHERE alias_norm = ANY($1::text[])
        ORDER BY array_position($1::text[], alias_norm)
        LIMIT 1
        """,
        candidates,
    )
    return row["company_id"] if row is not None else None


# Vendors whose token is `tenant/host/site` (or Avature's `company/board`);
# only the tenant reads like a name.
_TENANT_PREFIXED_ATS = {"workday", "oracle", "avature"}


def token_slug(ats: str, token: str) -> str:
    """The name-like substring of `token` to resolve a company against."""
    if ats in _TENANT_PREFIXED_ATS:
        return token.split("/", 1)[0]
    if ats == "eightfold" and "/" in token:
        # `host/domain`; the host is often shared (app.eightfold.ai), the
        # domain's first label is the employer.
        return token.split("/", 1)[1].split(".", 1)[0]
    return token


async def mint_company(
    conn: Connection,
    name: str,
    *,
    discovered_from: str,
    tags: dict[str, Any] | None = None,
) -> int:
    """Insert a ``pending`` company named `name`; returns its id.

    No domain: nothing that mints knows one, and a guess in a UNIQUE column
    blocks the real owner. ``name_machine`` keeps the guess after an operator
    renames the row on the Companies page.
    """
    return int(
        await conn.fetchval(
            """
            INSERT INTO company (name, discovered_from, tags)
            VALUES ($1, $2, $3::jsonb)
            RETURNING id
            """,
            name,
            discovered_from,
            {"name_machine": name, **(tags or {})},
        )
    )


async def write_company_aliases(conn: Connection, company_id: int, *raw: str | None) -> list[str]:
    """Point every spelling of *raw* at ``company_id``, never stealing one.

    The slug matters as much as the name: silver sees the slug on the wire for
    vendors whose postings carry no company name, and a name-only alias set
    would leave those postings unresolvable.

    Returns the keys actually written; one another company owns is skipped.
    """
    seen: dict[str, str] = {}
    for value in raw:
        if not value:
            continue
        for candidate in alias_candidates(value):
            seen.setdefault(candidate, value)
    if not seen:
        return []
    records = await conn.fetch(
        """
        INSERT INTO company_alias (alias_norm, company_id, alias)
        SELECT norm, $2, raw FROM unnest($1::text[], $3::text[]) AS t(norm, raw)
        ON CONFLICT (alias_norm) DO NOTHING
        RETURNING alias_norm
        """,
        list(seen.keys()),
        company_id,
        list(seen.values()),
    )
    return [record["alias_norm"] for record in records]


async def resolve_or_mint(
    conn: Connection,
    *names: str | None,
    discovered_from: str,
    tags: dict[str, Any] | None = None,
) -> tuple[int, bool]:
    """Find the company any of *names* already resolves to, else mint one.

    Returns ``(company_id, minted)``. Names resolve in the order given and the
    first non-empty one names a minted row; every name becomes an alias.

    Concurrent callers naming the same employer must land on one company, and
    ``company.name`` is not unique, so the alias keys are the only shared
    identity. Each key is taken as a transaction-scoped advisory lock, in
    sorted order so two callers holding overlapping keys cannot deadlock, and
    the alias lookup is repeated under the lock: the first caller mints and
    writes the aliases, every later one finds them. The lock lives until the
    caller's transaction commits, which is also when those aliases become
    visible -- so the caller must be inside one.
    """
    present = [name for name in dict.fromkeys(name.strip() for name in names if name) if name]
    if not present:
        raise ValueError("resolve_or_mint needs a name")
    for key in sorted({key for name in present for key in alias_candidates(name)}):
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"company_alias:{key}",
        )
    company_id = None
    for name in present:
        company_id = await resolve_company(conn, name)
        if company_id is not None:
            break
    minted = company_id is None
    if company_id is None:
        company_id = await mint_company(
            conn, present[0], discovered_from=discovered_from, tags=tags
        )
    await write_company_aliases(conn, company_id, *present)
    return company_id, minted


# Hosts that serve job boards for many employers. A posting URL on one names
# the vendor, never the employer, so it is no company's domain -- not even the
# vendor's own, which a person can still set by hand.
@cache
def _board_hosts() -> frozenset[str]:
    from keyscan.rules import RULES

    return frozenset(domain for rule in RULES for domain in rule.domains)


def normalize_domain(value: str | None, *, board_hosts_ok: bool = False) -> str | None:
    """The bare host in *value* (a URL or a host), or None if it is no domain.

    Lowercase, no scheme, path, query, port or leading ``www.`` -- the form
    every stored ``company.domain`` already has, so the UNIQUE column compares
    like with like. A job-board host is refused unless *board_hosts_ok*: a
    person typing ``rippling.com`` for Rippling means it, a feed never does.
    """
    host = (value or "").strip().lower()
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", host)
    host = re.split(r"[/?#]", host, maxsplit=1)[0]
    host = host.rsplit("@", 1)[-1].split(":", 1)[0].strip(".")
    host = host.removeprefix("www.")
    if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
        return None
    labels = host.split(".")
    if not board_hosts_ok and any(".".join(labels[i:]) in _board_hosts() for i in range(len(labels))):
        return None
    return host


# A job link on careers.acme.com names acme.com; the subdomain is the board.
_CAREER_LABELS = frozenset(
    "careers career jobs job karriere werkenbij vacatures recrutement recruiting "
    "join apply work hiring talent".split()
)


def _without_career_label(domain: str | None) -> str | None:
    if domain is None:
        return None
    label, _, rest = domain.partition(".")
    return rest if label in _CAREER_LABELS and "." in rest else domain


# Serving identity of a company row `c`: its domain once it has one. Shared by
# serving (allocation, joins) and every writer that can change the inputs.
COMPANY_KEY = "CASE WHEN c.domain IS NOT NULL THEN jsonb_build_array('domain', lower(c.domain)) ELSE jsonb_build_array('source_id', c.id, c.name) END"
# Held by serving's identity allocator; a re-key must not race it.
IDENTITY_LOCK = 627031007


async def company_identity_key(conn: Connection, company_id: int) -> Any:
    """The row's current serving key; read it before changing domain or name."""
    return await conn.fetchval(f"SELECT {COMPANY_KEY} FROM company c WHERE c.id = $1", company_id)


async def rekey_company_identity(conn: Connection, company_id: int, old_key: Any) -> None:
    """Keep a company's serving ID after its domain or (domainless) name changed.

    The mapping row under *old_key* moves to the row's current key and keeps
    the old one in ``previous_keys``. Without this the next publish allocates a
    fresh ID, and OneShot loses everything tied to the old one. Call it in the
    transaction that changed the row. A no-op where publication was never set
    up, where the key did not change, or where the new key already has a
    mapping of its own (re-pointing would violate its uniqueness).
    """
    if await conn.fetchval("SELECT to_regclass('frontend_identity')") is None:
        return
    await conn.execute("SELECT pg_advisory_xact_lock($1)", IDENTITY_LOCK)
    await conn.execute(
        f"""
        UPDATE frontend_identity m
           SET previous_keys = CASE WHEN m.previous_keys @> jsonb_build_array(m.key)
                                    THEN m.previous_keys
                                    ELSE m.previous_keys || jsonb_build_array(m.key) END,
               key = {COMPANY_KEY}
          FROM company c
         WHERE c.id = $1 AND m.kind = 'company' AND m.key = $2::jsonb
           AND m.key IS DISTINCT FROM {COMPANY_KEY}
           AND NOT EXISTS (
               SELECT 1 FROM frontend_identity taken
                WHERE taken.kind = 'company' AND taken.key = {COMPANY_KEY}
           )
        """,
        company_id,
        old_key,
    )


async def fill_company(
    conn: Connection,
    company_id: int,
    *,
    domain: str | None = None,
    description: str | None = None,
) -> bool:
    """Fill what a feed knows about a company into columns nobody has set.

    Machine facts never overwrite: an empty column is filled, a set one is left
    alone, and a verified or rejected row is a person's answer and is not
    touched at all. A domain another company already holds is skipped -- two
    rows claiming one domain is a merge, and that is a person's decision. Each
    filled value is also kept as ``<field>_machine`` so the Companies page can
    show what the machine guessed. Returns whether anything changed.

    Runs once per company: every attempt stamps ``fill_checked_at``, even one
    that found nothing usable, and a stamped row is never tried again.
    """
    domain = _without_career_label(normalize_domain(domain))
    description = (description or "").strip() or None
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT domain, description FROM company
                 WHERE id = $1 AND reviewed = 'pending'
                   AND NOT tags ? 'fill_checked_at'
                   FOR UPDATE
                """,
                company_id,
            )
            if row is None:
                return False
            await conn.execute(
                "UPDATE company SET tags = tags || jsonb_build_object('fill_checked_at', now()) WHERE id = $1",
                company_id,
            )
            patch: dict[str, Any] = {}
            if row["domain"] is None and domain is not None and not await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM company WHERE lower(domain) = $1)", domain
            ):
                patch["domain"] = domain
            if row["description"] is None and description is not None:
                patch["description"] = description
            if not patch:
                return False
            old_key = await company_identity_key(conn, company_id) if "domain" in patch else None
            await conn.execute(
                """
                UPDATE company
                   SET domain = COALESCE($2, domain),
                       description = COALESCE($3, description),
                       tags = tags || $4::jsonb
                 WHERE id = $1
                """,
                company_id,
                patch.get("domain"),
                patch.get("description"),
                {f"{field}_machine": value for field, value in patch.items()},
            )
            if old_key is not None:
                await rekey_company_identity(conn, company_id, old_key)
            return True
    except asyncpg.UniqueViolationError:
        # Another feed claimed the same domain since the check; theirs stands.
        return False


async def ensure_token_company(
    conn: Connection, *, ats: str, token: str, name: str | None
) -> tuple[int, bool]:
    """Resolve or mint the company for a token that has none; link the token.

    Returns ``(company_id, minted)``. Called from silver when a polled ATS
    feed's postings resolve to nothing, so a bulk-promoted token stops dropping
    every posting it fetches. Resolution and minting are ``resolve_or_mint``'s,
    so the caller must be inside a transaction (silver's poll path is).
    """
    ats, token = canonical_feed_identity(ats, token)
    slug = token_slug(ats, token)
    async with conn.transaction():
        company_id, minted = await resolve_or_mint(
            conn, name, slug, discovered_from="feed"
        )
        await conn.execute(
            """
            UPDATE token SET company_id = $1
             WHERE ats = $2 AND token = $3 AND company_id IS NULL
            """,
            company_id,
            ats,
            token,
        )
    return company_id, minted


async def touch_source_postings(
    conn: Connection, source: str, now: datetime, *, feed_id: int | None = None
) -> int:
    """Refresh ``last_seen_at`` for postings still in an unchanged snapshot.

    Called only when the response did not move -- a 304, or a snapshot hash that
    matched. ``snapshot_hash`` covers external ids *and* content, so a match
    proves every posting in that snapshot is still listed and a blanket touch is
    sound for them.

    It is not sound for a posting that dropped out of an *earlier* changed poll.
    That row was never in the matching snapshot, and bumping it here erases the
    only evidence it vanished. ``feed.last_change_at`` marks when the snapshot
    last moved: a ``last_seen_at`` older than that means the posting was already
    absent when the snapshot froze, so it is left alone.

    ``feed_id`` is optional so callers without one keep the blunt behaviour;
    every production caller passes it.
    """
    if feed_id is None:
        result = await conn.execute(
            "UPDATE job_posting SET last_seen_at = $2, missing_since = NULL WHERE source = $1",
            source,
            now,
        )
    else:
        result = await conn.execute(
            """
            UPDATE job_posting SET last_seen_at = $2, missing_since = NULL
            WHERE source = $1
              AND last_seen_at >= COALESCE(
                  (SELECT last_change_at FROM feed WHERE id = $3), last_seen_at
              )
            """,
            source,
            now,
            feed_id,
        )
    return int(result.rsplit(" ", 1)[-1])


async def upsert_job_posting(
    conn: Connection,
    *,
    source: str,
    source_key: str,
    company_id: int,
    values: dict[str, Any],
    content_hash: str,
    raw_id: int,
    seen_at: datetime,
    touch_seen: bool = True,
    ats_closed_at: datetime | None = None,
) -> tuple[int, bool, bool]:
    """Upsert the exact canonical projection declared by ``job_posting``.

    Returns ``(id, changed, created)``. The two flags differ only when a posting
    we already had comes back with new content: ``changed`` is true, ``created``
    is false. That gap is the whole distinction between churn and arrivals, and
    ``poll_log.new_postings`` is defined on ``created`` alone.

    ``touch_seen`` is false for replay. A replay reparses stored payloads; it
    observes nothing about whether the posting is still listed, and stamping
    ``last_seen_at`` from one would resurrect every posting that ever vanished.

    The tracking timestamps are passed separately rather than through ``values``
    on purpose: ``values`` feeds ``content_hash``, so folding them in there would
    report every observation as a content change.
    """
    # Single round trip: ``prev`` and the ``ins`` write share one query
    # snapshot, so ``prev`` sees the pre-upsert row even though ``ins`` may
    # have just inserted or updated it. That is what lets the outer SELECT
    # derive ``changed``/``created`` without a separate SELECT beforehand.
    row = await conn.fetchrow(
        """
        WITH prev AS (
            SELECT content_hash FROM job_posting
            WHERE source = $1 AND source_key = $2
        ), ins AS (
            INSERT INTO job_posting(
                source, source_key, company_id, title, description, location_text,
                source_url, apply_url, posted_at, job_type, remote, part_time,
                locations, attributes, status, last_seen_at, content_hash, raw_id,
                ats_closed_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $20, $12, $13,
                      $14, $15, $16, $17, $19)
            ON CONFLICT (source, source_key) DO UPDATE SET
                company_id = EXCLUDED.company_id,
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                location_text = EXCLUDED.location_text,
                source_url = EXCLUDED.source_url,
                apply_url = EXCLUDED.apply_url,
                posted_at = EXCLUDED.posted_at,
                -- Parse-time facts, not enrichment: the adapter re-derives them
                -- from the current raw payload on every pass, so they overwrite
                -- unconditionally rather than being invalidated like gold below.
                job_type = EXCLUDED.job_type,
                remote = EXCLUDED.remote,
                part_time = EXCLUDED.part_time,
                locations = EXCLUDED.locations,
                -- The Gold domain consumes title and company context, and is
                -- only defined for typed opportunities. Clear both value and
                -- stamp whenever one of those inputs changes.
                job_domain = CASE
                    WHEN job_posting.title IS DISTINCT FROM EXCLUDED.title
                        OR job_posting.company_id IS DISTINCT FROM EXCLUDED.company_id
                        OR job_posting.job_type IS DISTINCT FROM EXCLUDED.job_type
                    THEN NULL
                    ELSE job_posting.job_domain
                END,
                job_domain_version = CASE
                    WHEN job_posting.title IS DISTINCT FROM EXCLUDED.title
                        OR job_posting.company_id IS DISTINCT FROM EXCLUDED.company_id
                        OR job_posting.job_type IS DISTINCT FROM EXCLUDED.job_type
                    THEN NULL
                    ELSE job_posting.job_domain_version
                END,
                -- Skills consume only description; degree fields and summaries
                -- consume title and description; focus chips also consume the
                -- employer and only exist on typed postings. Comparing those
                -- inputs avoids invalidating them when unrelated stable fields
                -- change, or when the content-hash recipe itself changes.
                attributes = CASE
                    WHEN job_posting.description IS DISTINCT FROM EXCLUDED.description
                    THEN (job_posting.attributes
                          - 'skills' - 'skills_meta'
                          - 'degree' - 'degree_meta'
                          - 'summary' - 'summary_meta'
                          - 'focus' - 'focus_meta')
                         || EXCLUDED.attributes
                    WHEN job_posting.title IS DISTINCT FROM EXCLUDED.title
                    THEN (job_posting.attributes
                          - 'degree' - 'degree_meta'
                          - 'summary' - 'summary_meta'
                          - 'focus' - 'focus_meta')
                         || EXCLUDED.attributes
                    WHEN job_posting.company_id IS DISTINCT FROM EXCLUDED.company_id
                        OR job_posting.job_type IS DISTINCT FROM EXCLUDED.job_type
                    THEN (job_posting.attributes - 'focus' - 'focus_meta')
                         || EXCLUDED.attributes
                    ELSE job_posting.attributes || EXCLUDED.attributes
                END,
                -- The degree tag's revision stamp lives in a column, so
                -- invalidating the tag has to clear the column in the same
                -- statement. Dropping one without the other would either strand
                -- a stamp with no tag or re-enrich a row on every single pass.
                degree_version = CASE
                    WHEN job_posting.description IS DISTINCT FROM EXCLUDED.description
                        OR job_posting.title IS DISTINCT FROM EXCLUDED.title
                    THEN NULL
                    ELSE job_posting.degree_version
                END,
                -- status is monotonic, not write-once: a stated closure lands,
                -- and nothing reopens the row. A list harvest projects 'active'
                -- for every row it returns, so a symmetric overwrite would
                -- reactivate every closed posting on the very next poll.
                status = CASE
                    WHEN EXCLUDED.status = 'closed' THEN 'closed'
                    ELSE job_posting.status
                END,
                -- Only an observation of the live listing may advance this;
                -- replay passes touch_seen false. See the docstring.
                last_seen_at = CASE
                    WHEN $18 THEN EXCLUDED.last_seen_at
                    ELSE job_posting.last_seen_at
                END,
                -- Listed again: no longer missing. Replay observed nothing.
                missing_since = CASE
                    WHEN $18 THEN NULL
                    ELSE job_posting.missing_since
                END,
                -- First statement wins: the earliest poll that saw the vendor's
                -- own flag flip is the tightest bound we have on when it did.
                ats_closed_at = COALESCE(
                    job_posting.ats_closed_at, EXCLUDED.ats_closed_at
                ),
                content_hash = EXCLUDED.content_hash,
                raw_id = EXCLUDED.raw_id
            RETURNING id
        )
        SELECT
            ins.id AS id,
            (SELECT content_hash FROM prev) IS DISTINCT FROM $16 AS changed,
            NOT EXISTS (SELECT 1 FROM prev) AS created
        FROM ins
        """,
        source,
        source_key,
        company_id,
        values["title"],
        values.get("description"),
        values.get("location_text"),
        values.get("source_url"),
        values.get("apply_url"),
        values.get("posted_at"),
        values.get("job_type"),
        values.get("remote", "unknown"),
        values.get("locations", []),
        values["attributes"],
        values["status"],
        seen_at,
        content_hash,
        raw_id,
        touch_seen,
        ats_closed_at,
        values.get("part_time", "unknown"),
    )
    return row["id"], row["changed"], row["created"]


# Gold projection -----------------------------------------------------------


async def mark_missing_postings(
    conn: Connection, source: str, polled_at: datetime, *, listed_keys: list[str] | None = None
) -> int:
    """Stamp postings the complete poll at ``polled_at`` did not list.

    Call only after a whole, non-truncated poll. Supplied list IDs refresh
    presence even for items silver skipped. Older postings are absent; keep
    their first absence timestamp until they appear again.

    A signal only -- ``status`` is not changed.
    """
    from xjobscore.reconcile import enqueue

    async with conn.transaction():
        # List presence is independent of whether silver could parse the item.
        if listed_keys is not None:
            await conn.execute(
                """
                UPDATE job_posting SET last_seen_at = $2, missing_since = NULL
                WHERE source = $1 AND source_key = ANY($3::text[])
                  AND last_seen_at < $2
                """,
                source, polled_at, listed_keys,
            )
        rows = await conn.fetch(
            """
            UPDATE job_posting SET missing_since = $2
            WHERE source = $1
              AND missing_since IS NULL
              AND last_seen_at < $2
            RETURNING id
            """,
            source,
            polled_at,
        )
        await enqueue(conn, [row["id"] for row in rows])
        return len(rows)


async def close_posting_by_key(
    conn: Connection, *, source: str, source_key: str, closed_at: datetime,
    closure_kind: str = "link",
) -> bool:
    """Record link or page closure. True when this call closed an active row.

    Deliberately not routed through ``upsert_job_posting``: that refreshes every
    canonical column from the projection it is handed, and a probe carries no
    title, body, or URL to refresh them with. Running a tombstone through it
    would blank the very row we are trying to annotate.

    Each channel keeps its first detection independently. A later observation
    in another channel may add its own timestamp, including on a closed row.

    Keyed by ``(source, source_key)`` rather than id so replay can reach it: a
    reparse of a stored tombstone knows the posting's source identity, not the
    row id silver assigned it.
    """
    try:
        column = {"link": "linked_dead_at", "page": "paged_closed_at"}[closure_kind]
    except KeyError:
        raise ValueError(f"unknown closure kind: {closure_kind}") from None
    row = await conn.fetchrow(
        f"""
        WITH prev AS (
            SELECT status FROM job_posting
            WHERE source = $1 AND source_key = $2
        ), upd AS (
            UPDATE job_posting
            SET status = 'closed',
                {column} = COALESCE({column}, $3),
                link_checked_at = $3
            WHERE source = $1 AND source_key = $2
            RETURNING id
        )
        SELECT (SELECT status FROM prev) = 'active' AS closed FROM upd
        """,
        source,
        source_key,
        closed_at,
    )
    return bool(row and row["closed"])


async def mark_checked(
    conn: Connection, posting_ids: list[int], *, checked_at: datetime
) -> int:
    """Record that these postings were probed, whatever the probe found.

    A probe that came back live, blocked, or timed out still moves
    ``link_checked_at``: the point of the column is when we last looked, not
    what we concluded. Without it an inconclusive probe is indistinguishable
    from never having probed at all, and the next pass re-spends the request.

    ``last_seen_at`` is untouched on purpose even for a live result. That column
    means "present in a listing", and a probe is not a listing.
    """
    if not posting_ids:
        return 0
    result = await conn.execute(
        "UPDATE job_posting SET link_checked_at = $2 WHERE id = ANY($1::bigint[])",
        posting_ids,
        checked_at,
    )
    return int(result.rsplit(" ", 1)[-1])


async def dedup_candidates(conn: Connection) -> list[asyncpg.Record]:
    """Lightweight silver columns used to identify duplicate groups."""
    return await conn.fetch(
        """
        SELECT id, company_id, source, title, location_text, source_url,
               apply_url, posted_at, first_seen_at
        FROM job_posting
        ORDER BY id
        """
    )


async def dedup_descriptions(
    conn: Connection, posting_ids: list[int]
) -> list[asyncpg.Record]:
    """Fetch description evidence only for postings that formed groups."""
    if not posting_ids:
        return []
    return await conn.fetch(
        """
        SELECT id, description
        FROM job_posting
        WHERE id = ANY($1::bigint[])
        """,
        posting_ids,
    )


async def replace_dup_groups(
    conn: Connection, *, groups: list[dict[str, Any]], members: list[dict[str, Any]]
) -> int:
    """Bring every derived duplicate group in line with the plan; return how
    many groups the plan holds.

    Gold groups are a pure function of ``job_posting``, and the result is the
    same as clearing and rewriting everything. Human judgements must never live
    here -- a rebuild overwrites them.

    Only differences are written. Group ids are derived from the dedup key, so
    a rebuild over mostly unchanged data touches almost no rows. Rewriting all
    of them (~270k postings) locked the whole table against the feed workers'
    posting writes and deadlocked most runs once dedup became automatic.

    Order follows the foreign key ``job_posting.dup_group_id`` -> group (no
    ``ON DELETE`` action): groups exist before members point at them, and
    members leave a group before it is deleted.
    """
    group_ids = [group["id"] for group in groups]
    member_ids = [int(member["posting_id"]) for member in members]
    if groups:
        await conn.execute(
            """
            INSERT INTO job_posting_dup_group AS d (id, primary_posting_id, state, evidence)
            SELECT g.id, g.primary_posting_id, g.state, g.evidence
            FROM jsonb_to_recordset($1::jsonb)
                AS g(id uuid, primary_posting_id bigint, state text, evidence jsonb)
            ON CONFLICT (id) DO UPDATE SET
                primary_posting_id = EXCLUDED.primary_posting_id,
                state = EXCLUDED.state,
                evidence = EXCLUDED.evidence
            WHERE (d.primary_posting_id, d.state, d.evidence)
                IS DISTINCT FROM (EXCLUDED.primary_posting_id, EXCLUDED.state, EXCLUDED.evidence)
            """,
            groups,
        )
        await conn.execute(
            """
            UPDATE job_posting p SET dup_group_id = m.group_id
            FROM jsonb_to_recordset($1::jsonb) AS m(posting_id bigint, group_id uuid)
            WHERE p.id = m.posting_id
              AND p.dup_group_id IS DISTINCT FROM m.group_id
            """,
            members,
        )
    await conn.execute(
        """
        UPDATE job_posting SET dup_group_id = NULL
        WHERE dup_group_id IS NOT NULL AND NOT (id = ANY($1::bigint[]))
        """,
        member_ids,
    )
    await conn.execute(
        "DELETE FROM job_posting_dup_group WHERE NOT (id = ANY($1::uuid[]))",
        group_ids,
    )
    return len(groups)


async def upsert_keyscan_token(
    conn: Connection,
    *,
    ats: str,
    token: str,
    company_id: int,
    status: str,
    checked_at: datetime | None,
    discovered_at: datetime,
    imported_at: datetime,
) -> bool:
    """Write one keyscan-discovered token row. Returns whether a row was written.

    Scoped to rows this importer owns: the ``WHERE`` on the conflict branch
    leaves any token with a different or NULL source untouched. The caller must
    check the return value rather than assume the write took effect.
    """
    ats, token = canonical_feed_identity(ats, token)
    result = await conn.execute(
        """
        INSERT INTO token(ats, token, company_id, status, source, checked_at, discovered_at, imported_at)
        VALUES ($1, $2, $3, $4, 'keyscan', $5, $6, $7)
        ON CONFLICT (ats, token) DO UPDATE SET
            company_id = EXCLUDED.company_id,
            status = EXCLUDED.status,
            checked_at = EXCLUDED.checked_at,
            imported_at = EXCLUDED.imported_at
        WHERE token.source = 'keyscan'
        """,
        ats,
        token,
        company_id,
        status,
        checked_at,
        discovered_at,
        imported_at,
    )
    return result != "INSERT 0 0"


async def insert_discovered_tokens(
    conn: Connection,
    rows: list[tuple[str, str, datetime]],
) -> int:
    """Add keyscan discoveries to ``token`` as unvalidated. Returns rows added.

    Insert-only: an existing ``(ats, token)`` -- whatever its source, status or
    company -- is never touched, so a re-sighting can't duplicate a row or
    reset a validated one. A new row is ``status = 'unknown'`` with
    ``checked_at IS NULL``, which is what "never validated" means in this
    table; the first probe sets ``checked_at`` and the real status.
    ``company_id`` stays NULL until `keyscan_import` resolves one -- its
    upsert owns ``source = 'keyscan'`` rows, so it picks these up unchanged.
    """
    if not rows:
        return 0
    identities = {canonical_feed_identity(ats, token): discovered for ats, token, discovered in rows}
    result = await conn.execute(
        """
        INSERT INTO token(ats, token, status, source, discovered_at)
        SELECT ats, token, 'unknown', 'keyscan', discovered_at
          FROM unnest($1::text[], $2::text[], $3::timestamptz[]) AS t(ats, token, discovered_at)
        ON CONFLICT (ats, token) DO NOTHING
        """,
        [ats for ats, _ in identities],
        [token for _, token in identities],
        list(identities.values()),
    )
    return int(result.rsplit(" ", 1)[-1])


async def import_feed(
    conn: Connection, *, source: str, key: str, enabled: bool
) -> int:
    """Import one feed, setting ``polling`` only on first sight.

    ``enabled`` is the birth default for a feed PostgreSQL has never seen.
    Afterwards the column is operator state, so the conflict branch leaves it
    untouched while still returning the existing row's id.
    """
    source, key = canonical_feed_identity(source, key)
    require_source_allowed(source)
    feed_id = await conn.fetchval(
        """
        WITH upserted AS (
            INSERT INTO feed(source, key, polling, interval_s)
            VALUES ($1, $2, $3, CASE WHEN $1 = 'workable' THEN 43200 ELSE 900 END)
            ON CONFLICT (source, key) DO UPDATE SET source = EXCLUDED.source
            RETURNING id, source, key, (xmax = 0) AS inserted
        ), logged AS (
            INSERT INTO token_log (checked_at, ats, token, status)
            SELECT now(), source, key, 'feed_entered' FROM upserted WHERE inserted
        )
        SELECT id FROM upserted
        """,
        source,
        key,
        enabled,
    )
    return int(feed_id)


async def import_feeds(
    conn: Connection, identities: list[tuple[str, str]], *, enabled: bool
) -> tuple[int, int]:
    """Bulk :func:`import_feed`; returns (inserted, existing).

    ``enabled`` supplies the initial ``polling`` value, and the conflict branch
    is a no-op write on ``source``, so promoting a token that already has a feed
    row can never flip an operator's switch. Unlike :func:`set_feeds_enabled`,
    which resurrects, this one is deliberately inert on existing rows.

    ``xmax = 0`` distinguishes the two branches: PostgreSQL leaves it zero on a
    genuine insert and non-zero on the update path, which is the only way to
    tell them apart when both return a row. It also gates the ``feed_entered``
    log row, so re-importing a feed that already exists logs nothing.
    """
    if not identities:
        return (0, 0)
    identities = list(
        dict.fromkeys(canonical_feed_identity(*row) for row in identities)
    )
    for source, _ in identities:
        require_source_allowed(source)
    rows = await conn.fetch(
        """
        WITH upserted AS (
            INSERT INTO feed(source, key, polling, interval_s)
            SELECT source, key, $3, CASE WHEN source = 'workable' THEN 43200 ELSE 900 END
            FROM unnest($1::text[], $2::text[]) AS incoming(source, key)
            ON CONFLICT (source, key) DO UPDATE SET source = EXCLUDED.source
            RETURNING id, source, key, (xmax = 0) AS inserted
        ), logged AS (
            INSERT INTO token_log (checked_at, ats, token, status)
            SELECT now(), source, key, 'feed_entered' FROM upserted WHERE inserted
        )
        SELECT id, inserted FROM upserted
        """,
        [source for source, _ in identities],
        [key for _, key in identities],
        enabled,
    )
    inserted = sum(1 for row in rows if row["inserted"])
    return (inserted, len(rows) - inserted)
