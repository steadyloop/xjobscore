"""Derived gold-layer passes over silver.

Every gold artifact is recoverable from ``job_posting``. Set-like artifacts
such as duplicate groups are rebuilt wholesale; per-posting enrichments are
stamped into ``job_posting.attributes`` once per silver content revision.
Nothing here may hold human judgement or other irrecoverable state.

Adding a gold concern means appending a ``Step``; improving an existing one
means changing its own pure helpers. The two axes stay independent.
"""

from __future__ import annotations

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

from xjobscore import degrees, skills, store

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
    "degree_fields_input_hash",
    "degree_fields_payload",
    "normalize_title",
    "plan_dedup",
    "skills_input_hash",
    "skills_payload",
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


# TITLE NORMALIZATION ------------------------------------------------------
#
# `job_type` is no longer derived here. It is a parse-time, vendor-specific
# field on `job_posting`; see xjobscore/classify.py and the per-vendor rule
# tables in the adapters. Only the title normalizer survives, because dedup
# and the inspector's parse preview both consume it.

_CONFUSABLES = str.maketrans({"ꓟ": "M", "ꓡ": "L", "ꓣ": "R"})


def normalize_title(value: str) -> str:
    """Normalize a silver title for derived grouping and classification work."""
    value = unicodedata.normalize("NFKC", value.translate(_CONFUSABLES)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


# SKILLS -------------------------------------------------------------------

_SKILLS_VERSION = 1
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
    """Enrich each unstamped silver row once, in bounded batches.

    Silver removes ``skills`` and ``skills_meta`` when its canonical content
    hash changes, making that posting eligible again without making every gold
    invocation rescan its description in Python.
    """
    stamp = parsed_at or datetime.now(timezone.utc)
    last_id = 0
    enriched = 0
    while True:
        rows = await conn.fetch(
            """
            SELECT id, description
            FROM job_posting
            WHERE id > $1
              AND NOT (attributes ? 'skills_meta')
            ORDER BY id
            LIMIT $2
            """,
            last_id,
            _SKILLS_BATCH_SIZE,
        )
        if not rows:
            break
        last_id = int(rows[-1]["id"])
        updates = [
            (
                int(row["id"]),
                skills_payload(row["description"], parsed_at=stamp),
                row["description"] or "",
            )
            for row in rows
        ]
        if not dry_run:
            await conn.executemany(
                """
                UPDATE job_posting
                SET attributes = attributes || $2::jsonb
                WHERE id = $1
                  AND COALESCE(description, '') = $3
                  AND NOT (attributes ? 'skills_meta')
                """,
                updates,
            )
        enriched += len(updates)
    return Counts(postings=enriched)


# DEGREE FIELDS ------------------------------------------------------------

_DEGREES_VERSION = 1
_DEGREES_BATCH_SIZE = 1_000


def degree_fields_input_hash(description: str | None) -> str:
    """Fingerprint exactly the text consumed by the degree-field matcher."""
    return hashlib.sha256((description or "").encode("utf-8")).hexdigest()


def degree_fields_payload(description: str | None, *, parsed_at: datetime) -> dict[str, Any]:
    """The complete JSONB patch for one degree-field enrichment result."""
    return {
        "degree_fields": degrees.extract(description or ""),
        "degree_fields_meta": {
            "version": _DEGREES_VERSION,
            "parsed_at": _utc_text(parsed_at),
            "input_hash": degree_fields_input_hash(description),
        },
    }


async def _degrees_rebuild(
    conn: Connection,
    *,
    dry_run: bool = False,
    parsed_at: datetime | None = None,
) -> Counts:
    """Enrich each unstamped silver row with mentioned degree fields."""
    stamp = parsed_at or datetime.now(timezone.utc)
    last_id = 0
    enriched = 0
    while True:
        rows = await conn.fetch(
            """
            SELECT id, description
            FROM job_posting
            WHERE id > $1
              AND NOT (attributes ? 'degree_fields_meta')
            ORDER BY id
            LIMIT $2
            """,
            last_id,
            _DEGREES_BATCH_SIZE,
        )
        if not rows:
            break
        last_id = int(rows[-1]["id"])
        updates = [
            (
                int(row["id"]),
                degree_fields_payload(row["description"], parsed_at=stamp),
                row["description"] or "",
            )
            for row in rows
        ]
        if not dry_run:
            await conn.executemany(
                """
                UPDATE job_posting
                SET attributes = attributes || $2::jsonb
                WHERE id = $1
                  AND COALESCE(description, '') = $3
                  AND NOT (attributes ? 'degree_fields_meta')
                """,
                updates,
            )
        enriched += len(updates)
    return Counts(postings=enriched)


# DEDUP --------------------------------------------------------------------
#
# Stage one generates *candidates and evidence*, not verdicts. The key is the
# cheapest signal that fires on every posting; the precise signals are recorded
# as evidence so their real coverage can be measured before anything is keyed
# on them.

_NAMESPACE = uuid.UUID("9f8b2c14-6d3a-5e7f-8a1b-2c3d4e5f6071")
_STRATEGY_VERSION = 2
_KEYER = "company_role_location"

# A key shared by more members than this is a placeholder, not an identity, and
# the group would be too large to adjudicate by hand.
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
        "ats_identity": ats_identity(row["source_url"], row["apply_url"]),
        "description_hash": description_hash(row["description"]),
    }


@dataclass(frozen=True, slots=True)
class Plan:
    groups: list[dict[str, Any]]
    members: list[dict[str, Any]]
    counts: Counts


def plan_dedup(rows: Sequence[Mapping[str, Any]]) -> Plan:
    """Group silver rows by ``(company_id, normalized title, location)``.

    Pure: the whole strategy lives here and is testable without a database.
    """
    buckets: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row["company_id"] is None:
            continue
        role = normalize_title(row["title"] or "")
        if not role:
            continue
        # ``None`` is intentional: an absent location is a valid component of
        # the key rather than an error or a reason to discard the posting.
        location = _norm_text(row["location_text"])
        key = json.dumps([row["company_id"], role, location], separators=(",", ":"))
        buckets.setdefault(key, []).append(row)

    groups: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    counts = Counts()
    for key, bucket in buckets.items():
        if len(bucket) < 2:
            continue
        if len(bucket) > _MAX_GROUP:
            counts += Counts(skipped_keys=1, skipped_postings=len(bucket))
            continue
        group_id = str(uuid.uuid5(_NAMESPACE, f"{_KEYER}:{_STRATEGY_VERSION}:{key}"))
        groups.append(
            {
                "id": group_id,
                # Stage one never decides. Promotion to confirmed/rejected is a
                # later strategy working from the evidence recorded here.
                "state": "pending",
                "evidence": {
                    "strategy_version": _STRATEGY_VERSION,
                    "keyer": _KEYER,
                    "key": key,
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


# REGISTRY -----------------------------------------------------------------
#
# Steps are independent and order does not matter. Add ``requires`` and sort
# only once a step genuinely reads another's output.

STEPS: tuple[Step, ...] = (
    Step("skills", _SKILLS_VERSION, _skills_rebuild),
    Step("degrees", _DEGREES_VERSION, _degrees_rebuild),
    Step("dedup", _STRATEGY_VERSION, _dedup_rebuild),
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
