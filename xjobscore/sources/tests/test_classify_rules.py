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
    spec = {"classify": [{"from": "$site", "re": "campus", "is": "student_program"}]}
    assert resolve(spec, {}, {"site": "Campus"}) == "student_program"


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


# workday-list, from doc/raw_postings/workday.md ---------------------------

WORKDAY = json_api.CONFIGS["workday-list"]["fields"]


def workday_job_type(title="", external_path="", site="", time_type=""):
    return resolve(WORKDAY["job_type"],
                   {"title": title, "externalPath": external_path, "timeType": time_type},
                   {"site": site})


def workday_remote(remote_type="", locations_text="", title=""):
    return resolve(WORKDAY["remote"],
                   {"remoteType": remote_type, "locationsText": locations_text, "title": title})


@pytest.mark.parametrize(("title", "expected"), [
    ("Software Engineering Intern", "intern"),                      # md:17, 555 hits
    ("Metrology Co-Op", "coop"),                                    # md:61, 36 hits
    ("Werkstudent (m/w/d) im Bereich E-Commerce", "intern"),        # md:23, German tenants
    ("Praktikanten Marketing", "intern"),                           # md:19
    ("Auto Damage Trainee (Field)", "intern"),                      # md:22
    ("Software Engineer, New Grad", "student_program"),             # md:18, 134 hits
    ("Apprentice Electrician", "student_program"),                  # md:20, 31 hits
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


def test_the_slug_carries_the_signal_when_the_title_does_not():
    """md:34-35 -- 2 known cases where the title was edited after the slug was
    generated, so externalPath is the only surviving evidence."""
    assert workday_job_type(
        title="Mechanical Engineer",
        external_path="/job/Austin/Mechanical-Engineering---System-Packaging-Intern_R00338192",
    ) == "intern"


def test_coop_wins_over_intern_when_a_title_carries_both():
    """md:57-58 -- co-op titles are a subset of the intern-ish set, so the
    co-op rules must resolve first or every co-op is filed as an internship."""
    assert workday_job_type(title="Co-op Intern, Robotics") == "coop"


def test_feed_identity_is_a_last_resort_not_an_override():
    assert workday_job_type(title="Software Engineer", site="INTERN") == "intern"
    # A real title signal must not be overridden by the site it came from.
    assert workday_job_type(title="Software Engineering Co-op", site="INTERN") == "coop"


@pytest.mark.parametrize(("time_type", "expected"), [
    ("Part time", "part_time"),   # md:83, 103 hits
    ("part TIME", "part_time"),   # md:83 -- case-insensitive, same as Detail
    ("Full time", None),          # md:82, 1,278 hits -- must not fire
])
def test_time_type_maps_part_time_as_the_doc_measured(time_type, expected):
    assert workday_job_type(time_type=time_type) == expected


@pytest.mark.parametrize(("title", "expected"), [
    ("Pharmacy Technician - Inpatient, PRN", "part_time"),               # md:89
    ("Bilingual Spanish Part Time Retail Sales Consultant", "part_time"),  # md:87
    ("Doradca Klienta (Part-time)", "part_time"),                        # md:88
])
def test_title_carries_part_time_when_time_type_is_blank(title, expected):
    """md:85-89 -- blank-timeType rows still carry the signal in the title."""
    assert workday_job_type(title=title) == expected


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
    ("PhD Research Intern, Generative AI - 2026", "research"),
    ("Student Researcher, Robotics", None),  # "research" with no internship signal
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


def ashby_remote(workplace_type="", location="", secondary=()):
    return resolve(ASHBY["remote"],
                   {"workplaceType": workplace_type, "location": location,
                    "secondaryLocations": [{"location": value} for value in secondary]})


@pytest.mark.parametrize(("title", "expected"), [
    ("Software Engineering Intern", "intern"),                  # md:32, 276 title hits
    ("Intern, Financial Planning", "intern"),                   # md:32
    ("Software Engineer, New Grad", "student_program"),         # md:29, 47 hits
    ("December 2026 New Graduate Engineer, Software", "student_program"),
    ("CoDesign & NextGen - New College Grad", "student_program"),
    ("Software Engineer (2027 University Grad)", "student_program"),
    ("Quantitative Trader - 2027 Graduate Program", "student_program"),
    ("Apprentice Electrician", None),                            # vocational, not college
    ("Software Engineer", None),
])
def test_ashby_titles_classify_as_the_doc_measured(title, expected):
    assert ashby_job_type(title=title) == expected


def test_ashby_phd_new_grad_is_not_an_undergraduate_program():
    assert ashby_job_type(title="Applied Scientist, PhD New Grad") is None


@pytest.mark.parametrize("title", [
    "Forward-Deployed Solution Engineer - International",  # md:35, 4 rows
    "Internal Audit Manager",                              # md:36, 2 rows
    "Design Verification Engineer - Internal IP",          # md:36
])
def test_ashby_boundaries_reject_the_66_internal_titles(title):
    """md:34 -- bare /intern/ matches 342 against the regex's 276."""
    assert ashby_job_type(title=title) is None


def test_ashby_university_recruiter_is_not_a_student_program():
    """md:37 -- it hires interns, it is not one. `university` and `campus` measure
    only 5 titles between them and 2 are this, so neither bare form has a rule."""
    assert ashby_job_type(title="University Recruiter") is None


@pytest.mark.parametrize("title", [
    "WerkstudentIn Business Development & Talent Operations",  # md:43-44
    "Werkstudent/in Recruiting",
    "Werkstudent Content & Brand (m/w/d)",
])
def test_ashby_werkstudent_matches_with_no_right_boundary(title):
    """md:Student Programs -- three of the four German rows carry an inflected
    tail, so the pattern ends in `[a-z]*` rather than at a boundary."""
    assert ashby_job_type(title=title) == "student_program"


def test_ashby_working_student_is_reachable_by_no_other_signal():
    """md:Student Programs -- the English form of Werkstudent. Four of its
    seven rows say neither Intern in the title nor Intern in employmentType."""
    assert ashby_job_type(title="Working Student, IT") == "student_program"


@pytest.mark.parametrize(("kwargs", "expected"), [
    ({"title": "Founder's Associate Intern / Working Student"}, "intern"),
    ({"title": "Working Student- Ecodesign", "employment_type": "Intern"}, "intern"),
])
def test_ashby_explicit_internship_evidence_precedes_working_student(kwargs, expected):
    """Three of the ten measured Working Student/Werkstudent rows carry an
    explicit internship signal; that narrower evidence must still win."""
    assert ashby_job_type(**kwargs) == expected


def test_ashby_working_student_outranks_a_mixed_early_talent_department():
    """Current Statista roles use one mixed bucket for working students,
    interns, and trainees. The role-specific title is the stronger fact."""
    assert ashby_job_type(
        title="Working Student Billing & Receivable Operations",
        department="Working students / Interns / Trainees",
        team="Working students / Interns / Trainees",
    ) == "student_program"


def test_ashby_campus_ambassador_is_the_student_not_the_program_staff():
    assert ashby_job_type(title="Nolla Skin Campus Ambassador") == "student_program"
    assert ashby_job_type(title="Technical Community Lead, Campus Leaders") is None


def test_ashby_exact_description_evidence_reaches_undergraduate_programs():
    assert ashby_job_type(
        title="AI Resident",
        description="No degree required; strong undergrads, grad students, and builders welcome.",
    ) == "student_program"
    assert ashby_job_type(
        title="Student Ambassador",
        description="Currently enrolled at a university - Bachelors Level",
    ) == "student_program"
    assert ashby_job_type(
        title="Law School Student Ambassador",
        description="Current JD candidate",
    ) is None


@pytest.mark.parametrize(("payload", "expected"), [
    ({"title": "Working Student, IT", "employmentType": "FullTime"}, "student_program"),
    ({"title": "Intern / Working Student, IT", "employmentType": "FullTime"}, "intern"),
    ({"title": "Working Student, IT", "employmentType": "Intern"}, "intern"),
    ({"title": "Applied Scientist, PhD New Grad", "employmentType": "FullTime"}, None),
    ({"title": "Nolla Skin Campus Ambassador", "employmentType": "PartTime"}, "student_program"),
])
def test_ashby_parse_emits_student_program_with_narrower_signals_first(payload, expected):
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
    """md:45 -- praktikum and praktikant are both 0 in this corpus. workday-list
    carries them; porting them here would be a guess wearing a measurement's
    clothes, so the row falls through to no signal."""
    assert ashby_job_type(title="Praktikum Marketing") is None


def test_ashby_employment_type_reaches_rows_the_title_cannot():
    """Explicit internship metadata outranks the student-program title rule."""
    assert ashby_job_type(title="Working Student- Ecodesign", employment_type="Intern") == "intern"


def test_ashby_research_internships_use_the_visible_intern_type():
    assert ashby_job_type(title="Research Internships", employment_type="Intern") == "intern"


@pytest.mark.parametrize(("title", "expected"), [
    ("Research Scientist Intern", "intern"),
    ("AI Research Engineer Intern", "intern"),   # word before "Intern"
    ("ML Research Intern (Fall 2026)", "intern"),
    ("Research Assistant - Structural Bioinformatics", None),  # no internship signal
])
def test_ashby_research_is_a_subject_not_a_job_type(title, expected):
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
    ("Brand Ambassador", "PartTime", "part_time"),                        # md:95
    # md:100 -- typed Contract, not PartTime. The enum wins, so the title
    # phrase below it never runs.
    ("[CONTRACT] Part-Time Recruiting Coordinator", "Contract", None),
    # md:101 -- which leaves the title as a tie-breaker for the FullTime rows.
    ("Part-Time Studio Coordinator", "FullTime", "part_time"),
])
def test_ashby_employment_type_outranks_the_part_time_title_phrase(
        title, employment_type, expected):
    assert ashby_job_type(title=title, employment_type=employment_type) == expected


def test_ashby_hourly_pay_is_not_a_part_time_signal():
    """md:103-106 -- 277 hourly components, 174 of them FullTime. It tracks
    interns and shift work, so `compensation` has no rule at all."""
    assert ashby_job_type(title="Audiobook Specialist", employment_type="FullTime") is None


@pytest.mark.parametrize(("department", "expected"), [
    ("Internship Program", "intern"),        # md:58, 17 rows
    ("Early Career", "student_program"),     # md:58, 9 rows
    ("Internal Ops", None),                  # md:59, 13 rows -- the false positive
])
def test_ashby_department_and_team_carry_the_same_signal(department, expected):
    """md:61 -- the two are equal on 62% of rows, so both are queried."""
    assert ashby_job_type(department=department) == expected
    assert ashby_job_type(team=department) == expected


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
    ("Software Engineer, New Grad", "student_program"),   # md:21
    ("New Graduate Engineer, Software", "student_program"),
    ("Technical Support Engineer - University Graduate 2026", "student_program"),
    ("Working Student - Tech", "student_program"),
    ("Werkstudent", "student_program"),
    ("Data Center Apprentice Program", "student_program"),
    ("Apprentice Weld Support Technician", "student_program"),
    ("Software Engineer", None),
])
def test_greenhouse_titles_classify_as_the_doc_measured(title, expected):
    assert greenhouse_job_type(title=title) == expected


@pytest.mark.parametrize(("title", "expected"), [
    ("Quantitative Research Intern", "intern"),
    ("2026 BSc/MSc/PhD Quantitative Research/Strat Internship", "intern"),
    ("Research Engineer Intern (Fall 2026)", "intern"),
    ("Researcher Intern", "intern"),
    ("International Sector Analyst, Market Intelligence (MI)-Data Research", None),
    ("Internal Audit Research Manager", None),
    ("Research Scientist", None),               # research with no internship signal
    ("Clinical Research Associate II", None),   # a career title, not a student one
])
def test_greenhouse_research_is_a_subject_not_a_job_type(title, expected):
    assert greenhouse_job_type(title=title) == expected


def test_greenhouse_research_coop_stays_coop():
    assert greenhouse_job_type(
        title="Applied Research Intern, Proactive Intelligence & Customer "
        "World Models (PhD / Graduate Co-op)"
    ) == "coop"


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
    """md:69 -- "Applied Research Intern, ... (PhD / Graduate Co-op)" matches
    both, and co-op is the narrower bucket."""
    assert greenhouse_job_type(
        title="Applied Research Intern, Systems (PhD / Graduate Co-op)") == "coop"


def test_greenhouse_employment_type_matches_a_tenant_prefixed_value():
    """md:39 -- "Jump Trading - Intern" is 60 rows, so the enum is matched as a
    substring rather than compared for equality."""
    assert greenhouse_job_type(metadata=bag(Employment_Type="Jump Trading - Intern")) == "intern"
    assert greenhouse_job_type(metadata=bag(Employment_Type="Summer Internship")) == "intern"
    assert greenhouse_job_type(metadata=bag(Employment_Type="Full-time - Campus")) == "student_program"


def test_greenhouse_summer_analyst_is_an_internship_family():
    assert greenhouse_job_type(title="2027 Research Summer Analyst") == "intern"


@pytest.mark.parametrize(("title", "departments"), [
    ("Machine Learning Engineer (PhD, New Grad)", ("Early Career",)),
    ("Algorithm Developer - 2027 PhDs", ("Campus",)),
    ("University Recruiter", ("University Recruiting",)),
    ("ICML 2026 - University Recruiting", ("University",)),
    ("Campus Ambassador", ("University Relations",)),
    ("Mission Ops Engineer - SkillBridge", ("Early Career/New Grad",)),
])
def test_greenhouse_student_program_exclusions_precede_broad_groups(title, departments):
    assert greenhouse_job_type(title=title, departments=departments) is None


def test_greenhouse_explicit_internship_precedes_phd_exclusion():
    assert greenhouse_job_type(title="PhD Research Intern") == "intern"


@pytest.mark.parametrize("title", [
    "SkillBridge Software Engineering Intern",
    "Skillbridge Administrative Operations Apprentice",
    "Campus Ambassador Intern",
])
def test_greenhouse_eligibility_exclusions_are_terminal(title):
    assert greenhouse_job_type(title=title) is None


def test_greenhouse_workday_job_profile_is_read_as_well():
    """md:52-59 -- 239 rows, a second tenant-defined home for the same fact."""
    assert greenhouse_job_type(metadata=bag(Workday_Job_Profile="Technology Intern")) == "intern"


@pytest.mark.parametrize(("department", "expected"), [
    ("Internships", "intern"),                             # md:43, 47 rows
    ("Mechanical Engineering Internships", "intern"),      # md:46, 18 rows
    ("Campus", "student_program"),                         # md:44, 35 rows
    ("University - Open Positions", "student_program"),    # md:46, 20 rows
    ("Templates : Early Career/New Grad", "student_program"),  # md:49, 10 rows
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
        assert greenhouse_job_type(metadata=bag(Time_Type=value)) == "part_time"
    for value in ("Full time", "Full Time", "Full-time", ""):
        assert greenhouse_job_type(metadata=bag(Time_Type=value)) is None


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
    ("Junior Consumer Support Navigator (Part-Time)", "part_time"),         # md:93
    ("Software Engineer", None),
])
def test_lever_titles_classify_as_the_doc_measured(text, expected):
    assert lever_job_type(text=text) == expected


def test_lever_research_has_no_rule_because_it_measures_zero_here():
    """lever.md's corpus (2,404 rows / 16 boards) carries zero research∩intern
    titles -- unlike ashby, greenhouse and workday-list, which each measure a
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


def test_lever_university_recruiter_is_not_a_student_program():
    """md:42 -- hires interns, is not one. The bare `university` and `campus`
    title forms are dropped for it; only the phrase forms survive."""
    assert lever_job_type(text="Recruiter, University and Alternative Channel") is None


def test_lever_apprentice_has_no_rule_because_it_measures_zero_here():
    """md:37 -- 0 hits on Lever, where workday-list measures 31. A bucket the
    survey measures nothing for gets no rule."""
    assert lever_job_type(text="Apprentice Electrician") is None


@pytest.mark.parametrize(("commitment", "expected"), [
    ("Internship", "intern"),         # md:47, 41 rows
    ("Intern", "intern"),             # md:48, 5 rows
    ("Part-Time", "part_time"),       # md:84, 1 row
    ("International Office Entity", None),   # md:49, 34 rows
    ("International EOR", None),             # md:49, 28 rows
])
def test_lever_commitment_is_tenant_free_text_not_an_enum(commitment, expected):
    """md:49 -- bare /intern/ would add 62 rows of these two values alone."""
    assert lever_job_type(commitment=commitment) == expected


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
    ("Campus - Quantitative Trading", "student_program"),   # md:58, 3 rows
    ("Campus", "student_program"),                          # md:59, 2 rows
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
    ("Praktikum (all genders) im Bereich Clinical Supply Management", "intern"),  # md:63
    ("Pflichtpraktikum - Vertrieb / Sales - REF95665X", "intern"),                # md:63
    ("Pharmaziepraktikum in der Forschung und Entwicklung", "intern"),            # compound
    ("Praktikant (all genders) im Bereich Human Resources", "intern"),            # md:63
    ("Werkstudent (m/w/d) im Bereich R&D Engineering", "intern"),                 # md:65, 69
    ("Werkstudent/in Marketing", "intern"),                                       # no right boundary
    ("Revenue Operations Analyst - Working Student", "intern"),                   # md:65
    ("Estagio Juridico Trabalhista e Recursos Humanos", "intern"),                # md:68, pt-BR
    ("Estágio Jurídico", "intern"),                                               # md:68, accented
    ("Intern - Maintenance Technician (นักศึกษาฝึกงาน) Start in January 2027", "intern"),  # md:68, th
    ("Berufsausbildung Kfz-Mechatroniker (m/w/d)", "student_program"),            # md:66
    ("Ausbildung Werkstoffprüfer (m/w/d)", "student_program"),                    # md:66
])
def test_smartrecruiters_non_english_titles_are_matched_unconditionally(name, expected):
    """md:57-71 -- 777 rows (2.5%) are non-English across 20 language codes, and
    an English-only rule misses them. md:69-71 is why `language.code` cannot
    gate this: "Werkstudent (m/w/d) im Bereich R&D Engineering" is tagged `en`
    and "Praktikant / Werkstudent (m/w/d)" is tagged `en-GB`, because the code
    describes the ad, not the title."""
    assert sr_job_type(name=name) == expected


@pytest.mark.parametrize("name", [
    "Pharmaziepraktikum in der Forschung und Entwicklung",
    "Berufsausbildung Industriemechaniker (m/w/d)",
    "Pflichtpraktikum - Product Data Management - REF96583W",
])
def test_smartrecruiters_german_compounds_need_the_leading_wildcard(name):
    """The stems compound, and matcher()'s left boundary rejects the compound
    form: a bare `praktikum` cannot match "Pharmaziepraktikum", nor `ausbildung`
    "Berufsausbildung". Dropping the leading `[a-z]*` silently loses most of
    this vocabulary -- which is the failure mode the dialect is designed to make
    loud, but here it would be a rule that merely under-fires."""
    assert classify.hit(name, "praktikum|ausbildung") is False
    assert sr_job_type(name=name) is not None


def test_smartrecruiters_an_internship_in_the_training_department_is_an_intern():
    """The ordering trap the ausbildung rule creates: "Praktikum - HR
    Ausbildung" matches both, and it is an internship *in* the apprenticeship
    team, not an apprenticeship. Intern tokens must resolve first."""
    assert sr_job_type(
        name="Praktikum - HR Ausbildung, Recruiting & Personalentwicklung - REF98048S",
    ) == "intern"


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
    assert sr_job_type(name=name) is None
    assert sr_job_type(name=name, employment="part-time") == "part_time"


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
    assert oracle_job_type(job_schedule="Part time") == "part_time"
    assert oracle_job_type(job_schedule="Full time") is None
    assert oracle_job_type(job_schedule="") is None
    # An intern row that is also part time is an intern first.
    assert oracle_job_type(title="Summer Intern", job_schedule="Part time") == "intern"


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


# eightfold, from doc/raw_postings/eightfold.md -------------------------------

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
    all rather than a rule measured somewhere else."""
    assert ef_job_type(name="Part-Time Maintenance Technician") is None


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
    ("Quantitative Research Internship - PhD: Summer 2027", "intern"),  # md:47, 3 hits
    ("Equity Analyst Internship: Summer 2027", "intern"),               # md:47, 3 hits
    ("ETF Sales Internship: Summer 2027", "intern"),
])
def test_icims_title_intern(title, expected):
    assert icims_job_type(title=title) == expected


@pytest.mark.parametrize("title", [
    "Director, Internal Audit",                                # md:49
    "Internal Auditor II",                                     # md:49
    "Senior Software Engineer, Rivian Internal Mobile App",    # md:50
])
def test_icims_boundaries_reject_the_three_internal_rows(title):
    """md:48 -- the smallest such gap of any vendor (3 extras), but the same
    pattern applies everywhere."""
    assert icims_job_type(title=title) is None


def test_icims_co_op_spelling_is_not_uniform():
    """md:104-108 -- the corpus carries a spaced-hyphen form the plain co-?op
    pattern misses, so the separators are optional on both sides. Bounded and
    unbounded agree here, but the boundaries stay regardless."""
    assert icims_job_type(title="Equity Options AI Co-op with Drexel University") == "coop"
    assert icims_job_type(title="Hardware Engineer University Co - Op Placement 2027") == "coop"


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
    the student_program pattern carries only the phrase forms on every vendor.
    A recruiter hires interns; they are not one."""
    assert icims_job_type(title=title) != "student_program"


def test_icims_part_time_comes_from_the_enum():
    """md:120-122 -- unlike INTERN, PART_TIME is filled by three tenants (costco
    150, rivian 33, amd 6) and is the only clean part-time field in the corpus.
    The title agrees but adds nothing: all 33 of its hits are one rivian retail
    family that fills both (md:130-133)."""
    assert icims_job_type(employment_type="PART_TIME") == "part_time"
    assert icims_job_type(employment_type="FULL_TIME") is None


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
    "job_type": {"intern", "coop", "student_program", "research", "part_time"},
    "remote": {"yes", "hybrid", "unknown"},
}


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
