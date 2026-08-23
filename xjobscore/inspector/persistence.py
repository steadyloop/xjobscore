"""PostgreSQL persistence for inspector-only fetch results and scheduling state.

Everything here lives in a dedicated schema (``inspector`` by default), never
``public``.

This store is deliberately independent of ``schema.sql``. It stages the
latest usable payload per feed plus a short latency history; it is not a
second posting store and holds nothing the pipeline depends on.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from xjobscore.feed_identity import feed_ref as compute_feed_ref
from xjobscore.inspector.cache import RunSnapshot

# The staged payload lives in schema.sql's tables, keyed (source, feed_key), so
# a staged feed can be promoted into the pipeline without copying schemas. Only
# this store's own bookkeeping stays in ``self.schema``.
_STAGING = "public"

_LATENCY_COLUMNS = (
    "result_id", "feed_ref", "feed_id", "source", "feed_key", "run_trigger",
    "started_at", "finished_at", "elapsed_ms", "status", "http_status",
    "item_count", "raw_size_bytes", "truncated", "error_class",
    "error_detail", "retry_count",
)

# Aliased where schema.sql renamed a column, so the RunSnapshot built in _load
# keeps reading the field names it always did.
_FETCH_COLUMNS = (
    "result_id", "feed_id", "source", "feed_key", "run_trigger",
    "started_at", "fetched_at AS finished_at", "elapsed_ms", "status",
    "http_status", "item_count", "raw_size_bytes", "truncated",
    "error_class", "error_detail", "size_bytes", "envelope", "network",
    "etag AS response_etag",
)

_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS {schema};

-- The staged payload itself lives in schema.sql's fetched_feed and
-- fetched_feed_posting, keyed (source, feed_key) so a staged feed can be
-- promoted into the pipeline without copying between schemas. Tests point
-- ``staging`` at their throwaway schema; production leaves it at ``public``.
-- Only this store's own bookkeeping is created below.

-- Lightweight attempt history. No payload, envelope, request waterfall, or
-- parse output is retained here.
CREATE TABLE IF NOT EXISTS {schema}.fetch_latency (
    result_id      TEXT PRIMARY KEY,
    feed_ref       TEXT NOT NULL,
    feed_id        BIGINT NOT NULL,
    source         TEXT NOT NULL,
    feed_key       TEXT NOT NULL,
    run_trigger    TEXT NOT NULL DEFAULT 'manual',
    started_at     TIMESTAMPTZ NOT NULL,
    finished_at    TIMESTAMPTZ NOT NULL,
    elapsed_ms     DOUBLE PRECISION NOT NULL,
    status         TEXT NOT NULL,
    http_status    INTEGER,
    item_count     INTEGER NOT NULL,
    raw_size_bytes BIGINT NOT NULL,
    truncated      BOOLEAN NOT NULL,
    error_class    TEXT,
    error_detail   TEXT,
    retry_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS fetch_latency_feed_idx
    ON {schema}.fetch_latency (feed_ref, finished_at DESC);
CREATE INDEX IF NOT EXISTS fetch_latency_source_idx
    ON {schema}.fetch_latency (source, finished_at DESC);

CREATE TABLE IF NOT EXISTS {schema}.meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Hand labels over public.job_posting rows, keyed by the posting's natural
-- key: gold tables are rebuilt destructively (store.py: "Human judgements
-- must never live here"), and this store's DSN can in principle diverge from
-- public's, so no FK. No CHECK on label either -- IF NOT EXISTS never alters
-- an existing table, so a CHECK would freeze the vocabulary at first boot;
-- the API layer enforces it instead. The snapshots keep a labeled eval set
-- meaningful after the posting row itself is deleted or reinserted.
CREATE TABLE IF NOT EXISTS {schema}.posting_label (
    source           TEXT NOT NULL,
    source_key       TEXT NOT NULL,
    label            TEXT NOT NULL,
    note             TEXT,
    title_snapshot   TEXT,
    company_snapshot TEXT,
    labeled_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, source_key)
);

-- Human review of derived duplicate candidates. The gold group table is
-- rebuilt destructively, so reviews live here without a foreign key. A group
-- id is stable for one strategy key while its evidence can change; retaining
-- each fingerprint preserves history and makes changed groups require review.
CREATE TABLE IF NOT EXISTS {schema}.duplicate_group_review (
    group_id     UUID NOT NULL,
    fingerprint  TEXT NOT NULL,
    decision     TEXT NOT NULL,
    note         TEXT,
    reviewed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, fingerprint)
);
"""


def _json(value: Any) -> str:
    """Encode a value for a jsonb parameter.

    Always serializes, including for ``str``: asyncpg sends a ``str`` straight
    through to jsonb, so an html or xml payload passed as-is would reach
    PostgreSQL as a malformed JSON document. ``default=str`` keeps a stray
    datetime in a harvested payload from failing the whole run.
    """
    return json.dumps(value, ensure_ascii=False, default=str)


def _item_payload(item: dict[str, Any]) -> str:
    """The jsonb text for an item's payload.

    Only ``json`` items carry ``payload_json``, serialized once upstream so the
    same bytes back both the size accounting and this column. Every other kind
    holds a raw document string that has to be encoded as a JSON scalar here.
    """
    payload_json = item.get("payload_json")
    if payload_json is not None:
        return payload_json
    return _json(item["payload"])


def _loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _iso(value: datetime | None) -> str | None:
    """Render a timestamp for the JSON API, which reads ISO strings throughout."""
    return value.isoformat() if value is not None else None


class ResultStore:
    """A PostgreSQL-backed latest-payload staging area plus latency history.

    The latest usable payload and network snapshot replace in place per feed.
    The last ``history_limit`` attempts retain only timing/status scalars.
    Parse previews are derived on read and never persisted.
    """

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "inspector",
        staging: str = _STAGING,
        history_limit: int = 50,
    ) -> None:
        for name in (schema, staging):
            if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
                raise ValueError("schema must be a lowercase unquoted identifier")
        self.dsn = dsn
        self.schema = schema
        # Where fetched_feed/fetched_feed_posting live. Tests point this at their
        # throwaway schema so they never write the pipeline's real tables.
        self.staging = staging
        self.history_limit = history_limit
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    # Lifecycle ------------------------------------------------------------

    async def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(
                        self.dsn, min_size=1, max_size=10
                    )
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def create_schema(self) -> None:
        """Create the inspector-owned schema and tables."""
        pool = await self.pool()
        async with pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL.format(schema=self.schema))

    async def initialize(self) -> None:
        """Create the inspector schema."""
        await self.create_schema()

    # Run persistence ------------------------------------------------------

    async def save(self, snapshot: RunSnapshot) -> None:
        ref = compute_feed_ref(snapshot.source, snapshot.key)
        pool = await self.pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.fetch_latency (
                    result_id, feed_ref, feed_id, source, feed_key, run_trigger,
                    started_at, finished_at, elapsed_ms, status, http_status,
                    item_count, raw_size_bytes, truncated,
                    error_class, error_detail, retry_count
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                          $13, $14, $15, $16, $17)
                ON CONFLICT (result_id) DO NOTHING
                """,
                snapshot.result_id,
                ref,
                snapshot.feed_id,
                snapshot.source,
                snapshot.key,
                snapshot.trigger,
                snapshot.started_at,
                snapshot.finished_at,
                snapshot.elapsed_ms,
                snapshot.status,
                snapshot.http_status,
                snapshot.item_count,
                snapshot.raw_size_bytes,
                bool(snapshot.truncated),
                snapshot.error_class,
                snapshot.error_detail,
                int(snapshot.network.get("summary", {}).get("retry_count") or 0),
            )
            # A failure is recorded above but never erases the last usable
            # staging payload. Successful empty boards are valid replacements.
            has_staged_payload = await conn.fetchval(
                f"SELECT true FROM {self.staging}.fetched_feed"
                " WHERE source = $1 AND feed_key = $2",
                snapshot.source,
                snapshot.key,
            )
            if snapshot.status in {"ok", "partial"} or (
                snapshot.status == "not_modified" and has_staged_payload
            ):
                await conn.execute(
                    f"""
                    INSERT INTO {self.staging}.fetched_feed (
                        source, feed_key, status, http_status, envelope, network,
                        fetched_at, etag, res_hash, result_id, feed_id,
                        run_trigger, started_at, elapsed_ms, item_count,
                        raw_size_bytes, size_bytes, truncated,
                        error_class, error_detail
                    ) VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9,
                              $10, $11, $12, $13, $14, $15, $16, $17, $18,
                              $19, $20)
                    ON CONFLICT (source, feed_key) DO UPDATE SET
                        status = excluded.status,
                        http_status = excluded.http_status,
                        envelope = excluded.envelope,
                        network = excluded.network,
                        fetched_at = excluded.fetched_at,
                        etag = excluded.etag,
                        res_hash = excluded.res_hash,
                        result_id = excluded.result_id,
                        feed_id = excluded.feed_id,
                        run_trigger = excluded.run_trigger,
                        started_at = excluded.started_at,
                        elapsed_ms = excluded.elapsed_ms,
                        item_count = excluded.item_count,
                        raw_size_bytes = excluded.raw_size_bytes,
                        size_bytes = excluded.size_bytes,
                        truncated = excluded.truncated,
                        error_class = excluded.error_class,
                        error_detail = excluded.error_detail
                    """,
                    snapshot.source,
                    snapshot.key,
                    snapshot.status,
                    snapshot.http_status,
                    _json(snapshot.envelope),
                    _json(snapshot.network),
                    snapshot.finished_at,
                    snapshot.response_etag,
                    None,  # res_hash: the engine owns listing hashes, not staging
                    snapshot.result_id,
                    snapshot.feed_id,
                    snapshot.trigger,
                    snapshot.started_at,
                    snapshot.elapsed_ms,
                    snapshot.item_count,
                    snapshot.raw_size_bytes,
                    snapshot.size_bytes,
                    bool(snapshot.truncated),
                    snapshot.error_class,
                    snapshot.error_detail,
                )
                await conn.execute(
                    f"DELETE FROM {self.staging}.fetched_feed_posting"
                    " WHERE source = $1 AND feed_key = $2",
                    snapshot.source,
                    snapshot.key,
                )
                await conn.executemany(
                    f"""
                    INSERT INTO {self.staging}.fetched_feed_posting (
                        source, feed_key, item_index, external_id, kind,
                        payload, raw_document, item_version
                    ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)
                    """,
                    [
                        (
                            snapshot.source,
                            snapshot.key,
                            index,
                            item["external_id"],
                            item["kind"],
                            _item_payload(item),
                            _json(item["raw_document"]),
                            item.get("item_version"),
                        )
                        for index, item in enumerate(snapshot.items)
                    ],
                )
            await conn.execute(
                f"""
                DELETE FROM {self.schema}.fetch_latency
                WHERE feed_ref = $1 AND result_id NOT IN (
                    SELECT result_id FROM {self.schema}.fetch_latency
                    WHERE feed_ref = $1
                    ORDER BY finished_at DESC, result_id DESC
                    LIMIT $2
                )
                """,
                ref,
                self.history_limit,
            )

    # Run reads ------------------------------------------------------------

    async def load(self) -> list[RunSnapshot]:
        return await self._load()

    async def load_feed(self, feed_id: int) -> RunSnapshot | None:
        snapshots = await self._load("WHERE feed_id = $1", feed_id)
        return snapshots[0] if snapshots else None

    async def load_ref(self, source: str, key: str) -> RunSnapshot | None:
        """The staged run for one feed. Takes (source, key) rather than a
        feed_ref: the staging table is keyed on them, and the ref is a hash that
        cannot be inverted in SQL."""
        snapshots = await self._load(
            "WHERE source = $1 AND feed_key = $2", source, key
        )
        return snapshots[0] if snapshots else None

    async def _load(self, condition: str = "", *params: Any) -> list[RunSnapshot]:
        pool = await self.pool()
        async with pool.acquire() as conn:
            runs = await conn.fetch(
                f"""
                SELECT {", ".join(_FETCH_COLUMNS)}
                FROM {self.staging}.fetched_feed {condition}
                ORDER BY source, feed_key
                """,
                *params,
            )
            snapshots: list[RunSnapshot] = []
            for row in runs:
                item_rows = await conn.fetch(
                    f"""
                    SELECT external_id, kind, payload, raw_document
                    FROM {self.staging}.fetched_feed_posting
                    WHERE source = $1 AND feed_key = $2 ORDER BY item_index
                    """,
                    row["source"],
                    row["feed_key"],
                )
                snapshots.append(
                    RunSnapshot(
                        result_id=row["result_id"],
                        feed_id=row["feed_id"],
                        source=row["source"],
                        key=row["feed_key"],
                        started_at=row["started_at"],
                        finished_at=row["finished_at"],
                        elapsed_ms=row["elapsed_ms"],
                        status=row["status"],
                        http_status=row["http_status"],
                        item_count=row["item_count"],
                        raw_size_bytes=row["raw_size_bytes"],
                        truncated=row["truncated"],
                        error_class=row["error_class"],
                        error_detail=row["error_detail"],
                        items=tuple(
                            {
                                "external_id": item["external_id"],
                                "kind": item["kind"],
                                "payload": _loads(item["payload"]),
                                "raw_document": _loads(item["raw_document"]),
                            }
                            for item in item_rows
                        ),
                        envelope=_loads(row["envelope"]) or {},
                        size_bytes=row["size_bytes"],
                        network=_loads(row["network"]) or {},
                        trigger=row["run_trigger"],
                        response_etag=row["response_etag"],
                    )
                )
            return snapshots

    async def latest_validator(self, source: str, key: str) -> tuple[str | None, int]:
        """The staged payload's ETag and item count, without loading the payload.

        Answering this from ``fetched_feed`` alone is what lets a caller decide
        whether a conditional request is safe before paying for one.
        """
        pool = await self.pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT etag, item_count FROM {self.staging}.fetched_feed
                WHERE source = $1 AND feed_key = $2
                """,
                source,
                key,
            )
        return (row["etag"], row["item_count"]) if row else (None, 0)

    async def load_latest_items_ref(
        self, source: str, key: str
    ) -> tuple[dict[str, Any], ...]:
        """Every reusable raw item from the staged run, in response order.

        A caller reuses these to skip per-posting detail requests, comparing
        against the stored ``payload``. Only the newest run is available -- the
        staged rows are replaced wholesale -- so a truncated run reuses just
        what it managed to stage and the rest is re-fetched next time.
        """
        pool = await self.pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT external_id, kind, payload
                FROM {self.staging}.fetched_feed_posting
                WHERE source = $1 AND feed_key = $2 ORDER BY item_index
                """,
                source,
                key,
            )
        return tuple(
            {
                "external_id": row["external_id"],
                "kind": row["kind"],
                "payload": _loads(row["payload"]),
            }
            for row in rows
        )

    async def summary_groups(self) -> list[dict[str, Any]]:
        pool = await self.pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT * FROM (
                    SELECT DISTINCT ON (feed_ref) {", ".join(_LATENCY_COLUMNS)}
                    FROM {self.schema}.fetch_latency
                    ORDER BY feed_ref, finished_at DESC, result_id DESC
                ) AS latest
                ORDER BY source, finished_at DESC
                """
            )
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            summary = self._latency_entry(row)
            groups.setdefault(summary["source"], []).append(summary)
        return [
            {"source": source, "results": results} for source, results in groups.items()
        ]

    def _latency_entry(self, row: asyncpg.Record) -> dict[str, Any]:
        entry = dict(row)
        entry["key"] = entry.pop("feed_key")
        entry["trigger"] = entry.pop("run_trigger")
        entry["started_at"] = _iso(entry["started_at"])
        entry["finished_at"] = _iso(entry["finished_at"])
        entry["network"] = {"retry_count": entry.pop("retry_count")}
        entry["parse"] = {"status": None, "summary": None}
        return entry

    async def run_history(self, feed_ref: str, limit: int = 50) -> list[dict[str, Any]]:
        return await self._run_series(
            "WHERE feed_ref = $1",
            (feed_ref,),
            max(1, min(limit, self.history_limit)),
        )

    async def recent_runs(
        self, *, trigger: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        condition, params = "", ()
        if trigger is not None:
            condition, params = "WHERE run_trigger = $1", (trigger,)
        return await self._run_series(condition, params, max(1, limit))

    async def _run_series(
        self, condition: str, params: tuple, limit: int
    ) -> list[dict[str, Any]]:
        pool = await self.pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {", ".join(_LATENCY_COLUMNS)}
                FROM {self.schema}.fetch_latency {condition}
                ORDER BY finished_at DESC, result_id DESC
                LIMIT ${len(params) + 1}
                """,
                *params,
                limit,
            )
        history = []
        for row in rows:
            entry = self._latency_entry(row)
            entry["network_summary"] = entry.pop("network")
            history.append(entry)
        return history

    async def run_network(self, result_id: str) -> dict[str, Any] | None:
        pool = await self.pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT network FROM {self.staging}.fetched_feed WHERE result_id = $1",
                result_id,
            )
        return (_loads(row["network"]) or {}) if row is not None else None

    async def source_stats(self) -> list[dict[str, Any]]:
        pool = await self.pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT source,
                       count(*) AS run_count,
                       count(DISTINCT feed_ref) AS feed_count,
                       count(*) FILTER (WHERE status = 'error') AS error_count,
                       count(*) FILTER (WHERE status = 'not_modified')
                           AS not_modified_count,
                       count(*) FILTER (WHERE status = 'partial') AS partial_count,
                       avg(elapsed_ms) AS avg_elapsed_ms,
                       max(elapsed_ms) AS max_elapsed_ms,
                       -- sum() over bigint yields numeric, which serializes as
                       -- a JSON string; the API has always exposed a number.
                       COALESCE(sum(raw_size_bytes), 0)::bigint AS raw_size_bytes,
                       COALESCE(sum(retry_count), 0)::bigint AS retry_count,
                       max(finished_at) AS last_finished_at
                FROM {self.schema}.fetch_latency
                GROUP BY source
                ORDER BY source
                """
            )
        stats = []
        for row in rows:
            entry = dict(row)
            entry["last_finished_at"] = _iso(entry["last_finished_at"])
            stats.append(entry)
        return stats

    # Meta -----------------------------------------------------------------

    async def get_meta(self, key: str, default: str | None = None) -> str | None:
        pool = await self.pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                f"SELECT value FROM {self.schema}.meta WHERE key = $1", key
            )
        return value if value is not None else default

    async def set_meta(self, key: str, value: str) -> None:
        pool = await self.pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.meta (key, value) VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value
                """,
                key,
                value,
            )

    # Posting labels -------------------------------------------------------

    async def posting_labels(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Every hand label, keyed (source, source_key).

        Read whole per request: the table only ever grows at human labeling
        speed, so a full scan is smaller than one staged payload.
        """
        pool = await self.pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT source, source_key, label, note, labeled_at
                FROM {self.schema}.posting_label
                """
            )
        return {
            (row["source"], row["source_key"]): {
                "label": row["label"],
                "note": row["note"],
                "labeled_at": _iso(row["labeled_at"]),
            }
            for row in rows
        }

    async def set_posting_labels(self, entries: list[dict[str, Any]]) -> int:
        """Upsert labels; a relabel overwrites in place and re-stamps labeled_at."""
        if not entries:
            return 0
        pool = await self.pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.executemany(
                f"""
                INSERT INTO {self.schema}.posting_label
                    (source, source_key, label, note, title_snapshot, company_snapshot)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (source, source_key) DO UPDATE SET
                    label = excluded.label,
                    note = excluded.note,
                    title_snapshot = excluded.title_snapshot,
                    company_snapshot = excluded.company_snapshot,
                    labeled_at = now()
                """,
                [
                    (
                        entry["source"],
                        entry["source_key"],
                        entry["label"],
                        entry.get("note"),
                        entry.get("title_snapshot"),
                        entry.get("company_snapshot"),
                    )
                    for entry in entries
                ],
            )
        return len(entries)

    async def delete_posting_labels(self, keys: list[tuple[str, str]]) -> int:
        """Clear labels by natural key; unknown keys are ignored, not errors."""
        if not keys:
            return 0
        pool = await self.pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                f"""
                DELETE FROM {self.schema}.posting_label
                WHERE (source, source_key) IN (
                    SELECT unnest($1::text[]), unnest($2::text[])
                )
                """,
                [key[0] for key in keys],
                [key[1] for key in keys],
            )
        return int(result.split()[-1])

    # Duplicate-group reviews ----------------------------------------------

    async def duplicate_group_reviews(
        self,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Every retained review, keyed by ``(group_id, fingerprint)``."""
        pool = await self.pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT group_id, fingerprint, decision, note, reviewed_at
                FROM {self.schema}.duplicate_group_review
                """
            )
        return {
            (str(row["group_id"]), row["fingerprint"]): {
                "decision": row["decision"],
                "note": row["note"],
                "reviewed_at": _iso(row["reviewed_at"]),
            }
            for row in rows
        }

    async def set_duplicate_group_review(
        self,
        *,
        group_id: str,
        fingerprint: str,
        decision: str,
        note: str | None = None,
    ) -> None:
        """Upsert one review version and refresh its review timestamp."""
        pool = await self.pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.duplicate_group_review
                    (group_id, fingerprint, decision, note)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (group_id, fingerprint) DO UPDATE SET
                    decision = excluded.decision,
                    note = excluded.note,
                    reviewed_at = now()
                """,
                UUID(group_id),
                fingerprint,
                decision,
                note,
            )

    async def delete_duplicate_group_review(
        self, *, group_id: str, fingerprint: str
    ) -> int:
        """Clear only the current review version; older history is retained."""
        pool = await self.pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                f"""
                DELETE FROM {self.schema}.duplicate_group_review
                WHERE group_id = $1 AND fingerprint = $2
                """,
                UUID(group_id),
                fingerprint,
            )
        return int(result.split()[-1])
