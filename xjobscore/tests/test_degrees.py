from __future__ import annotations

from xjobscore import degrees


def test_extracts_configured_degree_fields() -> None:
    description = (
        "Bachelor's in Computer Engineering, Applied Mathematics, or "
        "Biomedical Engineering required."
    )

    assert degrees.extract(description) == [
        "computer_science",
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


def test_adjacent_bio_terms_are_title_only() -> None:
    description = "Our company develops products for immunology and neuroscience."

    assert degrees.extract(description, title="Finance Intern") == []
    assert degrees.extract(description, title="Research Scientist, Immunology") == [
        "biotech"
    ]


def test_title_only_patterns_cover_observed_bioengineering_roles() -> None:
    assert degrees.extract("", title="Computational Neuroscience Intern") == ["biotech"]
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
