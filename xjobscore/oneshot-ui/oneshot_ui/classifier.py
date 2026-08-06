"""Pure text classification for stack evidence and hard eligibility gates."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

CLASSIFIER_VERSION = "posting-classifier/1.0"


class EvidenceLevel(str, Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    MENTIONED = "mentioned"


@dataclass(frozen=True, slots=True)
class StackEvidence:
    technology: str
    level: EvidenceLevel
    excerpt: str


@dataclass(frozen=True, slots=True)
class StackExtraction:
    evidence: tuple[StackEvidence, ...]
    algorithm_version: str = CLASSIFIER_VERSION

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(item.technology for item in self.evidence if item.level is EvidenceLevel.REQUIRED)

    @property
    def preferred(self) -> tuple[str, ...]:
        return tuple(item.technology for item in self.evidence if item.level is EvidenceLevel.PREFERRED)

    @property
    def mentioned(self) -> tuple[str, ...]:
        return tuple(item.technology for item in self.evidence if item.level is EvidenceLevel.MENTIONED)


# Canonical display name -> deliberately conservative token pattern.  The
# vocabulary is versioned along with the extractor so stored results replay.
_STACK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.I))
    for name, pattern in (
        ("Python", r"(?<![\w])python(?![\w])"),
        ("C++", r"(?<![\w])c\+\+(?![\w+])"),
        ("C", r"(?<![\w+])c(?![\w+#])"),
        ("Rust", r"(?<![\w])rust(?![\w])"),
        ("Go", r"(?<![\w])(?:go|golang)(?![\w])"),
        ("Java", r"(?<![\w])java(?!script|[\w])"),
        ("JavaScript", r"(?<![\w])javascript(?![\w])"),
        ("TypeScript", r"(?<![\w])typescript(?![\w])"),
        ("CUDA", r"(?<![\w])cuda(?![\w])"),
        ("Triton", r"(?<![\w])triton(?![\w])"),
        ("PyTorch", r"(?<![\w])pytorch(?![\w])"),
        ("TensorFlow", r"(?<![\w])tensorflow(?![\w])"),
        ("JAX", r"(?<![\w])jax(?![\w])"),
        ("TensorRT-LLM", r"(?<![\w])tensorrt(?:[\s-]?llm)?(?![\w])"),
        ("vLLM", r"(?<![\w])vllm(?![\w])"),
        ("SGLang", r"(?<![\w])sglang(?![\w])"),
        ("Ray", r"(?<![\w])ray(?![\w])"),
        ("LLVM", r"(?<![\w])llvm(?![\w])"),
        ("Linux", r"(?<![\w])linux(?![\w])"),
        ("Kubernetes", r"(?<![\w])kubernetes|(?<![\w])k8s(?![\w])"),
        ("Docker", r"(?<![\w])docker(?![\w])"),
        ("AWS", r"(?<![\w])aws(?![\w])|amazon\s+web\s+services"),
        ("GCP", r"(?<![\w])gcp(?![\w])|google\s+cloud"),
        ("Azure", r"(?<![\w])azure(?![\w])"),
        ("SQL", r"(?<![\w])sql(?![\w])"),
        ("PostgreSQL", r"(?<![\w])(?:postgresql|postgres)(?![\w])"),
        ("Redis", r"(?<![\w])redis(?![\w])"),
        ("Kafka", r"(?<![\w])(?:apache\s+)?kafka(?![\w])"),
        ("Spark", r"(?<![\w])(?:apache\s+)?spark(?![\w])"),
        ("React", r"(?<![\w])react(?:\.js|js)?(?![\w])"),
    )
)

_REQUIRED_CUE = re.compile(
    r"\b(?:required|requirements?|must|minimum qualifications?|basic qualifications?|"
    r"proficien(?:t|cy)|strong (?:knowledge|experience)|need(?:ed)?|at least)\b",
    re.I,
)
_PREFERRED_CUE = re.compile(
    r"\b(?:preferred|nice to have|bonus|a plus|desired|ideally|preferred qualifications?)\b",
    re.I,
)


def _lines(text: str) -> list[str]:
    text = re.sub(r"(?i)</?(?:li|p|div|br|h[1-6]|ul|ol)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return [re.sub(r"\s+", " ", line).strip(" \t\u2022-*:") for line in text.splitlines() if line.strip(" \t\u2022-*")]


def _heading_level(line: str) -> EvidenceLevel | None:
    if len(line) <= 80 and _PREFERRED_CUE.search(line):
        return EvidenceLevel.PREFERRED
    if len(line) <= 80 and _REQUIRED_CUE.search(line):
        return EvidenceLevel.REQUIRED
    return None


def extract_stack(text: str) -> StackExtraction:
    """Extract technology mentions while retaining requirement strength.

    Cues apply to their line.  A short qualifications heading also applies to
    following bullet/lines until another heading.  When a technology occurs
    more than once, its strongest evidence wins (required > preferred >
    mentioned), making the result deterministic and compact.
    """

    rank = {EvidenceLevel.MENTIONED: 0, EvidenceLevel.PREFERRED: 1, EvidenceLevel.REQUIRED: 2}
    selected: dict[str, StackEvidence] = {}
    section_level: EvidenceLevel | None = None

    for line in _lines(text):
        heading = _heading_level(line)
        if heading is not None and not any(pattern.search(line) for _, pattern in _STACK_PATTERNS):
            section_level = heading
            continue
        if len(line) <= 80 and re.search(r"\b(?:responsibilities|about (?:the role|us)|what you(?:'ll| will) do|benefits)\b", line, re.I):
            section_level = None

        if _REQUIRED_CUE.search(line):
            level = EvidenceLevel.REQUIRED
        elif _PREFERRED_CUE.search(line):
            level = EvidenceLevel.PREFERRED
        else:
            level = section_level or EvidenceLevel.MENTIONED

        for technology, pattern in _STACK_PATTERNS:
            if not pattern.search(line):
                continue
            evidence = StackEvidence(technology, level, line[:240])
            old = selected.get(technology)
            if old is None or rank[level] > rank[old.level]:
                selected[technology] = evidence

    return StackExtraction(tuple(selected[name] for name in sorted(selected, key=str.casefold)))


class EligibilityVerdict(str, Enum):
    ELIGIBLE = "eligible"
    REVIEW = "review"
    INELIGIBLE = "ineligible"


class EligibilityReason(str, Enum):
    CLOSED = "posting is not open"
    DEADLINE_PASSED = "stated deadline has passed"
    NO_STUDENT_SIGNAL = "no student-eligibility signal"
    GRADUATION_MISMATCH = "explicit graduation-date or class-year mismatch"
    WINDOW_MISMATCH = "explicit work-window mismatch"
    SENIORITY = "seniority is above the candidate range"
    EXPERIENCE = "stated minimum experience exceeds three years"
    ROLE_FAMILY = "role is outside CS/Math families"
    LOCATION = "location is incompatible with the work window"
    COOLDOWN = "company reapply cooldown is active"
    FULL_TIME_ENROLLMENT = "Path 4 posting requires full-time enrollment"
    ACTIVE_CLEARANCE = "active clearance requirement is a soft negative"


@dataclass(frozen=True, slots=True)
class EligibilityInput:
    title: str
    status: str = "open"
    deadline_at: date | datetime | None = None
    as_of: date | datetime | None = None
    student_signal: bool | None = None
    graduation_eligible: bool | None = None
    window_compatible: bool | None = None
    role_family_eligible: bool | None = None
    location_compatible: bool | None = None
    cooldown_active: bool | None = None
    min_years_experience: float | None = None
    path4: bool = False
    requires_full_time_enrollment: bool | None = None
    candidate_full_time_enrolled: bool | None = None
    active_clearance_required: bool = False


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    verdict: EligibilityVerdict
    reasons: tuple[EligibilityReason, ...]
    review_fields: tuple[str, ...]
    soft_negatives: tuple[EligibilityReason, ...]
    algorithm_version: str = CLASSIFIER_VERSION

    @property
    def should_score(self) -> bool:
        return self.verdict is EligibilityVerdict.ELIGIBLE


_SENIORITY = re.compile(r"\b(?:senior|sr\.?|staff|principal|lead|director|vp|head of|manager|architect|chief)\b", re.I)
_STUDENT_SIGNAL = re.compile(
    r"\b(?:intern(?:ship)?|co[\s-]?op|part[\s-]?time|contract(?:or)?|temporary|"
    r"fellow(?:ship)?|apprentice|working student|extern(?:ship)?|summer analyst|student)\b",
    re.I,
)


def has_student_signal(title: str, employment_type: str | None = None) -> bool:
    return bool(_STUDENT_SIGNAL.search(" ".join(part for part in (title, employment_type) if part)))


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _deadline_has_passed(
    deadline_at: date | datetime, as_of: date | datetime | None
) -> bool:
    if not isinstance(deadline_at, datetime):
        current_date = _as_date(as_of) if as_of is not None else date.today()
        return deadline_at <= current_date

    if isinstance(as_of, datetime):
        current_time = as_of
    elif isinstance(as_of, date):
        current_time = datetime.combine(as_of, datetime.min.time())
    else:
        current_time = datetime.now(tz=deadline_at.tzinfo)

    # Feed timestamps should be consistently aware or naive. When a caller
    # mixes them, use the timezone supplied by the other value so precision is
    # retained without raising during eligibility evaluation.
    if deadline_at.tzinfo is not None and current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=deadline_at.tzinfo)
    elif deadline_at.tzinfo is None and current_time.tzinfo is not None:
        deadline_at = deadline_at.replace(tzinfo=current_time.tzinfo)
    return deadline_at <= current_time


def evaluate_eligibility(value: EligibilityInput) -> EligibilityResult:
    """Apply drop-before-score gates; unresolved gates produce ``review``.

    ``None`` means unknown for factual gates.  The engine never converts an
    unknown into a rejection.  Missing ``min_years_experience`` means there is
    no *explicit* minimum and therefore is not an unknown exclusion.
    """

    failures: list[EligibilityReason] = []
    review: list[str] = []

    if value.status.casefold() != "open":
        failures.append(EligibilityReason.CLOSED)
    if value.deadline_at is not None and _deadline_has_passed(
        value.deadline_at, value.as_of
    ):
        failures.append(EligibilityReason.DEADLINE_PASSED)

    signal = value.student_signal
    if signal is False:
        failures.append(EligibilityReason.NO_STUDENT_SIGNAL)
    elif signal is None:
        review.append("student_signal")

    for field, reason, fail_when in (
        ("graduation_eligible", EligibilityReason.GRADUATION_MISMATCH, False),
        ("window_compatible", EligibilityReason.WINDOW_MISMATCH, False),
        ("role_family_eligible", EligibilityReason.ROLE_FAMILY, False),
        ("location_compatible", EligibilityReason.LOCATION, False),
        ("cooldown_active", EligibilityReason.COOLDOWN, True),
    ):
        current = getattr(value, field)
        if current is None:
            review.append(field)
        elif current is fail_when:
            failures.append(reason)

    if _SENIORITY.search(value.title):
        failures.append(EligibilityReason.SENIORITY)
    if value.min_years_experience is not None and value.min_years_experience > 3:
        failures.append(EligibilityReason.EXPERIENCE)

    if value.path4:
        if value.requires_full_time_enrollment is None:
            review.append("requires_full_time_enrollment")
        elif value.requires_full_time_enrollment:
            if value.candidate_full_time_enrolled is None:
                review.append("candidate_full_time_enrolled")
            elif not value.candidate_full_time_enrolled:
                failures.append(EligibilityReason.FULL_TIME_ENROLLMENT)

    soft = (EligibilityReason.ACTIVE_CLEARANCE,) if value.active_clearance_required else ()
    unique_failures = tuple(dict.fromkeys(failures))
    unique_review = tuple(dict.fromkeys(review))
    if unique_failures:
        verdict = EligibilityVerdict.INELIGIBLE
    elif unique_review:
        verdict = EligibilityVerdict.REVIEW
    else:
        verdict = EligibilityVerdict.ELIGIBLE
    return EligibilityResult(verdict, unique_failures, unique_review, soft)


__all__ = [
    "CLASSIFIER_VERSION",
    "EligibilityInput",
    "EligibilityReason",
    "EligibilityResult",
    "EligibilityVerdict",
    "EvidenceLevel",
    "StackEvidence",
    "StackExtraction",
    "evaluate_eligibility",
    "extract_stack",
    "has_student_signal",
]
