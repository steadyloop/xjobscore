from __future__ import annotations

from xjobscore import degrees


def test_extracts_configured_degree_fields() -> None:
    description = (
        "Bachelor's in Computer Engineering, Applied Mathematics, or "
        "Biomedical Engineering required."
    )

    assert degrees.extract(description) == [
        "cs",
        "mathematics",
        "med_device",
    ]


def test_extracts_independent_life_sciences_focus_areas() -> None:
    description = (
        "Experience in medical devices, biotechnology, and clinical affairs "
        "is required."
    )

    assert degrees.extract(description) == ["biotech", "clinical_affairs", "med_device"]


def test_extracts_forgiving_bioengineering_vocabulary() -> None:
    description = (
        "Biosystems engineering, computational-biology, bioprocess engineering, "
        "cell and gene therapy, and regenerative medicine experience preferred. "
        "Work includes biomechanics, biosensors, SaMD, and ISO 13485."
    )

    assert degrees.extract(description) == ["biotech", "med_device"]


def test_gcp_requires_clinical_context() -> None:
    assert degrees.extract("Deploy Python services to GCP with Kubernetes.") == []
    assert degrees.extract("Maintain ICH-GCP compliance for clinical studies.") == [
        "clinical_affairs"
    ]


def test_biotech_ignores_the_title_entirely() -> None:
    """biotech carries no title_patterns: only the description can name it."""
    description = "Our company develops products for immunology and neuroscience."

    assert degrees.extract(description, title="Finance Intern") == []
    # Adjacent bio vocabulary in the title no longer admits the field. It added
    # 529 tags across the active corpus, 68% on postings with no description --
    # therapy-area role names, not degree evidence.
    assert degrees.extract(description, title="Research Scientist, Immunology") == []
    assert degrees.extract("", title="Computational Neuroscience Intern") == []
    # The description channel is untouched.
    assert degrees.extract("BS in Bioengineering or related field.") == ["biotech"]


def test_title_only_patterns_cover_observed_device_roles() -> None:
    # med_device keeps its title channel; biotech is the one that gave it up.
    assert degrees.extract("", title="Biomedical Equipment Maintenance Technician") == [
        "med_device"
    ]
    assert degrees.extract("", title="Field Service Engineer, Surgical Robotics") == [
        "med_device"
    ]


def test_unrelated_text_has_no_degree_fields() -> None:
    assert degrees.extract("Build customer-facing web applications.") == []


def test_degree_canon_is_cached() -> None:
    assert degrees.canon() is degrees.canon()
