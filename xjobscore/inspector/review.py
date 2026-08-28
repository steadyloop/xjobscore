"""Read/write queries over ``public.job_posting`` for the opus Review page.

Telemetry-style, like ``postings.py``: every function borrows the pipeline
pool (see ``PolicyStore.pool_or_none``) and opens nothing of its own.

Scope is ``attributes ? 'opus'`` -- the set a prior migration stamped with a
title-only regex triage (tech / non-tech / ambiguous / other). That first
pass is measurably wrong in places (see the migration that added
``opus_by``/``opus_machine``), so this page exists to let a human correct it
row by row and record who decided:

  attributes.opus          current best label
  attributes.opus_by       'machine' | 'human'
  attributes.opus_at       ISO timestamp, set on a human write only
  attributes.opus_machine  the original regex guess, preserved across edits

Unlike ``postings.py``, which merges hand labels in from the inspector
schema, this page's labels live inside ``job_posting.attributes`` itself --
that was the deliberate choice for this set (see the plan): there is no
separate labels table to join.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

LABELS = ("tech", "non-tech", "ambiguous", "other")
STATES = ("all", "unreviewed", "reviewed", "changed")
ORDERS = ("newest", "company", "title")

_LABEL_PREDICATES = {
    "all": "TRUE",
    **{value: f"p.attributes->>'opus' = '{value}'" for value in LABELS},
}
_STATE_PREDICATES = {
    "all": "TRUE",
    "unreviewed": "COALESCE(p.attributes->>'opus_by', 'machine') = 'machine'",
    "reviewed": "p.attributes->>'opus_by' = 'human'",
    # A human decision that landed on a different bucket than the machine
    # guessed -- the corrections, which is what makes the pass measurable.
    "changed": (
        "p.attributes->>'opus_by' = 'human'"
        " AND p.attributes->>'opus' IS DISTINCT FROM p.attributes->>'opus_machine'"
    ),
}
_ORDER_CLAUSES = {
    "newest": "p.first_seen_at DESC, p.id DESC",
    "company": "c.name, p.first_seen_at DESC, p.id DESC",
    "title": "p.title, p.id",
}

_SELECT = """
SELECT p.id, p.source, p.source_key, c.name AS company, p.title,
       p.location_text, p.source_url, p.apply_url, p.posted_at, p.status,
       p.job_type, p.attributes,
       (p.attributes->'degree') ? 'cs' AS cs_field,
       c.labels->>'ck_list' AS ck_list,
       (p.description IS NOT NULL) AS has_description,
       count(*) OVER () AS total
FROM job_posting AS p
JOIN company AS c ON c.id = p.company_id
WHERE p.attributes ? 'opus'
  AND ($1::text IS NULL OR split_part(p.source, ':', 1) = $1)
  AND ($2::text = '' OR p.title ILIKE '%' || $2 || '%'
       OR c.name ILIKE '%' || $2 || '%'
       OR p.location_text ILIKE '%' || $2 || '%'
       OR p.source ILIKE '%' || $2 || '%')
  AND {label_predicate}
  AND {state_predicate}
ORDER BY {order_clause}
LIMIT $3 OFFSET $4
"""

_COUNTS = """
SELECT p.attributes->>'opus' AS label,
       COALESCE(p.attributes->>'opus_by', 'machine') AS opus_by,
       (p.attributes->>'opus_by' = 'human'
        AND p.attributes->>'opus' IS DISTINCT FROM p.attributes->>'opus_machine') AS changed
FROM job_posting AS p
WHERE p.attributes ? 'opus'
"""

_DETAIL = """
SELECT p.id, p.source, p.source_key, c.name AS company, p.title,
       p.description, p.location_text, p.source_url, p.apply_url,
       p.posted_at, p.status, p.job_type, p.attributes,
       (p.attributes->'degree') ? 'cs' AS cs_field,
       c.labels->>'ck_list' AS ck_list
FROM job_posting AS p
JOIN company AS c ON c.id = p.company_id
WHERE p.id = $1 AND p.attributes ? 'opus'
"""

_UPDATE = """
UPDATE job_posting
SET attributes = attributes || $2::jsonb
WHERE id = $1 AND attributes ? 'opus'
RETURNING id
"""


def _validate(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ValueError(f"unknown {name}: {value!r} (expected one of {allowed})")


def _attributes(value: Any) -> dict[str, Any]:
    # The pipeline pool installs a jsonb codec, so this normally arrives
    # decoded; be defensive because tests hand in plain fakes.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _shape(record: Any) -> dict[str, Any]:
    row = dict(record)
    row.pop("total", None)
    row["posted_at"] = _iso(row.get("posted_at"))
    attrs = _attributes(row.pop("attributes"))
    row["opus"] = attrs.get("opus")
    row["opus_by"] = attrs.get("opus_by", "machine")
    row["opus_at"] = attrs.get("opus_at")
    row["opus_machine"] = attrs.get("opus_machine")
    return row


def state_counts(rows: list[Any]) -> dict[str, int]:
    counts = {"all": 0, "unreviewed": 0, "reviewed": 0, "changed": 0}
    for row in rows:
        counts["all"] += 1
        by = row["opus_by"] or "machine"
        if by == "human":
            counts["reviewed"] += 1
            if row["changed"]:
                counts["changed"] += 1
        else:
            counts["unreviewed"] += 1
    return counts


def label_counts(rows: list[Any]) -> dict[str, int]:
    counts = {value: 0 for value in LABELS}
    for row in rows:
        if row["label"] in counts:
            counts[row["label"]] += 1
    counts["total"] = len(rows)
    return counts


async def query(
    pool,
    *,
    source: str | None = None,
    label: str = "all",
    state: str = "unreviewed",
    search: str = "",
    order: str = "newest",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return one page of the opus review set."""
    _validate("label", label, ("all",) + LABELS)
    _validate("state", state, STATES)
    _validate("order", order, ORDERS)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    sql = _SELECT.format(
        label_predicate=_LABEL_PREDICATES[label],
        state_predicate=_STATE_PREDICATES[state],
        order_clause=_ORDER_CLAUSES[order],
    )
    async with pool.acquire() as conn:
        sources = [
            row["vendor"]
            for row in await conn.fetch(
                "SELECT DISTINCT split_part(p.source, ':', 1) AS vendor"
                " FROM job_posting AS p WHERE p.attributes ? 'opus' ORDER BY 1"
            )
        ]
        count_rows = await conn.fetch(_COUNTS)
        records = await conn.fetch(sql, source, search.strip(), limit, offset)

    return {
        "sources": sources,
        "filters": {
            "source": source,
            "label": label,
            "state": state,
            "search": search.strip(),
            "order": order,
        },
        "total": records[0]["total"] if records else 0,
        "offset": offset,
        "limit": limit,
        "label_counts": label_counts(count_rows),
        "state_counts": state_counts(count_rows),
        "rows": [_shape(record) for record in records],
    }


async def detail(pool, posting_id: int) -> dict[str, Any] | None:
    """One full review-set row, description included; None when unknown."""
    async with pool.acquire() as conn:
        record = await conn.fetchrow(_DETAIL, posting_id)
    if record is None:
        return None
    row = dict(record)
    row["posted_at"] = _iso(row.get("posted_at"))
    attrs = _attributes(row.pop("attributes"))
    row["opus"] = attrs.get("opus")
    row["opus_by"] = attrs.get("opus_by", "machine")
    row["opus_at"] = attrs.get("opus_at")
    row["opus_machine"] = attrs.get("opus_machine")
    return row


async def set_labels(pool, entries: list[dict[str, Any]]) -> int:
    """Write a human decision for each ``{id, label}`` entry; returns count written.

    Always stamps ``opus_by='human'`` and ``opus_at`` to now. ``opus_machine``
    is never touched here -- it was seeded once by the provenance migration
    and stays put across any number of human re-decisions, so the original
    regex guess (and thus the correction rate) is never lost.

    The patch is handed to asyncpg as a plain dict, not ``json.dumps``-ed text
    -- the pipeline pool installs a jsonb codec (``store._install_jsonb_codec``)
    that runs every jsonb parameter through ``orjson.dumps`` itself. Passing an
    already-serialized string double-encodes it into a jsonb *string* scalar,
    and ``attributes || 'a scalar'`` doesn't merge -- jsonb concatenation wraps
    a non-array operand into an array, which then fails
    ``job_posting_attributes_check`` (attributes must stay an object).
    """
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for entry in entries:
                patch = {
                    "opus": entry["label"],
                    "opus_by": "human",
                    "opus_at": now,
                }
                result = await conn.fetchrow(_UPDATE, entry["id"], patch)
                if result is not None:
                    written += 1
    return written
