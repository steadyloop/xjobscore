"""Parse-time `job_type` / `remote` derivation.

The first section covers the `{"classify": [...]}` expression form itself, which
is vendor-neutral machinery. Every section after it is one vendor, and every
case there is transcribed from a specific line of that vendor's survey in
doc/raw_postings/ -- these are the false positives and recall gaps the survey
measured against a real sample, so a rule change that reintroduces one fails
here. Line citations are written `md:NN` against the vendor's own file.

Nothing in the vendor sections generalises. Another ATS names different fields,
fills them at different rates, and produces different false positives; its rules
come from its own doc. What the vendors do share is the *shape* of the table --
co-op resolves before intern, a structured on-site statement terminates at
`unknown` rather than falling through, and a bucket the survey measures nothing
for gets no rule at all -- so each section below tests those three the same way.
"""

from __future__ import annotations

import pytest

from xjobscore import classify
from xjobscore.records import Feed, Item
from xjobscore.sources import json_api


def resolve(spec, payload=None, context=None):
    return json_api._value(spec, payload or {}, context or {})


# The expression form ------------------------------------------------------


def test_first_match_wins_in_declaration_order():
    spec = {"classify": [
        {"from": "@t", "re": "alpha", "is": "first"},
        {"from": "@t", "re": "beta", "is": "second"},
    ]}
    assert resolve(spec, {"t": "alpha beta"}) == "first"


def test_default_is_returned_when_nothing_matches():
    spec = {"classify": [{"from": "@t", "re": "alpha", "is": "x"}], "default": "fallback"}
    assert resolve(spec, {"t": "nothing here"}) == "fallback"


def test_absent_default_yields_none():
    assert resolve({"classify": [{"from": "@t", "re": "alpha", "is": "x"}]}, {"t": "no"}) is None


def test_a_negative_pattern_skips_only_its_own_rule():
    """A documented false positive must fall through to the next signal rather
    than ending the scan -- otherwise one bad field silences the good ones."""
    spec = {"classify": [
        {"from": "@a", "re": "match", "not": "veto", "is": "from_a"},
        {"from": "@b", "re": "match", "is": "from_b"},
    ]}
    assert resolve(spec, {"a": "veto match", "b": "match"}) == "from_b"


@pytest.mark.parametrize("payload", [{}, {"t": None}, {"t": ""}, {"t": 42}])
def test_a_missing_or_non_string_field_is_skipped_not_raised(payload):
    spec = {"classify": [
        {"from": "@t", "re": "alpha", "is": "x"},
        {"from": "@u", "re": "beta", "is": "y"},
    ]}
    assert resolve(spec, {**payload, "u": "beta"}) == "y"


def test_rules_read_context_not_only_payload():
    """`$site` is known for free at list-call time because the site is part of
    the request URL, so the feed key is a legitimate signal source."""
    spec = {"classify": [{"from": "$site", "re": "campus", "is": "new_grad"}]}
    assert resolve(spec, {}, {"site": "Campus"}) == "new_grad"


def test_boundaries_reject_a_substring_inside_a_longer_word():
    assert classify.hit("Software Intern", "intern") is True
    assert classify.hit("International Tax", "intern") is False
    assert classify.hit("Internal Audit", "intern") is False


# `map` + `where`, the tenant name/value bag accessor -----------------------
#
# Greenhouse's `metadata` carries its key in a sibling field rather than in the
# path, so one entry is selected by predicate. The composite below is the exact
# form the greenhouse rules use; these cases pin the edges a rule table cannot
# see for itself.

BAG = [
    {"name": "Employment Type", "value": "Jump Trading - Intern"},
    {"name": "Time Type", "value": "Part time"},
]


def pick(name, payload):
    return resolve({"first": {"map": "@metadata",
                              "where": {"eq": ["@name", name]},
                              "value": "@value"}},
                   payload)


def test_where_selects_the_named_entry_out_of_the_bag():
    assert pick("Time Type", {"metadata": BAG}) == "Part time"
    assert pick("Employment Type", {"metadata": BAG}) == "Jump Trading - Intern"


@pytest.mark.parametrize("payload", [
    {},                                        # key absent
    {"metadata": None},                        # null on 5,681 greenhouse rows
    {"metadata": []},                          # present but empty
    {"metadata": [{"name": "Time Type"}]},     # named, no value
])
def test_a_missing_bag_yields_none_rather_than_raising(payload):
    """`metadata` is tenant-defined, so every one of these shapes is real. Each
    resolves to None, which `classify` then skips like any other absent field."""
    assert pick("Time Type", payload) is None


def test_an_unmatched_name_does_not_leak_a_sibling_entry():
    """The whole point of the predicate: without it `first` would return
    whichever entry the tenant happened to order first."""
    assert pick("Location Type", {"metadata": BAG}) is None


# workday, from doc/raw_postings/workday.md ---------------------------

WORKDAY = json_api.CONFIGS["workday"]["fields"]


def workday_job_type(title="", external_path="", site="", time_type=""):
    return resolve(WORKDAY["job_type"],
                   {"title": title, "externalPath": external_path, "timeType": time_type},
                   {"site": site})


def workday_part_time(title="", time_type=""):
    return resolve(WORKDAY["part_time"], {"title": title, "timeType": time_type})


def workday_remote(remote_type="", locations_text="", title=""):
    return resolve(WORKDAY["remote"],
                   {"remoteType": remote_type, "locationsText": locations_text, "title": title})


@pytest.mark.parametrize(("title", "expected"), [
    ("Software Engineering Intern", "intern"),                      # md:17, 555 hits
    ("Metrology Co-Op", "coop"),                                    # md:61, 36 hits
    # ENGLISH-ONLY: md:23 and md:19 still measure these as German intern rows,
    # but the werkstudent/praktikum stems were dropped from the rule table, so
    # they now fall through unclassified, and bare `trainee` was dropped too:
    # on its own it is on-the-job training for a full-time hire (GEICO claims,
    # BofA advisors), not a student opportunity. A `Graduate Trainee` is still
    # claimed by the graduate-programme rule and lands in `new_grad`.
    ("Werkstudent (m/w/d) im Bereich E-Commerce", None),            # md:23, was "intern"
    ("Praktikanten Marketing", None),                               # md:19, was "intern"
    ("Auto Damage Trainee (Field)", None),                          # md:22, was "intern"
    ("ACE Graduate Trainee", "new_grad"),
    ("Software Engineer, New Grad", "new_grad"),             # md:18, 134 hits
    # CARRIED REGRESSION. Bare `apprentice` is vocational here -- of the 63 rows
    # the broad rule claimed, ~3 were student opportunities and the rest were
    # trades, offshore graduate-entry ops schemes, or bare "Apprentice"
    # (workday.md:73-89). The shared table's `students?|apprentice(s|ship|ships)?`
    # (job_type.md:40) re-admits 61 of them. Pinned as `student` so the
    # regression is visible: the fix is a trades veto in the shared `not` list,
    # not a Workday override.
    ("Apprentice Electrician", "student"),
    ("Student Youth Apprentice", "student"),
    ("Working Student, Software Engineering", "student"),
    ("Intern / Working Student", "intern"),
    ("Software Engineer", None),
])
def test_titles_classify_as_the_doc_measured(title, expected):
    assert workday_job_type(title=title) == expected


@pytest.mark.parametrize("title", [
    "International Tax Manager",      # md:21 -- bare /intern/ adds 88 of these
    "Internal Communications Lead",
    "Security Cooperation Analyst",   # md:62 -- dropping the trailing boundary adds 5
    "Design Technology Co-Optimization (DTCO) Engineer",  # md:63
])
def test_documented_false_positives_stay_unclassified(title):
    assert workday_job_type(title=title) is None


def test_the_plural_internships_is_not_missed():
    """md:24-27 records `\\m(intern|interns|internship)\\M` missing ~30 rows of
    one tenant's 'X Internships: City - Fall 2026'. The canon uses the
    `intern(s|ship|ships)?` form the doc prescribes instead."""
    assert workday_job_type(title="Data Science Internships: Austin - Fall 2026") == "intern"


def test_coop_wins_over_intern_when_a_title_carries_both():
    """md:57-58 -- co-op titles are a subset of the intern-ish set, so the
    co-op rules must resolve first or every co-op is filed as an internship."""
    assert workday_job_type(title="Co-op Intern, Robotics") == "coop"


def test_feed_identity_is_not_read_at_all():
    """The board names a recruiting channel, not the audience of every row on
    it. Measured live the `$site` rules were 15-for-35 wrong: `boeing/wd1/intern`
    alone contributed 15 rows of trade hiring and an International Tax Project
    Manager. The campus sites' real rows are reachable from the title instead."""
    assert workday_job_type(title="Software Engineer", site="INTERN") is None
    assert workday_job_type(title="International Tax Project Manager",
                            site="INTERN") is None
    assert workday_job_type(title="Software Engineering Co-op", site="INTERN") == "coop"


@pytest.mark.parametrize(("title", "expected"), [
    # Recovered from the title now that `$site` no longer stands in for them.
    ("Associate Consultant, Launch Program 2027 - Toronto, Canada", "new_grad"),
    ("Adams Application Software Developer  (Recent Grad 2026)", "new_grad"),
    ("Recent Graduates - Data Engineer", "new_grad"),
    # Bare titles the campus sites used to claim carry no audience signal.
    ("Quantitative Researcher", None),
    ("Field Analytics Advisor", None),
])
def test_workday_new_grad_phrases_replace_the_site_rule(title, expected):
    assert workday_job_type(title=title) == expected


@pytest.mark.parametrize(("time_type", "expected"), [
    ("Part time", "yes"),   # md:83, 103 hits
    ("part TIME", "yes"),   # md:83 -- case-insensitive, same as Detail
    ("Full time", "unknown"),     # md:82, 1,278 hits -- must not fire
])
def test_time_type_maps_part_time_as_the_doc_measured(time_type, expected):
    assert workday_part_time(time_type=time_type) == expected


@pytest.mark.parametrize(("title", "expected"), [
    ("Pharmacy Technician - Inpatient, PRN", "yes"),               # md:89
    ("Bilingual Spanish Part Time Retail Sales Consultant", "yes"),  # md:87
    ("Doradca Klienta (Part-time)", "yes"),                        # md:88
])
def test_title_carries_part_time_when_time_type_is_blank(title, expected):
    """md:85-89 -- blank-timeType rows still carry the signal in the title."""
    assert workday_part_time(title=title) == expected


@pytest.mark.parametrize(("remote_type", "expected"), [
    ("Remote", "yes"),              # md:96, 60 hits
    ("Hybrid", "hybrid"),           # md:95, 190 hits
    ("Remote/Hybrid", "hybrid"),    # md:98 -- hybrid must be tested first
    ("", "unknown"),
])
def test_remote_type_maps_as_the_doc_measured(remote_type, expected):
    assert workday_remote(remote_type=remote_type) == expected


def test_the_not_remote_trap_terminates_rather_than_falling_through():
    """md:99-101 -- 'Office Worker (NOT Remote)' is the canonical trap. A
    structured on-site statement must also stop the weaker title rule from
    claiming the same row."""
    assert workday_remote(remote_type="Office Worker (NOT Remote)",
                  title="Remote Support Engineer") == "unknown"


def test_remote_sensing_is_not_a_work_location():
    """md:120 -- 3 satellite-imagery roles match a bare title /remote/."""
    assert workday_remote(title="Remote Sensing Software Engineer") == "unknown"


@pytest.mark.parametrize("locations_text", ["2 Locations", "3 Locations"])
def test_multi_site_rollups_carry_no_signal(locations_text):
    """md:103-108 -- multi-site postings collapse to a rollup instead of naming
    the sites, so location patterns only ever apply to single-location rows."""
    assert workday_remote(locations_text=locations_text) == "unknown"


def test_location_text_carries_remote_on_single_site_rows():
    assert workday_remote(locations_text="Austin, TX (Remote)") == "yes"
    assert workday_remote(locations_text="Hyderabad, India") == "unknown"


def test_remote_never_asserts_a_negative():
    """The domain has no 'no'. An explicit on-site statement is left
    indistinguishable from no signal at all."""
    assert workday_remote(remote_type="Onsite", locations_text="Austin, TX") == "unknown"


@pytest.mark.parametrize(("title", "expected"), [
    ("Research Intern", "research"),                              # md:Research
    # "PhD Research Intern, Generative AI - 2026" moved to the exclusion test
    # below: job_type.md:16 voids a doctoral title before `research` is reached.
    # Was None under the Workday-measured table. The shared table's bare
    # `students?` (job_type.md:40) claims it as `student` -- enrolled work, not
    # a research internship. workday.md:126-130 measures the phrase at 1 row
    # corpus-wide, so the change is real but not material.
    ("Student Researcher, Robotics", "student"),
    ("Research Scientist", None),            # same -- a career title, not a student one
])
def test_workday_research_is_a_research_flavoured_internship(title, expected):
    """`research` is not (undergraduate|student) research -- that phrase is 1
    row corpus-wide -- it is an internship whose title also says "research".
    A research title with no internship signal stays unclassified rather than
    guessing at seniority from the word alone."""
    assert workday_job_type(title=title) == expected


# ashby, from doc/raw_postings/ashby.md ------------------------------------

ASHBY = json_api.CONFIGS["ashby"]["fields"]


def ashby_job_type(title="", employment_type="", department="", team="", description=""):
    return resolve(ASHBY["job_type"], {"title": title, "employmentType": employment_type,
                                       "department": department, "team": team,
                                       "descriptionPlain": description})


def ashby_part_time(title="", employment_type=""):
    return resolve(ASHBY["part_time"],
                   {"title": title, "employmentType": employment_type})


def ashby_remote(workplace_type="", location="", secondary=()):
    return resolve(ASHBY["remote"],
                   {"workplaceType": workplace_type, "location": location,
                    "secondaryLocations": [{"location": value} for value in secondary]})


@pytest.mark.parametrize(("title", "expected"), [
    ("Software Engineering Intern", "intern"),                   # job_type.md:30
    ("Intern, Financial Planning", "intern"),
    ("Software Engineer, New Grad", "new_grad"),                 # job_type.md:55
    ("December 2026 New Graduate Engineer, Software", "new_grad"),
    ("CoDesign & NextGen - New College Grad", "new_grad"),
    ("Software Engineer (2027 University Grad)", "new_grad"),
    ("Quantitative Trader - 2027 Graduate Program", "new_grad"),
    ("Summer Analyst, Markets", "intern"),                       # job_type.md:33
    ("Software Engineer", None),
])
def test_ashby_titles_classify_under_the_shared_table(title, expected):
    assert ashby_job_type(title=title) == expected


@pytest.mark.parametrize("title", [
    "Registered Nurse, ICU",       # job_type.md:15
    "Pharmacy Technician",         # job_type.md:14
    "SkillBridge Intern",          # job_type.md:13 -- voids despite "Intern"
])
def test_ashby_excluded_tier_voids_licensure_and_service_gated_titles(title):
    """The Ashby table had no excluded tier at all; the shared one adds 493
    rows of it on this corpus. SkillBridge is the load-bearing order case --
    it carries "Intern" and would resolve as one if excluded ran later."""
    assert ashby_job_type(title=title) is None


def test_ashby_apprentice_is_a_student_under_the_shared_table():
    """BEHAVIOUR CHANGE, 2026-08-26. The Ashby table had no apprentice rule and
    left this NULL; job_type.md:38 carries a bare `apprentice(s|ship|ships)?`.

    The workday adapter documents the same pattern measuring ~3 student rows in
    63 (Apprentice Machinist, EEG Technologist Apprentice) and narrows it to
    `students? (youth )?apprentices?` for that reason. The shared table does
    not, so a vocational trade title now reads as `student` here. Pinned as the
    known cost of adopting the shared table verbatim."""
    assert ashby_job_type(title="Apprentice Electrician") == "student"


@pytest.mark.parametrize("title", [
    "Applied Scientist, PhD New Grad",           # else new_grad, via `grad(uate|uates|s)?`
    "PhD Research Intern, Generative AI - 2026",  # else research
    "PhD Software Engineering Intern",            # else intern
    "Postdoctoral Research Associate",            # the joined word `postdoc` alone misses
    "Postdoc Researcher",
    "Doctoral Intern",
])
def test_a_doctoral_title_is_voided_whatever_else_it_says(title):
    """job_type.md:16 -- terminal, and ahead of every typing rule, so a doctoral
    posting cannot reach intern/research/new_grad on its other words.

    This REVERSES the 2026-08-26 behaviour change: the Ashby table had opened
    with a terminal `ph\\.?d\\.?` rule, the shared table dropped it, and the shared
    table has now taken it back. Measured cost corpus-wide is 1 row -- Pika's
    `Research Intern (BS/MS/PhD)`, the only doctoral-flagged title that also
    admits a bachelor's student."""
    assert ashby_job_type(title=title) is None
    assert workday_job_type(title=title) is None


@pytest.mark.parametrize("title", [
    "Forward-Deployed Solution Engineer - International",  # md:35, 4 rows
    "Internal Audit Manager",                              # md:36, 2 rows
    "Design Verification Engineer - Internal IP",          # md:36
])
def test_ashby_boundaries_reject_the_66_internal_titles(title):
    """md:34 -- bare /intern/ matches 342 against the regex's 276."""
    assert ashby_job_type(title=title) is None


def test_ashby_university_recruiter_is_not_a_new_grad():
    """md:37 -- it hires interns, it is not one. `university` and `campus` measure
    only 5 titles between them and 2 are this, so neither bare form has a rule."""
    assert ashby_job_type(title="University Recruiter") is None


@pytest.mark.parametrize("title", [
    "WerkstudentIn Business Development & Talent Operations",  # md:43-44
    "Werkstudent/in Recruiting",
    "Werkstudent Content & Brand (m/w/d)",
])
def test_ashby_werkstudent_is_unclassified_under_english_only(title):
    """ENGLISH-ONLY. md:43-44 measures these as new_grad and the rule
    used to carry `werkstudent[a-z]*` with an inflected tail for exactly the
    three rows below. The German stem is gone, so they fall through. The
    English `working students?` form still resolves -- see the test below."""
    assert ashby_job_type(title=title) is None


def test_ashby_working_student_is_reachable_by_no_other_signal():
    """md:Student Programs -- the English form of Werkstudent. Four of its
    seven rows say neither Intern in the title nor Intern in employmentType."""
    assert ashby_job_type(title="Working Student, IT") == "student"


def test_ashby_explicit_internship_evidence_precedes_working_student():
    """An internship signal in the TITLE still wins -- the intern rule runs
    ahead of the student rule. The `employmentType` half of this case is gone
    with the enum leg; see the title-only test below."""
    assert ashby_job_type(title="Founder's Associate Intern / Working Student") == "intern"


def test_ashby_reads_only_the_title_and_the_department_intern_tier():
    """BEHAVIOUR CHANGE, 2026-08-26. The enum, team and description legs were
    dropped with the Ashby-measured table. `department` survives for the
    internship tier alone -- see the test below."""
    assert ashby_job_type(employment_type="Intern") is None
    assert ashby_job_type(team="Early Career") is None
    # Only the intern tier is read off department: the student and new_grad
    # patterns measured 15 rows here of which ~2 were right.
    assert ashby_job_type(department="Early Career") is None
    assert ashby_job_type(department="Student Support") is None
    assert ashby_job_type(
        description="No degree required; strong undergrads, grad students, and builders welcome.",
    ) is None
    # The title alone decides it, whatever the mixed bucket around it says.
    assert ashby_job_type(
        title="Working Student Billing & Receivable Operations",
        department="Working students / Interns / Trainees",
        team="Working students / Interns / Trainees",
    ) == "student"


def test_ashby_department_speaks_only_where_the_title_is_silent():
    """The department leg is LAST, and only for the internship tier. 3 rows
    corpus-wide, all residency titles filed under an `Internships` department --
    the department is the only place their internship status is stated."""
    assert ashby_job_type(title="AI Research Resident",
                          department="Internships") == "intern"
    # A title that already classified is not retyped by the department.
    assert ashby_job_type(title="Software Engineer, New Grad",
                          department="Internships") == "new_grad"


def test_ashby_department_cannot_revive_an_excluded_title():
    """The excluded tier is terminal, so these stop at the top of the table and
    never reach the department leg. Moving that rule higher would retype the 2
    `Internships & Co-ops` SkillBridge rows as coop."""
    assert ashby_job_type(title="SkillBridge: Production Control Warehouse Specialist",
                          department="Internships & Co-ops") is None


def test_ashby_campus_ambassador_is_the_known_gap():
    """BEHAVIOUR CHANGE, 2026-08-26 -- the one place the shared table is not a
    superset of the Ashby one. `campus( growth)? ambassadors?` was an
    Ashby-only rule and is deliberately NOT carried over as a per-vendor patch;
    it belongs upstream in job_type.md. 2 rows corpus-wide.

    `campus hires?` (job_type.md:57) does not reach these -- "Campus
    Ambassador" is not "Campus Hire"."""
    assert ashby_job_type(title="Nolla Skin Campus Ambassador") is None
    assert ashby_job_type(title="Technical Community Lead, Campus Leaders") is None


def test_ashby_student_titles_no_longer_need_description_evidence():
    """The bare `students?` rule reaches Student Ambassador from the title, so
    the two exact description phrases the Ashby table needed are redundant.

    The law-school row is the cost: the Ashby table kept it NULL by requiring
    a Bachelors-level description phrase, and the shared table's student `not`
    list has no clause for a JD candidate, so it now reads as `student`."""
    assert ashby_job_type(title="Student Ambassador") == "student"
    assert ashby_job_type(title="Law School Student Ambassador") == "student"
    # AI Resident carried no title signal at all -- only the description did.
    assert ashby_job_type(title="AI Resident") is None


@pytest.mark.parametrize(("payload", "expected"), [
    ({"title": "Working Student, IT", "employmentType": "FullTime"}, "student"),
    ({"title": "Intern / Working Student, IT", "employmentType": "FullTime"}, "intern"),
    # employmentType no longer reaches job_type, so the enum cannot retype the
    # title the way it did while the Ashby table read it.
    ({"title": "Working Student, IT", "employmentType": "Intern"}, "student"),
    ({"title": "Applied Scientist, PhD New Grad", "employmentType": "FullTime"}, None),
    ({"title": "Nolla Skin Campus Ambassador", "employmentType": "PartTime"}, None),
])
def test_ashby_parse_reads_the_title_through_the_adapter(payload, expected):
    """Exercise the adapter boundary, not only the rule resolver used above."""
    posting = json_api.parse(
        "ashby",
        Feed(id=1, source="ashby", key="acme"),
        Item("posting-1", {
            "department": "Engineering",
            "team": "Engineering",
            "descriptionPlain": "",
            **payload,
        }),
        {},
    )[0]

    assert posting.job_type == expected


def test_ashby_praktikum_has_no_rule_because_it_measures_zero_here():
    """md:45 -- praktikum and praktikant are both 0 in this corpus, so there was
    never a rule here to remove. (workday used to carry them and no longer
    does under ENGLISH-ONLY.) The row falls through to no signal."""
    assert ashby_job_type(title="Praktikum Marketing") is None


def test_ashby_employment_type_no_longer_reaches_job_type():
    """BEHAVIOUR CHANGE, 2026-08-26. This row used to resolve `intern` off the
    enum; with the enum leg gone the title's own signal decides it."""
    assert ashby_job_type(title="Working Student- Ecodesign",
                          employment_type="Intern") == "student"


@pytest.mark.parametrize(("title", "expected"), [
    ("Research Internships", "research"),
    ("Research Scientist Intern", "research"),
    ("AI Research Engineer Intern", "research"),   # word before "Intern"
    ("ML Research Intern (Fall 2026)", "research"),
    ("Research Assistant - Structural Bioinformatics", None),  # no internship signal
])
def test_ashby_research_internships_carry_the_research_label(title, expected):
    """BEHAVIOUR CHANGE, 2026-08-26. The Ashby table deliberately folded these
    into `intern` so they stayed visible as internships; job_type.md:25 gives
    research its own tier ahead of internship, matching workday. 61 rows.

    Anything downstream that filtered on `intern` alone now misses them."""
    assert ashby_job_type(title=title) == expected


def test_ashby_research_coop_stays_coop():
    assert ashby_job_type(title="Research Co-Op") == "coop"


def test_ashby_coop_resolves_before_the_employment_type_enum():
    """md:82 -- 4 of the 7 co-op titles are typed `Intern`, so an enum rule
    placed first would consume them."""
    assert ashby_job_type(title="Mechanical Engineering Fall Co-Op",
                          employment_type="Intern") == "coop"


@pytest.mark.parametrize("title", [
    "Software Engineer, Cooperative AI",                                # md:80
    "Engineering Manager, Cooperative Systems",
    "International Business Development & Defense Cooperation Manager",  # md:81
])
def test_ashby_coop_boundaries_reject_cooperative(title):
    """md:80 -- dropping the boundaries adds exactly these 4 rows."""
    assert ashby_job_type(title=title) != "coop"


@pytest.mark.parametrize(("title", "employment_type", "expected"), [
    ("Brand Ambassador", "PartTime", "yes"),                        # md:95
    # md:100 -- typed Contract, not PartTime. The enum wins, so the title
    # phrase below it never runs.
    ("[CONTRACT] Part-Time Recruiting Coordinator", "Contract", "unknown"),
    # md:101 -- which leaves the title as a tie-breaker for the FullTime rows.
    ("Part-Time Studio Coordinator", "FullTime", "yes"),
])
def test_ashby_employment_type_outranks_the_part_time_title_phrase(
        title, employment_type, expected):
    assert ashby_part_time(title=title, employment_type=employment_type) == expected


def test_ashby_hourly_pay_is_not_a_part_time_signal():
    """md:103-106 -- 277 hourly components, 174 of them FullTime. It tracks
    interns and shift work, so `compensation` has no rule at all."""
    assert ashby_part_time(
        title="Audiobook Specialist", employment_type="FullTime") == "unknown"


@pytest.mark.parametrize(("department", "expected"), [
    ("Internship Program", "intern"),   # md:58 -- the one tier still read
    ("Early Career", None),             # md:58, 9 rows -- new_grad tier dropped
    ("Internal Ops", None),             # md:59, 13 rows -- the false positive
])
def test_ashby_department_reads_the_intern_tier_only(department, expected):
    """BEHAVIOUR CHANGE, 2026-08-26. The team leg went with the Ashby table and
    department kept only its internship pattern.

    Measured before removal: department/team fired on 188 rows, of which 139
    only repeated what the title already said, 26 contradicted it, and 23 were
    the sole signal -- and only 3 of those 23 were correct. Restricting
    department to the intern tier keeps those 3 and drops the false positives
    (`PHARMACY` -> Shipping Coordinator, `Student Support` -> Student Success
    Coach)."""
    assert ashby_job_type(department=department) == expected
    # team is not read at all, whatever it says.
    assert ashby_job_type(team=department) is None


@pytest.mark.parametrize(("workplace_type", "expected"), [
    ("OnSite", "unknown"),   # md:115, 7,413 rows
    ("Hybrid", "hybrid"),    # md:116, 4,464 rows
    ("Remote", "yes"),       # md:117, 3,919 rows
])
def test_ashby_workplace_type_is_the_field(workplace_type, expected):
    assert ashby_remote(workplace_type=workplace_type) == expected


def test_ashby_hybrid_is_not_remote():
    """md:121-125 -- `isRemote` is derived from workplaceType and counts Hybrid
    as remote, 53% of its true rows. It stays in `extra`, unread."""
    assert ashby_remote(workplace_type="Hybrid") != "yes"


@pytest.mark.parametrize(("workplace_type", "expected"), [
    ("OnSite", "unknown"),   # md:133 -- 15 such rows
    ("Hybrid", "hybrid"),    # md:133 -- 51 such rows
])
def test_ashby_workplace_type_wins_where_location_contradicts_it(
        workplace_type, expected):
    """md:134 -- trust workplaceType where both exist. OnSite terminates rather
    than falling through, so the location rules cannot re-assert remote."""
    assert ashby_remote(workplace_type=workplace_type, location="US Remote") == expected


def test_ashby_location_covers_the_gap_cohort():
    """md:135 -- 261 remote-in-location rows carry no workplaceType at all, and
    they are the only reason to parse this string."""
    assert ashby_remote(location="Remote - United States") == "yes"
    assert ashby_remote(location="Boston (Hybrid)") == "hybrid"


@pytest.mark.parametrize("location", [
    "Work From Home",   # md:131 -- all three measure 0 here
    "Home-Based, UK",
    "Distributed",
])
def test_ashby_does_not_port_greenhouses_location_patterns(location):
    """md:131 -- work from home, home-based and distributed are all 0 on Ashby.
    Greenhouse measures 87 rows of Distributed alone; the list is not shared."""
    assert ashby_remote(location=location) == "unknown"


def test_ashby_a_remote_secondary_location_is_remote_eligible():
    """md:138-144 -- 359 rows pair a non-remote primary with a remote secondary.
    A single-site read of `location` calls every one of them onsite."""
    assert ashby_remote(location="New York", secondary=["Remote, USA"]) == "yes"
    assert ashby_remote(location="New York", secondary=["Austin, TX"]) == "unknown"


def test_ashby_secondary_location_uses_the_same_remote_fallbacks_as_primary():
    assert ashby_remote(location="Winnersh", secondary=["Hybrid"]) == "hybrid"
    assert ashby_remote(location="New York", secondary=["Anywhere"]) == "yes"


def test_ashby_workplace_type_still_wins_over_secondary_location():
    assert ashby_remote(
        workplace_type="OnSite", location="Winnersh", secondary=["Hybrid"]
    ) == "unknown"


# greenhouse, from doc/raw_postings/greenhouse.md ---------------------------

GREENHOUSE = json_api.CONFIGS["greenhouse"]["fields"]


def greenhouse_job_type(title="", metadata=None, departments=()):
    return resolve(GREENHOUSE["job_type"],
                   {"title": title, "metadata": metadata,
                    "departments": [{"name": name} for name in departments]})


def greenhouse_remote(location="", metadata=None):
    return resolve(GREENHOUSE["remote"], {"location": {"name": location}, "metadata": metadata})


def bag(**entries):
    """A tenant `metadata` array. md:3 -- null on 5,681 rows, so `None` is the
    other real shape and is covered by the accessor tests above."""
    return [{"name": name.replace("_", " "), "value": value, "value_type": "short_text"}
            for name, value in entries.items()]


@pytest.mark.parametrize(("title", "expected"), [
    ("Campus Systems Engineer (Intern)", "intern"),       # md:23
    ("Naval Architect Co-op - Winter 2027", "coop"),      # md:68, 5 hits
    ("Software Engineer, New Grad", "new_grad"),   # md:21
    ("New Graduate Engineer, Software", "new_grad"),
    ("Technical Support Engineer - University Graduate 2026", "new_grad"),
    ("Working Student - Tech", "student"),
    ("Werkstudent", None),  # ENGLISH-ONLY: German stem dropped, was "student"
    ("Data Center Apprentice Program", "student"),
    ("Apprentice Weld Support Technician", "student"),
    ("Software Engineer", None),
])
def test_greenhouse_titles_classify_as_the_doc_measured(title, expected):
    assert greenhouse_job_type(title=title) == expected


@pytest.mark.parametrize(("title", "expected"), [
    ("International Sector Analyst, Market Intelligence (MI)-Data Research", None),
    ("Internal Audit Research Manager", None),
    ("Research Scientist", None),               # research with no internship signal
    ("Clinical Research Associate II", None),   # a career title, not a student one
])
def test_greenhouse_research_is_a_subject_not_a_job_type(title, expected):
    assert greenhouse_job_type(title=title) == expected


def test_greenhouse_research_coop_stays_coop():
    assert greenhouse_job_type(
        title="Summer 2027 Research Internship/Co-op") == "coop"


@pytest.mark.parametrize("title", [
    "Director, Internal Audit",                                # md:25, 10 rows
    "Senior Growth Engagement Manager, International",         # md:25, 6 rows
    "Senior Software Engineer - Database Engine Internals",    # md:26, 6 rows
])
def test_greenhouse_boundaries_reject_the_261_internal_titles(title):
    """md:24 -- bare /intern/ matches 717 against the regex's 456."""
    assert greenhouse_job_type(title=title) is None


def test_greenhouse_coop_boundaries_reject_scoop():
    """md:67 -- Scoop contains coop, and dropping the leading boundary is the
    one thing that admits it."""
    assert greenhouse_job_type(title="Software Engineer, Scoop Core") is None


def test_greenhouse_coop_resolves_before_intern_on_a_title_carrying_both():
    """md:69 -- "Summer 2027 Software Engineering Internship/Co-op" matches
    both, and co-op is the narrower bucket."""
    assert greenhouse_job_type(
        title="Summer 2027 Software Engineering Internship/Co-op") == "coop"


@pytest.mark.parametrize("title", [
    # The doc-measured co-op/intern fixtures above used to be PhD titles. The
    # Excluded block fires first and terminally now, so the degree gate wins
    # over every bucket the rest of the title would otherwise earn.
    "Applied Research Intern, Systems (PhD / Graduate Co-op)",
    "PhD GenAI Research Scientist Intern",
    "Quantitative Trader Intern, PhD",
    "Campus AI Researcher, PhD/Postdoc (Intern)",
    "Postdoctoral Research Scientist",
])
def test_greenhouse_degree_gate_pre_empts_every_bucket(title):
    """job_type.md:16 -- a doctoral-only posting is not a student one."""
    assert greenhouse_job_type(title=title) is None


def test_greenhouse_employment_type_matches_a_tenant_prefixed_value():
    """md:39 -- "Jump Trading - Intern" is 60 rows, so the enum is matched as a
    substring rather than compared for equality."""
    assert greenhouse_job_type(metadata=bag(Employment_Type="Jump Trading - Intern")) == "intern"
    assert greenhouse_job_type(metadata=bag(Employment_Type="Summer Internship")) == "intern"


def test_greenhouse_summer_analyst_is_an_internship_family():
    assert greenhouse_job_type(title="2027 Research Summer Analyst") == "intern"


@pytest.mark.parametrize(("title", "departments"), [
    ("Algorithm Developer - 2027 PhDs", ("Campus",)),
    ("University Recruiter", ("University Recruiting",)),
    ("ICML 2026 - University Recruiting", ("University",)),
    ("Campus Ambassador", ("University Relations",)),
])
def test_greenhouse_new_grad_exclusions_precede_broad_groups(title, departments):
    assert greenhouse_job_type(title=title, departments=departments) is None


@pytest.mark.parametrize(("department", "expected"), [
    ("Internships", "intern"),                             # md:43, 47 rows
    ("Mechanical Engineering Internships", "intern"),      # md:46, 18 rows
    ("Templates : Early Career/New Grad", "new_grad"),  # md:49, 10 rows
    ("Internal Audit", None),                              # md:50, 25 rows
    ("Internal Fleet Operations", None),                   # md:50, 14 rows
    ("International G and A", None),                       # md:50, 10 rows
])
def test_greenhouse_departments_flatten_to_one_subject(department, expected):
    """`departments` is an array of {name}; the rule concatenates it so one
    pattern reads every entry."""
    assert greenhouse_job_type(departments=(department,)) == expected


def test_greenhouse_time_type_is_the_only_real_part_time_signal():
    """md:93-100 -- three casings of a closed set. The subject is lowercased and
    `.?` absorbs the hyphen, so one pattern covers all of them."""
    for value in ("Part time", "Part Time", "Part-time"):
        assert resolve(GREENHOUSE["part_time"],
                       {"metadata": bag(Time_Type=value)}) == "yes"
    for value in ("Full time", "Full Time", "Full-time", ""):
        assert resolve(GREENHOUSE["part_time"],
                       {"metadata": bag(Time_Type=value)}) == "unknown"


def test_greenhouse_content_has_no_rule_at_all():
    """md:74-80 -- 47 co-op hits in `content`, dominated by Riot Games' perks
    boilerplate ("we have co-op gaming areas"). md:85 -- "hours per week"
    likewise appears in full-time benefits copy. The field is never read."""
    assert greenhouse_job_type(title="Principal QA Engineer") is None


@pytest.mark.parametrize(("value", "expected"), [
    ("On-Site", "unknown"),                   # md:106, 763 rows
    ("Hybrid (Travel-Required)", "hybrid"),   # md:108, 14 rows
    ("Remote", "yes"),                        # md:107, 18 rows
    ("", "unknown"),                          # md:106, 107 blanks
])
def test_greenhouse_location_type_is_the_structured_statement(value, expected):
    assert greenhouse_remote(metadata=bag(Location_Type=value)) == expected


def test_greenhouse_on_site_terminates_rather_than_falling_through():
    """`Location Type` fills 2.8% of rows and `location.name` matches remote on
    14%, so without a terminal rule the noisier signal would overrule the
    structured one on every row that carries both."""
    assert greenhouse_remote(location="Remote - United States",
                             metadata=bag(Location_Type="On-Site")) == "unknown"


@pytest.mark.parametrize(("location", "expected"), [
    ("Remote - United States", "yes"),                       # md:111, 306 rows
    ("United States (Remote)", "yes"),                       # md:119, 103 rows
    ("Distributed", "yes"),                                  # md:120, 87 rows
    ("San Francisco, CA, US; Remote, US", "yes"),            # md:116, 136 rows
    ("Austin, TX", "unknown"),
])
def test_greenhouse_location_name_carries_the_recall(location, expected):
    """md:110 -- 4,624 of 32,753 rows, the widest remote signal of any vendor
    here. md:124 -- multi-site strings are remote-eligible and counted in it."""
    assert greenhouse_remote(location=location) == expected


def test_greenhouse_hybrid_is_excluded_before_a_remote_match_is_trusted():
    """md:123 -- hybrid appears in 627 location strings, and several name both."""
    assert greenhouse_remote(location="New York, NY (Hybrid)") == "hybrid"
    assert greenhouse_remote(location="Hybrid - Remote within Canada") == "hybrid"


# lever, from doc/raw_postings/lever.md -------------------------------------

LEVER = json_api.CONFIGS["lever"]["fields"]


def lever_job_type(text="", commitment="", department="", team=""):
    return resolve(LEVER["job_type"],
                   {"text": text, "categories": {"commitment": commitment,
                                                 "department": department, "team": team}})


def lever_remote(workplace_type=""):
    return resolve(LEVER["remote"], {"workplaceType": workplace_type})


@pytest.mark.parametrize(("text", "expected"), [
    ("Software Engineer, Internship", "intern"),                            # md:38, 47 hits
    ("Quantitative Trading Intern - Summer 2027", "intern"),                # md:38
    ("Electrical Engineering Spring Co-op (January 2027)", "coop"),         # md:71, the only one
    ("Software Engineer", None),
])
def test_lever_titles_classify_as_the_doc_measured(text, expected):
    assert lever_job_type(text=text) == expected


def test_lever_research_has_no_rule_because_it_measures_zero_here():
    """lever.md's corpus (2,404 rows / 16 boards) carries zero research∩intern
    titles -- unlike ashby, greenhouse and workday, which each measure a
    real one. Porting the pattern here anyway would be a guess wearing a
    measurement's clothes, so a research-flavoured title on lever falls
    through to whatever its other signals say -- here, plain `intern`."""
    assert lever_job_type(text="Research Intern") == "intern"


@pytest.mark.parametrize("text", [
    "Business Development Manager - Romania Internal Security & Caucasus",  # md:40
    "V-BAT Internal Training Instructor",                                   # md:40
    "Retail Banking Analyst (Cedacri International)",                       # md:41, 4 rows
])
def test_lever_boundaries_reject_the_7_internal_titles(text):
    """md:39 -- bare /intern/ matches 54 against the regex's 47."""
    assert lever_job_type(text=text) is None


def test_lever_university_recruiter_is_not_a_new_grad():
    """md:42 -- hires interns, is not one. The bare `university` and `campus`
    title forms are dropped for it; only the phrase forms survive."""
    assert lever_job_type(text="Recruiter, University and Alternative Channel") is None


def test_lever_apprentice_has_no_rule_because_it_measures_zero_here():
    """md:37 -- 0 hits on Lever, where workday measures 31. A bucket the
    survey measures nothing for gets no rule."""
    assert lever_job_type(text="Apprentice Electrician") is None


@pytest.mark.parametrize(("commitment", "expected"), [
    ("Internship", "intern"),         # md:47, 41 rows
    ("Intern", "intern"),             # md:48, 5 rows
    ("International Office Entity", None),   # md:49, 34 rows
    ("International EOR", None),             # md:49, 28 rows
])
def test_lever_commitment_is_tenant_free_text_not_an_enum(commitment, expected):
    """md:49 -- bare /intern/ would add 62 rows of these two values alone."""
    assert lever_job_type(commitment=commitment) == expected


def test_lever_part_time_reads_commitment_then_text():
    """md:84 (1 row on the enum) and md:93 (2 on the title). Thin, but measured."""
    assert resolve(LEVER["part_time"], {"categories": {"commitment": "Part-Time"}}) == "yes"
    assert resolve(LEVER["part_time"],
                   {"text": "Junior Consumer Support Navigator (Part-Time)"}) == "yes"
    assert resolve(LEVER["part_time"], {"text": "Software Engineer"}) == "unknown"


def test_lever_cjk_commitments_have_no_rule_because_the_domain_has_no_bucket():
    """md:87-88 -- 正社員 (58) is the third-largest full-time value and 契約社員 (2)
    is contract. Neither full-time nor contract is a `job_type` value, so there
    is nothing to write a rule against."""
    assert lever_job_type(text="Sales Representative", commitment="正社員") is None
    assert lever_job_type(text="Sales Representative", commitment="契約社員") is None


def test_lever_department_is_the_only_signal_on_the_zoox_rows():
    """md:53-55 -- 5 rows where title and commitment both miss."""
    assert lever_job_type(text="Contract Student Worker - Data Analyst (Part-time)",
                          department="Zoox Internships") == "intern"


@pytest.mark.parametrize(("team", "expected"), [
    ("Campus - Quantitative Trading", "new_grad"),   # md:58, 3 rows
    ("Campus", "new_grad"),                          # md:59, 2 rows
    ("International Business Development", None),           # md:60, 25 rows
])
def test_lever_team_carries_the_campus_signal(team, expected):
    assert lever_job_type(team=team) == expected


def test_lever_description_text_has_no_rule():
    """md:62-65 -- 38 hits, 37 already caught by the title, and the single extra
    row is a false positive. Zero recall gain, one bad row."""
    assert lever_job_type(text="Recruiter, University and Alternative Channel") is None


@pytest.mark.parametrize(("workplace_type", "expected"), [
    ("onsite", "unknown"),   # md:106, 887 rows
    ("remote", "yes"),       # md:106, 800 rows
    ("hybrid", "hybrid"),    # md:107, 679 rows
    ("", "unknown"),
])
def test_lever_workplace_type_is_the_whole_remote_story(workplace_type, expected):
    """md:103-104 -- 100% fill on a closed 3-value set. Because it never misses,
    no rule placed below it could ever fire, which is why `categories.location`
    has none: it mentions remote on 6 rows of 2,366 and 2 of those 5 contradict
    the enum (md:112-118)."""
    assert lever_remote(workplace_type=workplace_type) == expected


# smartrecruiters, from doc/raw_postings/smartRecruiters.md ------------------

SMARTRECRUITERS = json_api.CONFIGS["smartrecruiters"]["fields"]


def sr_job_type(name="", experience="", employment=""):
    return resolve(SMARTRECRUITERS["job_type"],
                   {"name": name, "experienceLevel": {"id": experience},
                    "typeOfEmployment": {"id": employment}})


def sr_remote(remote=None, hybrid=None):
    return resolve(SMARTRECRUITERS["remote"],
                   {"location": {"remote": remote, "hybrid": hybrid}})


@pytest.mark.parametrize(("name", "expected"), [
    ("Intern - Maintenance Engineer (Studying Master's Degree)", "intern"),   # md:49, 6 hits
    ("PhD Research Scientist Intern - Edge AI", "intern"),                    # md:49
    ("2027 Operations Development Program Intern (Undergraduate)", "intern"),
    ("Summer Internships 2027", "intern"),
])
def test_smartrecruiters_title_intern(name, expected):
    assert sr_job_type(name=name) == expected


@pytest.mark.parametrize("name", [
    "Lead, Data Science Internal Audit",                    # md:51, 4 hits
    "Internal Audit Manager",                               # md:51, 4 hits
    "Director, International Pricing and Revenue Management",  # md:52, 3 hits
    "Delivery Expert (09459) - 11951 International Dr, Ste C5",  # md:53-55
])
def test_smartrecruiters_boundaries_reject_the_91_internal_rows(name):
    """md:50 -- a bare /intern/ matches 202 against the bounded form's 111, and
    all 91 extras are Internal / International. The last case is this vendor's
    own trap: Domino's puts the street address in the title, so any road name
    carrying a keyword becomes a hit."""
    assert sr_job_type(name=name) is None


def test_smartrecruiters_cooper_street_is_not_a_co_op():
    """md:79-80 -- dropping the co-op boundaries adds 15 rows, every one of them
    a Domino's street address."""
    assert sr_job_type(name="Delivery Driver(09371) - 7207 S Cooper St") is None
    assert sr_job_type(name="Assistant Manager(06901) - 1838 Cooper St.") is None
    assert sr_job_type(name="Software Engineering Co-Op - Winter 2027") == "coop"


def test_smartrecruiters_co_op_resolves_before_the_internship_enum():
    """The ordering that matters most on this vendor: "Engineering Lab CO-OP"
    carries experienceLevel=internship, so an enum-first table would type every
    co-op as a plain intern."""
    assert sr_job_type(name="Engineering Lab CO-OP", experience="internship") == "coop"
    assert sr_job_type(name="CO-OP EHS (Environmental Health & Safety)",
                       experience="internship") == "coop"


@pytest.mark.parametrize(("name", "expected"), [
    ("Praktikum (all genders) im Bereich Clinical Supply Management", None),  # md:63
    ("Pflichtpraktikum - Vertrieb / Sales - REF95665X", None),                # md:63
    ("Pharmaziepraktikum in der Forschung und Entwicklung", None),            # compound
    ("Praktikant (all genders) im Bereich Human Resources", None),            # md:63
    ("Werkstudent (m/w/d) im Bereich R&D Engineering", None),                 # md:65, 69
    ("Werkstudent/in Marketing", None),                                       # no right boundary
    ("Estagio Juridico Trabalhista e Recursos Humanos", None),                # md:68, pt-BR
    ("Estágio Jurídico", None),                                               # md:68, accented
    ("Berufsausbildung Kfz-Mechatroniker (m/w/d)", None),                     # md:66
    ("Ausbildung Werkstoffprüfer (m/w/d)", None),                             # md:66
])
def test_smartrecruiters_non_english_titles_are_dropped_under_english_only(name, expected):
    """ENGLISH-ONLY. md:57-71 measures 777 rows (2.5%) as non-English across 20
    language codes and every title below as a student role; the de / pt-BR / th
    rules that read them were removed, so they now fall through unclassified.
    This is the measured cost of the English-only scope, pinned so the loss is
    visible rather than silent.

    md:69-71 remains the reason `language.code` could not gate this instead:
    "Werkstudent (m/w/d) im Bereich R&D Engineering" is tagged `en` and
    "Praktikant / Werkstudent (m/w/d)" is tagged `en-GB`, because the code
    describes the ad, not the title."""
    assert sr_job_type(name=name) == expected


@pytest.mark.parametrize(("name", "expected"), [
    # md:65 -- the English rendering German and Nordic tenants use when the
    # title itself is in English. Deliberately kept.
    ("Revenue Operations Analyst - Working Student", "intern"),
    # A non-English token in an otherwise English title is still reachable:
    # the English `intern` rule matches, the Thai one is not needed.
    ("Intern - Maintenance Technician (นักศึกษาฝึกงาน) Start in January 2027", "intern"),
])
def test_smartrecruiters_english_titles_survive_the_non_english_removal(name, expected):
    assert sr_job_type(name=name) == expected


@pytest.mark.parametrize("name", [
    "Pharmaziepraktikum in der Forschung und Entwicklung",
    "Berufsausbildung Industriemechaniker (m/w/d)",
    "Pflichtpraktikum - Product Data Management - REF96583W",
])
def test_smartrecruiters_german_compounds_are_unclassified_under_english_only(name):
    """ENGLISH-ONLY. These compounds are unclassified now that the stems are
    gone. The first assertion is kept as a live note for whoever restores them:
    the stems compound, and matcher()'s left boundary rejects the compound form,
    so a bare `praktikum` cannot match "Pharmaziepraktikum" nor `ausbildung`
    "Berufsausbildung". Any restored rule needs the leading `[a-z]*` back or it
    silently under-fires on most of this vocabulary."""
    assert classify.hit(name, "praktikum|ausbildung") is False
    assert sr_job_type(name=name) is None


def test_smartrecruiters_the_ausbildung_ordering_trap_is_moot_under_english_only():
    """ENGLISH-ONLY. This row used to exercise an ordering trap: "Praktikum - HR
    Ausbildung" matched both the intern and the apprenticeship rule, and it is an
    internship *in* the apprenticeship team, not an apprenticeship, so the intern
    token had to resolve first. Both rules are gone and the row is unclassified.
    Restoring either one alone would silently mistype it -- restore both, in the
    original order."""
    assert sr_job_type(
        name="Praktikum - HR Ausbildung, Recruiting & Personalentwicklung - REF98048S",
    ) is None


def test_smartrecruiters_the_enums_still_carry_rows_no_token_matches():
    """md:60-61 -- the structured fields flag 64 non-English rows an
    English-only title rule misses, and the token list above is not exhaustive
    of the 20 languages present. "Aussendienstmitarbeiter in der
    Pharmaindustrie" carries no student token in any of them."""
    name = "Aussendienstmitarbeiter in der Pharmaindustrie (all genders)"
    assert sr_job_type(name=name) is None
    assert sr_job_type(name=name, experience="internship") == "intern"


def test_smartrecruiters_three_intern_signals_are_complements():
    """md:28-29 -- union 175, but title alone 111, experienceLevel alone 139 and
    typeOfEmployment alone 97. None subsumes the others, so each must fire on
    its own."""
    assert sr_job_type(name="Computing Undergraduate Student Intern") == "intern"
    assert sr_job_type(experience="internship") == "intern"
    assert sr_job_type(employment="intern") == "intern"


def test_smartrecruiters_the_two_enums_disagree_freely_and_either_wins():
    """md:41-42 -- 57 rows have experienceLevel=internship while
    typeOfEmployment=permanent, because the two are set by different tenant
    workflows. Both are still an intern statement."""
    assert sr_job_type(experience="internship", employment="permanent") == "intern"


@pytest.mark.parametrize("name", [
    "Cashier - Pizza Maker (05468) Full/Part-time",     # md:99
    "GROWTH OPPORTUNITIES Assistant Manager (05459) Full/Part time",  # md:100
])
def test_smartrecruiters_title_is_not_a_part_time_signal(name):
    """md:98-100 -- DO NOT USE. 246 title hits but 107 are
    typeOfEmployment=permanent, because `Full/Part time` advertises *either*
    schedule and is the dominant form of the match."""
    assert resolve(SMARTRECRUITERS["part_time"], {"name": name}) == "unknown"
    assert resolve(SMARTRECRUITERS["part_time"],
                   {"name": name, "typeOfEmployment": {"id": "part-time"}}) == "yes"


@pytest.mark.parametrize(("remote", "hybrid", "expected"), [
    (True, False, "yes"),         # md:118, 879 rows
    (False, True, "hybrid"),      # md:117, 2,257 rows
    (False, False, "unknown"),    # md:116, 28,190 rows
    (None, None, "unknown"),
])
def test_smartrecruiters_remote_reads_the_two_booleans(remote, hybrid, expected):
    """md:115-121 -- real JSON booleans at 100% fill, mutually exclusive
    (remote=true,hybrid=true is 0 rows). `classify` skips a non-string `from`,
    so this is a lookup/fallback chain rather than a rule table; these cases
    pin that it still behaves first-match-wins."""
    assert sr_remote(remote=remote, hybrid=hybrid) == expected


def test_smartrecruiters_a_remote_row_still_carries_a_physical_office():
    """md:123-128 -- fullLocation is the hiring anchor, not the work location,
    which is why it gets no rule. Only 19 of 31,326 rows contain the word at
    all and 3 of those are remote=false."""
    assert sr_remote(remote=True, hybrid=False) == "yes"


# oracle, from doc/raw_postings/oracle.md ------------------------------------

ORACLE = json_api.CONFIGS["oracle"]["fields"]


def oracle_job_type(title="", job_schedule="", requisition_type=""):
    return resolve(ORACLE["job_type"], {"Title": title, "JobSchedule": job_schedule,
                                        "RequisitionType": requisition_type})


def oracle_remote(code=""):
    return resolve(ORACLE["remote"], {"WorkplaceTypeCode": code})


@pytest.mark.parametrize(("title", "expected"), [
    ("DRC Customer Success Intern - OVIP", "intern"),        # md:22, 6 hits
    ("OCI Software Engineer Intern - OVIP", "intern"),       # md:22, 6 hits
    ("Intern (Bachelor's)", "intern"),                       # md:22, 4 hits
    ("2027 Data for Good Hackathon - Summer Internship", "intern"),
])
def test_oracle_title_intern(title, expected):
    assert oracle_job_type(title=title) == expected


@pytest.mark.parametrize("title", [
    "Internal Audit Manager, Vice President",                 # md:27, 4 hits
    "International Private Bank, Relationship Partner",       # md:27, 4 hits
])
def test_oracle_boundaries_reject_the_267_internal_rows(title):
    """md:26 -- the largest such gap of any vendor surveyed: a bare /intern/
    matches 533 against the bounded form's 266."""
    assert oracle_job_type(title=title) is None


@pytest.mark.parametrize(("title", "expected"), [
    # BEHAVIOUR CHANGE, 2026-08-27 -- the shared table replaced the
    # Oracle-measured one. Measured over all 14,129 distinct postings: typed
    # 222 before, 469 after, 0 label disagreements on the 213 both type.
    #
    # 218 of the 247 adds are `summer analysts?|summer associates?` alone --
    # an internship that never says "intern", and the entire summer programme
    # of two investment-bank boards (hdpc 172, jpmc 46).
    ("2027 | APEJ | Sydney | Operations | Summer Analyst", "intern"),  # job_type.md:33
    ("2027 | Americas | Detroit | Wealth Management, Financial Planning "
     "| Summer Analyst", "intern"),                                   # job_type.md:33
    # Bare `students?`, where the old table carried only `apprentices?`.
    ("Student - Mfg", "student"),                                     # job_type.md:40
    ("Placement Student - Customer Engineering", "student"),          # job_type.md:40
    # `grad(uate|uates|s)?|ncg`, where the old rule needed an exact phrase.
    ("CX Sales Support Specialist - Fresh Graduate", "new_grad"),     # job_type.md:55
    ("Future Finance Leaders Program - Recent Grad/Full Time", "new_grad"),
    # A tier the old table did not have at all.
    ("2027 Quantitative Research - Risk and Treasury - Summer Internship "
     "- Associate - United States", "research"),                      # job_type.md:25
])
def test_oracle_shared_table_types_rows_the_measured_one_missed(title, expected):
    """The largest gain of any vendor swapped so far -- 2.1x the typed rows,
    and every one of them a pattern the measured table simply did not carry."""
    assert oracle_job_type(title=title) == expected


@pytest.mark.parametrize("title", [
    "Intern - Pharmacy",                                  # job_type.md:14, was intern
    "AI&I/Data Science Intern (PhD in progress) On-site",  # job_type.md:16, was intern
    "Early Careers Campus Recruiter - Corporate Functions",   # job_type.md:71, was new_grad
    "Early Career Development Program Manager, US Private Bank & Solutions",
])
def test_oracle_shared_table_drops_are_all_deliberate(title):
    """The swap drops 5 rows and no more -- there are no plain gaps. Two are
    licensure- or doctoral-gated, three run the early-careers channel rather
    than enter it, which the measured table had no veto for and typed wrong."""
    assert oracle_job_type(title=title) is None


def test_oracle_co_op_conversion_resolves_as_co_op_not_intern():
    """md:62 -- "2025 - Summer Intern Co-op Conversion" matches both patterns,
    which is precisely why co-op is declared first."""
    assert oracle_job_type(title="2025 - Summer Intern Co-op Conversion") == "coop"
    assert oracle_job_type(title="2027 - Saudi Arabia Co-Op Training") == "coop"


@pytest.mark.parametrize("requisition_type", ["Campus", "Student", "University Relations"])
def test_oracle_requisition_type_is_not_a_signal(requisition_type):
    """md:29-40 -- deliberately unread. It is a tenant-configured vocabulary
    ("HR4HR", "Shop", "Full Service Professional"), and Campus is a recruiting
    channel rather than an employment type: 986 Campus/Student/University rows,
    only 215 of which the title agrees are interns. Mapping it would type ~770
    experienced hires as students."""
    assert oracle_job_type(title="Senior Software Engineer",
                           requisition_type=requisition_type) is None


def test_oracle_part_time_comes_from_job_schedule_last():
    """md:67-70 -- a closed two-value field, null on the 7,006 rows of the one
    board that never sets it, which simply means no rule fires."""
    assert resolve(ORACLE["part_time"], {"JobSchedule": "Part time"}) == "yes"
    assert resolve(ORACLE["part_time"], {"JobSchedule": "Full time"}) == "unknown"
    assert resolve(ORACLE["part_time"], {"JobSchedule": ""}) == "unknown"
    # The two axes are independent: a part-time internship is both at once,
    # which the old single-valued job_type could not express.
    assert oracle_job_type(title="Summer Intern", job_schedule="Part time") == "intern"
    assert resolve(ORACLE["part_time"],
                   {"Title": "Summer Intern", "JobSchedule": "Part time"}) == "yes"


@pytest.mark.parametrize(("code", "expected"), [
    ("ORA_REMOTE", "yes"),        # md:87, 265 rows
    ("ORA_HYBRID", "hybrid"),     # md:86, 802 rows
    ("ORA_ON_SITE", "unknown"),   # md:86, 2,384 rows
    ("", "unknown"),              # 86.2% of the corpus never sets it
])
def test_oracle_remote_reads_the_machine_code_not_the_label(code, expected):
    """md:83-88 -- WorkplaceType spells a fourth value, "On-site with
    Flexibility", that maps onto ORA_ON_SITE anyway, so the code is the field."""
    assert oracle_remote(code=code) == expected


# eightfold, from doc/raw_postings/eightfold.md and regex/job_type.md ---------

EIGHTFOLD = json_api.CONFIGS["eightfold"]["fields"]


def ef_job_type(name=""):
    return resolve(EIGHTFOLD["job_type"], {"name": name})


def ef_remote(work_location_option="", location_flexibility=""):
    return resolve(EIGHTFOLD["remote"],
                   {"workLocationOption": work_location_option,
                    "locationFlexibility": location_flexibility})


@pytest.mark.parametrize(("name", "expected"), [
    ("Student Intern", "intern"),                       # md:118, 7 hits
    ("Maintenance Intern - Mechanical", "intern"),      # md:118, 3 hits
    ("Commercial and Business Internship", "intern"),   # md:119, 3 hits
    ("HR Intern", "intern"),                            # md:119, 3 hits
])
def test_eightfold_title_intern(name, expected):
    assert ef_job_type(name=name) == expected


@pytest.mark.parametrize("name", [
    "Interim Engineering Intern_Systems- 2026",   # md:97, 2 hits
    "Interim Engineering Intern_2027_HW",         # md:97
    "Interim Intern_OneIT",                       # md:98
    "1 year  Internship_SW_CDC",                  # md:98
])
def test_eightfold_underscore_titles_match_in_this_dialect(name):
    """md:93-102 warns that word boundaries MISS these, and prescribes an
    underscore-terminated alternative. That warning is about the SURVEY's
    dialect: Postgres `\\M` is built on `\\w`, where `_` is a word character.
    `classify.matcher()` wraps patterns in an explicit `(?![a-z0-9])` lookaround
    that excludes `_`, so the plain pattern already reaches all 7 rows and the
    md:100 fix must NOT be transcribed -- adding it would be dead weight, and
    dropping the boundaries to "fix" it re-admits the 34 Internal rows below."""
    assert ef_job_type(name=name) == "intern"


@pytest.mark.parametrize("name", [
    "Staff Internal Auditor",                        # md:107
    "Internal Control Manager - FCSD",               # md:107
    "AI Transformation Manager - Internal Control",  # md:107
    "Internal Control Analyst",                      # md:108
])
def test_eightfold_boundaries_still_reject_the_34_internal_rows(name):
    """The other half of the underscore case: Morgan Stanley alone contributes
    24 of these (md:106-108)."""
    assert ef_job_type(name=name) is None


def test_eightfold_has_no_part_time_rule():
    """md:127-131 -- **Undetectable**. No employment-type field exists and the
    title matches part-time on 4 rows in 7,949, so the bucket gets no rule at
    all rather than a rule measured somewhere else. The shared table has no
    part_time tier either, so adopting it changed nothing here."""
    assert ef_job_type(name="Part-Time Maintenance Technician") is None


@pytest.mark.parametrize(("name", "expected"), [
    # BEHAVIOUR CHANGE, 2026-08-27 -- the shared table replaced the
    # Eightfold-measured one. These are the 24 rows whose label moved,
    # measured over all 9,719 distinct postings in the 6 boards.
    #
    # 16 new new_grad, from patterns the old table did not carry.
    ("2026 Campus Hire_ Engineer_ HW", "new_grad"),      # job_type.md:57, 8 rows
    ("New Graduate- AI SW SDK Engineer", "new_grad"),    # job_type.md:55, 7 rows
    ("Graduate Geosolutions Geophysicist", "new_grad"),  # job_type.md:55
    ("Equity Research - Associate, Rotational Program (New York)",
     "new_grad"),                                        # job_type.md:63, 1 row
    # 8 intern -> research. A refinement: the old table had no research tier.
    ("Research Intern - Self-Improving AI", "research"),  # job_type.md:25
    ("Research Sciences INTERN", "research"),             # job_type.md:25
])
def test_eightfold_shared_table_types_rows_the_measured_one_missed(name, expected):
    """The old table typed 235 of 9,719 rows, the shared one 248, and on the
    224 both type they agree on the label EVERY time -- 0 disagreements. The
    whole difference is which rows get typed at all, and how finely."""
    assert ef_job_type(name=name) == expected


@pytest.mark.parametrize("name", [
    "Applied Science PhD Intern - Clipchamp",                # was intern
    "Applied Science: PhD Microsoft AI Internship Opportunities",
    "PhD Internship- AI Research",
])
def test_eightfold_doctoral_internships_now_void(name):
    """job_type.md:16 -- terminal and ahead of every typing rule, so a
    doctoral-enrolment-gated posting is voided whatever else the title says.
    These 3 are the ONLY rows the shared table stops typing; there are no
    plain gaps. Three more rows join them from the untyped side (2 PhD /
    postdoc, 1 nurse), for 6 excluded corpus-wide."""
    assert ef_job_type(name=name) is None


@pytest.mark.parametrize(("option", "expected"), [
    ("hybrid", "hybrid"),        # md:42, 231 rows
    ("remote_local", "yes"),     # md:42, 17 rows
    ("onsite", "unknown"),       # md:42, 7,653 rows
    ("", "unknown"),
])
def test_eightfold_onsite_is_the_unset_default_not_a_statement(option, expected):
    """md:44-53 -- Microsoft is 2,228 of 2,228 `onsite`, zero hybrid, zero
    remote, which is not a credible description of its actual arrangements. The
    value is what the field holds when the tenant never populated it, so
    `hybrid` and `remote_local` are trustworthy positives while `onsite` must
    resolve to unknown."""
    assert ef_remote(work_location_option=option) == expected


def test_eightfold_location_flexibility_is_relocation_not_remote():
    """md:56-62 -- orthogonal to the enum, not a refinement of it: 183 of its
    231 rows are `onsite`. It means "we will consider candidates anywhere in
    this country"."""
    assert ef_remote(work_location_option="onsite",
                     location_flexibility="flexible_within_country") == "unknown"


# icims, from doc/raw_postings/icms.md ---------------------------------------

ICIMS = json_api.CONFIGS["icims"]["fields"]


def icims_job_type(title="", employment_type="", categories=None):
    return resolve(ICIMS["job_type"], {"title": title, "employment_type": employment_type,
                                       "categories": categories or []})


@pytest.mark.parametrize(("title", "expected"), [
    ("Equity Analyst Internship: Summer 2027", "intern"),               # md:47, 3 hits
    ("ETF Sales Internship: Summer 2027", "intern"),
])
def test_icims_title_intern(title, expected):
    assert icims_job_type(title=title) == expected


@pytest.mark.parametrize(("title", "expected"), [
    # BEHAVIOUR CHANGE, 2026-08-27 -- the shared table replaced the
    # iCIMS-measured one. Measured over all 3,496 distinct postings: typed 137
    # before, 158 after, 0 label disagreements on the 111 both type.
    ("Graduate Trainee - Systems Design Engineer", "new_grad"),   # job_type.md:55
    ("Commercial Sales Rep (2026 NCG)", "new_grad"),              # job_type.md:55, `ncg`
    ("Apprentice, Private Equity", "student"),                    # job_type.md:40
    ("UIUC Research Park Intern - Validation", "research"),       # job_type.md:25
])
def test_icims_shared_table_types_rows_the_measured_one_missed(title, expected):
    """29 new_grad where the old rule needed the exact phrase `new grads?|
    graduate programs?`, 6 student from bare `apprentice` (md:48 recorded the
    word as absent; it is not absent any more), and 12 intern -> research."""
    assert icims_job_type(title=title) == expected


@pytest.mark.parametrize("title", [
    "Quantitative Research Internship - PhD: Summer 2027",   # was intern, md:47
    "Machine Learning Internship - PhD: 2027",
    "2027 PhD Research Engineering Intern/Co-op",            # was coop
])
def test_icims_doctoral_internships_now_void(title):
    """job_type.md:16 -- terminal and ahead of every typing rule. sig spells its
    whole quant programme this way, so this is 9 of the 10 rows the shared table
    stops typing. The tenth is "Early Career - Emerging Talent Programs and
    Recruiting Coordinator", vetoed as a channel operator (job_type.md:71)."""
    assert icims_job_type(title=title) is None


@pytest.mark.parametrize("title", [
    "Director, Internal Audit",                                # md:49
    "Internal Auditor II",                                     # md:49
    "Senior Software Engineer, Rivian Internal Mobile App",    # md:50
])
def test_icims_boundaries_reject_the_three_internal_rows(title):
    """md:48 -- the smallest such gap of any vendor (3 extras), but the same
    pattern applies everywhere."""
    assert icims_job_type(title=title) is None


def test_icims_spaced_hyphen_co_op_is_a_carried_regression():
    """KNOWN REGRESSION, 2026-08-27, and the only cost of the swap that is not
    a policy decision. md:104-108 measured a spaced-hyphen form here and widened
    this vendor's pattern to `co\\s?-?\\s?ops?` for it; the shared table's
    `co-?ops?` (job_type.md:20) does not reach it, so 2 rows go untyped.

    Pinned as the regression it is, NOT re-forked with a per-vendor override:
    the fix belongs upstream in the shared doc, and widening it there needs its
    own head-to-head across Workday and Eightfold first."""
    assert icims_job_type(title="Equity Options AI Co-op with Drexel University") == "coop"
    assert icims_job_type(title="Hardware Engineer University Co - Op Placement 2027") is None


def test_icims_intern_employment_type_is_not_a_signal():
    """md:66-71 -- all 47 INTERN rows are one tenant, and that tenant also tags
    experienced roles INTERN. A documented precision failure on a single-tenant
    field earns no rule; the title still decides."""
    assert icims_job_type(title="Quantitative Systematic Trader | Experienced Hire",
                          employment_type="INTERN") is None
    assert icims_job_type(title="Senior Trader - Prediction Markets | Experienced Hire",
                          employment_type="INTERN") is None


def test_icims_categories_are_a_single_tenants_vocabulary():
    """md:73-79 -- "Interns + Co-ops" (48) and "Internships" (5) have zero false
    positives, but every such value belongs to one tenant's job-family naming,
    so a rule keyed on them is a rule about one company."""
    assert icims_job_type(title="Software Engineer",
                          categories=[{"name": "Interns + Co-ops"}]) is None


@pytest.mark.parametrize("title", [
    "Product Guide III (Part-time) - University Village Seattle",   # md:52
    "Sales Advisor IV - University Village Seattle",                # md:53
    "University Recruiting Coordinator (Contract)",                 # md:53
    "Quantitative Campus Recruiter",                                # md:54
])
def test_icims_bare_university_and_campus_are_excluded(title):
    """md:52-54 -- measured as retail-store and recruiter noise, which is why
    the new_grad pattern carries only the phrase forms on every vendor.
    A recruiter hires interns; they are not one."""
    assert icims_job_type(title=title) != "new_grad"


def test_icims_part_time_comes_from_the_enum():
    """md:120-122 -- unlike INTERN, PART_TIME is filled by three tenants (costco
    150, rivian 33, amd 6) and is the only clean part-time field in the corpus.
    The title agrees but adds nothing: all 33 of its hits are one rivian retail
    family that fills both (md:130-133)."""
    assert resolve(ICIMS["part_time"], {"employment_type": "PART_TIME"}) == "yes"
    assert resolve(ICIMS["part_time"], {"employment_type": "FULL_TIME"}) == "unknown"


def test_icims_has_no_remote_rule():
    """md:140-166 -- `location_type` is DO NOT USE (a geocoding-precision hint,
    LAT_LNG/ANY); the only genuine arrangement field is one tenant's tags3 with
    3 rows; and the title matches remote and hybrid ZERO times. The column stays
    at its 'unknown' default rather than carrying a guess."""
    assert "remote" not in ICIMS


# THE PATTERN DIALECT -------------------------------------------------------
#
# The surveys in doc/raw_postings/ are written in Postgres regex; a rule table
# is not. A pattern transcribed in the wrong dialect matches nothing, which is
# indistinguishable at runtime from a signal the vendor does not carry -- so
# `matcher` rejects it instead of compiling it. See doc/raw_postings/
# transcribing.md for the translation these tests enforce.


@pytest.mark.parametrize("pattern", [
    r"\m(intern|interns|internship)\M",  # transcribed verbatim from a survey
    r"\mintern\M",
    r"\bintern\b",                       # \b is redundant here, and wrong on "c++"
    r"\Bintern",
    "(?i)intern",                        # the subject is lowercased, not the pattern
    "Intern",                            # can never match
    "new grad|Graduate Program",         # ...including one alternate out of several
    "^intern",
    "intern$",
])
def test_wrong_dialect_is_rejected_not_silently_compiled(pattern):
    with pytest.raises(ValueError):
        classify.matcher(pattern)


@pytest.mark.parametrize("pattern", [
    "intern(s|ship|ships)?",
    "co-?ops?",
    "new grads?|graduate program|university talent",
    "werkstudent[a-z]*|praktikum",
    "c\\+\\+",                  # symbol-bearing alias: why \b is unusable here
    "实习",                      # non-Latin: no case, no ASCII boundary
    r"a\sb",                    # escape sequences are exempt from the case rule
    r"a\Sb",
    r"costs 100\$",             # an escaped trailing $ is a literal, not an anchor
])
def test_well_formed_patterns_compile(pattern):
    assert classify.matcher(pattern) is not None


def _rules(spec):
    """Every classify rule anywhere inside a config, however nested."""
    if isinstance(spec, dict):
        yield from spec.get("classify", ())
        for value in spec.values():
            yield from _rules(value)
    elif isinstance(spec, list):
        for value in spec:
            yield from _rules(value)


_DOMAINS = {
    "job_type": {"intern", "coop", "student", "new_grad", "research"},
    "remote": {"yes", "hybrid", "unknown"},
    # Schedule, not opportunity kind. No 'no': a stated full-time value is not
    # trustworthy enough to assert, exactly as with `remote`.
    "part_time": {"yes", "unknown"},
}


# Vendors that had no job_type table until the coverage pass ---------------
#
# Each table below is transcribed from that vendor's own survey. The counts in
# the config comments are the measured ones; the cases here are the survey's
# own example values, so a rule that stops firing fails loudly rather than
# quietly emptying a tab.


def vendor_job_type(vendor, payload):
    return resolve(json_api.CONFIGS[vendor]["fields"]["job_type"], payload)


def vendor_part_time(vendor, payload):
    return resolve(json_api.CONFIGS[vendor]["fields"]["part_time"], payload)


@pytest.mark.parametrize(("vendor", "payload", "expected"), [
    # amazon.md -- co-op first, and 2 of the 6 co-op titles also say Intern.
    ("amazon", {"title": "Robotics - Applied Scientist II Intern / Co-op - 2026"}, "coop"),
    ("amazon", {"title": "2026 Software Development Engineer Intern"}, "intern"),

    # apple.md -- the plural tail carries Internships-titled reqs (md:15).
    ("apple", {"postingTitle": "Engineering Program Management Masters Internships"}, "intern"),
    ("apple", {"postingTitle": "Senior Manager, International Sales"}, None),

    # comeet.md -- `Intern` is a member of the closed seniority enum (md:22).
    ("comeet", {"name": "Customer Engineer Intern", "experience_level": "Entry-level"}, "intern"),
    ("comeet", {"name": "Support Rep", "experience_level": "Intern"}, "intern"),

    # workable.md -- `experience` is the intern field and agrees with the title 8/8.
    ("workable", {"title": "Intern, AML Compliance", "experience": "Internship"}, "intern"),
    ("workable", {"title": "Analyst", "experience": "Internship"}, "intern"),

    # recruitee.md -- a closed 9-value employment enum carries three buckets.
    ("recruitee", {"title": "Digital Forensics Intern", "employment_type_code": "internship"}, "intern"),
    ("recruitee", {"title": "Bakker", "employment_type_code": "apprenticeship"}, "student"),

    # talentreef.md -- 8,519 rows, and partTime is 37% of them.
    ("talentreef", {"title": "Pharmacy Intern", "contractType": "fullTime"}, "intern"),

    # ukg.md -- both rules are forward cover; the survey measures 0 of each.
    ("ukg", {"Title": "Nursing Intern"}, "intern"),
    ("ukg", {"Title": "Registered Nurse"}, None),

    # gem.md -- INTERN is a first-class enum member; prefer it to the title.
    ("gem", {"title": "Software Engineer Intern (US)", "job": {"employmentType": "INTERN"}}, "intern"),

    # rippling.md -- the part-time codes and the human label are both covered.
    ("rippling", {"name": "Machine Learning Software Engineer Intern - Winter 2027"}, "intern"),

    # bamboohr.md -- a 16-row corpus; these are existence statements, not rates.
    ("bamboohr", {"jobOpeningName": "Marketing, Communication, and Events Intern"}, "intern"),
])
def test_newly_covered_vendors_classify_as_their_survey_measured(vendor, payload, expected):
    assert vendor_job_type(vendor, payload) == expected


@pytest.mark.parametrize(("vendor", "payload", "expected"), [
    # The same survey rows as above, now read from the orthogonal `part_time`
    # column. Each vendor's own schedule field, never the title or description
    # except where its survey measured the title to be the only carrier.
    ("amazon", {"title": "Warehouse Associate", "job_schedule_type": "part-time"}, "yes"),
    ("amazon", {"title": "Software Development Engineer",
                "job_schedule_type": "full-time"}, "unknown"),
    ("comeet", {"name": "Barista", "employment_type": "Part-time"}, "yes"),
    ("workable", {"title": "Coordinator", "employment_type": "Part-time"}, "yes"),
    ("recruitee", {"title": "Verkoper", "employment_type_code": "parttime_fixed_term"}, "yes"),
    ("talentreef", {"title": "Server (PT/FT)", "contractType": "partTime"}, "yes"),
    ("rippling", {"name": "Analyst", "employmentType": {"label": "Salaried, part-time"}}, "yes"),
    ("rippling", {"name": "Tech", "employmentType": {"label": "HOURLY_PT"}}, "yes"),
    ("bamboohr", {"jobOpeningName": "Recruiter",
                  "employmentStatusLabel": "Part-Time Temporary"}, "yes"),
])
def test_newly_covered_vendors_part_time_as_their_survey_measured(vendor, payload, expected):
    assert vendor_part_time(vendor, payload) == expected


def test_part_time_is_orthogonal_to_job_type():
    """The split's whole point: a part-time internship is both at once. The
    single-valued job_type ranked part-time last, so it stored `intern` and
    dropped the schedule entirely."""
    payload = {"title": "2027 Software Dev Engineer Intern", "job_schedule_type": "part-time"}
    assert vendor_job_type("amazon", payload) == "intern"
    assert vendor_part_time("amazon", payload) == "yes"


@pytest.mark.parametrize(("vendor", "payload"), [
    # The Internal / International trap, which each of these surveys measured
    # firing on its own corpus. Boundaries are the only thing rejecting them.
    ("apple", {"postingTitle": "Senior Manager, International Sales"}),        # 12 extra, md:20
    ("talentreef", {"title": "Shift Leader - INTERNAL ONLY"}),                 # 11 extra, md:15
    ("rippling", {"name": "Group Product Manager, International"}),            # 67% error rate, md:56
])
def test_newly_covered_vendors_reject_the_internal_trap(vendor, payload):
    assert vendor_job_type(vendor, payload) is None


@pytest.mark.parametrize(("vendor", "payload", "why"), [
    # Each survey names a field that looks authoritative and is not. Pinning the
    # negative keeps a future "obvious" improvement from reintroducing it.
    ("gem", {"title": "Governance & Policy Fellow", "job": {"employmentType": "FULL_TIME"}},
     "gem.md:53 -- a fellowship is not an internship; do not fold in `fellow`"),
    ("rippling", {"name": "Eng", "employmentType": {"label": "Temporary / Intern"}},
     "rippling.md:53 -- TEMP is one enum value for two concepts, needs a title check"),
    ("workable", {"title": "Analyst", "experience": "Mid-Senior level", "employment_type": "Temporary"},
     "workable.md:64 -- Temporary is one tenant's intern convention, not a vendor meaning"),
    ("recruitee", {"title": "Analyst", "experience_code": "student_college"},
     "recruitee.md:34 -- a seniority band, not a statement that the posting is a student program"),
])
def test_newly_covered_vendors_do_not_read_the_fields_their_surveys_reject(vendor, payload, why):
    assert vendor_job_type(vendor, payload) is None, why


@pytest.mark.parametrize(("vendor", "payload"), [
    # Both fields are non-str in the payload, and the classify engine skips any
    # non-str value -- so a rule on either could never fire. Pinned so the
    # absence reads as measured rather than forgotten.
    ("apple", {"postingTitle": "Software Engineer", "standardWeeklyHours": 20}),
    ("ukg", {"Title": "Analyst", "FullTime": False}),
])
def test_numeric_and_boolean_part_time_signals_are_deliberately_unread(vendor, payload):
    assert vendor_job_type(vendor, payload) is None


def test_every_vendor_with_a_job_type_table_ranks_coop_first():
    """Co-ops are rarer than interns and their titles are a subset of the
    intern-ish set on every surveyed vendor, so an intern rule that ran first
    would consume them. Enforced across all vendors, not per table."""
    for name, config in json_api.CONFIGS.items():
        spec = config.get("fields", {}).get("job_type")
        if not isinstance(spec, dict) or "classify" not in spec:
            continue
        buckets = [rule["is"] for rule in spec["classify"]]
        if "coop" not in buckets or "intern" not in buckets:
            continue
        assert buckets.index("coop") < buckets.index("intern"), name


def test_every_configured_pattern_is_in_the_right_dialect():
    """Sweep every vendor, so a bad pattern fails in CI rather than by quietly
    classifying nothing on a feed nobody is watching."""
    for name, config in json_api.CONFIGS.items():
        for rule in _rules(config):
            for key in ("re", "not"):
                if key in rule:
                    classify.matcher(rule[key])  # raises on the wrong dialect


def test_every_configured_rule_emits_a_value_in_its_column_domain():
    """A typo'd bucket name reaches the database as a CHECK violation at write
    time, one full pipeline run away from the config that caused it."""
    for name, config in json_api.CONFIGS.items():
        for field, domain in _DOMAINS.items():
            spec = config.get("fields", {}).get(field)
            if not isinstance(spec, dict) or "classify" not in spec:
                continue
            emitted = {rule["is"] for rule in spec["classify"]}
            emitted.add(spec.get("default"))
            assert emitted <= domain | {None}, f"{name}.{field}"
            assert all({"from", "re", "is"} <= set(rule) for rule in spec["classify"])
