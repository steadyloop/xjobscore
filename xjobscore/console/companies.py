"""Queries and operator verification edits over ``public.company``.

``company`` is the pipeline's ground truth: ``job_posting.company_id`` is NOT
NULL, so every posting that survives silver is already committed to a company
row. What is *not* settled is whether that row is correct. Names arrive as ATS
token slugs (``8fleet-inc``) and domains as unreviewed guesses, and nothing
before this page ever recorded a verdict on either. This module is that writer.

There is deliberately no staging table. A candidate *is* a ``company`` row
carrying ``reviewed = 'pending'``; promotion writes that column rather than copying
between tables. Two consequences shape everything below:

- Rejection cannot be a DELETE. ``job_posting_company_id_fkey`` is NO ACTION,
  so a company with postings refuses to be removed. A rejected row is marked
  ``reviewed = 'rejected'`` and stays.
- The verdict lives in ``reviewed``/``reviewed_at``/``reviewed_by``; ``tags``
  keeps only the original machine guess. The guess is written once and never
  overwritten, so the share of domains an operator had to correct stays
  measurable after the fact.

Telemetry-style: every function borrows the pipeline pool (see
``PolicyStore.pool_or_none``) and opens nothing of its own. Untrusted text only
ever arrives as a bind parameter; the only values interpolated into SQL are
pre-validated enum keys.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from xjobscore import store

# The serving cohort -- the job types the frontend actually publishes. A
# company is worth verifying in proportion to how often it will be seen, and
# nothing outside these types is ever seen.
SERVING_JOB_TYPES = ("intern", "coop", "student", "new_grad", "research")

# Keep this tuple in the same order as the filter control on the page.
FILTERS = ("queue", "unverified", "verified", "rejected", "all")
ORDERS = ("impact", "name", "recent")

# `reviewed` is NOT NULL DEFAULT 'pending', so unreviewed is a value rather
# than an absence -- the three-valued-logic trap the jsonb version had (NOT
# (NULL = 'rejected') is NULL, quietly hiding the whole backlog) cannot recur.
_REJECTED = "c.reviewed = 'rejected'"
_VERIFIED = "c.reviewed = 'verified'"
_PENDING = "c.reviewed = 'pending'"

_FILTER_PREDICATES = {
    # The default view: unreviewed rows that actually carry serving postings.
    # An unreviewed company with nothing to apply to is not a backlog item.
    "queue": f"({_PENDING}) AND COALESCE(s.active_postings, 0) > 0",
    "unverified": _PENDING,
    "verified": _VERIFIED,
    "rejected": _REJECTED,
    "all": "TRUE",
}

_ORDER_CLAUSES = {
    # Verifying Micron once pays for 387 postings; alphabetical order would
    # bury it behind a few hundred single-posting companies.
    "impact": "COALESCE(s.active_postings, 0) DESC, c.id",
    "name": "lower(c.name), c.id",
    "recent": "c.reviewed_at DESC NULLS LAST, c.id",
}

# The job-types bind lands at a different position in each statement below,
# so the CTE names it rather than hard-coding one.
#
# One pass, not two plus a LATERAL. ``job_posting.company_id`` is a NOT NULL
# foreign key with no index (checked: the only company_id index in the database
# is ``company_alias_company_idx``), so a per-row correlated lookup for the
# sample posting seq-scans 353k rows once per company -- 41 s for a 663-row
# page. DISTINCT ON walks the job_type-filtered set a single time, and the
# window count rides along on the same scan.
_SERVING = """
WITH serving AS (
    SELECT DISTINCT ON (j.company_id)
           j.company_id,
           count(*) OVER (PARTITION BY j.company_id)::int AS active_postings,
           j.title      AS sample_title,
           j.apply_url  AS sample_apply_url,
           j.source_url AS sample_source_url
      FROM job_posting AS j
     WHERE j.status = 'active'
       AND j.job_type = ANY({types}::text[])
     ORDER BY j.company_id,
              COALESCE(j.posted_at, j.first_seen_at) DESC,
              j.id DESC
), company_tokens AS (
    SELECT t.company_id,
           array_agg(t.ats || ':' || t.token ORDER BY t.ats, t.token) AS tokens
      FROM token AS t
     WHERE t.company_id IS NOT NULL
     GROUP BY t.company_id
), alias_counts AS (
    SELECT a.company_id, count(*)::int AS alias_count
      FROM company_alias AS a
     GROUP BY a.company_id
)
"""

_SELECT = (
    _SERVING.format(types="$3")
    + """
SELECT c.id, c.name, c.domain, c.description, c.tags,
       c.reviewed, c.reviewed_at,
       COALESCE(s.active_postings, 0) AS active_postings,
       COALESCE(ac.alias_count, 0) AS alias_count,
       EXISTS (
           SELECT 1 FROM company_logo AS g
            WHERE g.company_id = c.id AND g.bytes IS NOT NULL
       ) AS has_logo,
       ct.tokens AS tokens,
       -- The freshest serving posting is the evidence an operator actually
       -- decides on: its apply URL names the real employer far more reliably
       -- than a token slug does.
       s.sample_title,
       s.sample_apply_url,
       s.sample_source_url,
       count(*) OVER ()::int AS total
  FROM company AS c
  LEFT JOIN serving AS s ON s.company_id = c.id
  LEFT JOIN company_tokens AS ct ON ct.company_id = c.id
  LEFT JOIN alias_counts AS ac ON ac.company_id = c.id
 WHERE ({where_clause})
   AND ($4 = '' OR c.name ILIKE '%' || $4 || '%'
                OR c.domain ILIKE '%' || $4 || '%')
   AND ($5::bigint IS NULL OR c.id = $5)
 ORDER BY {order_clause}
 LIMIT $1 OFFSET $2
"""
)

# Deliberately unfiltered: the header reports the shape of the whole table, so
# the backlog number does not move when someone narrows the view.
_COUNTS = (
    _SERVING.format(types="$1")
    + f"""
SELECT count(*)::int AS total,
       count(*) FILTER (WHERE {_VERIFIED})::int AS verified,
       count(*) FILTER (WHERE {_REJECTED})::int AS rejected,
       count(*) FILTER (WHERE {_PENDING})::int AS unverified,
       count(*) FILTER (
           WHERE ({_PENDING}) AND COALESCE(s.active_postings, 0) > 0
       )::int AS queue,
       count(*) FILTER (WHERE c.domain IS NULL)::int AS no_domain,
       count(*) FILTER (
           WHERE COALESCE(s.active_postings, 0) > 0
       )::int AS serving
  FROM company AS c
  LEFT JOIN serving AS s ON s.company_id = c.id
"""
)

_ALIAS_INSERT = """
INSERT INTO company_alias (alias_norm, company_id, alias)
SELECT norm, $2, raw FROM unnest($1::text[], $3::text[]) AS t(norm, raw)
ON CONFLICT (alias_norm) DO NOTHING
RETURNING alias_norm
"""


def _validate(name: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ValueError(f"unknown {name}: {value!r}")
    return value


def _clean(value: str | None) -> str | None:
    """Trim, and treat a blank box as "no value" rather than an empty string.

    ``company.domain`` is UNIQUE, and '' is a value that collides with itself:
    two cleared domains would fight over one row.
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _review_state(record: Any) -> str:
    """The page's name for the verdict; `pending` reads as "unverified"."""
    verdict = record["reviewed"]
    return verdict if verdict in ("verified", "rejected") else "unverified"


def _shape(record: Any) -> dict[str, Any]:
    row = dict(record)
    row.pop("total", None)
    tags = row.get("tags") or {}
    row["tags"] = tags
    # The column is the source of truth; the page sees one name for the
    # verdict, not the column and an alias of it.
    row["review_state"] = _review_state(record)
    row.pop("reviewed", None)
    row["review_at"] = row.pop("reviewed_at", None)
    row["tokens"] = list(row["tokens"] or [])
    # The guess this row started with, so a reviewer can see what they are
    # being asked to confirm rather than only the current value.
    row["name_machine"] = tags.get("name_machine")
    row["domain_machine"] = tags.get("domain_machine")
    row["domain_corrected"] = (
        tags.get("domain_machine") is not None
        and tags.get("domain_machine") != row["domain"]
    )
    row["name_corrected"] = (
        tags.get("name_machine") is not None
        and tags.get("name_machine") != row["name"]
    )
    return row


async def query(
    pool,
    *,
    filter: str = "queue",
    order: str = "impact",
    search: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    _validate("filter", filter, FILTERS)
    _validate("order", order, ORDERS)
    limit = max(1, min(limit, 5000))
    offset = max(0, offset)
    select_query = _SELECT.format(
        where_clause=_FILTER_PREDICATES[filter],
        order_clause=_ORDER_CLAUSES[order],
    )
    job_types = list(SERVING_JOB_TYPES)
    async with pool.acquire() as conn:
        counts = await conn.fetchrow(_COUNTS, job_types)
        records = await conn.fetch(
            select_query, limit, offset, job_types, search.strip(), None
        )
    return {
        "counts": dict(counts) if counts else {},
        "total": records[0]["total"] if records else 0,
        "offset": offset,
        "limit": limit,
        "filter": filter,
        "order": order,
        "rows": [_shape(record) for record in records],
    }


async def get(pool, company_id: int) -> dict[str, Any] | None:
    select_query = _SELECT.format(
        where_clause=_FILTER_PREDICATES["all"],
        order_clause=_ORDER_CLAUSES["name"],
    )
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            select_query, 1, 0, list(SERVING_JOB_TYPES), "", company_id
        )
    return _shape(record) if record else None


async def _write_aliases(conn, company_id: int, names: list[str | None]) -> list[str]:
    """Point every spelling of *names* at ``company_id``, never stealing one.

    A rename must not orphan the old spelling: postings already resolved
    through the token slug, and ``silver`` will keep seeing that slug on the
    wire long after the display name is fixed. ``DO NOTHING`` is the safety --
    an ``alias_norm`` already owned by a different company stays owned by it,
    because a verification is not a merge.
    """
    seen: dict[str, str] = {}
    for raw in names:
        if not raw:
            continue
        for candidate in store.alias_candidates(raw):
            seen.setdefault(candidate, raw)
    if not seen:
        return []
    records = await conn.fetch(
        _ALIAS_INSERT, list(seen.keys()), company_id, list(seen.values())
    )
    return [record["alias_norm"] for record in records]


async def verify(
    pool,
    company_id: int,
    *,
    name: str | None = None,
    domain: str | None = None,
    description: str | None = None,
    by: str = "human",
) -> dict[str, Any] | None:
    """Apply the operator's corrections and stamp the verdict.

    ``None`` means "leave this field alone"; an empty string means "clear it".
    The two are different answers and the caller has to be able to say either.

    ``by`` records who decided. It defaults to the page's own answer because
    that is the only caller with a person behind it; a batch resolution passes
    its own name so that 'a human confirmed this' never becomes a claim no one
    can check.
    """
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                """
                SELECT id, name, domain, description, tags
                  FROM company WHERE id = $1 FOR UPDATE
                """,
                company_id,
            )
            if current is None:
                return None
            new_name = current["name"] if name is None else _clean(name)
            if not new_name:
                raise ValueError("name cannot be empty")
            new_domain = current["domain"] if domain is None else _clean(domain)
            new_description = (
                current["description"] if description is None else _clean(description)
            )
            tags = dict(current["tags"] or {})
            patch: dict[str, Any] = {}
            # Written once, never overwritten: the machine's original guess is
            # what makes the correction rate measurable later.
            if "name_machine" not in tags:
                patch["name_machine"] = current["name"]
            if "domain_machine" not in tags:
                patch["domain_machine"] = current["domain"]
            await conn.execute(
                """
                UPDATE company
                   SET name = $2,
                       domain = $3,
                       description = $4,
                       reviewed = 'verified',
                       reviewed_at = $5,
                       reviewed_by = $7,
                       tags = tags || $6::jsonb
                 WHERE id = $1
                """,
                company_id,
                new_name,
                new_domain,
                new_description,
                now,
                patch,
                by,
            )
            await _write_aliases(conn, company_id, [current["name"], new_name])
    return await get(pool, company_id)


async def set_review(
    pool, company_id: int, *, state: str
) -> dict[str, Any] | None:
    """Mark a row rejected, or send a reviewed row back to the queue.

    Rejection is a label rather than a DELETE on purpose:
    ``job_posting_company_id_fkey`` is NO ACTION, so any company carrying
    postings simply refuses to be deleted. Marking keeps the row -- and its
    postings -- while taking it out of the backlog.
    """
    _validate("state", state, ("rejected", "pending"))
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        if state == "rejected":
            result = await conn.execute(
                """
                UPDATE company
                   SET reviewed = 'rejected',
                       reviewed_at = $2,
                       reviewed_by = 'human'
                 WHERE id = $1
                """,
                company_id,
                now,
            )
        else:
            result = await conn.execute(
                """
                UPDATE company
                   SET reviewed = 'pending',
                       reviewed_at = NULL,
                       reviewed_by = NULL
                 WHERE id = $1
                """,
                company_id,
            )
    if result == "UPDATE 0":
        return None
    return await get(pool, company_id)


async def owner_of_domain(pool, domain: str | None) -> dict[str, Any] | None:
    """The company already holding *domain*, so a 409 can name it.

    A bare "that domain is taken" leaves the operator to go and find the other
    row by hand. Naming it is what makes the conflict actionable in one step.
    """
    cleaned = _clean(domain)
    if cleaned is None:
        return None
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            """
            SELECT c.id, c.name, c.domain, c.reviewed,
                   (SELECT count(*) FROM job_posting j WHERE j.company_id = c.id)::int
                       AS active_postings
              FROM company AS c WHERE c.domain = $1
            """,
            cleaned,
        )
    return dict(record) if record else None


# Every table that names a company. `company_alias` and `company_logo` are
# ON DELETE CASCADE from `company`, so they must be moved *before* the source
# row goes, not left to the cascade -- a cascade would delete the aliases that
# are the whole reason silver can still resolve the old spelling.
_MERGE_MOVES = (
    ("token", "company_id"),
    ("job_posting", "company_id"),
)


async def merge(pool, source_id: int, target_id: int) -> dict[str, Any] | None:
    """Move everything from *source_id* onto *target_id*, then drop the source.

    Two rows describing one employer is the normal outcome of minting a company
    per ATS token: `Tradeweb Europe Limited` arrives from an Oracle shard while
    `Tradeweb` already exists holding the domain. Nothing can decide that
    automatically -- the names differ, the domains do not overlap, and only a
    human can say they are the same company -- so the merge is an explicit act
    and this is its one writer.

    Direction matters and is the caller's: the *target* survives with its own
    name, domain and verdict intact. The source contributes its tokens, its
    postings, and its spellings.
    """
    if source_id == target_id:
        raise ValueError("cannot merge a company into itself")
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id FROM company WHERE id = ANY($1::bigint[]) FOR UPDATE",
                [source_id, target_id],
            )
            if {record["id"] for record in rows} != {source_id, target_id}:
                return None

            for table, column in _MERGE_MOVES:
                await conn.execute(
                    f"UPDATE {table} SET {column} = $2 WHERE {column} = $1",
                    source_id,
                    target_id,
                )

            # Aliases move by re-pointing, not by insert: `alias_norm` is the
            # primary key, so a spelling the target already owns simply stays
            # where it is and the source's duplicate is dropped.
            await conn.execute(
                """
                DELETE FROM company_alias AS a
                 WHERE a.company_id = $1
                   AND EXISTS (
                       SELECT 1 FROM company_alias AS b
                        WHERE b.alias_norm = a.alias_norm AND b.company_id = $2
                   )
                """,
                source_id,
                target_id,
            )
            await conn.execute(
                "UPDATE company_alias SET company_id = $2 WHERE company_id = $1",
                source_id,
                target_id,
            )

            # The source's own name has to survive as an alias even though its
            # row will not: silver keeps seeing that spelling on the wire.
            source = await conn.fetchrow(
                "SELECT name, domain FROM company WHERE id = $1", source_id
            )
            await _write_aliases(conn, target_id, [source["name"]])

            # A logo is per-company and the target keeps its own; the source's
            # is only worth moving when the target has none.
            await conn.execute(
                """
                DELETE FROM company_logo
                 WHERE company_id = $1
                   AND EXISTS (SELECT 1 FROM company_logo WHERE company_id = $2)
                """,
                source_id,
                target_id,
            )
            await conn.execute(
                "UPDATE company_logo SET company_id = $2 WHERE company_id = $1",
                source_id,
                target_id,
            )

            await conn.execute("DELETE FROM company WHERE id = $1", source_id)
    return await get(pool, target_id)
