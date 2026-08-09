from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oneshot_ui.classifier import (  # noqa: E402
    EligibilityInput,
    EligibilityReason,
    EligibilityVerdict,
    EvidenceLevel,
    evaluate_eligibility,
    extract_stack,
    has_student_signal,
)
from oneshot_ui.dedupe import (  # noqa: E402
    DedupeOutcome,
    PostingIdentity,
    PostingIdentifier,
    build_identifiers,
    compare_postings,
    extract_url_identifiers,
    normalize_url,
    safe_cluster_decision,
)
from oneshot_ui.lifecycle import (  # noqa: E402
    Authority,
    LifecycleState,
    ObservationLifecycle,
    Snapshot,
    SnapshotPolicy,
    apply_explicit_status,
    reconcile_snapshots,
    resolve_entity,
)
from oneshot_ui.scoring import (  # noqa: E402
    ResumeVariant,
    ScoreBand,
    ScoreInput,
    WindowFit,
    band_for_score,
    route_for_band,
    score_opportunity,
)
from oneshot_ui.taxonomy import (  # noqa: E402
    OpportunityType,
    PRIMARY_OPPORTUNITY_TYPES,
    classify_opportunity,
)


def test_taxonomy_is_closed_and_has_stable_slugs() -> None:
    assert len(PRIMARY_OPPORTUNITY_TYPES) == 8
    assert {item.slug for item in PRIMARY_OPPORTUNITY_TYPES} == {
        "spring_internship",
        "spring_coop",
        "summer_internship",
        "fall_internship",
        "fall_coop",
        "student_researcher",
        "student_role",
        "fellowship",
    }
    assert OpportunityType.REVIEW.display_label is None


@pytest.mark.parametrize(
    ("title", "kwargs", "expected"),
    [
        ("Winter Software Internship", {}, OpportunityType.SPRING_INTERNSHIP),
        ("Winter/Spring AI Hardware Intern", {}, OpportunityType.SPRING_INTERNSHIP),
        ("Software Engineering Co-op", {"work_term": "January-June"}, OpportunityType.SPRING_COOP),
        ("Extended Co-op", {"start_month": 7, "end_month": 12}, OpportunityType.FALL_COOP),
        ("Software Intern", {"work_term": "Summer 2027"}, OpportunityType.SUMMER_INTERNSHIP),
        ("Software Intern", {"work_term": "September-December"}, OpportunityType.FALL_INTERNSHIP),
        ("Student Researcher, BS/MS", {}, OpportunityType.STUDENT_RESEARCHER),
        ("Working Student Software Engineer", {}, OpportunityType.STUDENT_ROLE),
        ("AI Safety Fellowship", {}, OpportunityType.FELLOWSHIP),
    ],
)
def test_opportunity_normalization(title: str, kwargs: dict[str, object], expected: OpportunityType) -> None:
    result = classify_opportunity(title, **kwargs)
    assert result.opportunity_type is expected
    assert result.algorithm_version


def test_taxonomy_does_not_invent_labels() -> None:
    assert classify_opportunity("Off-Cycle Software Internship").opportunity_type is OpportunityType.REVIEW
    assert classify_opportunity("Year-Round Software Intern").opportunity_type is OpportunityType.REVIEW
    assert classify_opportunity("Summer Engineering Co-op").opportunity_type is OpportunityType.REVIEW
    assert classify_opportunity("Open Source Fellowship").opportunity_type is OpportunityType.FELLOWSHIP


def test_icims_portal_url_does_not_create_an_unrelated_api_token_identifier() -> None:
    identifiers = extract_url_identifiers("https://campus-americas.icims.com/jobs/1234/example-role/job")
    assert {item.namespace for item in identifiers} == {"canonical_url"}


def test_stack_extraction_keeps_required_preferred_and_mentioned_evidence() -> None:
    text = """
    <h2>Required qualifications</h2>
    <ul><li>Strong experience with Python and C++.</li><li>Linux systems knowledge.</li></ul>
    <h2>Preferred qualifications</h2>
    <p>Experience with CUDA or PyTorch is a plus.</p>
    <h2>About the role</h2>
    <p>The team deploys Kubernetes.</p>
    """
    result = extract_stack(text)
    assert set(result.required) == {"C++", "Linux", "Python"}
    assert set(result.preferred) == {"CUDA", "PyTorch"}
    assert result.mentioned == ("Kubernetes",)
    assert all(item.excerpt for item in result.evidence)
    assert all(item.level in EvidenceLevel for item in result.evidence)


def _eligible_input(**overrides: object) -> EligibilityInput:
    values: dict[str, object] = {
        "title": "Software Engineering Intern",
        "student_signal": True,
        "graduation_eligible": True,
        "window_compatible": True,
        "role_family_eligible": True,
        "location_compatible": True,
        "cooldown_active": False,
    }
    values.update(overrides)
    return EligibilityInput(**values)


def test_eligibility_unknown_routes_to_review_and_explicit_mismatch_rejects() -> None:
    review = evaluate_eligibility(_eligible_input(location_compatible=None))
    assert review.verdict is EligibilityVerdict.REVIEW
    assert review.review_fields == ("location_compatible",)

    rejected = evaluate_eligibility(_eligible_input(graduation_eligible=False))
    assert rejected.verdict is EligibilityVerdict.INELIGIBLE
    assert EligibilityReason.GRADUATION_MISMATCH in rejected.reasons


def test_eligibility_hard_filters_and_clearance_soft_negative() -> None:
    senior = evaluate_eligibility(_eligible_input(title="Senior Software Engineer Intern"))
    assert EligibilityReason.SENIORITY in senior.reasons
    years = evaluate_eligibility(_eligible_input(min_years_experience=4))
    assert EligibilityReason.EXPERIENCE in years.reasons
    cooldown = evaluate_eligibility(_eligible_input(cooldown_active=True))
    assert EligibilityReason.COOLDOWN in cooldown.reasons
    clearance = evaluate_eligibility(_eligible_input(active_clearance_required=True))
    assert clearance.verdict is EligibilityVerdict.ELIGIBLE
    assert clearance.soft_negatives == (EligibilityReason.ACTIVE_CLEARANCE,)
    assert has_student_signal("Quantitative Developer Intern")
    assert not has_student_signal("Software Engineer I")


def test_path4_full_time_enrollment_gate_only_activates_on_path4() -> None:
    dormant = evaluate_eligibility(
        _eligible_input(path4=False, requires_full_time_enrollment=True, candidate_full_time_enrolled=False)
    )
    assert dormant.verdict is EligibilityVerdict.ELIGIBLE
    active = evaluate_eligibility(
        _eligible_input(path4=True, requires_full_time_enrollment=True, candidate_full_time_enrolled=False)
    )
    assert EligibilityReason.FULL_TIME_ENROLLMENT in active.reasons


def test_deadline_is_an_explicit_structural_filter() -> None:
    result = evaluate_eligibility(
        _eligible_input(deadline_at=date(2026, 8, 3), as_of=date(2026, 8, 4))
    )
    assert EligibilityReason.DEADLINE_PASSED in result.reasons


def test_datetime_deadline_retains_time_precision() -> None:
    deadline = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)
    before = evaluate_eligibility(
        _eligible_input(
            deadline_at=deadline,
            as_of=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        )
    )
    assert before.verdict is EligibilityVerdict.ELIGIBLE

    at_deadline = evaluate_eligibility(
        _eligible_input(deadline_at=deadline, as_of=deadline)
    )
    assert EligibilityReason.DEADLINE_PASSED in at_deadline.reasons


def test_explainable_score_components_total_and_routing() -> None:
    result = score_opportunity(
        ScoreInput(
            segment=1,
            conversion_points=25,
            window_fit=WindowFit.EXACT,
            experience_points=15,
            latency_points=10,
            comp_points=5,
            title="ML Inference Intern",
            stack=("CUDA", "PyTorch"),
        )
    )
    assert result.total == 100
    assert sum(item.points for item in result.components) == 100
    assert result.band is ScoreBand.ONE_SHOT
    assert result.resume_variant is ResumeVariant.ML_SYSTEMS
    assert result.route.auto_submit is False
    assert result.route.submit_within is None
    assert {item.maximum for item in result.components} == {30, 25, 15, 10, 5}


@pytest.mark.parametrize(
    ("score", "band"),
    [(100, ScoreBand.ONE_SHOT), (90, ScoreBand.ONE_SHOT), (89, ScoreBand.TAILORED_FAST),
     (70, ScoreBand.TAILORED_FAST), (69, ScoreBand.VOLUME), (45, ScoreBand.VOLUME),
     (44, ScoreBand.LOG_ONLY), (0, ScoreBand.LOG_ONLY)],
)
def test_score_band_boundaries(score: int, band: ScoreBand) -> None:
    assert band_for_score(score) is band


def test_submission_slas_are_manual_for_every_band() -> None:
    routes = {band: route_for_band(band) for band in ScoreBand}
    assert all(not route.auto_submit for route in routes.values())
    assert routes[ScoreBand.TAILORED_FAST].submit_within == timedelta(hours=24)
    assert routes[ScoreBand.VOLUME].submit_within == timedelta(hours=1)


def test_scoring_validates_component_bounds_and_applies_soft_penalty() -> None:
    with pytest.raises(ValueError):
        score_opportunity(ScoreInput(1, 26, WindowFit.EXACT, 15, 10, 5))
    result = score_opportunity(
        ScoreInput(1, 25, WindowFit.EXACT, 15, 10, 5, active_clearance_penalty=20)
    )
    assert result.total == 80
    assert result.components[-1].name == "active_clearance_penalty"


def test_url_normalization_and_scoped_ats_identifier_extraction() -> None:
    normalized = normalize_url(
        "HTTPS://job-boards.greenhouse.io/acme/jobs/123/?utm_source=x&b=2&a=1#apply"
    )
    assert normalized == "https://job-boards.greenhouse.io/acme/jobs/123?a=1&b=2"
    ids = extract_url_identifiers(normalized)
    assert PostingIdentifier("greenhouse", "acme", "123", "url") in ids
    assert any(item.namespace == "canonical_url" for item in ids)

    lever = extract_url_identifiers("https://jobs.lever.co/Acme/abc-123")
    assert PostingIdentifier("lever", "acme", "abc-123", "url") in lever


def test_requisition_identifier_is_never_bare() -> None:
    assert not build_identifiers(requisition_id="R-123")
    scoped = build_identifiers(employer_id="employer-1", requisition_id=" R-123 ")
    assert scoped == (PostingIdentifier("employer_req", "employer-1", "r-123", "source"),)


def _posting(
    key: str,
    *,
    employer: str = "acme",
    title: str = "Software Engineer Intern",
    identifiers: tuple[PostingIdentifier, ...] = (),
) -> PostingIdentity:
    return PostingIdentity("feed", key, employer, title, identifiers)


def test_dedupe_exact_review_related_and_new_are_evidence_bearing() -> None:
    shared = PostingIdentifier("greenhouse", "acme", "123", "url")
    exact = compare_postings(_posting("a", identifiers=(shared,)), _posting("b", identifiers=(shared,)))
    assert exact.outcome is DedupeOutcome.EXACT and exact.auto_merge
    assert exact.shared_identifiers == (shared,)

    review = compare_postings(_posting("a"), _posting("b"))
    assert review.outcome is DedupeOutcome.REVIEW and not review.auto_merge

    req1 = PostingIdentifier("employer_req", "acme", "r1", "source")
    req2 = PostingIdentifier("employer_req", "acme", "r2", "source")
    related = compare_postings(_posting("a", identifiers=(req1,)), _posting("b", identifiers=(req2,)))
    assert related.outcome is DedupeOutcome.RELATED

    new = compare_postings(_posting("a", employer="acme"), _posting("b", employer="other"))
    assert new.outcome is DedupeOutcome.NEW


def test_dedupe_blocks_unsafe_transitivity() -> None:
    ats = PostingIdentifier("greenhouse", "acme", "123", "url")
    req1 = PostingIdentifier("employer_req", "acme", "r1", "source")
    req2 = PostingIdentifier("employer_req", "acme", "r2", "source")
    candidate = _posting("candidate", identifiers=(ats, req1))
    exact_member = _posting("exact", identifiers=(ats, req1))
    contradictory_member = _posting("distinct", identifiers=(req2,))
    decision = safe_cluster_decision(candidate, (exact_member, contradictory_member))
    assert decision.outcome is DedupeOutcome.REVIEW
    assert not decision.auto_merge
    assert "transitivity" in decision.evidence[0]


NOW = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


def _snapshot(run: str, ids: set[str], *, offset: int = 0, **overrides: object) -> Snapshot:
    values: dict[str, object] = {
        "feed_id": "feed-1",
        "run_id": run,
        "observed_ids": frozenset(ids),
        "polled_at": NOW + timedelta(hours=offset),
        "complete": True,
        "closes": True,
    }
    values.update(overrides)
    return Snapshot(**values)


def test_first_and_second_absence_then_reopen_preserves_history() -> None:
    observation = ObservationLifecycle("feed-1", "job-1", Authority.EMPLOYER, last_seen_at=NOW)
    first = reconcile_snapshots(_snapshot("p1", {"job-1"}), _snapshot("p2", set(), offset=1), (observation,))
    missing = first.observations[0]
    assert missing.state is LifecycleState.UNLISTED
    assert missing.consecutive_absences == 1
    assert missing.absent_since == NOW + timedelta(hours=1)

    second = reconcile_snapshots(_snapshot("p2", set(), offset=1), _snapshot("p3", set(), offset=2), (missing,))
    closed = second.observations[0]
    assert closed.state is LifecycleState.CLOSED
    assert closed.consecutive_absences == 2

    reopened = reconcile_snapshots(_snapshot("p3", set(), offset=2), _snapshot("p4", {"job-1"}, offset=3), (closed,))
    current = reopened.observations[0]
    assert current.state is LifecycleState.OPEN
    assert current.consecutive_absences == 0
    assert reopened.reopened_ids == ("job-1",)
    assert [event.state for event in current.history] == [
        LifecycleState.UNLISTED,
        LifecycleState.CLOSED,
        LifecycleState.OPEN,
    ]


def test_reliable_authoritative_feed_can_close_on_first_absence() -> None:
    observation = ObservationLifecycle("feed-1", "job-1", Authority.CANONICAL_EMPLOYER)
    result = reconcile_snapshots(
        _snapshot("p1", {"job-1"}),
        _snapshot("p2", set(), offset=1, reliable_authoritative=True),
        (observation,),
    )
    assert result.observations[0].state is LifecycleState.CLOSED


def test_partial_304_and_suspicious_snapshots_never_close() -> None:
    observation = ObservationLifecycle("feed-1", "job-0", Authority.EMPLOYER)
    baseline = _snapshot("p1", {f"job-{number}" for number in range(20)})
    partial = reconcile_snapshots(
        baseline,
        _snapshot("p2", set(), offset=1, truncated=True),
        (observation,),
    )
    assert partial.observations[0].state is LifecycleState.OPEN

    unchanged = reconcile_snapshots(
        baseline,
        _snapshot("p2", set(), offset=1, not_modified=True),
        (observation,),
    )
    assert unchanged.observations[0].state is LifecycleState.OPEN

    suspicious = reconcile_snapshots(
        baseline,
        _snapshot("p2", set(), offset=1),
        (observation,),
        policy=SnapshotPolicy(minimum_baseline_for_drop_check=10, quarantine_drop_fraction=0.8),
    )
    assert suspicious.state is LifecycleState.QUARANTINED
    assert suspicious.observations == (observation,)


def test_explicit_closure_entity_precedence_and_authoritative_conflict() -> None:
    canonical = ObservationLifecycle("canonical", "1", Authority.CANONICAL_EMPLOYER)
    canonical = apply_explicit_status(
        canonical,
        LifecycleState.CLOSED,
        at=NOW,
        evidence="confirmed apply endpoint 410",
    )
    stale_copy = ObservationLifecycle("aggregator", "x", Authority.AGGREGATOR)
    resolved = resolve_entity((canonical, stale_copy))
    assert resolved.state is LifecycleState.CLOSED

    other_origin = ObservationLifecycle("other-ats", "2", Authority.EMPLOYER)
    conflict = resolve_entity((canonical, other_origin))
    assert conflict.state is LifecycleState.CONFLICTING
    assert set(conflict.conflicting_observations) == {canonical.key, other_origin.key}


def test_entity_remains_open_if_an_authoritative_origin_is_open() -> None:
    open_origin = ObservationLifecycle("ats-a", "1", Authority.EMPLOYER)
    absent_origin = ObservationLifecycle("ats-b", "2", Authority.EMPLOYER, state=LifecycleState.UNLISTED)
    assert resolve_entity((open_origin, absent_origin)).state is LifecycleState.OPEN
