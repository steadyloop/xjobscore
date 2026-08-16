from __future__ import annotations

from pathlib import Path

import pytest

from xjobscore.opportunity import OpportunityError, VERDICTS, _load, canon, classify, selftest


def _canon(tmp_path: Path, body: str) -> tuple:
    path = tmp_path / "opportunity.toml"
    path.write_text(body, encoding="utf-8")
    return _load(path)


# -- classification --------------------------------------------------------


def test_shipped_canon_self_test_passes():
    """One function serves both the fixture table and this one-line check."""
    assert selftest() == []


def test_unmatched_posting_is_unclassified():
    assert classify("Regional Sales Director", "Own the enterprise pipeline.") == "unclassified"


def test_empty_description_does_not_raise():
    assert classify("Software Engineering Intern", None) == "summer_internship"
    assert classify("Software Engineering Intern", "") == "summer_internship"


def test_matching_ignores_case():
    assert classify("SOFTWARE ENGINEERING INTERN", None) == "summer_internship"


# -- ordering: the reject-before-title contract ----------------------------


def test_seniority_overrides_every_positive_signal():
    """A senior title is not a student role no matter what the body says."""
    assert classify("Senior Software Engineering Intern", None) == "unclassified"
    assert classify(
        "Senior Backend Engineer", "Must be currently enrolled in a degree program."
    ) == "unclassified"


def test_false_friend_stage_is_rejected_but_real_internship_survives():
    """`stage` is French/Dutch for internship; English also uses it for company
    maturity. The false-friends rule must reject the former without shadowing
    a genuine posting that also happens to name a stage."""
    assert classify("Account Executive, Early Stage", None) == "unclassified"
    assert classify("Early Stage Investment Intern", None) == "summer_internship"
    assert classify("Stage Fluids Engineer II", None) == "summer_internship"


def test_bare_opt_is_not_a_visa_signal():
    """`opt` alone matches `opt-in`/`opt-out` boilerplate; only the qualified
    CPT/OPT phrasing is trustworthy evidence."""
    assert classify("Manager, Options Trading", "We support opt-in benefit elections.") == "unclassified"


# -- description evidence is a fallback, not a co-signal -------------------


def test_description_evidence_only_consulted_when_title_is_silent():
    assert classify(
        "Software Engineer", "Must be currently enrolled in a Bachelor's program."
    ) == "student_role"


def test_weak_program_mention_goes_to_review_not_the_board():
    """Boilerplate that merely mentions a company's internship program must
    not be indistinguishable from a real internship posting."""
    assert classify("Software Engineer", "We offer a great internship program to all new hires.") == "review"


def test_non_english_evidence_is_strong_not_weak():
    assert classify("Ingenieur Logiciel", "Poste en alternance, 12 mois.") == "summer_internship"


# -- validation -------------------------------------------------------------


def test_unknown_key_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError, match="unknown key 'match'"):
        _canon(tmp_path, '[intern]\nverdict = "title"\ntype = "summer_internship"\nmatch = ["intern"]\n')


def test_unknown_verdict_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError, match="verdict must be one of"):
        _canon(tmp_path, '[intern]\nverdict = "maybe"\npatterns = ["intern"]\n')


def test_title_verdict_without_type_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError, match="requires a non-empty type"):
        _canon(tmp_path, '[intern]\nverdict = "title"\npatterns = ["intern"]\n')


def test_reject_verdict_with_type_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError, match="must not set type"):
        _canon(tmp_path, '[senior]\nverdict = "reject"\ntype = "summer_internship"\npatterns = ["senior"]\n')


def test_unknown_scope_is_rejected(tmp_path: Path):
    body = '[intern]\nverdict = "title"\ntype = "summer_internship"\nscope = "body"\npatterns = ["intern"]\n'
    with pytest.raises(OpportunityError, match="scope must be one of"):
        _canon(tmp_path, body)


def test_scope_defaults_to_title(tmp_path: Path):
    rules = _canon(tmp_path, '[intern]\nverdict = "title"\ntype = "summer_internship"\npatterns = ["intern"]\n')
    assert rules[0].scope == "title"


def test_empty_patterns_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError, match="non-empty array"):
        _canon(tmp_path, '[intern]\nverdict = "title"\ntype = "summer_internship"\npatterns = []\n')


def test_uppercase_pattern_is_rejected(tmp_path: Path):
    """Text is lowercased before matching, so an uppercase pattern could never hit."""
    body = '[intern]\nverdict = "title"\ntype = "summer_internship"\npatterns = ["Intern"]\n'
    with pytest.raises(OpportunityError, match="must be lowercase"):
        _canon(tmp_path, body)


def test_empty_canon_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError, match="canon is empty"):
        _canon(tmp_path, "")


def test_unreadable_canon_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError):
        _load(tmp_path / "absent.toml")


def test_malformed_toml_is_rejected(tmp_path: Path):
    with pytest.raises(OpportunityError):
        _canon(tmp_path, "[intern\nverdict =")


# -- the shipped canon -------------------------------------------------------


def test_shipped_canon_is_valid():
    rules = canon()
    assert len(rules) > 1
    for rule in rules:
        assert rule.verdict in VERDICTS, rule.name
        if rule.verdict in ("title", "evidence"):
            assert rule.type, rule.name
        else:
            assert rule.type is None, rule.name


def test_shipped_canon_rejects_seniority_before_any_title_rule():
    names = [rule.name for rule in canon()]
    senior_position = names.index("senior")
    first_title_position = next(i for i, rule in enumerate(canon()) if rule.verdict == "title")
    assert senior_position < first_title_position, names


def test_shipped_canon_places_false_friends_between_the_internship_rules():
    """`false-friends` must sit after `internship` (so "Early Stage Investment
    Intern" matches `intern` first) and before `internship-intl` (so the bare
    `stage` alias never sees an already-rejected posting)."""
    names = [rule.name for rule in canon()]
    assert names.index("internship") < names.index("false-friends") < names.index("internship-intl")


def test_canon_is_cached():
    assert canon() is canon()
