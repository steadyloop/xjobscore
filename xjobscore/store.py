"""The only code in the tree that touches the database.

Every SQL statement in the project lives in this file. Callers pass an open
asyncpg connection and get records back; nothing outside this module knows a
column name. That is what lets `schema.sql` change without a search across the
tree, and it is why `sources/` cannot reach the database even by accident --
there is nothing there to import.

This module maps records to rows and back. It decides nothing: what to poll
next is `work.py`, what a record means is `router.py`, and what is closed is
`reconcile.py`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import asyncpg

from xjobscore.records import Fact, Feed, Label, Posting

if TYPE_CHECKING:  # asyncpg is an implementation detail of this module
    from asyncpg import Connection

__all__ = [
    "all_feeds",
    "close_posting",
    "connect",
    "due_feeds",
    "finish_run",
    "get_feed",
    "latest_raw",
    "note_unknown_company",
    "open_postings_missed",
    "put_fact",
    "put_label",
    "resolve_company",
    "save_raw",
    "seen_external_ids",
    "set_feed_state",
    "set_next_poll",
    "start_run",
    "touch_posting",
    "upsert_feed",
    "upsert_posting",
]


async def _install_jsonb_codec(conn: Connection) -> None:
    # feed.config, feed.state, and the posting jsonb long-tail columns all move
    # as plain dicts/lists through this module -- the codec is the one place
    # that decision is made, so no query below carries a manual json.dumps or
    # a ::jsonb cast.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text"
    )


async def connect(dsn: str) -> Connection:
    """Open one connection. Pooling, if it is ever needed, belongs here too."""
    conn = await asyncpg.connect(dsn)
    await _install_jsonb_codec(conn)
    return conn


# --------------------------------------------------------------------------
# feeds -- the queue
# --------------------------------------------------------------------------


def _feed_from_row(row: asyncpg.Record) -> Feed:
    # complete/closes/credentialed/cadence_s have no columns yet -- they are
    # operator-set keys inside the config jsonb until the DDL catches up. This
    # function is the only place that reads them out of config; sources and
    # work.py just see a Feed that already looks like it has real columns.
    config = row["config"] or {}
    return Feed(
        id=row["id"],
        source=row["source"],
        key=row["key"],
        config=config,
        state=row["state"] or {},
        enabled=row["enabled"],
        complete=bool(config.get("complete", False)),
        closes=bool(config.get("closes", False)),
        credentialed=bool(config.get("credentialed", False)),
        cadence_s=int(config.get("cadence_s", 900)),
        next_poll_at=row["next_poll_at"],
    )


async def due_feeds(conn: Connection, now: Any, limit: int) -> list[Feed]:
    """Enabled feeds with next_poll_at <= now, never-run first.

    Served by `feed_due_idx` (next_poll_at NULLS FIRST WHERE enabled), so a
    feed that has never run sorts to the front instead of never being picked.

    The `feed` table has no capability columns yet, so `complete`, `closes`,
    `credentialed` and `cadence_s` are read out of the operator-owned `config`
    jsonb here and materialized onto the Feed. When the DDL lands, this
    function is the only thing that changes -- sources and work.py already see
    a Feed that looks identical either way.
    """
    rows = await conn.fetch(
        """
        SELECT id, source, key, config, state, enabled, next_poll_at
        FROM feed
        WHERE enabled AND (next_poll_at IS NULL OR next_poll_at <= $1)
        ORDER BY next_poll_at NULLS FIRST
        LIMIT $2
        """,
        now,
        limit,
    )
    return [_feed_from_row(row) for row in rows]


async def all_feeds(conn: Connection) -> list[Feed]:
    """Every feed, enabled or not -- the replay pass's input.

    Unlike `due_feeds`, not gated on `next_poll_at`: parsing bronze into
    silver runs on its own schedule (`run.py --parse`), not the poll's, and a
    disabled feed's already-stored raw_document rows are still fair game to
    replay.
    """
    rows = await conn.fetch("SELECT id, source, key, config, state, enabled, next_poll_at FROM feed")
    return [_feed_from_row(row) for row in rows]


async def get_feed(conn: Connection, source: str, key: str) -> Feed | None:
    """Look up by the natural key. Identity is (source, key), never a hash."""
    row = await conn.fetchrow(
        "SELECT id, source, key, config, state, enabled, next_poll_at "
        "FROM feed WHERE source = $1 AND key = $2",
        source,
        key,
    )
    return _feed_from_row(row) if row is not None else None


async def upsert_feed(
    conn: Connection,
    source: str,
    key: str,
    config: dict,
    *,
    enabled: bool = True,
) -> int:
    """Where a Target lands. Returns the feed id, new or existing.

    ON CONFLICT (source, key) DO NOTHING semantics for `config`: rediscovering
    a board must not overwrite what the operator has since configured on it.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO feed (source, key, config, enabled)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (source, key) DO NOTHING
        RETURNING id
        """,
        source,
        key,
        config,
        enabled,
    )
    if row is not None:
        return row["id"]
    existing = await conn.fetchrow(
        "SELECT id FROM feed WHERE source = $1 AND key = $2", source, key
    )
    return existing["id"]


async def set_next_poll(conn: Connection, feed_id: int, when: Any | None) -> None:
    """Write the due time. NULL means never again -- a spent one-off."""
    await conn.execute("UPDATE feed SET next_poll_at = $2 WHERE id = $1", feed_id, when)


async def set_feed_state(conn: Connection, feed_id: int, state: dict) -> None:
    """Replace `feed.state`. Never touches `config`; that is the operator's."""
    await conn.execute("UPDATE feed SET state = $2 WHERE id = $1", feed_id, state)


# --------------------------------------------------------------------------
# poll_run -- one row per attempt, the audit trail closure depends on
# --------------------------------------------------------------------------


async def start_run(conn: Connection, feed_id: int) -> int:
    """Insert a 'running' poll_run and return its id."""
    row = await conn.fetchrow(
        "INSERT INTO poll_run (feed_id, status) VALUES ($1, 'running') RETURNING id",
        feed_id,
    )
    return row["id"]


async def finish_run(
    conn: Connection,
    run_id: int,
    *,
    status: str,  # 'ok' | 'partial' | 'error'
    http_status: int | None = None,
    items_listed: int = 0,
    items_new: int = 0,
    items_changed: int = 0,
    items_gone: int = 0,
    error_class: str | None = None,
    error_detail: str | None = None,
) -> None:
    """Close out the run and stamp duration.

    `status` is load-bearing: closure may only ever be inferred from 'ok'. A
    fetch that died halfway through pagination is 'partial', because a board
    you failed to page through is not evidence that the rest is gone.
    """
    await conn.execute(
        """
        UPDATE poll_run SET
            finished_at   = now(),
            duration_ms   = EXTRACT(EPOCH FROM (now() - started_at)) * 1000,
            status        = $2,
            http_status   = $3,
            items_listed  = $4,
            items_new     = $5,
            items_changed = $6,
            items_gone    = $7,
            error_class   = $8,
            error_detail  = $9
        WHERE id = $1
        """,
        run_id,
        status,
        http_status,
        items_listed,
        items_new,
        items_changed,
        items_gone,
        error_class,
        error_detail,
    )


# --------------------------------------------------------------------------
# L1 bronze -- append-only, replayable, doubles as revision history
# --------------------------------------------------------------------------


async def save_raw(
    conn: Connection,
    feed_id: int,
    run_id: int,
    *,
    external_id: str,
    kind: str,  # 'json' | 'xml' | 'html' | 'mime' -- transport format, not role
    content_hash: str,
    payload_json: Any = None,
    payload_text: str | None = None,
    envelope: Any = None,
) -> bool:
    """Store one raw item. Returns False when the hash already exists.

    Unchanged polls insert nothing -- that is the UNIQUE
    (feed_id, external_id, kind, content_hash) doing the work, and the False
    return is what feeds items_changed. The hash must be over the canonicalized
    payload with volatile keys stripped; the stored payload stays verbatim.
    Miss one and every item looks changed on every poll.

    `envelope` is response-root facts no single item carries (Workable's
    company name); it is not part of content_hash, so it riding along on every
    row of a poll does not itself make an unchanged item look changed.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO raw_document
            (feed_id, poll_run_id, external_id, kind, content_hash, payload_json, payload_text, envelope)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (feed_id, external_id, kind, content_hash) DO NOTHING
        RETURNING id
        """,
        feed_id,
        run_id,
        external_id,
        kind,
        content_hash,
        payload_json,
        payload_text,
        envelope,
    )
    return row is not None


async def seen_external_ids(conn: Connection, feed_id: int) -> set[str]:
    """Every external_id raw_document has ever stored for this feed.

    Router's new/changed split needs this: save_raw's bool return only says
    whether *this hash* already existed, not whether the external_id itself
    is new to the feed. Served by raw_document_lookup, which otherwise has no
    reader at all.
    """
    rows = await conn.fetch(
        "SELECT DISTINCT external_id FROM raw_document WHERE feed_id = $1", feed_id
    )
    return {row["external_id"] for row in rows}


async def latest_raw(conn: Connection, feed_id: int) -> list[asyncpg.Record]:
    """The latest raw_document revision per external_id for one feed.

    This is what a replay pass parses: not the full history `raw_document`
    doubles as, just where each external_id currently stands. `DISTINCT ON
    (external_id) ... ORDER BY external_id, fetched_at DESC` matches
    `raw_document_lookup`'s column order exactly, so this is an index-only
    walk, not a sort.
    """
    return await conn.fetch(
        """
        SELECT DISTINCT ON (external_id)
            id, external_id, kind, payload_json, payload_text, envelope, poll_run_id, fetched_at
        FROM raw_document
        WHERE feed_id = $1
        ORDER BY external_id, fetched_at DESC
        """,
        feed_id,
    )


# --------------------------------------------------------------------------
# companies
# --------------------------------------------------------------------------


async def resolve_company(conn: Connection, raw: str) -> int | None:
    """One lookup against company_alias on lower(btrim(raw)).

    A hit proves the company is known. A miss proves nothing, which is why the
    caller follows it with `note_unknown_company` rather than creating one.
    """
    row = await conn.fetchrow(
        "SELECT company_id FROM company_alias WHERE alias_norm = lower(btrim($1))", raw
    )
    return row["company_id"] if row is not None else None


async def note_unknown_company(conn: Connection, raw: str, source: str) -> None:
    """Park an unresolved name in company_new for later resolution."""
    await conn.execute(
        """
        INSERT INTO company_new (name, source)
        VALUES (lower(btrim($1)), $2)
        ON CONFLICT (name) DO NOTHING
        """,
        raw,
        source,
    )


# --------------------------------------------------------------------------
# L2 canonical
# --------------------------------------------------------------------------

_POSTING_COLUMNS = (
    "title",
    "title_normalized",
    "url_posting",
    "apply_url",
    "location_raw",
    "description_html",
    "requisition_id",
    "employment_type",
    "workplace_type_raw",
    "published_at",
    "published_at_precision",
    "source_updated_at",
    "deadline_at",
    "source_active",
    "company_raw",
    "content_hash",
    "locations_extra",
    "content_sections",
    "source_extra",
)


def _posting_values(posting: Posting, *, title_normalized: str, content_hash: str) -> tuple:
    return (
        posting.title,
        title_normalized,
        posting.url_posting,
        posting.apply_url,
        posting.location_raw,
        posting.description_html,
        posting.requisition_id,
        posting.employment_type,
        posting.workplace_type_raw,
        posting.published_at,
        posting.published_at_precision,
        posting.source_updated_at,
        posting.deadline_at,
        posting.source_active,
        posting.company_raw,
        content_hash,
        posting.locations_extra,
        posting.content_sections,
        posting.source_extra,
    )


async def upsert_posting(
    conn: Connection,
    feed_id: int,
    company_id: int,
    posting: Posting,
    *,
    run_id: int,
    title_normalized: str,
    content_hash: str,
) -> tuple[int, bool]:
    """Insert or update on (feed_id, external_id). Returns (id, changed).

    `title_normalized` and `content_hash` are arguments, not computed here.
    Normalization must stay byte-identical to seeds/gen_umbrellas.py
    normalize_title() or role matching silently misses, so exactly one
    engine-owned function may produce it -- and store.py, which also serves
    reconcile and the viewer, is not that function's home.

    An unchanged posting must touch only last_seen_at / last_seen_poll_run_id;
    the table is fillfactor 80 so those stay HOT updates and never touch an
    index.
    """
    existing = await conn.fetchrow(
        "SELECT id, content_hash FROM posting WHERE feed_id = $1 AND external_id = $2",
        feed_id,
        posting.external_id,
    )

    if existing is None:
        columns = ("feed_id", "company_id", "external_id", *_POSTING_COLUMNS, "last_seen_poll_run_id")
        values = (
            feed_id,
            company_id,
            posting.external_id,
            *_posting_values(posting, title_normalized=title_normalized, content_hash=content_hash),
            run_id,
        )
        placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        row = await conn.fetchrow(
            f"INSERT INTO posting ({', '.join(columns)}) VALUES ({placeholders}) RETURNING id",
            *values,
        )
        return row["id"], True

    if existing["content_hash"] == content_hash:
        await touch_posting(conn, existing["id"], run_id, None)
        return existing["id"], False

    set_clause = ", ".join(f"{col} = ${i}" for i, col in enumerate(_POSTING_COLUMNS, start=2))
    values = _posting_values(posting, title_normalized=title_normalized, content_hash=content_hash)
    await conn.execute(
        f"""
        UPDATE posting SET
            {set_clause},
            last_seen_at = now(),
            last_seen_poll_run_id = ${len(values) + 2}
        WHERE id = $1
        """,
        existing["id"],
        *values,
        run_id,
    )
    return existing["id"], True


async def touch_posting(conn: Connection, posting_id: int, run_id: int, now: Any) -> None:
    """Record 'still there' without rewriting the row's content columns."""
    if now is None:
        await conn.execute(
            "UPDATE posting SET last_seen_at = now(), last_seen_poll_run_id = $2 WHERE id = $1",
            posting_id,
            run_id,
        )
    else:
        await conn.execute(
            "UPDATE posting SET last_seen_at = $2, last_seen_poll_run_id = $3 WHERE id = $1",
            posting_id,
            now,
            run_id,
        )


async def close_posting(
    conn: Connection,
    posting_id: int,
    *,
    reason: str,  # 'source_flag' | 'absent_from_poll' | 'manual'
    at: Any,
) -> None:
    """Set status='closed' plus closed_at. The CHECK ties the two together."""
    await conn.execute(
        "UPDATE posting SET status = 'closed', closed_at = $2, closed_reason = $3 WHERE id = $1",
        posting_id,
        at,
        reason,
    )


async def open_postings_missed(conn: Connection, feed_id: int, run_id: int) -> list[int]:
    """Open postings of this feed that the given run did not see.

    The closure sweep's input, served by `posting_sweep_idx`. Returning ids and
    not closing them is the point: whether absence *means* closed is
    reconcile's decision, gated on feed.complete, feed.closes and a run that
    finished 'ok'.
    """
    rows = await conn.fetch(
        """
        SELECT id FROM posting
        WHERE feed_id = $1 AND status = 'open' AND last_seen_poll_run_id IS DISTINCT FROM $2
        """,
        feed_id,
        run_id,
    )
    return [row["id"] for row in rows]


# --------------------------------------------------------------------------
# labels and facts -- no destination tables yet; see the DDL in the plan
# --------------------------------------------------------------------------


async def put_label(conn: Connection, posting_id: int, label: Label) -> None:
    """Attach a Label. Higher provenance wins per (posting, name).

    Unimplemented until the `label` table exists. Sources may already emit
    Labels; the router drops them until then.
    """
    raise NotImplementedError


async def put_fact(conn: Connection, company_id: int, fact: Fact) -> None:
    """Attach a company Fact. Unimplemented until `company_fact` exists."""
    raise NotImplementedError
