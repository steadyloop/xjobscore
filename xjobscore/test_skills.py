from __future__ import annotations

from pathlib import Path

import pytest

from xjobscore.skills import (
    CATEGORIES,
    Skill,
    SkillMatcher,
    SkillsError,
    _load,
    canon,
    extract,
    finditer,
)


def _canon(tmp_path: Path, body: str) -> dict[str, Skill]:
    path = tmp_path / "skills.yaml"
    path.write_text(body, encoding="utf-8")
    return _load(path)


def _skill(
    skill_id: str,
    *aliases: str,
    case_sensitive: bool = False,
) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id,
        category="stack",
        aliases=aliases,
        ambiguous_aliases=(),
        case_sensitive=case_sensitive,
    )


# -- Aho--Corasick matching ----------------------------------------------


def test_names_resolve_through_their_declared_aliases():
    assert extract("We build kernels with CUTLASS and NCCL.") == ["cutlass", "nccl"]


def test_aliases_collapse_onto_one_id():
    assert extract("Kubernetes") == extract("K8s") == ["kubernetes"]
    assert extract("We run Kubernetes; k8s experience required.") == ["kubernetes"]


def test_case_insensitive_aliases_are_casefolded():
    assert extract("CUTLASS") == extract("cutlass") == extract("Cutlass") == ["cutlass"]


def test_case_sensitive_aliases_preserve_case():
    matcher = SkillMatcher([_skill("sql", "SQL", case_sensitive=True)])
    assert matcher.extract("sql is prose; SQL is the skill") == ["sql"]
    assert matcher.extract("lowercase sql only") == []


def test_symbol_bearing_aliases_match():
    assert extract("Strong C++ required.") == ["cpp"]


def test_word_boundaries_reject_substrings():
    assert extract("Rustacean culture and gitignore files") == []
    assert extract("We use Rust. Check Git.") == ["git", "rust"]


def test_leftmost_longest_alias_wins_over_nested_skills():
    assert extract("CUDA C++") == ["cuda-cpp"]
    assert extract("numerical linear algebra") == ["numerical-linear-algebra"]
    assert extract("NVIDIA Triton Inference Server") == ["triton-inference-server"]


def test_failure_links_find_alias_after_a_partial_prefix():
    matcher = SkillMatcher([_skill("decoy", "x-barz"), _skill("bar", "bar")])
    assert matcher.extract("x-bar") == ["bar"]


def test_hits_are_deduplicated_and_sorted():
    assert extract("Linux, Docker, Linux, docker, LINUX") == ["docker", "linux"]


def test_empty_and_unmatched_text_yield_nothing():
    assert extract("") == []
    assert extract("Nothing technical here.") == []


def test_extract_only_emits_canon_ids():
    text = "CUDA, PyTorch, Kubernetes, Fortran, telepathy, roofline"
    assert set(extract(text)) <= set(canon())


def test_finditer_returns_source_evidence_and_offsets():
    text = "Use K8s and PYTHON."
    matches = list(finditer(text))
    assert [(match.skill_id, match.matched_text) for match in matches] == [
        ("kubernetes", "K8s"),
        ("python", "PYTHON"),
    ]
    assert all(text[match.start : match.end] == match.matched_text for match in matches)


def test_casefolding_preserves_offsets_after_expanding_unicode():
    matcher = SkillMatcher([_skill("python", "python")])
    text = "İ PYTHON"
    match = list(matcher.finditer(text))[0]
    assert (match.start, match.end, match.matched_text) == (2, 8, "PYTHON")


# -- ambiguous aliases ---------------------------------------------------


def test_ambiguous_aliases_are_not_matched():
    assert extract("You will go to market and go the extra mile.") == []
    assert extract("Reporting to C-level executives. Grade: C.") == []
    assert extract("A triton is a type of salamander.") == []


def test_ambiguous_skills_remain_reachable_through_safe_aliases():
    assert extract("Backend written in Golang") == ["go"]


def test_ambiguity_is_declared_separately_from_matchable_aliases():
    entries = canon()
    assert entries["go"].aliases == ("golang",)
    assert entries["go"].ambiguous_aliases == ("go",)
    assert entries["c"].patterns == ()
    assert entries["triton"].patterns == ()


# -- shipped canon -------------------------------------------------------


def test_shipped_yaml_canon_is_valid():
    entries = canon()
    assert len(entries) > 1
    for skill_id, skill in entries.items():
        assert skill.category in CATEGORIES, skill_id
        assert skill_id == skill_id.lower(), skill_id


def test_canon_is_cached():
    assert canon() is canon()


# -- YAML validation -----------------------------------------------------


def test_optional_alias_fields_and_case_sensitivity_default(tmp_path: Path):
    entries = _canon(
        tmp_path,
        "skill:\n  - id: duckdb\n    name: DuckDB\n    category: stack\n",
    )
    assert entries["duckdb"].aliases == ()
    assert entries["duckdb"].ambiguous_aliases == ()
    assert entries["duckdb"].case_sensitive is False


def test_unknown_key_is_rejected(tmp_path: Path):
    body = (
        "skill:\n  - id: rust\n    name: Rust\n    category: language\n"
        "    alias: [rustlang]\n"
    )
    with pytest.raises(SkillsError, match="unknown key 'alias'"):
        _canon(tmp_path, body)


def test_unknown_or_missing_category_is_rejected(tmp_path: Path):
    with pytest.raises(SkillsError, match="category must be one of"):
        _canon(tmp_path, "skill:\n  - id: rust\n    name: Rust\n    category: framework\n")
    with pytest.raises(SkillsError, match="category must be one of"):
        _canon(tmp_path, "skill:\n  - id: rust\n    name: Rust\n")


def test_missing_name_is_rejected(tmp_path: Path):
    with pytest.raises(SkillsError, match="name must be a non-empty string"):
        _canon(tmp_path, "skill:\n  - id: rust\n    category: language\n")


def test_aliases_must_be_string_arrays(tmp_path: Path):
    body = "skill:\n  - id: rust\n    name: Rust\n    category: language\n    aliases: rust\n"
    with pytest.raises(SkillsError, match="aliases must be an array"):
        _canon(tmp_path, body)


def test_contested_spelling_is_rejected(tmp_path: Path):
    body = """\
skill:
  - id: triton-language
    name: Triton Language
    category: language
    aliases: [triton]
  - id: triton-server
    name: Triton Server
    category: stack
    aliases: [TRITON]
"""
    with pytest.raises(SkillsError, match="claimed by both"):
        _canon(tmp_path, body)


def test_ambiguous_spelling_cannot_be_claimed_by_another_skill(tmp_path: Path):
    body = """\
skill:
  - id: ray
    name: Ray
    category: stack
    ambiguous_aliases: [ray]
  - id: ray-tracer
    name: Ray Tracer
    category: stack
    aliases: [ray]
"""
    with pytest.raises(SkillsError, match="claimed by both"):
        _canon(tmp_path, body)


def test_duplicate_id_is_rejected(tmp_path: Path):
    body = """\
skill:
  - id: rust
    name: Rust
    category: language
  - id: rust
    name: Rust Again
    category: language
"""
    with pytest.raises(SkillsError, match="duplicate skill id"):
        _canon(tmp_path, body)


def test_malformed_id_is_rejected(tmp_path: Path):
    body = 'skill:\n  - id: "Monte Carlo"\n    name: Monte Carlo\n    category: concept\n'
    with pytest.raises(SkillsError, match="lowercase hyphenated id"):
        _canon(tmp_path, body)


def test_non_boolean_case_sensitive_is_rejected(tmp_path: Path):
    body = (
        "skill:\n  - id: go\n    name: Go\n    category: language\n"
        "    case_sensitive: 'yes'\n"
    )
    with pytest.raises(SkillsError, match="case_sensitive must be a boolean"):
        _canon(tmp_path, body)


def test_empty_canon_is_rejected(tmp_path: Path):
    with pytest.raises(SkillsError, match="canon is empty"):
        _canon(tmp_path, "skill: []\n")


def test_wrong_root_is_rejected(tmp_path: Path):
    with pytest.raises(SkillsError, match="root must contain exactly"):
        _canon(tmp_path, "skills: []\n")


def test_unreadable_canon_is_rejected(tmp_path: Path):
    with pytest.raises(SkillsError):
        _load(tmp_path / "absent.yaml")


def test_malformed_yaml_is_rejected(tmp_path: Path):
    with pytest.raises(SkillsError):
        _canon(tmp_path, "skill: [\n")
