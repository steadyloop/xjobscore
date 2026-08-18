"""Classify explicit student-opportunity titles.

The shipped canon intentionally contains only title rules. Rule order remains
semantic: the first match wins.

``classify`` accepts only a title and never returns nothing: an unmatched
posting is `unclassified`, so every completed gold enrichment has a total
classification.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

__all__ = ["OpportunityError", "Rule", "OPPORTUNITY_PATH", "canon", "classify", "selftest", "SAMPLES"]

OPPORTUNITY_PATH = Path(__file__).parent / "registry" / "type.yaml"

_FIELDS = {"name", "type", "patterns"}

# `\b` fails on symbol-bearing or non-Latin patterns ("C++", "实习"); explicit
# lookaround keeps every alias matchable while still rejecting a bare substring
# inside a longer word.
_LEFT = r"(?<![a-z0-9])"
_RIGHT = r"(?![a-z0-9])"


class OpportunityError(ValueError):
    """The opportunity canon is malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    type: str
    patterns: tuple[str, ...]


def _entry(row: object, index: int, path: Path) -> Rule:
    where = f"{path}: type[{index}]"
    if not isinstance(row, dict):
        raise OpportunityError(f"{where} must be a mapping")
    unknown = sorted(set(row) - _FIELDS)
    if unknown:
        raise OpportunityError(f"{where}: unknown key {unknown[0]!r}")

    name = row.get("name")
    if not isinstance(name, str) or not name:
        raise OpportunityError(f"{where}: name must be a non-empty string")

    opportunity_type = row.get("type")
    if not isinstance(opportunity_type, str) or not opportunity_type:
        raise OpportunityError(f"{where}: type must be a non-empty string")

    patterns = row.get("patterns", [])
    if not isinstance(patterns, list) or not patterns:
        raise OpportunityError(f"{where}: patterns must be a non-empty array")
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern:
            raise OpportunityError(f"{where}: patterns must be non-empty strings")
        if pattern != pattern.lower():
            raise OpportunityError(f"{where}: pattern {pattern!r} must be lowercase")

    return Rule(
        name=name,
        type=opportunity_type,
        patterns=tuple(patterns),
    )


def _load(path: Path) -> tuple[Rule, ...]:
    try:
        with path.open(encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise OpportunityError(f"{path}: {exc}") from exc

    if not isinstance(document, dict) or "type" not in document:
        raise OpportunityError(f"{path}: root must contain a 'type' array")
    unknown = sorted(set(document) - {"type", "degree"})
    if unknown:
        raise OpportunityError(f"{path}: unknown root key {unknown[0]!r}")
    rows = document["type"]
    if not isinstance(rows, list):
        raise OpportunityError(f"{path}: type must be an array")

    rules = tuple(_entry(row, index, path) for index, row in enumerate(rows))
    if not rules:
        raise OpportunityError(f"{path}: canon is empty")
    return rules


@functools.lru_cache(maxsize=1)
def canon() -> tuple[Rule, ...]:
    """The validated rule table, in evaluation order. Read once, then cached."""
    return _load(OPPORTUNITY_PATH)


@functools.lru_cache(maxsize=1)
def _matchers() -> tuple[tuple[Rule, re.Pattern[str]], ...]:
    """Compile one title-pattern alternation per rule."""
    return tuple(
        (rule, re.compile(f"{_LEFT}({'|'.join(rule.patterns)}){_RIGHT}"))
        for rule in canon()
    )


def classify(title: str) -> str:
    """Return a title-derived ``opportunity_type`` or ``unclassified``."""
    title_text = (title or "").lower()
    for rule, pattern in _matchers():
        if pattern.search(title_text):
            return rule.type
    return "unclassified"


# (title, expected) -- descriptions cannot enter classification.
SAMPLES: tuple[tuple[str, str], ...] = (
    ("Software Engineering Intern", "internship"),
    ("Software Engineering Co-op", "coop"),
    ("AI Research Fellowship", "fellowship"),
    ("Software Apprentice", "apprenticeship"),
    ("Software Engineer, New Grad", "new_grad_program"),
    ("Graduate Research Appointment", "student_researcher"),
    ("Working Student, Platform Engineering", "student_role"),
    ("Student Researcher Intern", "internship"),
    ("Software Engineer", "unclassified"),
)


def selftest() -> list[str]:
    """Return all fixture failures; an empty result means the canon and the
    classifier agree with the recorded evidence."""
    failures = []
    for title, expected in SAMPLES:
        actual = classify(title)
        if actual != expected:
            failures.append(f"{title!r}: expected {expected!r}, got {actual!r}")
    return failures
