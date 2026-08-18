from __future__ import annotations

from xjobscore import degrees


def test_extracts_configured_degree_fields() -> None:
    description = (
        "Bachelor's in Computer Engineering, Applied Mathematics, or "
        "Biomedical Engineering required."
    )

    assert degrees.extract(description) == [
        "bioengineering",
        "computer_science",
        "mathematics",
    ]


def test_unrelated_text_has_no_degree_fields() -> None:
    assert degrees.extract("Build customer-facing web applications.") == []


def test_degree_canon_is_cached() -> None:
    assert degrees.canon() is degrees.canon()
