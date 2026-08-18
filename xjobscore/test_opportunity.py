from __future__ import annotations

from pathlib import Path

import pytest

from xjobscore.opportunity import OpportunityError, _load, canon, classify, selftest


def _canon(tmp_path: Path, body: str) -> tuple:
    path = tmp_path / "type.yaml"
    path.write_text(body, encoding="utf-8")
    return _load(path)


# -- classification --------------------------------------------------------


def test_shipped_canon_self_test_passes():
    """One function serves both the fixture table and this one-line check."""
    assert selftest() == []


def test_unmatched_posting_is_unclassified():
    assert classify("Regional Sales Director") == "unclassified"


def test_matching_ignores_case():
    assert classify("SOFTWARE ENGINEERING INTERN") == "internship"


# -- narrow shipped behavior -----------------------------------------------


@pytest.mark.parametrize("title", ["Software Co-op", "Software Coop", "Software Co op"])
def test_coop_title_is_classified(title: str):
    assert classify(title) == "coop"


@pytest.mark.parametrize(
    "title",
    [
        "Student Researcher",
        "Undergraduate Researcher",
        "Research Student",
        "Graduate Research Appointment",
        "Undergraduate Research Associate",
    ],
)
def test_student_research_title_is_classified(title: str):
    assert classify(title) == "student_researcher"


def test_research_internship_and_coop_keep_their_program_type():
    assert classify("Student Researcher Intern") == "internship"
    assert classify("Undergraduate Research Co-op") == "coop"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("AI Research Fellowship", "fellowship"),
        ("Working Student", "student_role"),
        ("New Graduate Engineer", "new_grad_program"),
        ("Software Apprentice", "apprenticeship"),
    ],
)
def test_explicit_student_opportunity_titles_are_classified(title: str, expected: str):
    assert classify(title) == expected


def test_ambiguous_research_and_fellow_titles_remain_unclassified():
    assert classify("Clinical Research Assistant") == "unclassified"
    assert classify("Principal Engineering Fellow") == "unclassified"


def test_internship_wins_when_categories_overlap():
    assert classify("AI Research Fellowship Intern") == "internship"
    assert classify("Intern / Working Student") == "internship"


# -- validation -------------------------------------------------------------


def test_unknown_key_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError, match="unknown key 'match'"):
        _canon(tmp_path, 'type:\n  - name: intern\n    type: summer_internship\n    match: [intern]\n')


def test_scope_and_verdict_are_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError, match="unknown key 'scope'"):
        _canon(
            tmp_path,
            'type:\n  - name: intern\n    type: summer_internship\n    scope: description\n    patterns: [intern]\n',
        )
    with pytest.raises(OpportunityError, match="unknown key 'verdict'"):
        _canon(
            tmp_path,
            'type:\n  - name: intern\n    verdict: title\n    type: summer_internship\n    patterns: [intern]\n',
        )


def test_missing_type_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError, match="type must be a non-empty string"):
        _canon(tmp_path, 'type:\n  - name: intern\n    patterns: [intern]\n')


def test_empty_patterns_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError, match="non-empty array"):
        _canon(tmp_path, 'type:\n  - name: intern\n    type: summer_internship\n    patterns: []\n')


def test_uppercase_pattern_is_rejected(tmp_path: Path):
    """Text is lowercased before matching, so an uppercase pattern could never hit."""
    body = 'type:\n  - name: intern\n    type: summer_internship\n    patterns: [Intern]\n'
    with pytest.raises(OpportunityError, match="must be lowercase"):
        _canon(tmp_path, body)


def test_empty_canon_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError, match="canon is empty"):
        _canon(tmp_path, "type: []\n")


def test_unreadable_canon_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError):
        _load(tmp_path / "absent.yaml")


def test_malformed_yaml_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError):
        _canon(tmp_path, "type:\n  - name: [intern\n")


# -- the shipped canon -------------------------------------------------------


def test_shipped_canon_is_valid():
    rules = canon()
    assert [rule.name for rule in rules] == [
        "internship",
        "coop",
        "fellowship",
        "apprenticeship",
        "new-grad",
        "student-research",
        "student",
    ]
    for rule in rules:
        assert rule.type in {
            "internship",
            "coop",
            "fellowship",
            "apprenticeship",
            "new_grad_program",
            "student_researcher",
            "student_role",
        }, rule.name


def test_canon_is_cached():
    assert canon() is canon()
