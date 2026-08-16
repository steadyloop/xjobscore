"""Read-only queries over ``public.job_posting`` for the Postings page.

Telemetry-style: every function borrows the pipeline pool (see
``PolicyStore.pool_or_none``) and opens nothing of its own. Hand labels live
in the inspector's schema (``persistence.ResultStore.posting_labels``) and
arrive here as a pre-fetched dict -- passed into SQL as parallel arrays
rather than joined across schemas, because the two stores' DSNs can in
principle point at different databases.

The SQL itself follows the telemetry precedent: exercised against a real
database by hand (see the verification notes in the repo plan); unit tests
cover validation, shaping, and the parameters handed to the pool.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

LABELS = ("relevant", "irrelevant", "unsure")
LABEL_STATES = ("all", "unlabeled", "labeled") + LABELS
ORDERS = ("newest", "company", "random")
STATUSES = ("all", "active", "closed")

# POSIX character classes, not \b: PostgreSQL ARE word boundaries ([[:<:]])
# read poorly and \b means backspace inside a bracket expression.
INTERN_TITLE_SQL_RE = (
    r"(^|[^a-z])(intern(ship)?s?|co[- ]?ops?"
    r"|fellow(ship)?s?|extern(ship)?s?)([^a-z]|$)"
)

_LABEL_PREDICATES = {
    "all": "TRUE",
    "unlabeled": "l.label IS NULL",
    "labeled": "l.label IS NOT NULL",
    **{value: f"l.label = '{value}'" for value in LABELS},
}
_ORDER_CLAUSES = {
    "newest": "p.first_seen_at DESC, p.id DESC",
    "company": "c.name, p.first_seen_at DESC, p.id DESC",
    # Stable for a fixed seed, so pagination never repeats or skips a row.
    "random": "md5($9::text || p.id::text), p.id",
}

_SELECT = """
WITH lab AS (
    SELECT unnest($1::text[]) AS source,
           unnest($2::text[]) AS source_key,
           unnest($3::text[]) AS label
)
SELECT p.id, p.source, p.source_key, c.name AS company, p.title,
       p.location_text, p.source_url, p.apply_url, p.posted_at, p.status,
       p.first_seen_at, p.last_seen_at, p.attributes, p.dup_group_id,
       (p.description IS NOT NULL) AS has_description,
       count(*) OVER () AS total
FROM job_posting AS p
JOIN company AS c ON c.id = p.company_id
LEFT JOIN lab AS l
  ON l.source = p.source AND l.source_key = p.source_key
WHERE ($4::text IS NULL OR split_part(p.source, ':', 1) = $4)
  AND ($5::text IS NULL OR p.status = $5)
  AND ($6::text = '' OR p.title ILIKE '%' || $6 || '%'
       OR c.name ILIKE '%' || $6 || '%'
       OR p.location_text ILIKE '%' || $6 || '%'
       OR p.source ILIKE '%' || $6 || '%')
  AND (NOT $7::bool OR p.title ~* $8)
  -- $9 is the shuffle seed; referenced here so the parameter list is stable
  -- across every order mode (asyncpg rejects unused arguments).
  AND ($9::text = $9::text)
  AND {label_predicate}
ORDER BY {order_clause}
LIMIT $10 OFFSET $11
"""

_DETAIL = """
SELECT p.id, p.source, p.source_key, c.name AS company, p.title,
       p.description, p.location_text, p.source_url, p.apply_url,
       p.posted_at, p.status, p.first_seen_at, p.last_seen_at,
       p.attributes, p.content_hash, p.raw_id, p.dup_group_id
FROM job_posting AS p
JOIN company AS c ON c.id = p.company_id
WHERE p.id = $1
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


def _merge_label(
    row: dict[str, Any], labels: dict[tuple[str, str], dict[str, Any]]
) -> dict[str, Any]:
    entry = labels.get((row["source"], row["source_key"]))
    row["label"] = entry["label"] if entry else None
    row["note"] = entry["note"] if entry else None
    row["labeled_at"] = entry["labeled_at"] if entry else None
    return row


def _shape(record: Any, labels: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    row = dict(record)
    row.pop("total", None)
    for field in ("posted_at", "first_seen_at", "last_seen_at"):
        row[field] = _iso(row.get(field))
    row["attributes"] = _attributes(row.get("attributes"))
    dup = row.get("dup_group_id")
    row["dup_group_id"] = str(dup) if dup is not None else None
    return _merge_label(row, labels)


def label_counts(labels: dict[tuple[str, str], dict[str, Any]]) -> dict[str, int]:
    counts = {value: 0 for value in LABELS}
    for entry in labels.values():
        if entry["label"] in counts:
            counts[entry["label"]] += 1
    counts["total"] = len(labels)
    return counts


async def query(
    pool,
    labels: dict[tuple[str, str], dict[str, Any]],
    *,
    source: str | None = None,
    status: str = "active",
    label_state: str = "all",
    search: str = "",
    intern_signal: bool = False,
    order: str = "newest",
    seed: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return one page of job_posting rows with label state merged in."""
    _validate("status", status, STATUSES)
    _validate("label_state", label_state, LABEL_STATES)
    _validate("order", order, ORDERS)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    if order == "random" and not seed:
        seed = secrets.token_hex(4)

    keys = sorted(labels)
    sql = _SELECT.format(
        label_predicate=_LABEL_PREDICATES[label_state],
        order_clause=_ORDER_CLAUSES[order],
    )
    async with pool.acquire() as conn:
        sources = [
            row["vendor"]
            for row in await conn.fetch(
                "SELECT DISTINCT split_part(source, ':', 1) AS vendor"
                " FROM job_posting ORDER BY 1"
            )
        ]
        records = await conn.fetch(
            sql,
            [key[0] for key in keys],
            [key[1] for key in keys],
            [labels[key]["label"] for key in keys],
            source,
            None if status == "all" else status,
            search.strip(),
            intern_signal,
            INTERN_TITLE_SQL_RE,
            seed,
            limit,
            offset,
        )
    return {
        "sources": sources,
        "filters": {
            "source": source,
            "status": status,
            "label_state": label_state,
            "search": search.strip(),
            "intern_signal": intern_signal,
            "order": order,
            "seed": seed,
        },
        "total": records[0]["total"] if records else 0,
        "offset": offset,
        "limit": limit,
        "label_counts": label_counts(labels),
        "rows": [_shape(record, labels) for record in records],
    }


async def detail(
    pool,
    labels: dict[tuple[str, str], dict[str, Any]],
    posting_id: int,
) -> dict[str, Any] | None:
    """One full job_posting row, description included; None when unknown."""
    async with pool.acquire() as conn:
        record = await conn.fetchrow(_DETAIL, posting_id)
    if record is None:
        return None
    row = dict(record)
    for field in ("posted_at", "first_seen_at", "last_seen_at"):
        row[field] = _iso(row.get(field))
    row["attributes"] = _attributes(row.get("attributes"))
    dup = row.get("dup_group_id")
    row["dup_group_id"] = str(dup) if dup is not None else None
    return _merge_label(row, labels)
