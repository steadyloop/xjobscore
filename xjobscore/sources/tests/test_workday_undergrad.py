"""Undergraduate recall fixes from the October 2026 Workday DB/live audit."""

import pytest

from xjobscore.sources import json_api


def resolve(title, description=None, key="acme/wd1/careers"):
    payload = {"title": title, "jobPostingInfo": {"jobDescription": description}}
    context = {"key": key}
    config = json_api.CONFIGS["workday"]
    return (
        json_api._value(config["detail"]["only"], payload, context) is True,
        json_api._value(config["fields"]["job_type"], payload, context),
    )


@pytest.mark.parametrize(("title", "kind"), [
    ("Undergraduate Research Assistant", "student"),
    ("Federal Work Study – Library/IT (Walton)", "student"),
    ("Workstudy Technician Assistant", "student"),
    ("Student Success Hub - Student Assistant (FWS)", "student"),
    ("Student Affairs Student Assistant (Student Wage)", "student"),
    ("Student Worker: Construction Engineering- Under Grad Research Assistant", "student"),
    ("Graduate Writing Consultant - Rhetoric (Open to Work Study and Non-Work Study)", "new_grad"),
])
def test_verified_participant_titles_open_details_and_classify(title, kind):
    assert resolve(title) == (True, kind)
    assert json_api.shared_job_type(title) == kind


@pytest.mark.parametrize("title", [
    "PhD Research Intern", "Nursing Student Assistant", "Pharmacy Work Study",
    "Process Engineer Co-op - Doctorate",
    "Graduate Assistant -Teaching | Computer Science Department",
    "Student Success Graduate Assistant", "Graduate Research Assistant (Work Study)",
    "Grad Teaching Assistant Intern", "Student Ph.D. Software Engineering Fellowship",
    "Student Nurse Apprentice", "SkillBridge Co Op",
])
def test_exclusions_cannot_be_revived_by_details_or_student_board(title):
    assert resolve(title, "Job Category: Student Hourly Job Profile: Student Help. "
                   "Paid internship for undergraduate students.",
                   "wisconsin/wd1/uw_students") == (False, None)


@pytest.mark.parametrize("title", [
    "Manager of Financial Aid Loans and Work-Study",
    "Assistant Director, Part-Time Work-Study Degree Programmes",
    "Undergraduate Research Assistant Supervisor",
    "Student Success Student Assistant Coordinator",
    "Co Op Program Manager", "Security Cooperation Analyst",
    "Design Technology Co-Optimization Engineer", "International Tax Manager",
    "Software Engineering Fellowship Director",
])
def test_staff_and_substring_collisions_are_not_new_opportunities(title):
    assert resolve(title) == (False, None)


@pytest.mark.parametrize(("title", "description", "key", "kind"), [
    ("Mechanical Design Engineer Co Op - BOSS Snowplow",
     "Minimal requirements: Pursuing a B.S. Degree in Mechanical Engineering.",
     "ttc/wd1/toro_external_careers", "coop"),
    ("SOA Platform Systems Co Op - 2027 Spring/Summer (Onsite)",
     "Must be pursuing Bachelor’s or Advanced degree in Systems Engineering.",
     "globalhr/wd5/rec_rtx_ext_gateway", "coop"),
    ("Engineering Co‑Op", "Currently enrolled undergraduate sophomore.",
     "acme/wd1/careers", "coop"),
    ("Software Engineering Fellowship - Summer 2027",
     "<p>This is a paid, full-time internship for <b>undergraduate students</b>.</p>",
     "cmegroup/wd1/cme_careers", "intern"),
    ("Summer Development Analyst",
     "<li>Enrolled in a bachelor's degree program in real estate.</li>",
     "greystar/wd1/campus", "intern"),
    ("Engineering (Manufacturing) Industrial Placement 2027",
     "<h3>What We Look For:</h3><p>University students graduating in 2029.</p>",
     "pg/wd5/1000", "intern"),
    ("Accounting Assistant",
     "<p>Job Category: Student Hourly</p><p>Job Profile: Student Help</p>",
     "wisconsin/wd1/uw_students", "student"),
    ("BOTANY LAB ASSISTANT",
     "Job Category: Student Hourly Job Profile: Student Help", "wisconsin/wd1/uw_students", "student"),
    ("Communications & Content Writer",
     "<h3>Required Qualifications</h3><ul><li>Current UT Austin student</li></ul>",
     "utaustin/wd1/utstudent", "student"),
])
def test_ambiguous_titles_need_description_confirmation(title, description, key, kind):
    assert resolve(title, key=key) == (True, None)
    assert resolve(title, description, key) == (True, kind)


@pytest.mark.parametrize(("title", "description", "key"), [
    ("Clerkship", "Job Category: Student Hourly Job Profile: Student Help. "
     "To qualify, you must be admitted to the CSD graduate degree program.",
     "wisconsin/wd1/uw_students"),
    ("SHSW Program Assistant", "Job Category: Student Hourly Job Profile: Student Help. "
     "GA – level work only: completion of bachelor’s degree and enrolled in a SHSW graduate program.",
     "wisconsin/wd1/uw_students"),
    ("2027 Spring Co op Neuroscience Research Cambridge MA",
     "Basic Qualifications: Currently pursuing a master or PhD degree in biomedical sciences.",
     "sanofi/wd3/careers"),
    ("2027 Spring Co op CMC Synthetics Platform Process and Pharmaceutical Engineering Scientist",
     "Basic Qualifications: Currently pursuing a Master's or PhD degree in Chemical Engineering.",
     "sanofi/wd3/careers"),
    ("Accounting Assistant", "Current students and faculty should apply internally.",
     "wisconsin/wd1/uw_students"),
    ("Accounting Assistant", "Job Category: Student Hourly Job Profile: Student Help",
     "unverified/wd1/students"),
    ("Accounting Assistant", "Job Category: Student Hourly Job Profile: Student Help. Graduate students only.",
     "wisconsin/wd1/uw_students"),
    ("Software Engineering Fellowship", "Candidates must have previous internship experience.",
     "acme/wd1/careers"),
    ("Software Engineering Fellowship", "<script>internship for undergraduate students</script>Senior engineer required.",
     "acme/wd1/careers"),
    ("Software Engineering Fellowship Director", "An internship for undergraduate students.",
     "acme/wd1/careers"),
    ("Summer Development Analyst Manager", "Enrolled in a bachelor's degree program.",
     "acme/wd1/careers"),
    ("Industrial Placement - MSc", "University students graduating in 2029.",
     "acme/wd1/careers"),
    ("", "Job Category: Student Hourly Job Profile: Student Help",
     "wisconsin/wd1/uw_students"),
])
def test_descriptions_do_not_turn_ambiguous_or_excluded_jobs_into_students(title, description, key):
    assert resolve(title, description, key)[1] is None


@pytest.mark.parametrize("description", [None, "", [], {}, 42])
def test_missing_or_malformed_descriptions_fail_closed(description):
    assert resolve("Software Engineering Fellowship", description) == (True, None)


def test_conditional_classification_requires_boolean_confirmation():
    spec = {"classify": [{"from": "@title", "re": "student", "when": "@eligible", "is": "student"}]}
    assert json_api._value(spec, {"title": "Student", "eligible": True}, {}) == "student"
    for value in [None, False, "true", "yes", 1]:
        assert json_api._value(spec, {"title": "Student", "eligible": value}, {}) is None
