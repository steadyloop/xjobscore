import pytest

from xjobscore.job_domain import classify, normalize_title


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer Intern",
        "Backend Engineer, New Grad",
        "Machine Learning Engineer Intern",
        "Research Intern - Reinforcement Learning, Robotics",
        "Applied Scientist, PhD New Grad",
        "Data Engineer Co-op",
        "Cyber Security Analyst Intern",
        "Kernel Engineer - New Grad",
        "Systems Research Engineer Intern - GPU Programming",
    ],
)
def test_strong_software_titles_are_labelled(title: str) -> None:
    assert classify(title).label == "software"
    assert classify(title).reason == "software_title"


@pytest.mark.parametrize(
    "title",
    [
        "AI Builder, Emerging Talent",
        "Platform Engineer Intern",
        "Data Analyst Intern",
    ],
)
def test_weak_software_titles_are_labelled_when_no_veto_fires(title: str) -> None:
    assert classify(title).reason == "software_weak_title"


@pytest.mark.parametrize(
    "title",
    [
        "Quantitative Researcher Intern",
        "Quantitative Trader",
        "Sales and Trading",
        "Graduate Equity Analyst",
        "Actuarial Analyst",
        "Portfolio Implementation Summer Analyst",
    ],
)
def test_quant_titles_are_finance(title: str) -> None:
    assert classify(title).label == "finance"
    assert classify(title).reason == "quant_title"


@pytest.mark.parametrize(
    "title",
    [
        "Research Summer Analyst",
        "Research Product Specialist Summer Analyst",
        "Credit Analyst Internship",
    ],
)
def test_generic_quant_roles_require_a_quant_employer(title: str) -> None:
    assert classify(title, quant_employer=False).label != "finance"
    assert classify(title, quant_employer=True).reason == "quant_employer_title"


@pytest.mark.parametrize(
    "title",
    [
        "Human Resources Intern, Digital & AI",
        "AI & Strategic Marketing Intern",
        "Legal Intern",
        "Store Operations Intern",
    ],
)
def test_nontechnical_titles_are_vetoed(title: str) -> None:
    assert classify(title).label is None


@pytest.mark.parametrize(
    "title",
    [
        "Analog Test Engineer",
        "Environmental Test Engineer Intern",
        "Electrical Test Engineer, Wafer Test",
    ],
)
def test_adjacent_disciplines_cannot_steal_a_shared_term(title: str) -> None:
    assert classify(title).label is None


def test_rescue_readmits_software_from_an_adjacent_discipline() -> None:
    # "Electronic Design Automation" is software despite the electrical qualifier.
    title = "Research Scientist, Electronic Design Automation - New College Grad"
    assert classify(title).label == "software"


@pytest.mark.parametrize(
    ("title", "collision"),
    [
        ("Quantum Signal Train Engineer Internship", "quantum is not quant"),
        ("Graduate Quantity Surveyor", "quantity is not quant"),
        ("Graduate, Tax Leadership Development Programme", "programme is not programmer"),
        ("Aerospace Systems Engineer I", "systems engineering is not software"),
        ("Fluid Systems Engineer I", "systems engineering is not software"),
    ],
)
def test_generalized_stems_do_not_swallow_lookalike_words(
    title: str, collision: str
) -> None:
    assert classify(title).label is None, collision


@pytest.mark.parametrize(
    "title",
    [
        "Corporate Tax Winter-Spring Internship",
        "Internal Audit Intern - Accounting & Finance",
        "Accounting and Finance Intern",
    ],
)
def test_corporate_finance_function_is_out_of_scope(title: str) -> None:
    assert classify(title, quant_employer=True).label != "finance"


@pytest.mark.parametrize(
    "title",
    [
        "Network Security Associate | New Grad",
        "Associate Linux/Windows Engineer - New Grad",
        "Identity Management Analyst - New Grad",
    ],
)
def test_infrastructure_roles_at_a_quant_employer_are_not_quant(title: str) -> None:
    assert classify(title, quant_employer=True).label != "finance"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # The veto words below are program names, not role nouns.
        ("Software Engineer, Communications", "software"),
        ("Software Engineer, Full Stack - Emerging Talent", "software"),
        ("2026 Applied Science Internship - Computer Vision - PhD Student "
         "Science Recruiting", "software"),
        ("Markets - Sales and Trading, Summer Analyst, New York City", "finance"),
    ],
)
def test_program_names_do_not_veto_a_real_role(title: str, expected: str) -> None:
    assert classify(title).label == expected


def test_normalize_strips_program_and_scheduling_vocabulary() -> None:
    assert normalize_title("2027 Summer Software Engineer Intern") == (
        "Software Engineer Intern"
    )


def test_software_wins_when_a_title_carries_both_domains() -> None:
    assert classify("Quantitative Software Developer Intern").label == "software"


@pytest.mark.parametrize(
    "title",
    [
        "Technology Intern- Summer 2027",
        "Humana Technology Internship - Summer 2027 (Early ID Participants)",
        "Digital Technology Intern - US - Atlanta, GA - Summer 2027",
        "Technology Interns",
        "Digital Technology Co-op - US - Spring 2027",
        "Infrastructure Intern",
    ],
)
def test_a_student_technology_rotation_is_software(title: str) -> None:
    assert classify(title).reason == "student_domain_title"


@pytest.mark.parametrize(
    "title",
    [
        "Surgical Technology Intern",
        "Assistive Technology Intern, Spring 2027",
        "Information Technology Intern",
        "Informational Technology Co-Op/Intern - Spring 2027",
        "Data Centre Infrastructure Apprentice",
        "Electrical Infrastructure Co-op",
        "Infrastructure Finance Intern",
    ],
)
def test_qualified_technology_internships_are_not_software(title: str) -> None:
    assert classify(title).label is None


@pytest.mark.parametrize(
    "title",
    [
        # The term is deliberately not widened to `technolog(y|ies) <role>`:
        # every one of these would follow it into `software`.
        "Technology Development Engineer Intern (Silicon Photonics)",
        "DRAM Device & Cell Technology Engineer",
        "Foundry Technology Engineer",
        "Advanced Technology Engineer (Wet Process)",
        "People Technology Analyst",
        "Operational Technology Engineer",
        # `intern[a-z]*` would match "Internal" here.
        "Vice President - Technology Internal Audit",
        # Adjacency is load-bearing: each of these has both a domain noun and a
        # student role noun, and none of them is software.
        "RiSM Internship for Students in Pharmaceutical Sciences, Pharmaceutical Technology",
        "Acoustic and Vibration Technologies Co-op",
        "Applied Process Technology (APT) Engineer Co-Op /Intern",
        "Intern - Yield Technology Equipment",
        "Technology Development Engineer Intern (Silicon Photonics)",
        "Quantum Technology Services Intern",
        "Head of the EA Infrastructure Fund",
        "Cloud Infrastructure Sales Representative, OCI",
    ],
)
def test_technology_alone_is_never_software(title: str) -> None:
    assert classify(title).label is None
