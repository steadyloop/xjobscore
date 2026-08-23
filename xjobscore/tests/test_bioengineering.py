import pytest

from xjobscore.bioengineering import title_evidence


@pytest.mark.parametrize(
    "title",
    [
        "Biomedical Engineer Intern",
        "Computational Neuroscience Intern",
        "Intern, Protein Design",
        "Clinical Research Intern",
        "Neuroengineer Intern",
    ],
)
def test_direct_bio_titles_do_not_need_an_employer_prior(title: str) -> None:
    assert title_evidence(title, bio_employer=False).reason == "direct_bio_title"


@pytest.mark.parametrize(
    "title",
    [
        "Engineering Intern",
        "Validation Engineer Trainee",
        "Field Technical Engineer Intern",
        "Research Associate",
        "Laboratory Technician Intern",
    ],
)
def test_generic_technical_titles_require_a_bio_employer(title: str) -> None:
    assert not title_evidence(title, bio_employer=False).recommend
    assert title_evidence(title, bio_employer=True).reason == (
        "bio_employer_technical_title"
    )


@pytest.mark.parametrize(
    "title",
    [
        "Finance Intern, Immunology",
        "Product Marketing Intern - Neuroscience",
        "Pharmaceutical Sales Intern",
        "Product Specialist Neuroscience Intern",
        "Biotechnology Partnerships Associate",
        "Videographer Internship - Medical Devices",
    ],
)
def test_nontechnical_titles_are_vetoed_even_at_bio_employers(title: str) -> None:
    assert not title_evidence(title, bio_employer=True).recommend


def test_boilerplate_cannot_admit_a_title() -> None:
    assert not title_evidence("Cybersecurity Intern", bio_employer=False).recommend
