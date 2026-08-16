from __future__ import annotations

from pathlib import Path

import pytest

from xjobscore.skills import CATEGORIES, SkillsError, _load, canon, extract


def _canon(tmp_path: Path, body: str) -> dict:
    path = tmp_path / "skills.toml"
    path.write_text(body, encoding="utf-8")
    return _load(path)


# -- matching ------------------------------------------------------------


def test_names_resolve_to_their_slug():
    assert extract("We build kernels with CUTLASS and NCCL.") == ["cutlass", "nccl"]


def test_aliases_collapse_onto_one_slug():
    """Every spelling is the same skill, so a JD naming both reports it once."""
    assert extract("Kubernetes") == extract("K8s") == ["kubernetes"]
    assert extract("We run Kubernetes; k8s experience required.") == ["kubernetes"]


def test_matching_ignores_case():
    assert extract("CUTLASS") == extract("cutlass") == extract("Cutlass") == ["cutlass"]


def test_symbol_bearing_names_still_match():
    """`\\b` cannot express a boundary after `+`, so the guards are lookaround."""
    assert extract("Strong C++ required.") == ["cpp"]


def test_word_boundaries_reject_substrings():
    assert extract("Rustacean culture") == []
    assert extract("gitignore") == []
    assert extract("We use Rust. Check Git.") == ["git", "rust"]


def test_longest_spelling_wins():
    """Ordered alternation would otherwise strand the tail of a longer name."""
    assert extract("numerical linear algebra") == ["numerical-linear-algebra"]
    assert extract("Nsight Systems") == ["nsight"]


def test_hits_are_deduplicated_and_sorted():
    assert extract("Linux, Docker, Linux, docker, LINUX") == ["docker", "linux"]


def test_empty_text_yields_nothing():
    assert extract("") == []
    assert extract("Nothing technical here.") == []


def test_extract_only_ever_emits_canon_slugs():
    text = "CUDA, PyTorch, Kubernetes, Fortran, telepathy, roofline"
    assert set(extract(text)) <= set(canon())


# -- ambiguity -----------------------------------------------------------


def test_ambiguous_names_are_not_matched():
    """A wrong label corrupts every comparison downstream; a miss does not."""
    assert extract("You will go to market and go the extra mile.") == []
    assert extract("Reporting to C-level executives. Grade: C.") == []
    assert extract("Deployed on NVIDIA Triton Inference Server") == []


def test_ambiguous_skills_remain_reachable_through_aliases():
    assert extract("Backend written in Golang") == ["go"]


def test_ambiguity_is_declared_in_the_canon():
    entries = canon()
    assert {slug for slug, skill in entries.items() if skill.ambiguous} == {"c", "go", "triton"}
    assert entries["go"].patterns == ("golang",)
    assert "go" not in entries["go"].patterns
    assert entries["c"].patterns == ()  # no spelling identifies the language
    assert entries["cutlass"].patterns == ("cutlass",)


# -- the shipped canon ---------------------------------------------------


def test_shipped_canon_is_valid():
    entries = canon()
    assert len(entries) > 1
    for slug, skill in entries.items():
        assert skill.category in CATEGORIES, slug
        assert slug == slug.lower(), slug
        assert all(alias == alias.lower() for alias in skill.aliases), slug


def test_shipped_canon_has_no_contested_spelling():
    seen: dict[str, str] = {}
    for skill in canon().values():
        for pattern in skill.patterns:
            assert seen.setdefault(pattern, skill.slug) == skill.slug, pattern


def test_canon_is_cached():
    assert canon() is canon()


# -- validation ----------------------------------------------------------


def test_optional_keys_default(tmp_path: Path):
    entries = _canon(tmp_path, '[duckdb]\ncategory = "stack"\n')
    assert entries["duckdb"].name == "duckdb"  # falls back to the slug
    assert entries["duckdb"].aliases == ()
    assert entries["duckdb"].ambiguous is False


def test_unknown_key_is_rejected(tmp_path: Path):
    """`alias =` would otherwise read as an empty list and silently stop matching."""
    with pytest.raises(SkillsError, match="unknown key 'alias'"):
        _canon(tmp_path, '[rust]\ncategory = "language"\nalias = ["rustlang"]\n')


def test_unknown_category_is_rejected(tmp_path: Path):
    with pytest.raises(SkillsError, match="category must be one of"):
        _canon(tmp_path, '[rust]\ncategory = "framework"\n')


def test_missing_category_is_rejected(tmp_path: Path):
    with pytest.raises(SkillsError, match="category must be one of"):
        _canon(tmp_path, '[rust]\nname = "Rust"\n')


def test_uppercase_alias_is_rejected(tmp_path: Path):
    """Text is lowercased before matching, so an uppercase alias could never hit."""
    with pytest.raises(SkillsError, match="must be lowercase"):
        _canon(tmp_path, '[kubernetes]\ncategory = "stack"\naliases = ["K8s"]\n')


def test_contested_spelling_is_rejected(tmp_path: Path):
    """Two owners for one spelling would make resolution alternation-order dependent."""
    body = (
        '[triton-lang]\nname = "Triton"\ncategory = "stack"\n\n'
        '[triton-server]\ncategory = "stack"\naliases = ["triton"]\n'
    )
    with pytest.raises(SkillsError, match="claimed by both"):
        _canon(tmp_path, body)


def test_malformed_slug_is_rejected(tmp_path: Path):
    with pytest.raises(SkillsError, match="lowercase hyphenated slug"):
        _canon(tmp_path, '["Monte Carlo"]\ncategory = "concept"\n')


def test_non_boolean_ambiguous_is_rejected(tmp_path: Path):
    with pytest.raises(SkillsError, match="ambiguous must be a boolean"):
        _canon(tmp_path, '[go]\ncategory = "language"\nambiguous = "yes"\n')


def test_empty_canon_is_rejected(tmp_path: Path):
    with pytest.raises(SkillsError, match="canon is empty"):
        _canon(tmp_path, "")


def test_unreadable_canon_is_rejected(tmp_path: Path):
    with pytest.raises(SkillsError):
        _load(tmp_path / "absent.toml")


def test_malformed_toml_is_rejected(tmp_path: Path):
    with pytest.raises(SkillsError):
        _canon(tmp_path, "[rust\ncategory =")
