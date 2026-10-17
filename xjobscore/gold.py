"""Derived gold-layer passes over silver.

Every gold artifact is recoverable from ``job_posting``. Set-like artifacts
such as duplicate groups are rebuilt wholesale; per-posting enrichments are
stamped into ``job_posting`` once per silver content revision.
Nothing here may hold human judgement or other irrecoverable state.

Adding a gold concern means appending a ``Step``; improving an existing one
means changing its own pure helpers. The two axes stay independent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import asyncpg

from xjobscore import bioengineering, degrees, job_domain, skills, store, summaries

if TYPE_CHECKING:
    from asyncpg import Connection

logger = logging.getLogger(__name__)

__all__ = [
    "Counts",
    "Plan",
    "STEPS",
    "Step",
    "ats_identity",
    "description_hash",
    "degree_input_hash",
    "degree_payload",
    "focus_input_hash",
    "focus_payload",
    "job_domain_label",
    "normalize_title",
    "plan_dedup",
    "skills_input_hash",
    "skills_payload",
    "apply_incremental",
    "stale_posting_ids",
    "summary_input_hash",
    "summary_payload",
    "steps",
]


@dataclass(frozen=True, slots=True)
class Counts:
    groups: int = 0
    postings: int = 0
    skipped_keys: int = 0
    skipped_postings: int = 0

    def __add__(self, other: "Counts") -> "Counts":
        return Counts(
            groups=self.groups + other.groups,
            postings=self.postings + other.postings,
            skipped_keys=self.skipped_keys + other.skipped_keys,
            skipped_postings=self.skipped_postings + other.skipped_postings,
        )


@dataclass(frozen=True, slots=True)
class Step:
    """One rebuildable gold artifact.

    ``version`` is stamped into the artifact so a strategy change can be
    recomputed on its own without touching the rest of the layer.
    """

    name: str
    version: int
    rebuild: Callable[..., Awaitable[Counts]]
    atomic: bool = False


_DEADLOCK_BACKOFF_MAX_S = 2.0


async def _executemany_with_deadlock_retry(
    conn: Connection,
    command: str,
    args: Sequence[Sequence[Any]],
    *,
    step: str,
) -> None:
    """Retry one independently committed enrichment batch after a deadlock."""
    attempt = 0
    while True:
        try:
            await conn.executemany(command, args)
            return
        except asyncpg.DeadlockDetectedError:
            attempt += 1
            delay = min(0.05 * 2 ** (attempt - 1), _DEADLOCK_BACKOFF_MAX_S)
            logger.warning(
                "enrichment batch deadlocked; retrying name=%s attempt=%d delay=%.2fs",
                step,
                attempt,
                delay,
            )
            await asyncio.sleep(delay)


# TITLE NORMALIZATION ------------------------------------------------------
#
# The stored `job_type` opportunity kind remains a parse-time, vendor-specific
# field; see xjobscore/classify.py and the per-vendor rule tables. The Gold step
# named `job_type` below derives the orthogonal `job_domain` UI classification.

_CONFUSABLES = str.maketrans({"ꓟ": "M", "ꓡ": "L", "ꓣ": "R"})


def normalize_title(value: str) -> str:
    """Normalize a silver title for derived grouping and classification work."""
    value = unicodedata.normalize("NFKC", value.translate(_CONFUSABLES)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


# JOB DOMAIN ---------------------------------------------------------------

_JOB_DOMAIN_VERSION = 1
_JOB_DOMAIN_BATCH_SIZE = 1_000


def job_domain_label(title: str, *, quant_employer: bool = False) -> str:
    """Return the complete stored domain classification for one posting."""
    evidence = job_domain.classify(title, quant_employer=quant_employer)
    return evidence.label or job_domain.UNLABELED_FILTER


async def _quant_company_ids(
    conn: Connection, company_ids: Sequence[int] | None = None
) -> set[int]:
    """Compute the employer prior consumed by the canonical domain classifier.

    The prior is per company -- its own active postings, nothing global -- so
    restricting both counts to ``company_ids`` returns exactly the full set
    intersected with them. The incremental path passes its affected companies:
    over job_posting_company_idx that is an index read, where the unrestricted
    form regex-scans every active title on every changed poll.
    """
    scope = "" if company_ids is None else "AND company_id = ANY($3::bigint[])"
    args: list[Any] = [job_domain.QUANT_PATTERN, job_domain.FINANCE_FUNCTION_PATTERN]
    if company_ids is not None:
        args.append(list(company_ids))
    rows = await conn.fetch(
        f"""
        WITH company_postings AS MATERIALIZED (
            SELECT company_id, COUNT(*) AS posting_count
            FROM job_posting
            WHERE status = 'active' {scope}
            GROUP BY company_id
        ), company_titles AS MATERIALIZED (
            SELECT company_id, COUNT(*) AS quant_count
            FROM job_posting
            WHERE status = 'active' {scope}
              AND title ~* $1
              AND NOT (title ~* $2)
            GROUP BY company_id
        )
        SELECT cp.company_id AS id
        FROM company_postings cp
        JOIN company_titles ct ON ct.company_id = cp.company_id
        WHERE cp.posting_count >= 3
          AND ct.quant_count::numeric / cp.posting_count >= 0.40
        """,
        *args,
    )
    return {int(row["id"]) for row in rows}


async def _job_type_rebuild(
    conn: Connection,
    *,
    dry_run: bool = False,
) -> Counts:
    """Materialize Software/Finance/Unlabeled for every typed opportunity."""
    quant_company_ids = await _quant_company_ids(conn)
    last_id = 0
    enriched = 0
    while True:
        rows = await conn.fetch(
            """
            SELECT id, title, company_id, job_domain, job_domain_version
            FROM job_posting
            WHERE id > $1
              AND job_type IS NOT NULL
            ORDER BY id
            LIMIT $2
            """,
            last_id,
            _JOB_DOMAIN_BATCH_SIZE,
        )
        if not rows:
            break
        last_id = int(rows[-1]["id"])
        updates = []
        for row in rows:
            label = job_domain_label(
                row["title"],
                quant_employer=int(row["company_id"]) in quant_company_ids,
            )
            if (
                row.get("job_domain") == label
                and row.get("job_domain_version") == _JOB_DOMAIN_VERSION
            ):
                continue
            updates.append(
                (
                    int(row["id"]),
                    label,
                    _JOB_DOMAIN_VERSION,
                    row["title"],
                    int(row["company_id"]),
                )
            )
        if updates and not dry_run:
            await _executemany_with_deadlock_retry(
                conn,
                """
                UPDATE job_posting
                SET job_domain = $2,
                    job_domain_version = $3
                WHERE id = $1
                  AND title = $4
                  AND company_id = $5
                  AND job_type IS NOT NULL
                  AND (
                    job_domain IS DISTINCT FROM $2
                    OR job_domain_version IS DISTINCT FROM $3
                  )
                """,
                updates,
                step="job_type",
            )
        enriched += len(updates)
    return Counts(postings=enriched)


# SKILLS -------------------------------------------------------------------

_SKILLS_VERSION = 2
_SKILLS_BATCH_SIZE = 1_000


def skills_input_hash(description: str | None) -> str:
    """Fingerprint exactly the text consumed by the skill matcher."""
    return hashlib.sha256((description or "").encode("utf-8")).hexdigest()


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def skills_payload(description: str | None, *, parsed_at: datetime) -> dict[str, Any]:
    """The complete JSONB patch for one skill-enrichment result."""
    return {
        "skills": skills.extract(description or ""),
        "skills_meta": {
            "version": _SKILLS_VERSION,
            "taxonomy_hash": skills.taxonomy_hash(),
            "parsed_at": _utc_text(parsed_at),
            "input_hash": skills_input_hash(description),
        },
    }


async def _skills_rebuild(
    conn: Connection,
    *,
    dry_run: bool = False,
    parsed_at: datetime | None = None,
) -> Counts:
    """Enrich each posting whose skill taxonomy is out of date.

    Silver removes ``skills`` and ``skills_meta`` when the description changes,
    while the version and taxonomy hash make matcher or dictionary changes
    eligible without requiring manual database cleanup.
    """
    stamp = parsed_at or datetime.now(timezone.utc)
    taxonomy_hash = skills.taxonomy_hash()
    last_id = 0
    enriched = 0
    while True:
        rows = await conn.fetch(
            """
            SELECT id, description
            FROM job_posting
            WHERE id > $1
              AND (
                COALESCE(attributes #>> '{skills_meta,version}', '') <> $3
                OR COALESCE(attributes #>> '{skills_meta,taxonomy_hash}', '') <> $4
              )
            ORDER BY id
            LIMIT $2
            """,
            last_id,
            _SKILLS_BATCH_SIZE,
            str(_SKILLS_VERSION),
            taxonomy_hash,
        )
        if not rows:
            break
        last_id = int(rows[-1]["id"])
        updates = [
            (
                int(row["id"]),
                skills_payload(row["description"], parsed_at=stamp),
                row["description"] or "",
                str(_SKILLS_VERSION),
                taxonomy_hash,
            )
            for row in rows
        ]
        if not dry_run:
            await _executemany_with_deadlock_retry(
                conn,
                """
                UPDATE job_posting
                SET attributes = attributes || $2::jsonb
                WHERE id = $1
                  AND COALESCE(description, '') = $3
                  AND (
                    COALESCE(attributes #>> '{skills_meta,version}', '') <> $4
                    OR COALESCE(attributes #>> '{skills_meta,taxonomy_hash}', '') <> $5
                  )
                """,
                updates,
                step="skills",
            )
        enriched += len(updates)
    return Counts(postings=enriched)


# ROLE SUMMARIES -----------------------------------------------------------

_SUMMARIES_VERSION = 1
_SUMMARIES_BATCH_SIZE = 1_000


def summary_input_hash(title: str, description: str | None) -> str:
    """Fingerprint exactly the posting text consumed by the extractor."""
    value = f"{title}\n{description or ''}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def summary_payload(
    title: str,
    description: str | None,
    *,
    parsed_at: datetime,
) -> dict[str, Any]:
    """The complete JSONB patch for one deterministic summary result."""
    summary = summaries.extract(title, description)
    return {
        "summary": summary,
        "summary_meta": {
            "version": _SUMMARIES_VERSION,
            "generator": "extractive",
            "status": "ok" if summary else "insufficient_evidence",
            "parsed_at": _utc_text(parsed_at),
            "input_hash": summary_input_hash(title, description),
        },
    }


async def _summaries_rebuild(
    conn: Connection,
    *,
    dry_run: bool = False,
    parsed_at: datetime | None = None,
) -> Counts:
    """Summarize classified student roles once per extractor version."""
    stamp = parsed_at or datetime.now(timezone.utc)
    version = str(_SUMMARIES_VERSION)
    last_id = 0
    enriched = 0
    while True:
        rows = await conn.fetch(
            """
            SELECT id, title, description
            FROM job_posting
            WHERE id > $1
              AND job_type IS NOT NULL
              AND COALESCE(attributes -> 'summary_meta' ->> 'version', '') <> $3
            ORDER BY id
            LIMIT $2
            """,
            last_id,
            _SUMMARIES_BATCH_SIZE,
            version,
        )
        if not rows:
            break
        last_id = int(rows[-1]["id"])
        updates = [
            (
                int(row["id"]),
                summary_payload(row["title"], row["description"], parsed_at=stamp),
                row["title"],
                row["description"] or "",
                version,
            )
            for row in rows
        ]
        if not dry_run:
            await _executemany_with_deadlock_retry(
                conn,
                """
                UPDATE job_posting
                SET attributes = attributes || $2::jsonb
                WHERE id = $1
                  AND title = $3
                  AND COALESCE(description, '') = $4
                  AND job_type IS NOT NULL
                  AND COALESCE(attributes -> 'summary_meta' ->> 'version', '') <> $5
                """,
                updates,
                step="summaries",
            )
        enriched += len(updates)
    return Counts(postings=enriched)


# DEGREE FIELDS ------------------------------------------------------------

_DEGREES_VERSION = 6
_DEGREES_BATCH_SIZE = 1_000


def _degree_text(description: str | None) -> str:
    """The posting text a degree field's ``patterns`` may match: description alone.

    A title names the role, not its degree requirement, so a title mention can
    only reach the matcher through a field's explicit ``title_patterns``.
    """
    return description or ""


def degree_input_hash(title: str, description: str | None) -> str:
    """Fingerprint exactly the posting text consumed by the degree matcher."""
    return hashlib.sha256(f"{title}\n{description or ''}".encode("utf-8")).hexdigest()


def degree_payload(
    title: str,
    description: str | None,
    *,
    parsed_at: datetime,
) -> dict[str, Any]:
    """The complete JSONB patch for one degree-field enrichment result.

    The scalar taxonomy revision remains in ``job_posting.degree_version`` so
    the staleness scan stays indexable. ``degree_meta.input_hash`` records the
    exact title and description used by the matcher; the title is included
    because a small set of fields deliberately declares ``title_patterns``.
    """
    return {
        "degree": degrees.extract(_degree_text(description), title=title),
        "degree_meta": {
            "version": _DEGREES_VERSION,
            "parsed_at": _utc_text(parsed_at),
            "input_hash": degree_input_hash(title, description),
        },
    }


async def _degrees_rebuild(
    conn: Connection,
    *,
    dry_run: bool = False,
    parsed_at: datetime | None = None,
) -> Counts:
    """Enrich each posting whose degree-field taxonomy is out of date."""
    stamp = parsed_at or datetime.now(timezone.utc)
    last_id = 0
    enriched = 0
    while True:
        # "Not the current version" is spelled as three range predicates rather
        # than <> or IS DISTINCT FROM because neither of those is indexable:
        # the planner answers < / > / IS NULL from job_posting_degree_version_idx
        # under a BitmapOr, but falls back to a full parallel seq scan for <>.
        # Measured on 292k rows: 0.12 ms here against 84 ms for the <> form.
        rows = await conn.fetch(
            """
            SELECT id, title, description
            FROM job_posting
            WHERE id > $1
              AND (
                degree_version IS NULL
                OR degree_version < $3
                OR degree_version > $3
              )
            ORDER BY id
            LIMIT $2
            """,
            last_id,
            _DEGREES_BATCH_SIZE,
            _DEGREES_VERSION,
        )
        if not rows:
            break
        last_id = int(rows[-1]["id"])
        updates = [
            (
                int(row["id"]),
                degree_payload(row["title"], row["description"], parsed_at=stamp),
                row["title"],
                row["description"] or "",
                _DEGREES_VERSION,
                degree_input_hash(row["title"], row["description"]),
            )
            for row in rows
        ]
        if not dry_run:
            await _executemany_with_deadlock_retry(
                conn,
                """
                -- Single-row lookup by primary key, so this guard is a filter
                -- and not an access path: IS DISTINCT FROM is the clearer
                -- spelling of the same condition the SELECT splits by range.
                UPDATE job_posting
                SET attributes = attributes || $2::jsonb,
                    degree_version = $5
                WHERE id = $1
                  AND title = $3
                  AND COALESCE(description, '') = $4
                  AND degree_version IS DISTINCT FROM $5
                  -- Both operands cast: asyncpg declares no parameter
                  -- types, so a bare `$2 #>> '{...}'` leaves Postgres
                  -- choosing between every `unknown #>> unknown`
                  -- candidate and refusing at prepare time. The SET
                  -- clause's own `$2::jsonb` does not settle it -- a
                  -- parameter's type is resolved once, for the whole
                  -- statement, and an explicit cast there says nothing
                  -- about this operand.
                  AND $2::jsonb #>> '{degree_meta,input_hash}'::text[] = $6
                """,
                updates,
                step="degrees",
            )
        enriched += len(updates)
    return Counts(postings=enriched)


# FOCUS CHIPS --------------------------------------------------------------
#
# MedTech / Regulatory / BioTech chips for the OneShot feed, which shows typed
# postings only. Stored rather than computed in SQL: the rules need ~45 regular
# expressions per posting. The version stamp folds in a fingerprint of the
# employer registry, so editing it re-enriches every typed posting.

_FOCUS_BATCH_SIZE = 1_000


def focus_input_hash(title: str, description: str | None, company: str | None) -> str:
    """Fingerprint the posting text and employer name consumed by the rules."""
    value = f"{title}\n{company or ''}\n{description or ''}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def focus_payload(
    title: str,
    description: str | None,
    company: str | None,
    *,
    parsed_at: datetime,
) -> dict[str, Any]:
    """The complete JSONB patch for one posting's focus chips."""
    evidence = bioengineering.classify(title, description, company=company or "")
    return {
        "focus": list(evidence.chips),
        "focus_meta": {
            "version": bioengineering.focus_version(),
            "reasons": dict(evidence.reasons),
            "parsed_at": _utc_text(parsed_at),
            "input_hash": focus_input_hash(title, description, company),
        },
    }


_FOCUS_UPDATE = """
    UPDATE job_posting
    SET attributes = attributes || $2::jsonb
    WHERE id = $1
      AND title = $3
      AND COALESCE(description, '') = $4
      AND company_id = $5
      AND job_type IS NOT NULL
      AND COALESCE(attributes #>> '{focus_meta,version}', '') <> $6
"""


async def _focus_rebuild(
    conn: Connection,
    *,
    dry_run: bool = False,
    parsed_at: datetime | None = None,
) -> Counts:
    """Stamp focus chips on every typed posting whose rules or registry changed."""
    stamp = parsed_at or datetime.now(timezone.utc)
    version = bioengineering.focus_version()
    last_id = 0
    enriched = 0
    while True:
        rows = await conn.fetch(
            """
            SELECT j.id, j.title, j.description, j.company_id, c.name AS company
            FROM job_posting j
            JOIN company c ON c.id = j.company_id
            WHERE j.id > $1
              AND j.job_type IS NOT NULL
              AND COALESCE(j.attributes #>> '{focus_meta,version}', '') <> $3
            ORDER BY j.id
            LIMIT $2
            """,
            last_id,
            _FOCUS_BATCH_SIZE,
            version,
        )
        if not rows:
            break
        last_id = int(rows[-1]["id"])
        updates = [
            (
                int(row["id"]),
                focus_payload(
                    row["title"], row["description"], row["company"], parsed_at=stamp
                ),
                row["title"],
                row["description"] or "",
                int(row["company_id"]),
                version,
            )
            for row in rows
        ]
        if not dry_run:
            await _executemany_with_deadlock_retry(
                conn, _FOCUS_UPDATE, updates, step="focus"
            )
        enriched += len(updates)
    return Counts(postings=enriched)


# DEDUP --------------------------------------------------------------------
#
# Stage one generates *candidates and evidence*, not verdicts. Prefer native
# posting identity when a URL exposes it; weaker fallbacks are deliberately
# isolated so they can never merge two different known requisitions.

_NAMESPACE = uuid.UUID("9f8b2c14-6d3a-5e7f-8a1b-2c3d4e5f6071")
_STRATEGY_VERSION = 5
_KEYER = "ats_requisition_then_apply"
_PRIMARY_SELECTOR = "direct_ats_then_posted_then_first_seen"

# A fallback key shared by more members than this is a placeholder, not an
# identity, and the group would be too large to adjudicate by hand. Extracted
# ATS identities are authoritative and are not subject to this safety cap.
_MAX_GROUP = 10

# Enough description to be distinctive without depending on the boilerplate
# tail that boards inject.
_DESCRIPTION_WORDS = 500
_DESCRIPTION_MIN_WORDS = 20

_ATS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("greenhouse", re.compile(r"^(?:job-boards|boards)\.greenhouse\.io/([^/]+)/jobs/(\d+)")),
    ("lever", re.compile(r"^jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})")),
    ("ashby", re.compile(r"^jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})")),
    ("smartrecruiters", re.compile(r"^jobs\.smartrecruiters\.com/([^/]+)/(\d+)")),
    ("icims", re.compile(r"^([a-z0-9-]+)\.icims\.com/jobs/(\d+)")),
    ("workday", re.compile(r"^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com/.*/job/.*?_([a-z0-9-]+)$")),
)


def _norm_text(value: str | None) -> str | None:
    """Fold to bare alphanumerics, decomposing accents rather than dropping them.

    NFKC alone leaves ``é`` precomposed, which the character class below would
    delete outright -- "San José" becoming "san jos".
    """
    if not value:
        return None
    decomposed = unicodedata.normalize("NFKD", value)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", folded).strip() or None


def ats_identity(*urls: str | None) -> str | None:
    """Resolve a posting URL to ``ats:token/native_id``; ``None`` when unparseable.

    Deliberately fails closed. A careers landing page shared by every posting at
    a company must yield no identity rather than one that merges them all --
    this is extraction, not URL string comparison, and the difference is the
    whole safety property.
    """
    for url in urls:
        if not url:
            continue
        parts = urlsplit(url.strip())
        host = re.sub(r"^www\.", "", parts.netloc.casefold())
        target = f"{host}{parts.path.casefold()}".rstrip("/")
        for name, pattern in _ATS_PATTERNS:
            found = pattern.search(target)
            if found is not None:
                return f"{name}:{found.group(1)}/{found.group(2)}"
    return None


def description_hash(value: str | None) -> str | None:
    """Hash a stable prefix of the description; ``None`` when too short to be distinctive."""
    if not value:
        return None
    words = value.casefold().split()[:_DESCRIPTION_WORDS]
    if len(words) < _DESCRIPTION_MIN_WORDS:
        return None
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()


def _member_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "posting_id": row["id"],
        "source": row["source"],
        "location": _norm_text(row["location_text"]),
        "ats_identity": ats_identity(row["apply_url"], row["source_url"]),
        "description_hash": description_hash(row.get("description")),
    }


def _is_direct_ats_posting(row: Mapping[str, Any]) -> bool:
    """Whether the row came from the ATS that owns its requisition identity."""
    identity = ats_identity(row["apply_url"], row["source_url"])
    if identity is None:
        return False
    provider = identity.partition(":")[0]
    source = (row["source"] or "").partition(":")[0].casefold()
    # Listing adapters are still first-party views of their underlying ATS.
    return source.removesuffix("-list") == provider


def _primary_rank(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Rank primary candidates without using database ID as a tie-breaker."""
    posted_at = row.get("posted_at")
    first_seen_at = row.get("first_seen_at")
    return (
        _is_direct_ats_posting(row),
        (posted_at is not None, posted_at),
        (first_seen_at is not None, first_seen_at),
    )


def _select_primary(bucket: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Return the sole best candidate, or no primary when the best rank ties."""
    ranked = [(_primary_rank(row), row) for row in bucket]
    best_rank = max(rank for rank, _row in ranked)
    winners = [row for rank, row in ranked if rank == best_rank]
    return winners[0] if len(winners) == 1 else None


@dataclass(frozen=True, slots=True)
class Plan:
    groups: list[dict[str, Any]]
    members: list[dict[str, Any]]
    counts: Counts


def _with_description_hashes(
    plan: Plan, descriptions: Mapping[int, str | None]
) -> Plan:
    """Attach heavyweight evidence after lightweight grouping has finished."""
    groups: list[dict[str, Any]] = []
    for group in plan.groups:
        evidence = group["evidence"]
        members = [
            {
                **member,
                "description_hash": description_hash(
                    descriptions.get(int(member["posting_id"]))
                ),
            }
            for member in evidence["members"]
        ]
        groups.append({**group, "evidence": {**evidence, "members": members}})
    return Plan(groups=groups, members=plan.members, counts=plan.counts)


def _dedup_key(row: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return the strongest safe identity available for one posting.

    A known ATS requisition wins even when source presentations disagree on
    title, location, or URL decoration. An unparseable but exact apply URL is a
    weaker fallback. Without either URL identity, leave the posting ungrouped:
    matching titles and locations can describe distinct openings.
    """
    company_id = row["company_id"]
    if company_id is None:
        return None

    identity = ats_identity(row["apply_url"], row["source_url"])
    if identity:
        parts = ["ats_requisition", company_id, identity]
        return "ats_requisition", json.dumps(parts, separators=(",", ":"))

    apply_url = (row["apply_url"] or "").strip()
    if apply_url:
        parts = ["exact_apply_url", company_id, apply_url]
        return "exact_apply_url", json.dumps(parts, separators=(",", ":"))

    return None


def plan_dedup(rows: Sequence[Mapping[str, Any]]) -> Plan:
    """Group silver rows by requisition identity with cautious fallbacks.

    Pure: the whole strategy lives here and is testable without a database.
    """
    buckets: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        key = _dedup_key(row)
        if key is None:
            continue
        buckets.setdefault(key, []).append(row)

    groups: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    counts = Counts()
    for (signal, key), bucket in buckets.items():
        if len(bucket) < 2:
            continue
        if signal != "ats_requisition" and len(bucket) > _MAX_GROUP:
            counts += Counts(skipped_keys=1, skipped_postings=len(bucket))
            continue
        primary = _select_primary(bucket)
        group_id = str(uuid.uuid5(_NAMESPACE, f"{_KEYER}:{_STRATEGY_VERSION}:{key}"))
        groups.append(
            {
                "id": group_id,
                "primary_posting_id": primary["id"] if primary is not None else None,
                # Stage one never decides. Promotion to confirmed/rejected is a
                # later strategy working from the evidence recorded here.
                "state": "pending",
                "evidence": {
                    "strategy_version": _STRATEGY_VERSION,
                    "keyer": _KEYER,
                    "signal": signal,
                    "key": key,
                    "primary_selector": _PRIMARY_SELECTOR,
                    "members": [_member_evidence(row) for row in bucket],
                },
            }
        )
        members.extend({"posting_id": row["id"], "group_id": group_id} for row in bucket)
        counts += Counts(groups=1, postings=len(bucket))
    return Plan(groups=groups, members=members, counts=counts)


async def _dedup_rebuild(conn: Connection, *, dry_run: bool = False) -> Counts:
    rows = await store.dedup_candidates(conn)
    plan = plan_dedup(rows)
    description_rows = await store.dedup_descriptions(
        conn, [int(member["posting_id"]) for member in plan.members]
    )
    plan = _with_description_hashes(
        plan,
        {int(row["id"]): row["description"] for row in description_rows},
    )
    if plan.counts.skipped_keys:
        logger.info(
            "dedup dropped over-large keys keys=%d postings=%d cap=%d",
            plan.counts.skipped_keys,
            plan.counts.skipped_postings,
            _MAX_GROUP,
        )
    if not dry_run:
        await store.replace_dup_groups(conn, groups=plan.groups, members=plan.members)
    return plan.counts


# INCREMENTAL APPLICATION --------------------------------------------------

async def _incremental_job_type(
    conn: Connection,
    posting_ids: Sequence[int],
    company_ids: Sequence[int],
    *,
    dry_run: bool,
) -> Counts:
    affected = {int(value) for value in company_ids}
    if posting_ids:
        affected.update(
            int(row["company_id"])
            for row in await conn.fetch(
                "SELECT DISTINCT company_id FROM job_posting WHERE id = ANY($1::bigint[])",
                list(posting_ids),
            )
        )
    if not affected:
        return Counts()

    quant_company_ids = await _quant_company_ids(conn, sorted(affected))
    rows = await conn.fetch(
        """
        SELECT id, title, company_id, job_domain, job_domain_version
        FROM job_posting
        WHERE company_id = ANY($1::bigint[]) AND job_type IS NOT NULL
        ORDER BY id
        """,
        sorted(affected),
    )
    updates = []
    for row in rows:
        label = job_domain_label(
            row["title"], quant_employer=int(row["company_id"]) in quant_company_ids
        )
        if row["job_domain"] == label and row["job_domain_version"] == _JOB_DOMAIN_VERSION:
            continue
        updates.append(
            (int(row["id"]), label, _JOB_DOMAIN_VERSION, row["title"], int(row["company_id"]))
        )
    if updates and not dry_run:
        await _executemany_with_deadlock_retry(
            conn,
            """
            UPDATE job_posting
            SET job_domain = $2, job_domain_version = $3
            WHERE id = $1 AND title = $4 AND company_id = $5
              AND job_type IS NOT NULL
            """,
            updates,
            step="job_type",
        )
    return Counts(postings=len(updates))


async def _incremental_skills(
    conn: Connection, posting_ids: Sequence[int], *, dry_run: bool
) -> Counts:
    if not posting_ids:
        return Counts()
    taxonomy_hash = skills.taxonomy_hash()
    rows = await conn.fetch(
        "SELECT id, description, attributes FROM job_posting WHERE id = ANY($1::bigint[])",
        list(posting_ids),
    )
    stamp = datetime.now(timezone.utc)
    updates = []
    for row in rows:
        meta = dict(row["attributes"] or {}).get("skills_meta") or {}
        if str(meta.get("version", "")) == str(_SKILLS_VERSION) and meta.get(
            "taxonomy_hash"
        ) == taxonomy_hash:
            continue
        updates.append(
            (
                int(row["id"]),
                skills_payload(row["description"], parsed_at=stamp),
                row["description"] or "",
            )
        )
    if updates and not dry_run:
        await _executemany_with_deadlock_retry(
            conn,
            """
            UPDATE job_posting SET attributes = attributes || $2::jsonb
            WHERE id = $1 AND COALESCE(description, '') = $3
            """,
            updates,
            step="skills",
        )
    return Counts(postings=len(updates))


async def _incremental_degrees(
    conn: Connection, posting_ids: Sequence[int], *, dry_run: bool
) -> Counts:
    if not posting_ids:
        return Counts()
    rows = await conn.fetch(
        """
        SELECT id, title, description, degree_version
        FROM job_posting WHERE id = ANY($1::bigint[])
        """,
        list(posting_ids),
    )
    stamp = datetime.now(timezone.utc)
    updates = []
    for row in rows:
        if row["degree_version"] == _DEGREES_VERSION:
            continue
        payload = degree_payload(row["title"], row["description"], parsed_at=stamp)
        updates.append(
            (
                int(row["id"]), payload, row["title"], row["description"] or "",
                _DEGREES_VERSION,
            )
        )
    if updates and not dry_run:
        await _executemany_with_deadlock_retry(
            conn,
            """
            UPDATE job_posting
            SET attributes = attributes || $2::jsonb, degree_version = $5
            WHERE id = $1 AND title = $3 AND COALESCE(description, '') = $4
            """,
            updates,
            step="degrees",
        )
    return Counts(postings=len(updates))


async def _incremental_summaries(
    conn: Connection, posting_ids: Sequence[int], *, dry_run: bool
) -> Counts:
    if not posting_ids:
        return Counts()
    rows = await conn.fetch(
        """
        SELECT id, title, description, attributes
        FROM job_posting
        WHERE id = ANY($1::bigint[]) AND job_type IS NOT NULL
        """,
        list(posting_ids),
    )
    stamp = datetime.now(timezone.utc)
    updates = []
    for row in rows:
        meta = dict(row["attributes"] or {}).get("summary_meta") or {}
        if str(meta.get("version", "")) == str(_SUMMARIES_VERSION):
            continue
        updates.append(
            (
                int(row["id"]),
                summary_payload(row["title"], row["description"], parsed_at=stamp),
                row["title"],
                row["description"] or "",
            )
        )
    if updates and not dry_run:
        await _executemany_with_deadlock_retry(
            conn,
            """
            UPDATE job_posting SET attributes = attributes || $2::jsonb
            WHERE id = $1 AND title = $3 AND COALESCE(description, '') = $4
              AND job_type IS NOT NULL
            """,
            updates,
            step="summaries",
        )
    return Counts(postings=len(updates))


async def _incremental_focus(
    conn: Connection, posting_ids: Sequence[int], *, dry_run: bool
) -> Counts:
    if not posting_ids:
        return Counts()
    rows = await conn.fetch(
        """
        SELECT j.id, j.title, j.description, j.company_id, j.attributes, c.name AS company
        FROM job_posting j
        JOIN company c ON c.id = j.company_id
        WHERE j.id = ANY($1::bigint[]) AND j.job_type IS NOT NULL
        """,
        list(posting_ids),
    )
    version = bioengineering.focus_version()
    stamp = datetime.now(timezone.utc)
    updates = []
    for row in rows:
        meta = dict(row["attributes"] or {}).get("focus_meta") or {}
        if str(meta.get("version", "")) == version:
            continue
        updates.append(
            (
                int(row["id"]),
                focus_payload(
                    row["title"], row["description"], row["company"], parsed_at=stamp
                ),
                row["title"],
                row["description"] or "",
                int(row["company_id"]),
                version,
            )
        )
    if updates and not dry_run:
        await _executemany_with_deadlock_retry(
            conn, _FOCUS_UPDATE, updates, step="focus"
        )
    return Counts(postings=len(updates))


async def apply_incremental(
    conn: Connection,
    name: str,
    posting_ids: Sequence[int],
    company_ids: Sequence[int] = (),
    *,
    dry_run: bool = False,
) -> Counts:
    """Apply one posting-local Gold step to a Silver change set.

    Job-domain classification deliberately expands to every typed posting at
    an affected company because its quant-employer prior is company-wide.
    """
    posting_ids = tuple(dict.fromkeys(int(value) for value in posting_ids))
    company_ids = tuple(dict.fromkeys(int(value) for value in company_ids))
    if name == "job_type":
        return await _incremental_job_type(
            conn, posting_ids, company_ids, dry_run=dry_run
        )
    if name == "skills":
        return await _incremental_skills(conn, posting_ids, dry_run=dry_run)
    if name == "degrees":
        return await _incremental_degrees(conn, posting_ids, dry_run=dry_run)
    if name == "summaries":
        return await _incremental_summaries(conn, posting_ids, dry_run=dry_run)
    if name == "focus":
        return await _incremental_focus(conn, posting_ids, dry_run=dry_run)
    raise ValueError(f"{name!r} is not an incremental Gold step")


async def stale_posting_ids(
    conn: Connection, name: str, *, limit: int = 1_000
) -> list[int]:
    """Return one bounded, deterministic batch still needing a Gold step."""
    if limit < 1:
        raise ValueError("limit must be positive")
    if name == "job_type":
        query = """
            SELECT id FROM job_posting
            WHERE job_type IS NOT NULL
              AND job_domain_version IS DISTINCT FROM $2
            ORDER BY id LIMIT $1
        """
        rows = await conn.fetch(query, limit, _JOB_DOMAIN_VERSION)
    elif name == "skills":
        query = """
            SELECT id FROM job_posting
            WHERE COALESCE(attributes #>> '{skills_meta,version}', '') <> $2
               OR COALESCE(attributes #>> '{skills_meta,taxonomy_hash}', '') <> $3
            ORDER BY id LIMIT $1
        """
        rows = await conn.fetch(query, limit, str(_SKILLS_VERSION), skills.taxonomy_hash())
    elif name == "degrees":
        query = """
            SELECT id FROM job_posting
            WHERE degree_version IS DISTINCT FROM $2
            ORDER BY id LIMIT $1
        """
        rows = await conn.fetch(query, limit, _DEGREES_VERSION)
    elif name == "summaries":
        query = """
            SELECT id FROM job_posting
            WHERE job_type IS NOT NULL
              AND COALESCE(attributes #>> '{summary_meta,version}', '') <> $2
            ORDER BY id LIMIT $1
        """
        rows = await conn.fetch(query, limit, str(_SUMMARIES_VERSION))
    elif name == "focus":
        query = """
            SELECT id FROM job_posting
            WHERE job_type IS NOT NULL
              AND COALESCE(attributes #>> '{focus_meta,version}', '') <> $2
            ORDER BY id LIMIT $1
        """
        rows = await conn.fetch(query, limit, bioengineering.focus_version())
    else:
        raise ValueError(f"{name!r} is not an incremental Gold step")
    return [int(row["id"]) for row in rows]


# REGISTRY -----------------------------------------------------------------
#
# Steps are independent and order does not matter. Add ``requires`` and sort
# only once a step genuinely reads another's output.

STEPS: tuple[Step, ...] = (
    Step("job_type", _JOB_DOMAIN_VERSION, _job_type_rebuild),
    Step("skills", _SKILLS_VERSION, _skills_rebuild),
    Step("degrees", _DEGREES_VERSION, _degrees_rebuild),
    Step("summaries", _SUMMARIES_VERSION, _summaries_rebuild),
    Step("focus", bioengineering.FOCUS_VERSION, _focus_rebuild),
    Step("dedup", _STRATEGY_VERSION, _dedup_rebuild, atomic=True),
)


def steps(only: str | None = None) -> tuple[Step, ...]:
    """Select every step, or the single named one."""
    if not only:
        return STEPS
    selected = tuple(step for step in STEPS if step.name == only)
    if not selected:
        known = ", ".join(step.name for step in STEPS)
        raise ValueError(f"unknown enrichment {only!r}; known enrichments: {known}")
    return selected
