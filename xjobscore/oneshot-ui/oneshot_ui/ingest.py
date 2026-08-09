"""Manual fetch result -> reversible unified-board read model."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import re
import sqlite3
from typing import Any

from xjobscore.records import Fact, Feed, Label, Posting

from .classifier import (
    EligibilityInput,
    EligibilityVerdict,
    evaluate_eligibility,
    extract_stack,
    has_student_signal,
)
from .db import Database
from .dedupe import (
    DEDUPE_VERSION,
    DedupeDecision,
    DedupeOutcome,
    PostingIdentifier,
    PostingIdentity,
    build_identifiers,
    normalize_title,
    safe_cluster_decision,
)
from .lifecycle import (
    LIFECYCLE_VERSION,
    Authority,
    LifecycleState,
    ObservationLifecycle,
    Snapshot,
    SnapshotPolicy,
    reconcile_snapshots,
    resolve_entity,
)
from .scheduler import ManualFetchResult
from .scoring import ScoreBand, ScoreInput, WindowFit, route_for_band, score_opportunity
from .taxonomy import PRIMARY_OPPORTUNITY_TYPES, classify_opportunity
from .util import content_hash, dumps, iso, loads, new_id, normalize_name, public_id, utc_now


@dataclass(frozen=True, slots=True)
class IngestCounts:
    postings: int = 0
    created_entities: int = 0
    changed_revisions: int = 0
    exact_links: int = 0
    review_candidates: int = 0
    related_candidates: int = 0
    labels: int = 0
    facts: int = 0


@dataclass(frozen=True, slots=True)
class PreparedPosting:
    posting: Posting
    company_id: str
    company_name: str
    observation_id: str
    identity: PostingIdentity
    identifiers: tuple[PostingIdentifier, ...]
    document: dict[str, Any]
    content_hash: str
    opportunity_slug: str
    opportunity_confidence: float
    opportunity_evidence: tuple[str, ...]
    opportunity_version: str
    stack: tuple[dict[str, str], ...]
    stack_version: str
    eligibility: str
    eligibility_reasons: tuple[str, ...]
    eligibility_version: str
    score: int
    band: str
    score_components: tuple[dict[str, Any], ...]
    score_version: str
    score_route: dict[str, Any]
    resume_variant: str
    authority: str
    status: str


def _years(text: str) -> float | None:
    values = [
        float(match.group(1))
        for match in re.finditer(
            r"\b(?:minimum(?:\s+of)?\s+)?(\d+(?:\.\d+)?)\+?\s+years?(?:\s+of)?\s+(?:professional\s+)?experience\b",
            text,
            re.I,
        )
    ]
    return max(values) if values else None


def _role_family(title: str) -> bool:
    if re.search(
        r"\b(?:sales|support|help\s*desk|manual\s+qa|recruit|marketing|account(?:\s+executive)?|"
        r"product\s+manager|designer|technician|nurse|clinical|legal|finance\s+operations)\b",
        title,
        re.I,
    ):
        return False
    if re.search(
        r"\b(?:mechanical|civil|chemical|biomedical|manufacturing|industrial|"
        r"petroleum|mining|geotechnical|structural|nuclear|clinical|biology|"
        r"biologist|chemist|wet\s*lab|materials science)\b",
        title,
        re.I,
    ) and not re.search(
        r"\b(?:software|computer|data|machine learning|ml|ai|embedded|firmware|"
        r"controls software|computational|quant|statistics|mathematics)\b",
        title,
        re.I,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:software|developer|engineer|engineering|machine learning|ml|ai|quant|"
            r"data|infrastructure|systems?|compiler|research|scientist|computational|mathematics?|"
            r"statistics?|security|backend|platform|gpu|database|runtime)\b",
            title,
            re.I,
        )
    )


def _graduation_eligible(text: str, candidate_year: int = 2027) -> bool:
    """Reject only an explicit range that cannot contain the profile year."""
    signals = tuple(
        re.finditer(
            r"\b(?:graduat\w*|class of|degree completion|expected completion)\b",
            text,
            re.I,
        )
    )
    if not signals:
        return True
    years: set[int] = set()
    for signal in signals:
        window = text[max(0, signal.start() - 100) : signal.end() + 140]
        years.update(
            int(year) for year in re.findall(r"\b20(?:2[4-9]|3[0-2])\b", window)
        )
    if not years:
        return True
    return candidate_year in years or (min(years) <= candidate_year <= max(years))


def _location_compatible(
    location: str | None,
    opportunity_slug: str,
    *,
    home_location: str = "San Diego, CA",
    relocate_for_internship: bool = True,
) -> bool:
    value = (location or "").casefold()
    if not value or any(token in value for token in ("remote", "united states", "multiple")):
        return True
    home_city = home_location.split(",", 1)[0].strip().casefold()
    if home_city and home_city in value:
        return True
    return relocate_for_internship and opportunity_slug in {
        "spring_internship",
        "summer_internship",
        "fall_internship",
        "student_researcher",
        "student_role",
        "fellowship",
    }


def _segment(title: str, stack: tuple[str, ...]) -> int:
    text = " ".join((title, *stack)).casefold()
    if any(token in text for token in ("inference", "vllm", "sglang", "pytorch", "triton", "ml systems", "machine learning systems")):
        return 1
    if any(token in text for token in ("cuda", "gpu", "accelerator", "compiler", "llvm", "embedded", "autonomy")):
        return 2
    if any(token in text for token in ("quant", "trading", "market data", "low latency", "hft")):
        return 3
    if any(token in text for token in ("backend", "distributed", "database", "platform", "infrastructure", "linux", "kubernetes", "systems")):
        return 4
    return 5


def _comp_points(text: str) -> int:
    if not re.search(r"(?:\$\s?\d|\bUSD\b|per\s+hour|/hr|salary|compensation)", text, re.I):
        return 0
    return 3


def _resume_slug(value: str) -> str:
    return {
        "ml_systems": "ml-systems",
        "quant": "quant-systems",
        "systems": "backend-infra",
        "general": "backend-infra",
    }.get(value, "backend-infra")


def _safe_identifiers(
    posting: Posting, company_id: str
) -> tuple[PostingIdentifier, ...]:
    identifiers: set[PostingIdentifier] = set()
    for url in (posting.apply_url, posting.url_posting, posting.origin_url):
        if not url:
            continue
        try:
            identifiers.update(build_identifiers(urls=(url,)))
        except ValueError:
            continue
    if posting.requisition_id:
        identifiers.update(
            build_identifiers(
                employer_id=company_id,
                requisition_id=posting.requisition_id,
            )
        )
    return tuple(sorted(identifiers))


def prepare_posting(
    result: ManualFetchResult | Feed,
    posting: Posting,
    *,
    profile: dict[str, Any] | None = None,
    active_cooldowns: frozenset[str] = frozenset(),
    company_id_override: str | None = None,
    opportunity_type_override: str | None = None,
) -> PreparedPosting:
    feed = result if isinstance(result, Feed) else result.feed
    profile = profile or {}
    company_name = posting.company_raw or feed.company_name or feed.key
    company_key = feed.company_id or normalize_name(company_name) or feed.key
    company_id = company_id_override or public_id("company", company_key)
    observation_id = public_id("observation", feed.source, feed.key, posting.external_id)
    identifiers = _safe_identifiers(posting, company_id)
    identity = PostingIdentity(
        source_instance=f"{feed.source}:{feed.key}",
        external_id=posting.external_id,
        employer_id=company_id,
        title=posting.title,
        identifiers=identifiers,
        location=posting.location_raw,
        employment_type=posting.employment_type,
        published_at=posting.published_at,
    )
    document = asdict(posting)
    text = " ".join(
        part
        for part in (
            posting.title,
            posting.employment_type,
            posting.location_raw,
            posting.description_html,
        )
        if part
    )
    opportunity = classify_opportunity(
        posting.title,
        employment_type=posting.employment_type,
        description=posting.description_html,
    )
    opportunity_slug = opportunity_type_override or opportunity.opportunity_type.slug
    primary_slugs = frozenset(value.slug for value in PRIMARY_OPPORTUNITY_TYPES)
    primary_opportunity = opportunity_slug in primary_slugs
    stack_result = extract_stack(text)
    stack = tuple(
        {
            "technology": item.technology,
            "level": item.level.value,
            "excerpt": item.excerpt,
        }
        for item in stack_result.evidence
    )
    active_clearance = bool(re.search(r"\bactive\s+(?:security\s+)?clearance\b", text, re.I))
    graduation_match = re.search(r"\b(20\d{2})", str(profile.get("graduation_date", "2027-12")))
    graduation_year = int(graduation_match.group(1)) if graduation_match else 2027
    requires_full_time_enrollment = bool(
        re.search(
            r"\b(?:must|required to)\s+(?:be\s+)?(?:currently\s+)?enrolled\s+full[\s-]?time\b|"
            r"\bfull[\s-]?time\s+enrollment\s+(?:is\s+)?required\b",
            text,
            re.I,
        )
    )
    eligibility = evaluate_eligibility(
        EligibilityInput(
            title=posting.title,
            status="closed" if posting.state == "closed" or posting.source_active is False else "open",
            deadline_at=posting.deadline_at,
            student_signal=has_student_signal(posting.title, posting.employment_type),
            graduation_eligible=_graduation_eligible(text, graduation_year),
            window_compatible=(
                True
                if primary_opportunity
                else None
            ),
            role_family_eligible=_role_family(posting.title),
            location_compatible=_location_compatible(
                posting.location_raw,
                opportunity_slug,
                home_location=str(profile.get("home_location", "San Diego, CA")),
                relocate_for_internship=bool(
                    profile.get("relocate_for_internship", True)
                ),
            ),
            cooldown_active=company_id in active_cooldowns,
            min_years_experience=_years(text),
            path4=requires_full_time_enrollment,
            requires_full_time_enrollment=(
                True if requires_full_time_enrollment else None
            ),
            candidate_full_time_enrolled=(
                not bool(profile.get("reduced_enrollment", False))
                if requires_full_time_enrollment
                else None
            ),
            active_clearance_required=active_clearance,
        )
    )
    stack_names = tuple(item["technology"] for item in stack)
    score_result = score_opportunity(
        ScoreInput(
            segment=_segment(posting.title, stack_names),
            conversion_points=9 if posting.fidelity == "secondhand" else 14,
            window_fit=(
                WindowFit.EXACT
                if primary_opportunity
                else WindowFit.NEGOTIATION
            ),
            experience_points=15 if len(stack_names) >= 3 else 12 if stack_names else 8,
            latency_points=2 if posting.fidelity == "secondhand" else 5,
            comp_points=_comp_points(text),
            title=posting.title,
            stack=stack_names,
            active_clearance_penalty=10 if active_clearance else 0,
        )
    )
    reasons = tuple(reason.value for reason in eligibility.reasons) + tuple(
        f"Review: {field}" for field in eligibility.review_fields
    )
    status = "closed" if posting.state == "closed" or posting.source_active is False else "open"
    routable_band = (
        score_result.band
        if eligibility.verdict is EligibilityVerdict.ELIGIBLE
        else ScoreBand.LOG_ONLY
    )
    route = route_for_band(routable_band)
    return PreparedPosting(
        posting=posting,
        company_id=company_id,
        company_name=company_name,
        observation_id=observation_id,
        identity=identity,
        identifiers=identifiers,
        document=document,
        content_hash=content_hash(document),
        opportunity_slug=opportunity_slug,
        opportunity_confidence=1.0 if opportunity_type_override else opportunity.confidence,
        opportunity_evidence=opportunity.evidence,
        opportunity_version=opportunity.algorithm_version,
        stack=stack,
        stack_version=stack_result.algorithm_version,
        eligibility=eligibility.verdict.value,
        eligibility_reasons=reasons,
        eligibility_version=eligibility.algorithm_version,
        score=score_result.total,
        band=routable_band.value,
        score_components=tuple(asdict(component) for component in score_result.components),
        score_version=score_result.algorithm_version,
        score_route={
            "submit_within_hours": (
                int(route.submit_within.total_seconds() // 3600)
                if route.submit_within is not None
                else None
            ),
            "auto_submit": route.auto_submit,
            "instruction": route.instruction,
        },
        resume_variant=_resume_slug(score_result.resume_variant.value),
        authority="secondhand" if posting.fidelity == "secondhand" else "origin",
        status=status,
    )


class BoardIngestor:
    """Persist canonical parser output after an explicit manual fetch."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _stored_posting(row: dict[str, Any]) -> Posting:
        """Rebuild the policy inputs retained on a current observation."""
        is_closed = row["status"] == "closed"
        return Posting(
            external_id=row["external_id"],
            title=row["title"],
            url_posting=row["posting_url"],
            fidelity="secondhand" if row["authority"] == "secondhand" else "origin",
            state="closed" if is_closed else "open",
            closed_evidence=(
                (row["closed_evidence"] or "stored explicit closure")
                if is_closed
                else None
            ),
            company_raw=row["company_raw"],
            apply_url=row["apply_url"],
            location_raw=row["location"],
            description_html=row["description_html"],
            requisition_id=row["requisition_id"],
            employment_type=row["employment_type"],
            workplace_type_raw=row["workplace_type"],
            published_at=(
                datetime.fromisoformat(row["published_at"])
                if row["published_at"]
                else None
            ),
            deadline_at=(
                datetime.fromisoformat(row["deadline_at"])
                if row["deadline_at"]
                else None
            ),
            source_active=(
                bool(row["source_active"])
                if row["source_active"] is not None
                else None
            ),
            source_extra=loads(row["source_extra_json"], {}),
        )

    async def _prepare_current_entities(
        self,
        *,
        profile: dict[str, Any],
        active_cooldowns: frozenset[str],
        company_ids: set[str] | None = None,
    ) -> list[tuple[str, PreparedPosting]]:
        """Prepare each current entity from its persisted representative."""
        params: tuple[object, ...] = ()
        company_clause = ""
        if company_ids is not None:
            if not company_ids:
                return []
            ordered_ids = sorted(company_ids)
            company_clause = (
                f"AND entity.company_id IN ({','.join('?' for _ in ordered_ids)})"
            )
            params = tuple(ordered_ids)
        rows = await self.database.fetch_all(
            f"""
            SELECT observation.*, entity.id AS entity_id,
                   entity.company_id AS entity_company_id,
                   entity.company_name AS entity_company_name,
                   entity.manual_opportunity_type,
                   entity.manual_classification_evidence
            FROM job_entities AS entity
            JOIN posting_observations AS observation
              ON observation.job_entity_id = entity.id
            WHERE entity.status != 'merged'
              {company_clause}
              AND observation.id = (
                SELECT candidate.id FROM posting_observations AS candidate
                WHERE candidate.job_entity_id = entity.id
                ORDER BY
                  CASE candidate.authority
                    WHEN 'origin' THEN 3 WHEN 'manual' THEN 2 ELSE 1 END DESC,
                  CASE candidate.status
                    WHEN 'open' THEN 2 WHEN 'unlisted' THEN 1 ELSE 0 END DESC,
                  LENGTH(COALESCE(candidate.description_html, '')) DESC,
                  candidate.last_seen_at DESC
                LIMIT 1
              )
            ORDER BY entity.id
            """,
            params,
        )
        prepared: list[tuple[str, PreparedPosting]] = []
        for row in rows:
            feed = Feed(
                id=int(row["feed_id"] or 0),
                source=row["source"],
                key=row["feed_key"],
                config={},
                state={},
                company_name=row["entity_company_name"],
            )
            current = prepare_posting(
                feed,
                self._stored_posting(row),
                profile=profile,
                active_cooldowns=active_cooldowns,
                company_id_override=row["entity_company_id"],
                opportunity_type_override=row["manual_opportunity_type"],
            )
            prepared.append(
                (row["entity_id"], replace(current, observation_id=row["id"]))
            )
        return prepared

    async def recompute_profile(self, profile: dict[str, Any]) -> int:
        """Re-evaluate every current job locally after a profile change.

        This reads only durable observations and facts; it never resolves a
        catalog feed or invokes the network gateway.
        """
        now = utc_now()
        cooldown_rows = await self.database.fetch_all(
            """
            SELECT company_id, value_json, expires_at FROM company_facts
            WHERE name = 'cooldown_until'
            """
        )
        active_cooldowns = frozenset(
            row["company_id"]
            for row in cooldown_rows
            if (
                (row["expires_at"] and row["expires_at"] > now)
                or (
                    isinstance(loads(row["value_json"], None), str)
                    and loads(row["value_json"], "") > now
                )
            )
        )
        refreshes = await self._prepare_current_entities(
            profile=profile,
            active_cooldowns=active_cooldowns,
        )

        def operation(connection: sqlite3.Connection) -> int:
            refreshed_at = utc_now()
            for entity_id, item in refreshes:
                self._refresh_entity(connection, entity_id, item, refreshed_at)
            return len(refreshes)

        return await self.database.transaction(operation)

    async def ingest(self, result: ManualFetchResult) -> IngestCounts:
        # Raw observations remain durable even when parsing fails. Publishing
        # a parser's partial prefix or a truncated transport snapshot would
        # make an incomplete board look valid. Both remain locally replayable.
        if (
            result.error is not None
            or result.parser_state != "available"
            or result.record is None
            or result.outcome in {"error", "not_modified", "partial"}
            or result.record.truncated
        ):
            return IngestCounts()
        postings = tuple(item for item in result.canonical if isinstance(item, Posting))
        facts = tuple(item for item in result.canonical if isinstance(item, Fact))
        profile_row = await self.database.fetch_one(
            "SELECT document_json FROM profile WHERE active = 1 LIMIT 1"
        )
        profile = loads(profile_row["document_json"], {}) if profile_row else {}
        now = utc_now()
        cooldown_rows = await self.database.fetch_all(
            """
            SELECT company_id, value_json, expires_at FROM company_facts
            WHERE name = 'cooldown_until'
            """
        )
        active_cooldowns = {
            row["company_id"]
            for row in cooldown_rows
            if (
                (row["expires_at"] and row["expires_at"] > now)
                or (
                    isinstance(loads(row["value_json"], None), str)
                    and loads(row["value_json"], "") > now
                )
            )
        }
        # Canonical output is a set of facts, not an ordered command stream.
        # A cooldown arriving beside a Posting must affect that Posting even
        # when the parser happened to emit the Fact after it.
        incoming_cooldowns = {
            normalize_name(fact.company_raw): (
                (iso(fact.expires_at) is not None and iso(fact.expires_at) > now)
                or (isinstance(fact.value, str) and fact.value > now)
            )
            for fact in facts
            if fact.name == "cooldown_until"
        }
        company_rows = await self.database.fetch_all(
            "SELECT id, normalized_name FROM companies"
        )
        affected_company_ids: set[str] = set()
        for company in company_rows:
            active = incoming_cooldowns.get(company["normalized_name"])
            if active is None:
                continue
            affected_company_ids.add(company["id"])
            if active:
                active_cooldowns.add(company["id"])
            else:
                active_cooldowns.discard(company["id"])
        for posting in postings:
            company_name = posting.company_raw or result.feed.company_name or result.feed.key
            if incoming_cooldowns.get(normalize_name(company_name), False):
                company_key = result.feed.company_id or normalize_name(company_name) or result.feed.key
                active_cooldowns.add(public_id("company", company_key))
        prepared = tuple(
            prepare_posting(
                result,
                item,
                profile=profile,
                active_cooldowns=frozenset(active_cooldowns),
            )
            for item in postings
        )
        labels = tuple(item for item in result.canonical if isinstance(item, Label))

        # Fact-only cooldown updates still need to refresh existing current
        # jobs. Reuse the same preparation policy against each entity's current
        # representative, without pretending the fact poll re-observed it.
        posting_company_ids = {item.company_id for item in prepared}
        fact_refreshes = await self._prepare_current_entities(
            profile=profile,
            active_cooldowns=frozenset(active_cooldowns),
            company_ids=affected_company_ids - posting_company_ids,
        )

        def operation(connection: sqlite3.Connection) -> IngestCounts:
            counts = IngestCounts()
            for item in prepared:
                delta = self._upsert_posting(connection, result, item)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO poll_memberships(
                        poll_id, observation_id, external_id
                    ) VALUES (?, ?, ?)
                    """,
                    (result.poll_id, item.observation_id, item.posting.external_id),
                )
                counts = IngestCounts(
                    postings=counts.postings + 1,
                    created_entities=counts.created_entities + delta.created_entities,
                    changed_revisions=counts.changed_revisions + delta.changed_revisions,
                    exact_links=counts.exact_links + delta.exact_links,
                    review_candidates=counts.review_candidates + delta.review_candidates,
                    related_candidates=counts.related_candidates + delta.related_candidates,
                    labels=counts.labels,
                    facts=counts.facts,
                )
            for label in labels:
                if self._persist_label(connection, result, label):
                    counts = IngestCounts(**{**asdict(counts), "labels": counts.labels + 1})
            for fact in facts:
                if self._persist_fact(connection, fact):
                    counts = IngestCounts(**{**asdict(counts), "facts": counts.facts + 1})
            for entity_id, refreshed in fact_refreshes:
                self._refresh_entity(connection, entity_id, refreshed, utc_now())
            # Membership describes the source snapshot, not merely what this
            # parser version understood. A raw ID may retain an already-known
            # posting from this feed, but an arbitrary new non-posting item
            # cannot manufacture a board entity.
            connection.execute(
                """
                INSERT OR IGNORE INTO poll_memberships(
                    poll_id, observation_id, external_id
                )
                SELECT ?, posting.id, posting.external_id
                FROM posting_observations AS posting
                JOIN observations AS raw
                  ON raw.poll_id = ?
                 AND raw.external_id = posting.external_id
                WHERE posting.feed_id = ?
                """,
                (result.poll_id, result.poll_id, result.feed.id),
            )
            self._reconcile_snapshot(connection, result)
            return counts

        return await self.database.transaction(operation)

    @staticmethod
    def _authority(value: str) -> Authority:
        if value == "origin":
            return Authority.CANONICAL_EMPLOYER
        if value == "manual":
            # A user capture records interest, not employer-owned evidence that
            # the opportunity remains open.
            return Authority.AGGREGATOR
        return Authority.AGGREGATOR

    @classmethod
    def _refresh_lifecycle_aggregate(
        cls, connection: sqlite3.Connection, entity_id: str, now: str
    ) -> None:
        rows = connection.execute(
            "SELECT * FROM posting_observations WHERE job_entity_id = ? ORDER BY id",
            (entity_id,),
        ).fetchall()
        if not rows:
            return
        observations = tuple(
            ObservationLifecycle(
                source_instance=f"{row['source']}:{row['feed_key']}",
                external_id=row["external_id"],
                authority=cls._authority(row["authority"]),
                state=LifecycleState(row["status"]),
                consecutive_absences=int(row["absence_count"] or 0),
                absent_since=(
                    datetime.fromisoformat(row["absent_since"])
                    if row["absent_since"]
                    else None
                ),
                last_seen_at=(
                    datetime.fromisoformat(row["last_seen_at"])
                    if row["last_seen_at"]
                    else None
                ),
                last_seen_poll_run_id=(
                    str(row["last_poll_id"]) if row["last_poll_id"] else None
                ),
                explicit_closed=bool(row["explicit_closed"]),
            )
            for row in rows
        )
        if all(item.state is LifecycleState.UNLISTED for item in observations):
            status = LifecycleState.UNLISTED.value
        else:
            status = resolve_entity(observations).state.value
        source_count = len(rows)
        authoritative_open = any(
            item.authority.authoritative and item.state is LifecycleState.OPEN
            for item in observations
        )
        last_seen = max(row["last_seen_at"] for row in rows)
        connection.execute(
            """
            UPDATE job_entities SET source_count = ?, authoritative_open = ?,
                status = ?, last_seen_at = ?, version = version + 1, updated_at = ?
            WHERE id = ?
            """,
            (source_count, int(authoritative_open), status, last_seen, now, entity_id),
        )

    @classmethod
    def _reconcile_snapshot(
        cls, connection: sqlite3.Connection, result: ManualFetchResult
    ) -> None:
        record = result.record
        assert record is not None
        already_reconciled = connection.execute(
            "SELECT 1 FROM poll_snapshots WHERE poll_id = ?", (result.poll_id,)
        ).fetchone()
        if already_reconciled is not None:
            return
        now = utc_now()
        member_rows = connection.execute(
            "SELECT external_id FROM poll_memberships WHERE poll_id = ?",
            (result.poll_id,),
        ).fetchall()
        member_ids = frozenset(row["external_id"] for row in member_rows)
        previous_row = connection.execute(
            """
            SELECT s.*, p.started_at
            FROM poll_snapshots s JOIN polls p ON p.id = s.poll_id
            WHERE s.feed_id = ? AND s.poll_id < ?
              AND s.status IN ('baseline', 'applied')
            ORDER BY s.poll_id DESC LIMIT 1
            """,
            (result.feed.id, result.poll_id),
        ).fetchone()
        eligible = (
            result.feed.complete
            and result.feed.closes
            and result.outcome == "ok"
            and not record.truncated
        )
        if not eligible:
            connection.execute(
                """
                INSERT OR REPLACE INTO poll_snapshots(
                    poll_id, feed_id, status, member_count, previous_poll_id,
                    reason, algorithm_version, created_at
                ) VALUES (?, ?, 'ineligible', ?, ?, ?, ?, ?)
                """,
                (
                    result.poll_id,
                    result.feed.id,
                    len(member_ids),
                    previous_row["poll_id"] if previous_row else None,
                    "feed is not complete/closing or response was partial",
                    LIFECYCLE_VERSION,
                    now,
                ),
            )
            connection.execute(
                "UPDATE feeds SET snapshot_state = 'ineligible', snapshot_reason = ? WHERE id = ?",
                (
                    "feed is not complete/closing or response was partial",
                    result.feed.id,
                ),
            )
            return

        current_poll = connection.execute(
            "SELECT started_at FROM polls WHERE id = ?", (result.poll_id,)
        ).fetchone()
        current = Snapshot(
            feed_id=str(result.feed.id),
            run_id=str(result.poll_id),
            observed_ids=member_ids,
            polled_at=datetime.fromisoformat(current_poll["started_at"]),
            complete=True,
            closes=True,
            truncated=False,
        )
        previous: Snapshot | None = None
        if previous_row is not None:
            previous_members = connection.execute(
                "SELECT external_id FROM poll_memberships WHERE poll_id = ?",
                (previous_row["poll_id"],),
            ).fetchall()
            previous = Snapshot(
                feed_id=str(result.feed.id),
                run_id=str(previous_row["poll_id"]),
                observed_ids=frozenset(row["external_id"] for row in previous_members),
                polled_at=datetime.fromisoformat(previous_row["started_at"]),
                complete=True,
                closes=True,
            )

        rows = connection.execute(
            """
            SELECT * FROM posting_observations
            WHERE feed_id = ? AND explicit_closed = 0
            ORDER BY id
            """,
            (result.feed.id,),
        ).fetchall()
        lifecycles = tuple(
            ObservationLifecycle(
                source_instance=str(result.feed.id),
                external_id=row["external_id"],
                authority=cls._authority(row["authority"]),
                state=LifecycleState(row["status"]),
                consecutive_absences=int(row["absence_count"] or 0),
                absent_since=(
                    datetime.fromisoformat(row["absent_since"])
                    if row["absent_since"]
                    else None
                ),
                last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
                last_seen_poll_run_id=(
                    str(row["last_poll_id"]) if row["last_poll_id"] else None
                ),
            )
            for row in rows
        )
        resolution = reconcile_snapshots(
            previous,
            current,
            lifecycles,
            policy=SnapshotPolicy(
                minimum_baseline_for_drop_check=10,
                quarantine_drop_fraction=0.80,
            ),
        )
        snapshot_status = (
            "quarantined"
            if resolution.state is LifecycleState.QUARANTINED
            else "baseline" if previous is None else "applied"
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO poll_snapshots(
                poll_id, feed_id, status, member_count, previous_poll_id,
                reason, algorithm_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.poll_id,
                result.feed.id,
                snapshot_status,
                len(member_ids),
                previous_row["poll_id"] if previous_row else None,
                resolution.reason,
                resolution.algorithm_version,
                now,
            ),
        )
        connection.execute(
            "UPDATE feeds SET snapshot_state = ?, snapshot_reason = ? WHERE id = ?",
            (snapshot_status, resolution.reason, result.feed.id),
        )
        if resolution.state is LifecycleState.QUARANTINED or previous is None:
            return

        by_external = {row["external_id"]: row for row in rows}
        affected: set[str] = set()
        for updated in resolution.observations:
            old = by_external[updated.external_id]
            new_status = updated.state.value
            new_absent = iso(updated.absent_since)
            if (
                new_status == old["status"]
                and updated.consecutive_absences == old["absence_count"]
                and new_absent == old["absent_since"]
            ):
                continue
            connection.execute(
                """
                UPDATE posting_observations SET status = ?, absence_count = ?,
                    absent_since = ?, explicit_closed = 0
                WHERE id = ?
                """,
                (
                    new_status,
                    updated.consecutive_absences,
                    new_absent,
                    old["id"],
                ),
            )
            reason = updated.history[-1].reason if updated.history else resolution.reason
            connection.execute(
                """
                INSERT INTO lifecycle_events(
                    id, observation_id, job_entity_id, from_status, to_status,
                    reason, poll_id, created_at, algorithm_version, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(), old["id"], old["job_entity_id"], old["status"],
                    new_status, reason, result.poll_id, now, LIFECYCLE_VERSION,
                    dumps({"absence_count": updated.consecutive_absences}),
                ),
            )
            affected.add(old["job_entity_id"])
        for entity_id in affected:
            cls._refresh_lifecycle_aggregate(connection, entity_id, now)

    @staticmethod
    def _identity_for_entity(
        connection: sqlite3.Connection, entity_id: str
    ) -> tuple[PostingIdentity, ...]:
        rows = connection.execute(
            """
            SELECT o.*, e.company_id
            FROM posting_observations o
            JOIN job_entities e ON e.id = o.job_entity_id
            WHERE o.job_entity_id = ?
            ORDER BY o.id
            """,
            (entity_id,),
        ).fetchall()
        identities: list[PostingIdentity] = []
        for row in rows:
            id_rows = connection.execute(
                "SELECT namespace, scope, value, provenance FROM posting_identifiers WHERE observation_id = ?",
                (row["id"],),
            ).fetchall()
            identities.append(
                PostingIdentity(
                    source_instance=f"{row['source']}:{row['feed_key']}",
                    external_id=row["external_id"],
                    employer_id=row["company_id"],
                    title=row["title"],
                    identifiers=tuple(
                        PostingIdentifier(
                            identifier["namespace"],
                            identifier["scope"],
                            identifier["value"],
                            identifier["provenance"],
                        )
                        for identifier in id_rows
                    ),
                    location=row["location"],
                    employment_type=row["employment_type"],
                    published_at=(
                        datetime.fromisoformat(row["published_at"])
                        if row["published_at"]
                        else None
                    ),
                )
            )
        return tuple(identities)

    def _find_entity(
        self, connection: sqlite3.Connection, item: PreparedPosting
    ) -> tuple[
        str | None,
        str | None,
        DedupeDecision | None,
        str | None,
        list[tuple[str, DedupeDecision]],
    ]:
        existing = connection.execute(
            "SELECT job_entity_id FROM posting_observations WHERE id = ?",
            (item.observation_id,),
        ).fetchone()
        if existing:
            return existing["job_entity_id"], None, None, None, []

        exact_entities: set[str] = set()
        for identifier in item.identifiers:
            rows = connection.execute(
                """
                SELECT DISTINCT o.job_entity_id
                FROM posting_identifiers i
                JOIN posting_observations o ON o.id = i.observation_id
                JOIN job_entities e ON e.id = o.job_entity_id
                WHERE i.namespace = ? AND i.scope = ? AND i.value = ?
                  AND e.status != 'merged'
                """,
                identifier.key,
            ).fetchall()
            exact_entities.update(row["job_entity_id"] for row in rows)
        if len(exact_entities) == 1:
            entity_id = next(iter(exact_entities))
            members = self._identity_for_entity(connection, entity_id)
            decision = safe_cluster_decision(item.identity, members)
            if decision.auto_merge:
                matched = connection.execute(
                    "SELECT id FROM posting_observations WHERE job_entity_id = ? ORDER BY id LIMIT 1",
                    (entity_id,),
                ).fetchone()
                return (
                    None,
                    entity_id,
                    decision,
                    matched["id"] if matched else None,
                    [],
                )
        comparisons: list[tuple[str, DedupeDecision]] = []
        if len(exact_entities) > 1:
            collision = DedupeDecision(
                DedupeOutcome.REVIEW,
                False,
                ("deterministic identifiers point at multiple existing entities",),
                algorithm_version=DEDUPE_VERSION,
            )
            comparisons.extend((entity_id, collision) for entity_id in sorted(exact_entities))
        rows = connection.execute(
            """
            SELECT id FROM job_entities
            WHERE company_id = ? AND title_normalized = ? AND status != 'merged'
            ORDER BY created_at, id
            LIMIT 20
            """,
            (item.company_id, normalize_title(item.posting.title)),
        ).fetchall()
        for row in rows:
            if row["id"] in exact_entities:
                continue
            members = self._identity_for_entity(connection, row["id"])
            if members:
                comparisons.append(
                    (row["id"], safe_cluster_decision(item.identity, members))
                )
        return None, None, None, None, comparisons

    @staticmethod
    def _new_entity(connection: sqlite3.Connection, item: PreparedPosting, now: str) -> str:
        entity_id = new_id()
        posting = item.posting
        connection.execute(
            """
            INSERT INTO job_entities (
                id, company_id, company_name, title, title_normalized,
                opportunity_type, opportunity_confidence, opportunity_evidence_json,
                location, workplace_type, employment_type, description_html,
                posting_url, apply_url, published_at, deadline_at,
                first_seen_at, last_seen_at, status, eligibility,
                eligibility_reasons_json, stack_json, score, score_band,
                score_components_json, resume_variant, authoritative_open,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                item.company_id,
                item.company_name,
                posting.title,
                normalize_title(posting.title),
                item.opportunity_slug,
                item.opportunity_confidence,
                dumps(item.opportunity_evidence),
                posting.location_raw,
                posting.workplace_type_raw,
                posting.employment_type,
                posting.description_html,
                posting.url_posting,
                posting.apply_url or posting.url_posting,
                iso(posting.published_at),
                iso(posting.deadline_at),
                now,
                now,
                item.status,
                item.eligibility,
                dumps(item.eligibility_reasons),
                dumps(item.stack),
                item.score,
                item.band,
                dumps(item.score_components),
                item.resume_variant,
                int(item.authority == "origin" and item.status == "open"),
                now,
                now,
            ),
        )
        connection.execute(
            """
            UPDATE job_entities SET route_instruction = ?, submit_within_hours = ?,
                auto_submit = 0
            WHERE id = ?
            """,
            (
                item.score_route["instruction"],
                item.score_route["submit_within_hours"],
                entity_id,
            ),
        )
        return entity_id

    def _upsert_posting(
        self,
        connection: sqlite3.Connection,
        result: ManualFetchResult,
        item: PreparedPosting,
    ) -> IngestCounts:
        now = utc_now()
        connection.execute(
            """
            INSERT INTO companies(id, display_name, normalized_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                display_name = excluded.display_name,
                normalized_name = excluded.normalized_name,
                updated_at = excluded.updated_at
            """,
            (item.company_id, item.company_name, normalize_name(item.company_name), now, now),
        )
        (
            existing_entity_id,
            exact_target_id,
            exact_decision,
            matched_observation_id,
            comparisons,
        ) = self._find_entity(connection, item)
        entity_id = existing_entity_id
        created = 0
        exact = 0
        if entity_id is None:
            entity_id = self._new_entity(connection, item, now)
            created = 1

        posting = item.posting
        old = connection.execute(
            """
            SELECT content_hash, revision_no, status, absence_count,
                   first_seen_at, explicit_closed
            FROM posting_observations WHERE id = ?
            """,
            (item.observation_id,),
        ).fetchone()
        revision_no = (old["revision_no"] + 1) if old and old["content_hash"] != item.content_hash else (old["revision_no"] if old else 1)
        connection.execute(
            """
            INSERT INTO posting_observations (
                id, feed_id, source, feed_key, external_id, job_entity_id,
                company_raw, title, location, employment_type, workplace_type,
                description_html, posting_url, apply_url, requisition_id,
                published_at, deadline_at, source_active, authority, status,
                explicit_closed, closed_evidence, content_hash, revision_no,
                first_seen_at, last_seen_at, last_poll_id,
                source_extra_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                job_entity_id = excluded.job_entity_id,
                company_raw = excluded.company_raw,
                title = excluded.title,
                location = excluded.location,
                employment_type = excluded.employment_type,
                workplace_type = excluded.workplace_type,
                description_html = excluded.description_html,
                posting_url = excluded.posting_url,
                apply_url = excluded.apply_url,
                requisition_id = excluded.requisition_id,
                published_at = excluded.published_at,
                deadline_at = excluded.deadline_at,
                source_active = excluded.source_active,
                authority = excluded.authority,
                status = excluded.status,
                explicit_closed = excluded.explicit_closed,
                closed_evidence = excluded.closed_evidence,
                absence_count = 0,
                absent_since = NULL,
                content_hash = excluded.content_hash,
                revision_no = excluded.revision_no,
                last_seen_at = excluded.last_seen_at,
                last_poll_id = excluded.last_poll_id,
                source_extra_json = excluded.source_extra_json
            """,
            (
                item.observation_id,
                result.feed.id,
                result.feed.source,
                result.feed.key,
                posting.external_id,
                entity_id,
                posting.company_raw,
                posting.title,
                posting.location_raw,
                posting.employment_type,
                posting.workplace_type_raw,
                posting.description_html,
                posting.url_posting,
                posting.apply_url,
                posting.requisition_id,
                iso(posting.published_at),
                iso(posting.deadline_at),
                None if posting.source_active is None else int(posting.source_active),
                item.authority,
                item.status,
                int(item.status == "closed"),
                (
                    posting.closed_evidence
                    or ("source_active=false" if posting.source_active is False else None)
                ),
                item.content_hash,
                revision_no,
                now if old is None else old["first_seen_at"],
                now,
                result.poll_id,
                dumps(posting.source_extra),
            ),
        )
        if old is None and item.status == "closed":
            old_status = None
        elif old is not None and old["status"] != item.status:
            old_status = old["status"]
        else:
            old_status = None
        if old_status is not None or (old is None and item.status == "closed"):
            reason = (
                posting.closed_evidence
                or "source explicitly reported the posting closed"
                if item.status == "closed"
                else "source ID returned with an open status"
            )
            connection.execute(
                """
                INSERT INTO lifecycle_events(
                    id, observation_id, job_entity_id, from_status, to_status,
                    reason, poll_id, created_at, algorithm_version, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(), item.observation_id, entity_id, old_status,
                    item.status, reason, result.poll_id, now, LIFECYCLE_VERSION,
                    dumps({"explicit": item.status == "closed"}),
                ),
            )
        changed = int(old is None or old["content_hash"] != item.content_hash)
        if changed:
            connection.execute(
                """
                INSERT OR IGNORE INTO posting_revisions(
                    id, observation_id, revision_no, content_hash, snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    public_id("revision", item.observation_id, revision_no, item.content_hash),
                    item.observation_id,
                    revision_no,
                    item.content_hash,
                    dumps(item.document),
                    now,
                ),
            )
        for identifier in item.identifiers:
            connection.execute(
                """
                INSERT OR IGNORE INTO posting_identifiers(
                    observation_id, namespace, scope, value, provenance, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item.observation_id,
                    identifier.namespace,
                    identifier.scope,
                    identifier.value,
                    identifier.provenance,
                    now,
                ),
            )

        if exact_target_id is not None and exact_decision is not None:
            # Materialize the new entity first, then record an ordinary merge.
            # That gives an automatic exact link the same lossless reversal
            # semantics as a human-reviewed merge.
            self._refresh_entity(connection, entity_id, item, now)
            before = {
                "survivor": dict(
                    connection.execute(
                        "SELECT * FROM job_entities WHERE id = ?", (exact_target_id,)
                    ).fetchone()
                ),
                "absorbed": dict(
                    connection.execute(
                        "SELECT * FROM job_entities WHERE id = ?", (entity_id,)
                    ).fetchone()
                ),
            }
            candidate_id = self._persist_dedupe_candidate(
                connection,
                exact_target_id,
                entity_id,
                exact_decision,
                "merged",
                now,
            )
            if matched_observation_id is not None:
                left_observation, right_observation = sorted(
                    (matched_observation_id, item.observation_id)
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO link_evidence(
                        id, observation_a_id, observation_b_id, method,
                        confidence, details_json, algorithm_version,
                        review_state, created_at
                    ) VALUES (?, ?, ?, 'deterministic_identifier', 1.0, ?, ?,
                              'accepted', ?)
                    """,
                    (
                        public_id("link", left_observation, right_observation),
                        left_observation,
                        right_observation,
                        dumps(
                            {
                                "evidence": exact_decision.evidence,
                                "shared_identifiers": [
                                    asdict(value)
                                    for value in exact_decision.shared_identifiers
                                ],
                            }
                        ),
                        exact_decision.algorithm_version,
                        now,
                    ),
                )
            connection.execute(
                "UPDATE posting_observations SET job_entity_id = ? WHERE id = ?",
                (exact_target_id, item.observation_id),
            )
            connection.execute(
                """
                UPDATE job_entities SET status = 'merged', merged_into_id = ?,
                    updated_at = ? WHERE id = ?
                """,
                (exact_target_id, now, entity_id),
            )
            connection.execute(
                """
                INSERT INTO merge_operations(
                    id, candidate_id, survivor_id, absorbed_id,
                    moved_observation_ids_json, created_at,
                    moved_application_ids_json, before_state_json
                ) VALUES (?, ?, ?, ?, ?, ?, '[]', ?)
                """,
                (
                    new_id(), candidate_id, exact_target_id, entity_id,
                    dumps([item.observation_id]), now, dumps(before),
                ),
            )
            entity_id = exact_target_id
            exact = 1

        review = related = 0
        for other_entity_id, decision in comparisons:
            if other_entity_id == entity_id and decision.outcome is DedupeOutcome.EXACT:
                continue
            state = "related" if decision.outcome is DedupeOutcome.RELATED else "pending"
            self._persist_dedupe_candidate(
                connection, entity_id, other_entity_id, decision, state, now
            )
            review += int(decision.outcome is DedupeOutcome.REVIEW)
            related += int(decision.outcome is DedupeOutcome.RELATED)

        self._refresh_entity(connection, entity_id, item, now)
        return IngestCounts(
            postings=1,
            created_entities=created,
            changed_revisions=changed,
            exact_links=exact,
            review_candidates=review,
            related_candidates=related,
        )

    @staticmethod
    def _persist_dedupe_candidate(
        connection: sqlite3.Connection,
        entity_a_id: str,
        entity_b_id: str,
        decision: DedupeDecision,
        state: str,
        now: str,
    ) -> str:
        left, right = sorted((entity_a_id, entity_b_id))
        candidate_id = public_id("dedupe", left, right)
        connection.execute(
            """
            INSERT INTO dedupe_candidates(
                id, entity_a_id, entity_b_id, outcome, confidence,
                reasons_json, differences_json, state, created_at, updated_at,
                algorithm_version, shared_identifiers_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_a_id, entity_b_id) DO UPDATE SET
                outcome = excluded.outcome,
                confidence = excluded.confidence,
                reasons_json = excluded.reasons_json,
                differences_json = excluded.differences_json,
                algorithm_version = excluded.algorithm_version,
                shared_identifiers_json = excluded.shared_identifiers_json,
                state = CASE
                    WHEN dedupe_candidates.state IN ('kept_separate', 'deferred', 'merged', 'reversed')
                    THEN dedupe_candidates.state
                    ELSE excluded.state
                END,
                updated_at = excluded.updated_at
            """,
            (
                candidate_id,
                left,
                right,
                decision.outcome.value,
                (
                    1.0
                    if decision.outcome is DedupeOutcome.EXACT
                    else 0.75 if decision.outcome is DedupeOutcome.REVIEW else 0.55
                ),
                dumps(decision.evidence),
                dumps({"auto_merge": decision.auto_merge}),
                state,
                now,
                now,
                decision.algorithm_version,
                dumps([asdict(value) for value in decision.shared_identifiers]),
            ),
        )
        return candidate_id

    @staticmethod
    def _refresh_entity(
        connection: sqlite3.Connection, entity_id: str, item: PreparedPosting, now: str
    ) -> None:
        representative = connection.execute(
            """
            SELECT * FROM posting_observations
            WHERE job_entity_id = ?
            ORDER BY
                CASE authority WHEN 'origin' THEN 3 WHEN 'manual' THEN 2 ELSE 1 END DESC,
                CASE status WHEN 'open' THEN 2 WHEN 'unlisted' THEN 1 ELSE 0 END DESC,
                LENGTH(COALESCE(description_html, '')) DESC,
                last_seen_at DESC
            LIMIT 1
            """,
            (entity_id,),
        ).fetchone()
        counts = connection.execute(
            """
            SELECT COUNT(*) AS source_count,
                   SUM(CASE WHEN authority = 'origin' AND status = 'open' THEN 1 ELSE 0 END) AS origin_open,
                   SUM(CASE WHEN authority = 'origin' AND status = 'closed' THEN 1 ELSE 0 END) AS origin_closed
            FROM posting_observations WHERE job_entity_id = ?
            """,
            (entity_id,),
        ).fetchone()
        if representative is None:
            return
        origin_open = int(counts["origin_open"] or 0)
        origin_closed = int(counts["origin_closed"] or 0)
        if origin_open and origin_closed:
            status = "conflicting"
        elif origin_open:
            status = "open"
        elif origin_closed:
            status = "closed"
        else:
            status = representative["status"]
        representative_is_current = representative["id"] == item.observation_id
        if representative_is_current:
            connection.execute(
                "DELETE FROM job_requirements WHERE job_entity_id = ?",
                (entity_id,),
            )
            for evidence in item.stack:
                connection.execute(
                    """
                    INSERT INTO job_requirements(
                        id, job_entity_id, technology, level, evidence,
                        algorithm_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        public_id("requirement", entity_id, evidence["technology"]),
                        entity_id,
                        evidence["technology"],
                        evidence["level"],
                        evidence["excerpt"],
                        item.stack_version,
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE job_entities SET
                    company_name = ?, title = ?, title_normalized = ?,
                    opportunity_type = COALESCE(manual_opportunity_type, ?),
                    opportunity_confidence = CASE
                        WHEN manual_opportunity_type IS NULL THEN ? ELSE 1.0 END,
                    opportunity_evidence_json = CASE
                        WHEN manual_opportunity_type IS NULL THEN ?
                        ELSE opportunity_evidence_json END,
                    location = ?, workplace_type = ?,
                    employment_type = ?, description_html = ?, posting_url = ?, apply_url = ?,
                    published_at = ?, deadline_at = ?, last_seen_at = ?, status = ?,
                    eligibility = ?, eligibility_reasons_json = ?, stack_json = ?,
                    score = ?, score_band = ?, score_components_json = ?, resume_variant = ?,
                    route_instruction = ?, submit_within_hours = ?, auto_submit = 0,
                    source_count = ?, authoritative_open = ?, version = version + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    item.company_name,
                    representative["title"],
                    normalize_title(representative["title"]),
                    item.opportunity_slug,
                    item.opportunity_confidence,
                    dumps(item.opportunity_evidence),
                    representative["location"],
                    representative["workplace_type"],
                    representative["employment_type"],
                    representative["description_html"],
                    representative["posting_url"],
                    representative["apply_url"] or representative["posting_url"],
                    representative["published_at"],
                    representative["deadline_at"],
                    now,
                    status,
                    item.eligibility,
                    dumps(item.eligibility_reasons),
                    dumps(item.stack),
                    item.score,
                    item.band,
                    dumps(item.score_components),
                    item.resume_variant,
                    item.score_route["instruction"],
                    item.score_route["submit_within_hours"],
                    counts["source_count"],
                    int(origin_open > 0),
                    now,
                    entity_id,
                ),
            )
            effective = connection.execute(
                """
                SELECT opportunity_type, opportunity_evidence_json,
                       manual_opportunity_type, manual_classification_evidence
                FROM job_entities WHERE id = ?
                """,
                (entity_id,),
            ).fetchone()
            opportunity_version = (
                "human-opportunity-override/1"
                if effective["manual_opportunity_type"]
                else item.opportunity_version
            )
            connection.execute(
                """
                INSERT INTO classification_snapshots(
                    id, job_entity_id, opportunity_type, eligibility,
                    evidence_json, algorithm_version, created_at, versions_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(), entity_id, effective["opportunity_type"], item.eligibility,
                    dumps({
                        "opportunity": loads(effective["opportunity_evidence_json"], []),
                        "eligibility": item.eligibility_reasons,
                        "human_override": effective["manual_classification_evidence"],
                    }),
                    opportunity_version, now,
                    dumps({
                        "opportunity": opportunity_version,
                        "eligibility": item.eligibility_version,
                        "stack": item.stack_version,
                    }),
                ),
            )
            connection.execute(
                """
                INSERT INTO score_snapshots(
                    id, job_entity_id, score, band, components_json,
                    resume_variant, algorithm_version, created_at, route_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(), entity_id, item.score, item.band,
                    dumps(item.score_components), item.resume_variant,
                    item.score_version, now, dumps(item.score_route),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE job_entities SET source_count = ?, authoritative_open = ?,
                    status = ?, last_seen_at = ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (counts["source_count"], int(origin_open > 0), status, now, now, entity_id),
            )
        BoardIngestor._refresh_lifecycle_aggregate(connection, entity_id, now)

    @staticmethod
    def _persist_label(
        connection: sqlite3.Connection, result: ManualFetchResult, label: Label
    ) -> bool:
        row = connection.execute(
            """
            SELECT job_entity_id FROM posting_observations
            WHERE source = ? AND feed_key = ? AND external_id = ?
            """,
            (result.feed.source, result.feed.key, label.external_id),
        ).fetchone()
        if row is None:
            return False
        connection.execute(
            """
            INSERT INTO classification_snapshots(
                id, job_entity_id, opportunity_type, eligibility,
                evidence_json, algorithm_version, created_at
            ) VALUES (?, ?, 'unclassified', 'review', ?, 'source-label/1', ?)
            """,
            (
                new_id(), row["job_entity_id"],
                dumps({"name": label.name, "value": label.value, "provenance": label.provenance, "confidence": label.confidence}),
                utc_now(),
            ),
        )
        return True

    @staticmethod
    def _persist_fact(connection: sqlite3.Connection, fact: Fact) -> bool:
        normalized = normalize_name(fact.company_raw)
        company = connection.execute(
            "SELECT id FROM companies WHERE normalized_name = ? LIMIT 1", (normalized,)
        ).fetchone()
        if company is None:
            return False
        connection.execute(
            """
            INSERT INTO company_facts(
                id, company_id, name, value_json, provenance, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, name, provenance) DO UPDATE SET
                value_json = excluded.value_json,
                expires_at = excluded.expires_at
            """,
            (
                public_id("company-fact", company["id"], fact.name, fact.provenance),
                company["id"], fact.name, dumps(fact.value), fact.provenance,
                iso(fact.expires_at), utc_now(),
            ),
        )
        return True
