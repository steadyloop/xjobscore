"""Deterministic opportunity-label normalization.

The six primary labels in this module are a closed vocabulary.  ``REVIEW``
and ``UNCLASSIFIED`` are workflow outcomes, not additional work-slot labels.
No classifier in this module makes a network or model call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

TAXONOMY_VERSION = "opportunity-taxonomy/1.0"


class OpportunityType(str, Enum):
    SPRING_INTERNSHIP = "Spring Internship"
    SPRING_COOP = "Spring Co-op"
    SUMMER_INTERNSHIP = "Summer Internship"
    FALL_INTERNSHIP = "Fall Internship"
    FALL_COOP = "Fall Co-op"
    STUDENT_RESEARCHER = "Student Researcher"

    # Workflow outcomes.  These must never be rendered as month-table labels.
    UNCLASSIFIED = "unclassified"
    REVIEW = "review"

    @property
    def slug(self) -> str:
        return {
            OpportunityType.SPRING_INTERNSHIP: "spring_internship",
            OpportunityType.SPRING_COOP: "spring_coop",
            OpportunityType.SUMMER_INTERNSHIP: "summer_internship",
            OpportunityType.FALL_INTERNSHIP: "fall_internship",
            OpportunityType.FALL_COOP: "fall_coop",
            OpportunityType.STUDENT_RESEARCHER: "student_researcher",
            OpportunityType.UNCLASSIFIED: "unclassified",
            OpportunityType.REVIEW: "review",
        }[self]

    @property
    def display_label(self) -> str | None:
        return self.value if self in PRIMARY_OPPORTUNITY_TYPES else None


PRIMARY_OPPORTUNITY_TYPES: tuple[OpportunityType, ...] = (
    OpportunityType.SPRING_INTERNSHIP,
    OpportunityType.SPRING_COOP,
    OpportunityType.SUMMER_INTERNSHIP,
    OpportunityType.FALL_INTERNSHIP,
    OpportunityType.FALL_COOP,
    OpportunityType.STUDENT_RESEARCHER,
)


@dataclass(frozen=True, slots=True)
class OpportunityClassification:
    opportunity_type: OpportunityType
    evidence: tuple[str, ...]
    confidence: float
    algorithm_version: str = TAXONOMY_VERSION

    @property
    def needs_review(self) -> bool:
        return self.opportunity_type is OpportunityType.REVIEW

    @property
    def is_primary(self) -> bool:
        return self.opportunity_type in PRIMARY_OPPORTUNITY_TYPES


_COOP = re.compile(r"\b(?:co[\s-]?op|cooperative\s+education)\b", re.I)
_INTERNSHIP = re.compile(r"\bintern(?:ship)?\b", re.I)
_STUDENT_RESEARCHER = re.compile(r"\bstudent\s+researcher\b", re.I)
_FALLBACK = re.compile(
    r"\b(?:fellow(?:ship)?|extern(?:ship)?|part[\s-]?time\s+student(?:\s+developer)?|"
    r"academic[\s-]?year|contract(?:or)?|open[\s-]?source)\b",
    re.I,
)

_SPRING = re.compile(
    r"\b(?:winter(?:\s*/\s*spring)?|winter[\s-]+spring|spring|"
    r"jan(?:uary)?\s*(?:-|\u2013|\u2014|to|through)\s*(?:apr(?:il)?|may|jun(?:e)?))\b",
    re.I,
)
_SUMMER = re.compile(
    r"\b(?:summer|may\s*(?:-|\u2013|\u2014|to|through)\s*aug(?:ust)?|"
    r"jun(?:e)?\s*(?:-|\u2013|\u2014|to|through)\s*aug(?:ust)?)\b",
    re.I,
)
_FALL = re.compile(
    r"\b(?:fall|autumn|sep(?:t(?:ember)?)?\s*(?:-|\u2013|\u2014|to|through)\s*dec(?:ember)?|"
    r"jul(?:y)?\s*(?:-|\u2013|\u2014|to|through)\s*dec(?:ember)?)\b",
    re.I,
)
_OFF_CYCLE = re.compile(r"\boff[\s-]?cycle\b", re.I)
_YEAR_ROUND = re.compile(r"\byear[\s-]?round\b", re.I)
_EXTENDED = re.compile(r"\bextended\s+co[\s-]?op\b", re.I)


def _seasons(text: str) -> set[str]:
    result: set[str] = set()
    if _SPRING.search(text):
        result.add("spring")
    if _SUMMER.search(text):
        result.add("summer")
    if _FALL.search(text):
        result.add("fall")
    return result


def _season_from_months(start_month: int | None, end_month: int | None, *, coop: bool) -> str | None:
    if start_month is None:
        return None
    if not 1 <= start_month <= 12 or (end_month is not None and not 1 <= end_month <= 12):
        raise ValueError("start_month and end_month must be in 1..12")
    if start_month in (1, 2):
        return "spring"
    if start_month in (5, 6):
        return "summer"
    if start_month == 7:
        # July-December is explicitly a Fall co-op; July-August remains summer.
        if coop and end_month is not None and end_month >= 11:
            return "fall"
        return "summer"
    if start_month in (8, 9):
        return "fall"
    return None


def classify_opportunity(
    title: str,
    *,
    employment_type: str | None = None,
    description: str | None = None,
    work_term: str | None = None,
    start_month: int | None = None,
    end_month: int | None = None,
) -> OpportunityClassification:
    """Normalize employer wording into the closed work-slot vocabulary.

    High-signal title/employment wording wins over incidental seasons in a
    description.  Ambiguous or contradictory season evidence is routed to
    review; missing season evidence is left unclassified rather than guessed.
    """

    primary_text = " ".join(part for part in (title, employment_type, work_term) if part)
    all_text = " ".join(part for part in (primary_text, description) if part)

    if _STUDENT_RESEARCHER.search(primary_text):
        return OpportunityClassification(
            OpportunityType.STUDENT_RESEARCHER,
            ("employer wording: student researcher",),
            1.0,
        )

    coop = bool(_COOP.search(primary_text))
    internship = bool(_INTERNSHIP.search(primary_text))
    if coop and internship:
        return OpportunityClassification(
            OpportunityType.REVIEW,
            ("both internship and co-op role types are stated",),
            0.0,
        )

    if not coop and not internship:
        if _STUDENT_RESEARCHER.search(all_text):
            return OpportunityClassification(
                OpportunityType.REVIEW,
                ("student researcher appears only outside the primary role wording",),
                0.5,
            )
        reason = "fallback opportunity type is outside the six-label vocabulary" if _FALLBACK.search(all_text) else "no primary opportunity type stated"
        return OpportunityClassification(OpportunityType.UNCLASSIFIED, (reason,), 0.0)

    seasons = _seasons(primary_text)
    evidence_source = "primary role wording"
    if not seasons and work_term is None and description:
        seasons = _seasons(description)
        evidence_source = "description"

    month_season = _season_from_months(start_month, end_month, coop=coop)
    if month_season:
        seasons.add(month_season)
        evidence_source = "stated term/months"

    if len(seasons) > 1:
        return OpportunityClassification(
            OpportunityType.REVIEW,
            (f"conflicting seasons: {', '.join(sorted(seasons))}",),
            0.0,
        )

    if not seasons:
        special = []
        if _OFF_CYCLE.search(all_text):
            special.append("off-cycle requires a Spring or Fall term")
        if _YEAR_ROUND.search(all_text):
            special.append("year-round is availability, not an opportunity label")
        if _EXTENDED.search(all_text):
            special.append("extended co-op requires its actual work term")
        if special:
            return OpportunityClassification(OpportunityType.REVIEW, tuple(special), 0.0)
        return OpportunityClassification(
            OpportunityType.UNCLASSIFIED,
            ("role type is known but work season is not stated",),
            0.25,
        )

    season = next(iter(seasons))
    if coop:
        if season == "spring":
            label = OpportunityType.SPRING_COOP
        elif season == "fall":
            label = OpportunityType.FALL_COOP
        else:
            # Summer co-op is not in the closed vocabulary.  It cannot be
            # silently rewritten as an internship.
            return OpportunityClassification(
                OpportunityType.REVIEW,
                ("Summer Co-op has no label in the closed vocabulary",),
                0.0,
            )
    else:
        label = {
            "spring": OpportunityType.SPRING_INTERNSHIP,
            "summer": OpportunityType.SUMMER_INTERNSHIP,
            "fall": OpportunityType.FALL_INTERNSHIP,
        }[season]

    normalized = "Winter normalized to Spring" if season == "spring" and re.search(r"\bwinter\b", primary_text, re.I) else f"{season.title()} term"
    return OpportunityClassification(label, (normalized, evidence_source), 1.0)


__all__ = [
    "OpportunityClassification",
    "OpportunityType",
    "PRIMARY_OPPORTUNITY_TYPES",
    "TAXONOMY_VERSION",
    "classify_opportunity",
]
