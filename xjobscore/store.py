"""PostgreSQL mechanics for the schema.sql-backed core pipeline."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import re
from typing import TYPE_CHECKING, Any
import unicodedata

import asyncpg

from xjobscore.records import Feed

if TYPE_CHECKING:
    from asyncpg import Connection, Pool

__all__ = [
    "align_company_identity",
    "all_feeds",
    "append_poll_observation",
    "claim_due_feeds",
    "claim_feed",
    "clear_registry_company_domains",
    "connect",
    "dedup_candidates",
    "due_feeds",
    "fetchable_feeds",
    "finish_schedule_failure",
    "finish_schedule_success",
    "latest_raw",
    "prune_registry_aliases",
    "replace_dup_groups",
    "replay_feeds",
    "reconcile_registry_inventory",
    "resolve_company",
    "save_raw",
    "set_feed_enabled",
    "set_schedule_interval",
    "set_schedule_paused",
    "source_instance",
    "touch_source_postings",
    "upsert_job_posting",
    "upsert_registry_alias",
    "upsert_registry_company",
    "upsert_registry_feed",
    "upsert_registry_token",
]


async def _install_jsonb_codec(conn: Connection) -> None:
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text"
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
        max_size=20,
        init=_install_jsonb_codec,
        **({"server_settings": {"search_path": search_path}} if search_path else {}),
    )


def source_instance(feed: Feed) -> str:
    """Return the collision-safe posting namespace documented by schema.sql."""
    return f"{feed.source}:{feed.key}"


def _feed_from_row(row: asyncpg.Record) -> Feed:
    return Feed(
        id=row["id"],
        source=row["source"],
        key=row["key"],
        enabled=row["enabled"],
        company_id=row.get("company_id"),
        company_name=row.get("company_name"),
        etag=row.get("etag"),
        res_hash=row.get("res_hash"),
        interval_s=int(row.get("interval_s") or 900),
        strikes=int(row.get("strikes") or 0),
        due_at=row.get("due_at"),
        hold_until=row.get("hold_until"),
    )


_FEED_SELECT = """
    SELECT
        f.id, f.source, f.key, f.enabled,
        f.interval_s, f.etag, f.res_hash, f.strikes, f.due_at, f.hold_until,
        t.company_id, c.name AS company_name
    FROM feed f
    LEFT JOIN token t ON t.ats = f.source AND t.token = f.key
    LEFT JOIN company c ON c.id = t.company_id
"""


async def claim_due_feeds(
    conn: Connection,
    now: datetime,
    limit: int,
    *,
    lease_seconds: int = 3600,
) -> list[Feed]:
    """Lease due feed rows atomically for horizontally safe workers."""
    lease_until = now + timedelta(seconds=max(60, lease_seconds))
    rows = await conn.fetch(
        """
        WITH picked AS (
            SELECT f.id
            FROM feed f
            WHERE f.enabled
              AND f.paused_at IS NULL
              AND f.due_at <= $1
              AND (f.hold_until IS NULL OR f.hold_until <= $1)
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
            f.id, f.source, f.key, f.enabled,
            f.interval_s, f.etag, f.res_hash, f.strikes, f.due_at,
            -- The lease must come from the CTE. A data-modifying
            -- CTE's writes are invisible to the rest of the same query, so
            -- `f.hold_until` here is the *pre-claim* value -- NULL on a feed
            -- nobody has polled. finish_schedule_*() then guards on
            -- `hold_until = $7`, which never matches NULL, so every completed
            -- poll reported a lost lease and skipped its poll_observation row.
            x.hold_until,
            t.company_id, c.name AS company_name
        FROM claimed x
        JOIN feed f ON f.id = x.id
        LEFT JOIN token t ON t.ats = f.source AND t.token = f.key
        LEFT JOIN company c ON c.id = t.company_id
        ORDER BY f.due_at, f.id
        """,
        now,
        max(1, limit),
        lease_until,
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
              AND f.enabled
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
            f.id, f.source, f.key, f.enabled,
            f.interval_s, f.etag, f.res_hash, f.strikes, f.due_at,
            -- The lease must come from the CTE: a data-modifying
            -- CTE's writes are invisible to the rest of the same query, so
            -- `f.hold_until` would be the pre-claim value and finish_schedule_*'s
            -- `hold_until = $N` guard would never match. Same trap as
            -- claim_due_feeds above.
            x.hold_until,
            t.company_id, c.name AS company_name
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


async def due_feeds(conn: Connection, now: datetime, limit: int) -> list[Feed]:
    """Read the claimable queue without taking leases, for dry runs."""
    rows = await conn.fetch(
        _FEED_SELECT
        + """
        WHERE f.enabled
          AND f.paused_at IS NULL
          AND f.due_at <= $1
          AND (f.hold_until IS NULL OR f.hold_until <= $1)
        ORDER BY f.due_at, f.id
        LIMIT $2
        """,
        now,
        max(1, limit),
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
        WHERE f.enabled
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


async def finish_schedule_success(
    conn: Connection,
    feed: Feed,
    *,
    now: datetime,
    due_at: datetime,
    etag: str | None,
    res_hash: str | None,
    changed: bool,
) -> bool:
    """Complete a claimed poll without overwriting a reclaimed worker's lease."""
    result = await conn.execute(
        """
        UPDATE feed SET
            etag = $3,
            res_hash = COALESCE($4, res_hash),
            strikes = 0,
            due_at = $5,
            hold_until = NULL,
            last_poll_at = $2,
            last_change_at = CASE WHEN $6 THEN $2 ELSE last_change_at END
        WHERE id = $1 AND hold_until = $7
        """,
        feed.id,
        now,
        etag,
        res_hash,
        due_at,
        changed,
        feed.hold_until,
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


async def append_poll_observation(
    conn: Connection,
    *,
    feed_id: int,
    polled_at: datetime,
    status: str,
    new_postings: int = 0,
) -> None:
    """Record what one completed poll did -- the Scheduler page's only input.

    Append-only and never updated: the row is the fact that a poll happened, so
    a later poll adds a row rather than revising this one. ``status`` mirrors
    ``work.Outcome``; ``new_postings`` counts first sightings only, never
    re-seen postings whose content merely changed.
    """
    await conn.execute(
        """
        INSERT INTO poll_observation (feed_id, polled_at, status, new_postings)
        VALUES ($1, $2, $3, $4)
        """,
        feed_id,
        polled_at,
        status,
        new_postings,
    )


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
    return await conn.fetchval(
        "UPDATE feed SET enabled = $3 WHERE source = $1 AND key = $2 RETURNING id",
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
    Ashby has no rows to flip until the first import. Unlike
    :func:`upsert_registry_feed` the conflict branch writes ``enabled`` through
    -- this is an explicit operator flip, not a registry sync, so it must
    resurrect a feed that was previously switched off.

    Disabling never inserts: a feed absent from PostgreSQL is already off.
    """
    if not identities:
        return 0
    sources = [source for source, _ in identities]
    keys = [key for _, key in identities]
    if not enabled:
        result = await conn.execute(
            """
            UPDATE feed SET enabled = false
            WHERE (source, key) IN (SELECT * FROM unnest($1::text[], $2::text[]))
            """,
            sources,
            keys,
        )
        return int(result.rsplit(" ", 1)[-1])
    rows = await conn.fetch(
        """
        INSERT INTO feed(source, key, enabled)
        SELECT source, key, true
        FROM unnest($1::text[], $2::text[]) AS incoming(source, key)
        ON CONFLICT (source, key) DO UPDATE SET enabled = true
        RETURNING id
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
    """Hold or release one feed's polling without touching ``feed.enabled``.

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


async def resolve_company(conn: Connection, raw: str) -> int | None:
    exact = unicodedata.normalize("NFKC", raw).strip().casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", exact).strip("-")
    without_suffix = re.sub(
        r"-(?:inc|incorporated|llc|ltd|limited|corp|corporation|co|company)$",
        "",
        slug,
    )
    candidates = list(
        dict.fromkeys(value for value in (exact, slug, without_suffix) if value)
    )
    row = await conn.fetchrow(
        """
        SELECT company_id
        FROM alias
        WHERE alias_norm = ANY($1::text[])
        ORDER BY array_position($1::text[], alias_norm)
        LIMIT 1
        """,
        candidates,
    )
    return row["company_id"] if row is not None else None


async def touch_source_postings(conn: Connection, source: str, now: datetime) -> int:
    result = await conn.execute(
        "UPDATE job_posting SET last_seen_at = $2 WHERE source = $1", source, now
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
) -> tuple[int, bool, bool]:
    """Upsert the exact canonical projection declared by ``job_posting``.

    Returns ``(id, changed, created)``. The two flags differ only when a posting
    we already had comes back with new content: ``changed`` is true, ``created``
    is false. That gap is the whole distinction between churn and arrivals, and
    ``poll_observation.new_postings`` is defined on ``created`` alone.
    """
    existing = await conn.fetchrow(
        "SELECT id, content_hash FROM job_posting WHERE source = $1 AND source_key = $2",
        source,
        source_key,
    )
    changed = existing is None or existing["content_hash"] != content_hash
    row = await conn.fetchrow(
        """
        INSERT INTO job_posting(
            source, source_key, company_id, title, description, location_text,
            source_url, apply_url, posted_at, attributes, status,
            last_seen_at, content_hash, raw_id
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        ON CONFLICT (source, source_key) DO UPDATE SET
            company_id = EXCLUDED.company_id,
            title = EXCLUDED.title,
            description = EXCLUDED.description,
            location_text = EXCLUDED.location_text,
            source_url = EXCLUDED.source_url,
            apply_url = EXCLUDED.apply_url,
            posted_at = EXCLUDED.posted_at,
            -- Gold enrichment survives an unchanged silver replay. A changed
            -- canonical projection invalidates derived results and stamps so
            -- the next gold pass recomputes them from current silver fields.
            attributes = CASE
                WHEN job_posting.content_hash IS DISTINCT FROM EXCLUDED.content_hash
                THEN (job_posting.attributes
                      - 'opportunity_type' - 'opportunity_type_meta'
                      - 'skills' - 'skills_meta'
                      - 'degree_fields' - 'degree_fields_meta')
                     || EXCLUDED.attributes
                ELSE job_posting.attributes || EXCLUDED.attributes
            END,
            status = EXCLUDED.status,
            last_seen_at = EXCLUDED.last_seen_at,
            content_hash = EXCLUDED.content_hash,
            raw_id = EXCLUDED.raw_id
        RETURNING id
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
        values["attributes"],
        values["status"],
        seen_at,
        content_hash,
        raw_id,
    )
    return row["id"], changed, existing is None


# Gold projection -----------------------------------------------------------


async def dedup_candidates(conn: Connection) -> list[asyncpg.Record]:
    """Silver columns the gold dedup step keys and gathers evidence on."""
    return await conn.fetch(
        """
        SELECT id, company_id, source, title, location_text, description,
               source_url, apply_url
        FROM job_posting
        ORDER BY id
        """
    )


async def replace_dup_groups(
    conn: Connection, *, groups: list[dict[str, Any]], members: list[dict[str, Any]]
) -> int:
    """Rebuild every derived duplicate group and return how many were written.

    Destructive by design: gold groups are a pure function of ``job_posting``.
    Human judgements must never live here -- a rebuild erases them.

    ``job_posting.dup_group_id`` carries no ``ON DELETE`` action, so membership
    is cleared before the groups it references are removed.
    """
    await conn.execute(
        "UPDATE job_posting SET dup_group_id = NULL WHERE dup_group_id IS NOT NULL"
    )
    await conn.execute("DELETE FROM job_posting_dup_group")
    if not groups:
        return 0
    await conn.execute(
        """
        INSERT INTO job_posting_dup_group(id, primary_posting_id, state, evidence)
        SELECT g.id, NULL, g.state, g.evidence
        FROM jsonb_to_recordset($1::jsonb) AS g(id uuid, state text, evidence jsonb)
        """,
        groups,
    )
    await conn.execute(
        """
        UPDATE job_posting p SET dup_group_id = m.group_id
        FROM jsonb_to_recordset($1::jsonb) AS m(posting_id bigint, group_id uuid)
        WHERE p.id = m.posting_id
        """,
        members,
    )
    return len(groups)


# Registry projection -------------------------------------------------------


async def clear_registry_company_domains(conn: Connection, company_ids: list[int]) -> None:
    await conn.execute(
        "UPDATE company SET domain = NULL, domain_verified_at = NULL WHERE id = ANY($1::bigint[])",
        company_ids,
    )


async def upsert_registry_company(
    conn: Connection,
    *,
    company_id: int,
    name: str,
    domain: str | None,
    domain_verified_at: datetime | None,
    description: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO company(id, name, domain, domain_verified_at, description)
        OVERRIDING SYSTEM VALUE
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            domain = EXCLUDED.domain,
            domain_verified_at = EXCLUDED.domain_verified_at,
            description = EXCLUDED.description
        """,
        company_id,
        name,
        domain,
        domain_verified_at,
        description,
    )


async def upsert_registry_alias(
    conn: Connection, *, alias_norm: str, company_id: int
) -> None:
    await conn.execute(
        """
        INSERT INTO alias(alias_norm, company_id, alias)
        VALUES ($1, $2, $1)
        ON CONFLICT (alias_norm) DO UPDATE SET
            company_id = EXCLUDED.company_id,
            alias = EXCLUDED.alias
        """,
        alias_norm,
        company_id,
    )


async def prune_registry_aliases(conn: Connection, aliases: list[str]) -> None:
    await conn.execute("DELETE FROM alias WHERE alias_norm <> ALL($1::text[])", aliases)


async def align_company_identity(conn: Connection) -> None:
    await conn.execute(
        """
        WITH identity_sequence AS (
            SELECT pg_get_serial_sequence('company', 'id')::regclass AS sequence_id
        )
        SELECT setval(
            sequence_id,
            GREATEST((SELECT MAX(id) FROM company), pg_sequence_last_value(sequence_id)),
            true
        )
        FROM identity_sequence
        """
    )


async def upsert_registry_token(
    conn: Connection, *, source: str, key: str, company_id: int, status: str
) -> None:
    await conn.execute(
        """
        INSERT INTO token(ats, token, company_id, status)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (ats, token) DO UPDATE SET
            company_id = EXCLUDED.company_id,
            status = EXCLUDED.status
        """,
        source,
        key,
        company_id,
        status,
    )


async def upsert_registry_feed(
    conn: Connection, *, source: str, key: str, enabled: bool
) -> int:
    """Insert a registry feed, seeding ``enabled`` only on first sight.

    The registry no longer carries an ``enabled`` flag, so ``enabled`` here is
    just the birth default for a feed PostgreSQL has never seen. Afterwards the
    column is operator state owned by the inspector, and re-syncing the registry
    must not resurrect a feed the operator switched off. The conflict branch is
    therefore a no-op write on ``source``, which leaves ``enabled`` untouched
    while still returning the existing row's id (``DO NOTHING`` would return no
    row at all).
    """
    feed_id = await conn.fetchval(
        """
        INSERT INTO feed(source, key, enabled)
        VALUES ($1, $2, $3)
        ON CONFLICT (source, key) DO UPDATE SET source = EXCLUDED.source
        RETURNING id
        """,
        source,
        key,
        enabled,
    )
    return int(feed_id)


async def reconcile_registry_inventory(
    conn: Connection, identities: list[dict[str, str]]
) -> None:
    """Disable feeds and tokens no longer present without deleting history."""
    await conn.execute(
        """
        UPDATE feed f SET enabled = false
        WHERE NOT EXISTS (
            SELECT 1
            FROM jsonb_to_recordset($1::jsonb) AS incoming(source text, key text)
            WHERE incoming.source = f.source AND incoming.key = f.key
        )
        """,
        identities,
    )
    await conn.execute(
        """
        UPDATE token t SET status = 'inactive'
        WHERE NOT EXISTS (
            SELECT 1
            FROM jsonb_to_recordset($1::jsonb) AS incoming(source text, key text)
            WHERE incoming.source = t.ats AND incoming.key = t.token
        )
        """,
        identities,
    )
