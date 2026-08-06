"""Explainable 0-100 opportunity scoring and routing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Iterable

SCORING_VERSION = "opportunity-score/1.0"


class WindowFit(str, Enum):
    EXACT = "exact"
    NEGOTIATION = "needs_start_date_negotiation"
    QUARTER_OFF = "needs_quarter_off"
    PATH4_SIX_PLUS_MONTHS = "path4_6_plus_months"
    PATH4_THREE_TO_SIX_MONTHS = "path4_3_to_6_months"
    PATH4_THREE_OR_FEWER_MONTHS = "path4_3_or_fewer_months"


WINDOW_POINTS: dict[WindowFit, int] = {
    WindowFit.EXACT: 15,
    WindowFit.NEGOTIATION: 8,
    WindowFit.QUARTER_OFF: 5,
    WindowFit.PATH4_SIX_PLUS_MONTHS: 15,
    WindowFit.PATH4_THREE_TO_SIX_MONTHS: 8,
    WindowFit.PATH4_THREE_OR_FEWER_MONTHS: 3,
}

SEGMENT_POINTS = {1: 30, 2: 25, 3: 25, 4: 15, 5: 7}


class ScoreBand(str, Enum):
    ONE_SHOT = "one_shot"
    TAILORED_FAST = "tailored_fast"
    VOLUME = "volume"
    LOG_ONLY = "log_only"


class ResumeVariant(str, Enum):
    ML_SYSTEMS = "ml_systems"
    SYSTEMS = "systems"
    QUANT = "quant"
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class ScoreInput:
    segment: int
    conversion_points: int
    window_fit: WindowFit
    experience_points: int
    latency_points: int
    comp_points: int
    title: str = ""
    stack: tuple[str, ...] = ()
    # Active-clearance handling is deliberately an explicit, auditable soft
    # modifier rather than a hidden hard filter.  Zero is the neutral default.
    active_clearance_penalty: int = 0


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    name: str
    points: int
    maximum: int
    explanation: str


@dataclass(frozen=True, slots=True)
class SubmissionRoute:
    band: ScoreBand
    submit_within: timedelta | None
    auto_submit: bool
    instruction: str


@dataclass(frozen=True, slots=True)
class ScoreResult:
    total: int
    components: tuple[ScoreComponent, ...]
    band: ScoreBand
    resume_variant: ResumeVariant
    route: SubmissionRoute
    algorithm_version: str = SCORING_VERSION


def _bounded(name: str, value: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} must be in 0..{maximum}")
    return value


def band_for_score(score: int) -> ScoreBand:
    if not 0 <= score <= 100:
        raise ValueError("score must be in 0..100")
    if score >= 90:
        return ScoreBand.ONE_SHOT
    if score >= 70:
        return ScoreBand.TAILORED_FAST
    if score >= 45:
        return ScoreBand.VOLUME
    return ScoreBand.LOG_ONLY


def route_for_band(band: ScoreBand) -> SubmissionRoute:
    if band is ScoreBand.ONE_SHOT:
        return SubmissionRoute(
            band,
            None,
            False,
            "Apply once per cycle at open with tailored materials and a referral; never auto-submit.",
        )
    if band is ScoreBand.TAILORED_FAST:
        return SubmissionRoute(
            band,
            timedelta(hours=24),
            False,
            "Submit a project-matched resume and three-sentence note within 24 hours.",
        )
    if band is ScoreBand.VOLUME:
        return SubmissionRoute(
            band,
            timedelta(hours=1),
            False,
            "Manually submit the standard resume variant within one hour.",
        )
    return SubmissionRoute(band, None, False, "Log for discovery; do not apply or spend a slot.")


def choose_resume_variant(title: str = "", stack: Iterable[str] = ()) -> ResumeVariant:
    text = " ".join((title, *(str(item) for item in stack))).casefold()
    quant = ("quant", "trading", "market maker", "low latency", "hft")
    ml_systems = (
        "machine learning",
        "ml ",
        "inference",
        "cuda",
        "pytorch",
        "triton",
        "tensorrt",
        "vllm",
        "sglang",
        "gpu",
    )
    systems = (
        "distributed",
        "backend",
        "compiler",
        "linux",
        "rust",
        "c++",
        "kubernetes",
        "database",
        "infrastructure",
        "performance",
    )
    if any(token in text for token in quant):
        return ResumeVariant.QUANT
    if any(token in text for token in ml_systems):
        return ResumeVariant.ML_SYSTEMS
    if any(token in text for token in systems):
        return ResumeVariant.SYSTEMS
    return ResumeVariant.GENERAL


def score_opportunity(value: ScoreInput) -> ScoreResult:
    """Compute the documented six-component score and submission route."""

    if value.segment not in SEGMENT_POINTS:
        raise ValueError("segment must be one of 1, 2, 3, 4, 5")
    segment = SEGMENT_POINTS[value.segment]
    conversion = _bounded("conversion_points", value.conversion_points, 25)
    experience = _bounded("experience_points", value.experience_points, 15)
    latency = _bounded("latency_points", value.latency_points, 10)
    comp = _bounded("comp_points", value.comp_points, 5)
    penalty = _bounded("active_clearance_penalty", value.active_clearance_penalty, 100)
    window = WINDOW_POINTS[value.window_fit]

    components = (
        ScoreComponent("segment_fit", segment, 30, f"segment {value.segment}"),
        ScoreComponent("conversion_probability", conversion, 25, "explicit conversion assessment"),
        ScoreComponent("window_fit", window, 15, value.window_fit.value),
        ScoreComponent("experience_value", experience, 15, "code ownership, named team, and target stack"),
        ScoreComponent("latency_advantage", latency, 10, "review cadence and applicant-flow advantage"),
        ScoreComponent("compensation", comp, 5, "compensation is a tiebreaker"),
    )
    subtotal = sum(component.points for component in components)
    total = max(0, min(100, subtotal - penalty))
    if penalty:
        components += (
            ScoreComponent(
                "active_clearance_penalty",
                -penalty,
                0,
                "soft negative; active clearance is required",
            ),
        )
    band = band_for_score(total)
    return ScoreResult(
        total,
        components,
        band,
        choose_resume_variant(value.title, value.stack),
        route_for_band(band),
    )


__all__ = [
    "SCORING_VERSION",
    "SEGMENT_POINTS",
    "WINDOW_POINTS",
    "ResumeVariant",
    "ScoreBand",
    "ScoreComponent",
    "ScoreInput",
    "ScoreResult",
    "SubmissionRoute",
    "WindowFit",
    "band_for_score",
    "choose_resume_variant",
    "route_for_band",
    "score_opportunity",
]
