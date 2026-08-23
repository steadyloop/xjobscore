from xjobscore import summaries


def test_extract_builds_a_bounded_role_summary_from_recognized_sections() -> None:
    description = """
    About Example Labs
    We are a fast-growing company with a mission to transform every industry.
    Role Overview
    Build and operate Python services that process high-volume customer events.
    Own a scoped backend project from technical design through production rollout.
    Collaborate with infrastructure and product engineers to improve platform reliability.
    Qualifications & Desired Skills
    Currently enrolled in a computer science or related undergraduate degree program.
    Experience with Python, SQL, and distributed systems is preferred.
    Pay Range Transparency
    The salary range is $30 to $45 per hour and includes comprehensive benefits.
    Equal Opportunity Employer
    We consider applicants without regard to race, color, religion, or disability.
    """

    result = summaries.extract("Software Engineering Intern", description)

    assert result is not None
    assert summaries.MIN_WORDS <= summaries.word_count(result) <= summaries.MAX_WORDS
    assert "Python services" in result
    assert "backend project" in result
    assert not {"salary", "benefits", "race", "fast-growing"}.intersection(
        result.casefold().split()
    )


def test_extract_understands_live_heading_variants_and_ignores_pay_copy() -> None:
    description = """
    At Example, we are passionate about building the world's leading data platform.
    The impact you will have:
    Work with a team to build features for a distributed data platform
    Manage your project end-to-end, including design, code, and feedback
    Learn how to operate services that scale while maintaining quality and security
    What we look for:
    You will graduate in fall 2027 with a degree in Computer Science or Engineering
    You have implementation experience with Python, Java, or C++
    Pay Range Transparency
    Actual compensation packages are based on experience and specific work location.
    About Example
    Example is headquartered in San Francisco and has offices around the world.
    """

    first = summaries.extract("Software Engineering Intern", description)
    second = summaries.extract("Software Engineering Intern", description)

    assert first == second
    assert first is not None
    assert summaries.MIN_WORDS <= summaries.word_count(first) <= summaries.MAX_WORDS
    assert "build features" in first
    assert "compensation" not in first.casefold()
    assert "headquartered" not in first.casefold()


def test_extract_does_not_force_a_summary_from_qualifications_or_legal_text() -> None:
    description = """
    About Example
    Example was founded in 2010 and is headquartered in New York.
    Qualifications
    Experience with Python and SQL is preferred.
    Strong written and verbal communication skills.
    Benefits
    Medical, dental, vision, and a 401(k) plan are available.
    Equal Opportunity Employer
    We consider applicants without regard to any protected status.
    """

    assert summaries.extract("Software Engineering Intern", description) is None


def test_extract_returns_a_short_source_only_when_it_describes_the_work() -> None:
    role = "Build internal APIs and data services, then ship a production project with the infrastructure team."
    legal = "Equal opportunity employer offering comprehensive benefits and a competitive salary range."

    assert summaries.extract("Backend Intern", role) == role
    assert summaries.extract("Backend Intern", legal) is None


def test_extract_handles_student_program_language_without_inventing_a_job() -> None:
    description = """
    About Example Trading
    Example Trading is a global firm founded by experienced market professionals.
    Software Development Intensive Program
    This program focuses on current sophomores in their second year of university.
    Participants will explore how trading technologies are designed and built.
    Using C++ and Python on Linux, participants will cover network protocols,
    distributed systems, and the fundamentals of automated trading systems.
    Candidates apply once; session placement is determined during the offer process.
    """

    result = summaries.extract("Software Development Intensive Program", description)

    assert result is not None
    assert summaries.MIN_WORDS <= summaries.word_count(result) <= summaries.MAX_WORDS
    assert "C++ and Python" in result
    assert "Candidates apply" not in result


def test_extract_prefers_duties_over_marketing_and_qualification_stacks() -> None:
    description = """
    Leading agencies and businesses worldwide rely on Example Labs to make the world safer.
    As a Product Engineer, you'll work with product managers and designers to solve complex problems, write production-grade code, and build features that matter.
    Build and ship full-stack features across core products and internal tools.
    Collaborate cross-functionally to turn complex requirements into shippable pieces.
    Qualifications
    A Bachelor's degree in Computer Science or a closely related technical field.
    One or two software engineering internships or equivalent project experience.
    """

    result = summaries.extract("New Grad Product Engineer", description)

    assert result is not None
    assert summaries.MIN_WORDS <= summaries.word_count(result) <= summaries.MAX_WORDS
    assert "production-grade code" in result
    assert "Leading agencies" not in result


def test_extract_skips_marketing_inside_a_role_section() -> None:
    description = """
    The Role
    Our customers are future-focused and so are we, always ahead of the next challenge.
    Come and join our elite team where we are building amazing products with amazing people.
    Assemble, test, and refine hardware prototypes with the system development team.
    Debug computer systems and document the results in engineering records.
    Organize the lab and track components used by multiple engineering projects.
    Qualifications
    Experience in engineering test, debug, technical support, or related work is preferred.
    """

    result = summaries.extract("Hardware Engineering Intern", description)

    assert result is not None
    assert "hardware prototypes" in result
    assert "future-focused" not in result
    assert "amazing people" not in result


def test_extract_recognizes_third_person_role_descriptions() -> None:
    description = """
    Therapists are responsible for applying current clinical knowledge and sound judgment in patient management.
    The therapist works closely with patients, families, and an interdisciplinary team to provide individualized care.
    Therapists also serve as advocates and educators for students, peers, and other medical professionals.
    The physical therapist assists with department and program planning.
    """

    result = summaries.extract("Physical Therapist - Part Time", description)

    assert result is not None
    assert summaries.MIN_WORDS <= summaries.word_count(result) <= summaries.MAX_WORDS
    assert "therapist works closely" in result


def test_extract_abstains_when_description_is_missing() -> None:
    assert summaries.extract("Software Intern", None) is None
    assert summaries.extract("Software Intern", "") is None
