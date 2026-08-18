"""Group-oriented reads over derived duplicate candidates for Inspector review."""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from xjobscore.inspector.postings import INTERN_TITLE_SQL_RE

DECISIONS = ("correct", "incorrect", "needs_review", "hidden")
REVIEW_STATES = ("all", "unreviewed", *DECISIONS)
ORDERS = ("newest", "company", "random")

_REVIEW_PREDICATES = {
    # Hidden groups stay recoverable without leaking into ordinary queues.
    "all": "review_decision IS DISTINCT FROM 'hidden'",
    "unreviewed": "review_decision IS NULL",
    **{value: f"review_decision = '{value}'" for value in DECISIONS},
}

_ORDER_CLAUSES = {
    "newest": "latest_seen_at DESC, id",
    "company": "company, title, id",
    "random": "md5($8::text || id::text), id",
}

_BASE = """
WITH reviews AS (
    SELECT *
    FROM unnest($1::uuid[], $2::text[], $3::text[])
         AS r(group_id, fingerprint, decision)
),
base AS (
    SELECT g.id,
           md5(g.evidence::text) AS fingerprint,
           g.state AS algorithm_state,
           g.evidence,
           (array_agg(c.name ORDER BY p.id))[1] AS company,
           (array_agg(p.title ORDER BY p.first_seen_at DESC, p.id DESC))[1] AS title,
           (array_agg(p.id ORDER BY p.first_seen_at DESC, p.id DESC))[1]
               AS representative_posting_id,
           count(*)::int AS member_count,
           array_agg(DISTINCT split_part(p.source, ':', 1)) AS sources,
           COALESCE(
               array_agg(DISTINCT p.location_text)
                   FILTER (WHERE p.location_text IS NOT NULL),
               ARRAY[]::text[]
           ) AS locations,
           max(p.last_seen_at) AS latest_seen_at,
           r.decision AS review_decision
    FROM job_posting_dup_group AS g
    JOIN job_posting AS p ON p.dup_group_id = g.id
    JOIN company AS c ON c.id = p.company_id
    LEFT JOIN reviews AS r
      ON r.group_id = g.id AND r.fingerprint = md5(g.evidence::text)
    GROUP BY g.id, r.decision
    HAVING ($4::text IS NULL
            OR bool_or(split_part(p.source, ':', 1) = $4))
       AND ($5::text = ''
            OR bool_or(p.title ILIKE '%' || $5 || '%'
                       OR c.name ILIKE '%' || $5 || '%'
                       OR p.location_text ILIKE '%' || $5 || '%'
                       OR p.source ILIKE '%' || $5 || '%'))
       AND (NOT $6::bool OR bool_or(p.title ~* $7))
)
"""

_COUNTS = _BASE + """
SELECT count(*) FILTER (WHERE review_decision IS DISTINCT FROM 'hidden')::int AS all,
       count(*) FILTER (WHERE review_decision IS NULL)::int AS unreviewed,
       count(*) FILTER (WHERE review_decision = 'correct')::int AS correct,
       count(*) FILTER (WHERE review_decision = 'incorrect')::int AS incorrect,
       count(*) FILTER (WHERE review_decision = 'needs_review')::int
           AS needs_review,
       count(*) FILTER (WHERE review_decision = 'hidden')::int AS hidden
FROM base
"""

_MEMBERS = """
SELECT p.dup_group_id, p.id, p.source, p.source_key, c.name AS company,
       p.title, p.location_text, p.source_url, p.apply_url, p.posted_at,
       p.status, p.first_seen_at, p.last_seen_at,
       (p.description IS NOT NULL) AS has_description
FROM job_posting AS p
JOIN company AS c ON c.id = p.company_id
WHERE p.dup_group_id = ANY($1::uuid[])
ORDER BY p.dup_group_id, p.id
"""


def _validate(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ValueError(f"unknown {name}: {value!r} (expected one of {allowed})")


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _review_args(
    reviews: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[list[UUID], list[str], list[str]]:
    keys = sorted(reviews)
    return (
        [UUID(group_id) for group_id, _ in keys],
        [fingerprint for _, fingerprint in keys],
        [str(reviews[key]["decision"]) for key in keys],
    )


def _warnings(evidence: Mapping[str, Any]) -> list[str]:
    members = evidence.get("members")
    if not isinstance(members, list):
        return []
    signals = {
        "location": {row.get("location") for row in members if row.get("location")},
        "ats_identity": {
            row.get("ats_identity") for row in members if row.get("ats_identity")
        },
        "description_hash": {
            row.get("description_hash")
            for row in members
            if row.get("description_hash")
        },
    }
    return [f"{name}_mismatch" for name, values in signals.items() if len(values) > 1]


def _member_evidence(evidence: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    members = evidence.get("members")
    if not isinstance(members, list):
        return {}
    return {
        int(row["posting_id"]): row
        for row in members
        if isinstance(row, dict) and row.get("posting_id") is not None
    }


async def query(
    pool,
    reviews: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    source: str | None = None,
    review_state: str = "unreviewed",
    search: str = "",
    intern_signal: bool = False,
    order: str = "newest",
    seed: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return one page of complete duplicate groups, never split by member."""
    _validate("review state", review_state, REVIEW_STATES)
    _validate("order", order, ORDERS)
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    if order == "random" and not seed:
        seed = secrets.token_hex(4)

    group_ids, fingerprints, decisions = _review_args(reviews)
    common_args = (
        group_ids,
        fingerprints,
        decisions,
        source,
        search.strip(),
        intern_signal,
        INTERN_TITLE_SQL_RE,
    )
    page_sql = _BASE + f"""
SELECT *
FROM base
WHERE {_REVIEW_PREDICATES[review_state]}
  AND ($8::text = $8::text)
ORDER BY {_ORDER_CLAUSES[order]}
LIMIT $9 OFFSET $10
"""

    async with pool.acquire() as conn:
        sources = [
            row["vendor"]
            for row in await conn.fetch(
                "SELECT DISTINCT split_part(source, ':', 1) AS vendor"
                " FROM job_posting ORDER BY 1"
            )
        ]
        counts_record = await conn.fetchrow(_COUNTS, *common_args)
        records = await conn.fetch(
            page_sql, *common_args, seed, limit, offset
        )
        ids = [record["id"] for record in records]
        member_records = await conn.fetch(_MEMBERS, ids) if ids else []

    by_group: dict[str, list[dict[str, Any]]] = {str(group_id): [] for group_id in ids}
    evidence_by_group: dict[str, dict[int, dict[str, Any]]] = {}
    for record in records:
        group_id = str(record["id"])
        evidence = _json_object(record["evidence"])
        evidence_by_group[group_id] = _member_evidence(evidence)
    for record in member_records:
        group_id = str(record["dup_group_id"])
        member = dict(record)
        member.pop("dup_group_id", None)
        for field in ("posted_at", "first_seen_at", "last_seen_at"):
            member[field] = _iso(member.get(field))
        member["evidence"] = evidence_by_group.get(group_id, {}).get(
            int(member["id"]), {}
        )
        by_group.setdefault(group_id, []).append(member)

    reviewed_group_ids = {group_id for group_id, _ in reviews}
    groups = []
    for record in records:
        group_id = str(record["id"])
        fingerprint = record["fingerprint"]
        evidence = _json_object(record["evidence"])
        review = reviews.get((group_id, fingerprint))
        groups.append(
            {
                "id": group_id,
                "fingerprint": fingerprint,
                "algorithm_state": record["algorithm_state"],
                "company": record["company"],
                "title": record["title"],
                "representative_posting_id": record["representative_posting_id"],
                "member_count": record["member_count"],
                "sources": list(record["sources"] or []),
                "locations": list(record["locations"] or []),
                "latest_seen_at": _iso(record["latest_seen_at"]),
                "warnings": _warnings(evidence),
                "review": dict(review) if review is not None else None,
                "changed_since_review": review is None
                and group_id in reviewed_group_ids,
                "members": by_group.get(group_id, []),
            }
        )

    counts = dict(counts_record) if counts_record is not None else {}
    return {
        "sources": sources,
        "filters": {
            "source": source,
            "review_state": review_state,
            "search": search.strip(),
            "intern_signal": intern_signal,
            "order": order,
            "seed": seed,
        },
        "total": int(counts.get(review_state) or 0),
        "offset": offset,
        "limit": limit,
        "review_counts": {
            value: int(counts.get(value) or 0)
            for value in ("all", "unreviewed", *DECISIONS)
        },
        "groups": groups,
    }


async def current_fingerprint(pool, group_id: UUID) -> str | None:
    """Read the current evidence fingerprint, or ``None`` after disappearance."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT md5(evidence::text) FROM job_posting_dup_group WHERE id = $1",
            group_id,
        )
