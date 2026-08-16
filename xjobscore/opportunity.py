"""Classify a posting's title and description against ``registry/opportunity.toml``.

One rule table, evaluated in order, first match wins. Unlike ``skills.py`` --
whose canon is an unordered set of independent labels -- rule order here is
semantic: a `reject` rule must run before the `title` rules it guards, and the
false-friends rule for "stage" must sit between the English and non-English
internship rules. See the TOML's header comment for the placement contract.

``classify`` never returns nothing: an unmatched posting is `unclassified`,
exactly like an explicit `reject`, so the field is always present and the
API's default-scope filter stays total.
"""

from __future__ import annotations

import functools
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["OpportunityError", "Rule", "OPPORTUNITY_PATH", "canon", "classify", "selftest", "SAMPLES"]

OPPORTUNITY_PATH = Path(__file__).parent / "registry" / "opportunity.toml"

VERDICTS = ("reject", "title", "evidence", "weak")
SCOPES = ("title", "description")
_FIELDS = {"verdict", "type", "scope", "patterns"}

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
    verdict: str
    type: str | None
    scope: str
    patterns: tuple[str, ...]


def _entry(name: object, row: object, path: Path) -> Rule:
    where = f"{path}: [{name}]"
    if not isinstance(name, str) or not name:
        raise OpportunityError(f"{path}: rule name must be a non-empty string")
    if not isinstance(row, dict):
        raise OpportunityError(f"{where} must be a TOML table")
    unknown = sorted(set(row) - _FIELDS)
    if unknown:
        raise OpportunityError(f"{where}: unknown key {unknown[0]!r}")

    verdict = row.get("verdict")
    if verdict not in VERDICTS:
        raise OpportunityError(f"{where}: verdict must be one of {VERDICTS}, got {verdict!r}")

    opportunity_type = row.get("type")
    if verdict in ("title", "evidence"):
        if not isinstance(opportunity_type, str) or not opportunity_type:
            raise OpportunityError(f"{where}: verdict {verdict!r} requires a non-empty type")
    elif opportunity_type is not None:
        raise OpportunityError(f"{where}: verdict {verdict!r} must not set type")

    scope = row.get("scope", "title")
    if scope not in SCOPES:
        raise OpportunityError(f"{where}: scope must be one of {SCOPES}, got {scope!r}")

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
        verdict=verdict,
        type=opportunity_type,
        scope=scope,
        patterns=tuple(patterns),
    )


def _load(path: Path) -> tuple[Rule, ...]:
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise OpportunityError(f"{path}: {exc}") from exc

    rules = tuple(_entry(name, row, path) for name, row in document.items())
    if not rules:
        raise OpportunityError(f"{path}: canon is empty")
    return rules


@functools.lru_cache(maxsize=1)
def canon() -> tuple[Rule, ...]:
    """The validated rule table, in evaluation order. Read once, then cached."""
    return _load(OPPORTUNITY_PATH)


@functools.lru_cache(maxsize=1)
def _matchers() -> tuple[tuple[Rule, re.Pattern[str]], ...]:
    """One compiled alternation per rule, not per pattern.

    Most postings fall through every title rule to the description rules, so
    per-pattern compilation would mean a regex pass per alias per posting.
    Per-rule keeps it to one pass per rule.
    """
    return tuple(
        (rule, re.compile(f"{_LEFT}({'|'.join(rule.patterns)}){_RIGHT}"))
        for rule in canon()
    )


def classify(title: str, description: str | None) -> str:
    """The ``opportunity_type`` for a posting. Never absent -- an unmatched
    posting is ``unclassified``, same as an explicit reject."""
    title_text = (title or "").lower()
    description_text = (description or "").lower()
    for rule, pattern in _matchers():
        haystack = title_text if rule.scope == "title" else description_text
        if not pattern.search(haystack):
            continue
        if rule.verdict == "reject":
            return "unclassified"
        if rule.verdict == "weak":
            return "review"
        assert rule.type is not None
        return rule.type
    return "unclassified"


# (title, description, expected) -- covers every trap recon found, so a future
# alias addition that reopens one fails loudly rather than silently.
SAMPLES: tuple[tuple[str, str | None, str], ...] = (
    ("Software Engineering Intern", None, "summer_internship"),
    ("Senior Software Engineering Intern", None, "unclassified"),
    ("Praktikant Data Science", None, "summer_internship"),
    ("Account Executive, Early Stage", None, "unclassified"),
    ("Early Stage Investment Intern", None, "summer_internship"),
    ("Stage Fluids Engineer II", None, "summer_internship"),
    ("Senior Research Fellow", None, "unclassified"),
    ("Research Fellow", None, "fellowship"),
    ("Werkstudent Marketing", None, "spring_coop"),
    ("Ausbildung zum Elektroniker", None, "spring_coop"),
    ("Student Researcher, ML", None, "student_researcher"),
    ("PhD Student, Robotics", None, "student_researcher"),
    ("Campus Recruiting Program", None, "student_role"),
    ("Software Engineer", "Must be currently enrolled in a Bachelor's program.", "student_role"),
    ("Software Engineer", "We offer a great internship program to all new hires.", "review"),
    ("Software Engineer", "We are looking for a strong backend engineer.", "unclassified"),
    ("Senior Backend Engineer", "Must be currently enrolled in a degree program.", "unclassified"),
    ("Manager, Options Trading", "We support opt-in benefit elections.", "unclassified"),
)


def selftest() -> list[str]:
    """Return all fixture failures; an empty result means the canon and the
    classifier agree with the recorded evidence."""
    failures = []
    for title, description, expected in SAMPLES:
        actual = classify(title, description)
        if actual != expected:
            failures.append(f"{title!r}: expected {expected!r}, got {actual!r}")
    return failures
