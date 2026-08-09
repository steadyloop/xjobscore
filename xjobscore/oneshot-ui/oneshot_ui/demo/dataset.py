"""The fictional corpus demo mode harvests.

Every posting here is written to land on a specific decision-engine outcome, so
the seeded board exercises the real classifier rather than hand-written JSON.
The intent of each one is stated in a trailing comment; if you edit a title or
a description, re-run ``python -m oneshot_ui.demo verify`` — the gates in
``classifier.py``, ``taxonomy.py`` and ``scoring.py`` are keyword-sensitive.

Companies are invented.  ATS vendor names are real because the dedupe module
extracts scoped identifiers from real vendor URL shapes, and a demo that used
fake hosts would silently skip that code path.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from xjobscore.records import Canonical, Fact, Feed, Label, Posting

__all__ = ["DEMO_FEEDS", "DEMO_PROFILE", "demo_rounds", "vendor_names"]


def _now() -> datetime:
    """Anchor the corpus to today at noon UTC, not to the current instant.

    A posting's content hash covers its timestamps, so a corpus built from
    ``datetime.now()`` would differ on every process start and make each fetch
    look like a revision. Anchoring to the day keeps a re-fetch a no-op while
    still letting the demo stay current across days.
    """
    return datetime.combine(
        datetime.now(timezone.utc).date(), time(12, 0), tzinfo=timezone.utc
    )


def _days(count: int) -> datetime:
    return _now() + timedelta(days=count)


def _description(
    summary: str,
    *,
    required: tuple[str, ...] = (),
    preferred: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
) -> str:
    """Build a posting body in the section shape ``extract_stack`` reads.

    A short heading carrying a requirement cue sets the evidence level for the
    bullets beneath it, which is how a technology becomes ``required`` rather
    than ``mentioned``.
    """
    parts = [f"<p>{summary}</p>"]
    if required:
        parts.append("<h3>Requirements</h3><ul>")
        parts.extend(f"<li>{line}</li>" for line in required)
        parts.append("</ul>")
    if preferred:
        parts.append("<h3>Preferred qualifications</h3><ul>")
        parts.extend(f"<li>{line}</li>" for line in preferred)
        parts.append("</ul>")
    for line in notes:
        parts.append(f"<p>{line}</p>")
    return "".join(parts)


# --------------------------------------------------------------------------
# Feeds: nine endpoints over six ATS vendors, eight companies.
# Northwind and Atlas Grid each appear on two vendors, which is what makes the
# cross-source dedupe and multi-source evidence paths reachable.
# --------------------------------------------------------------------------

DEMO_FEEDS: tuple[Feed, ...] = (
    Feed(
        id=9001, source="greenhouse", key="northwindrobotics", config={}, state={},
        complete=True, closes=True,
        company_id="northwind-robotics", company_name="Northwind Robotics",
    ),
    Feed(
        id=9002, source="workday", key="northwind", config={}, state={},
        complete=True, closes=True,
        company_id="northwind-robotics", company_name="Northwind Robotics",
    ),
    Feed(
        id=9003, source="lever", key="halcyon-systems", config={}, state={},
        complete=True, closes=True,
        company_id="halcyon-systems", company_name="Halcyon Systems",
    ),
    Feed(
        id=9004, source="ashby", key="meridian-labs", config={}, state={},
        complete=True, closes=True,
        company_id="meridian-labs", company_name="Meridian Labs",
    ),
    Feed(
        # Deliberately not complete/closing: its polls resolve to the
        # 'ineligible' snapshot state, so absence never closes anything here.
        id=9005, source="workable", key="borealis-health", config={}, state={},
        complete=False, closes=False,
        company_id="borealis-health", company_name="Borealis Health",
    ),
    Feed(
        id=9006, source="smartrecruiters", key="VantageQuant", config={}, state={},
        complete=True, closes=True,
        company_id="vantage-quant", company_name="Vantage Quant",
    ),
    Feed(
        id=9007, source="greenhouse", key="atlasgrid", config={}, state={},
        complete=True, closes=True,
        company_id="atlas-grid", company_name="Atlas Grid",
    ),
    Feed(
        id=9008, source="smartrecruiters", key="AtlasGrid", config={}, state={},
        complete=True, closes=True,
        company_id="atlas-grid", company_name="Atlas Grid",
    ),
    Feed(
        id=9009, source="lever", key="cobalt-foundry", config={}, state={},
        complete=True, closes=True,
        company_id="cobalt-foundry", company_name="Cobalt Foundry",
    ),
    # A source that fails on every fetch. Source Coverage is supposed to show
    # broken endpoints, so the demo needs one that is genuinely broken rather
    # than one whose error text was written into the row by hand.
    Feed(
        id=9010, source="icims", key="vertex-medtech", config={}, state={},
        complete=False, closes=False,
        company_id="vertex-medtech", company_name="Vertex MedTech",
    ),
)

# Fetching this feed raises, so its poll records a real error outcome.
FAILING_FEED_ID = 9010
FAILING_FEED_ERROR = "HTTP 503 from the iCIMS portal after 3 retries"


def vendor_names() -> tuple[str, ...]:
    return tuple(dict.fromkeys(feed.source for feed in DEMO_FEEDS))


DEMO_PROFILE: dict[str, object] = {
    "graduation_date": "2027-12",
    "citizenship": "US",
    "home_location": "San Diego, CA",
    "preferred_segments": ["ml_systems", "systems", "quant_dev", "backend_infra"],
    "relocate_for_internship": True,
    "reduced_enrollment": False,
}


# --------------------------------------------------------------------------
# Round one: the corpus as first observed.
# --------------------------------------------------------------------------


def _feed_9001() -> tuple[Canonical, ...]:
    """Northwind Robotics on Greenhouse."""
    return (
        # Spring Internship, eligible, segment 1 -> 82 -> tailored_fast.
        # Carries requisition NR-4471, the cross-vendor exact-match key.
        Posting(
            external_id="nr-4471",
            title="Spring Machine Learning Systems Intern",
            url_posting="https://boards.greenhouse.io/northwindrobotics/jobs/5512088",
            apply_url="https://boards.greenhouse.io/northwindrobotics/jobs/5512088#app",
            company_raw="Northwind Robotics",
            location_raw="San Diego, CA",
            requisition_id="NR-4471",
            employment_type="Internship",
            workplace_type_raw="hybrid",
            published_at=_days(-6),
            published_at_precision="date",
            deadline_at=_days(4),  # inside the 7-day Today window
            source_active=True,
            description_html=_description(
                "Join the inference platform team building low-latency serving for "
                "on-robot perception models.",
                required=(
                    "Strong experience with Python and PyTorch.",
                    "Proficiency writing CUDA kernels or Triton kernels.",
                ),
                preferred=(
                    "Exposure to vLLM or another batched inference server.",
                    "Familiarity with Linux performance tooling.",
                ),
                notes=(
                    "Hourly compensation is $52/hr for this internship.",
                    "Open to students graduating in 2027 or 2028.",
                ),
            ),
            source_extra={"team": "Inference Platform", "req_family": "intern-2027"},
        ),
        # Fall Co-op, eligible, segment 2 -> 77 -> tailored_fast.
        Posting(
            external_id="nr-8802",
            title="Fall Compiler Engineering Co-op",
            url_posting="https://boards.greenhouse.io/northwindrobotics/jobs/5512114",
            apply_url="https://boards.greenhouse.io/northwindrobotics/jobs/5512114#app",
            company_raw="Northwind Robotics",
            location_raw="Remote, United States",
            requisition_id="NR-8802",
            employment_type="Co-op",
            workplace_type_raw="remote",
            published_at=_days(-11),
            published_at_precision="date",
            deadline_at=_days(26),
            source_active=True,
            description_html=_description(
                "Work on the ahead-of-time compiler that lowers motion-planning graphs "
                "onto our accelerator.",
                required=(
                    "Required: strong C++ and LLVM internals.",
                    "Must have experience with Rust or a comparable systems language.",
                ),
                preferred=("Nice to have: GPU kernel scheduling background.",),
                notes=("Salary range published per state pay-transparency law.",),
            ),
            source_extra={"team": "Compiler"},
        ),
        # Student Researcher, eligible, segment 4 -> 67 -> volume.
        Posting(
            external_id="nr-9114",
            title="Student Researcher, Distributed Training Systems",
            url_posting="https://boards.greenhouse.io/northwindrobotics/jobs/5512190",
            apply_url="https://boards.greenhouse.io/northwindrobotics/jobs/5512190#app",
            company_raw="Northwind Robotics",
            location_raw="Remote, United States",
            requisition_id="NR-9114",
            employment_type="Student Researcher",
            workplace_type_raw="remote",
            published_at=_days(-3),
            published_at_precision="exact",
            deadline_at=_days(41),
            source_active=True,
            description_html=_description(
                "Research sharded optimizer state for multi-node training runs.",
                required=("Requirements: Python, plus JAX or an equivalent framework.",),
                preferred=("Ideally some exposure to Ray for cluster orchestration.",),
                notes=("Compensation is set by the research stipend band.",),
            ),
            source_extra={"team": "Research"},
        ),
        # No season stated -> unclassified -> eligibility review -> log_only.
        # This is the row the Classify action in the UI is meant to resolve.
        Posting(
            external_id="nr-7300",
            title="Software Engineering Intern",
            url_posting="https://boards.greenhouse.io/northwindrobotics/jobs/5512201",
            apply_url="https://boards.greenhouse.io/northwindrobotics/jobs/5512201#app",
            company_raw="Northwind Robotics",
            location_raw="San Diego, CA",
            requisition_id="NR-7300",
            employment_type="Internship",
            workplace_type_raw="onsite",
            published_at=_days(-2),
            published_at_precision="date",
            source_active=True,
            description_html=_description(
                "General software internship across the fleet-operations backend.",
                required=("Requirements: Go, Kubernetes, and PostgreSQL fundamentals.",),
                preferred=("Bonus: Docker and Redis experience.",),
            ),
            source_extra={"team": "Fleet Ops"},
        ),
        Label("nr-4471", "workplace_type", "hybrid"),
        Label("nr-4471", "sponsorship_available", False),
        Label("nr-8802", "workplace_type", "remote"),
        Label("nr-9114", "degree_level", "BS/MS"),
        Fact("Northwind Robotics", "referral", "Priya Raman, Robotics Platform", "human"),
        Fact("Northwind Robotics", "headcount", 480, "source"),
    )


def _feed_9002() -> tuple[Canonical, ...]:
    """Northwind Robotics on Workday — the same requisition, seen twice."""
    return (
        # Shares employer requisition NR-4471 with the Greenhouse posting:
        # a conflict-free scoped identifier match, so the engine auto-merges
        # and the survivor reports two unified sources.
        Posting(
            external_id="NR_4471_WD",
            title="Machine Learning Systems Intern - Spring 2027",
            url_posting=(
                "https://northwind.wd5.myworkdayjobs.com/en-US/Northwind_Careers/job/"
                "San-Diego-CA/Machine-Learning-Systems-Intern_NR-4471"
            ),
            apply_url=(
                "https://northwind.wd5.myworkdayjobs.com/en-US/Northwind_Careers/job/"
                "San-Diego-CA/Machine-Learning-Systems-Intern_NR-4471/apply"
            ),
            company_raw="Northwind Robotics",
            location_raw="San Diego, CA",
            requisition_id="NR-4471",
            employment_type="Internship",
            workplace_type_raw="hybrid",
            published_at=_days(-6),
            published_at_precision="date",
            deadline_at=_days(4),
            source_active=True,
            description_html=_description(
                "Spring 2027 internship on the inference platform team.",
                required=("Required: Python, PyTorch, and CUDA.",),
                preferred=("A plus: Triton kernel authoring.",),
                notes=("$52/hr.",),
            ),
            source_extra={"workday_site": "Northwind_Careers"},
        ),
        # Summer Internship, eligible, segment 4 -> 67 -> volume.
        Posting(
            external_id="NR_5510_WD",
            title="Summer Data Infrastructure Intern",
            url_posting=(
                "https://northwind.wd5.myworkdayjobs.com/en-US/Northwind_Careers/job/"
                "Austin-TX/Summer-Data-Infrastructure-Intern_NR-5510"
            ),
            apply_url=(
                "https://northwind.wd5.myworkdayjobs.com/en-US/Northwind_Careers/job/"
                "Austin-TX/Summer-Data-Infrastructure-Intern_NR-5510/apply"
            ),
            company_raw="Northwind Robotics",
            location_raw="Austin, TX",
            requisition_id="NR-5510",
            employment_type="Internship",
            workplace_type_raw="onsite",
            published_at=_days(-9),
            published_at_precision="date",
            deadline_at=_days(33),
            source_active=True,
            description_html=_description(
                "Build the telemetry warehouse that fleet analytics runs on.",
                required=("Requirements: SQL, Python, and Kafka fundamentals.",),
                preferred=("Desired: Spark or an equivalent batch engine.",),
                notes=("Compensation is $46/hr.",),
            ),
            source_extra={"workday_site": "Northwind_Careers"},
        ),
        Label("NR_5510_WD", "workplace_type", "onsite"),
    )


def _feed_9003() -> tuple[Canonical, ...]:
    """Halcyon Systems on Lever — carries the 'related openings' pair."""
    related_body = _description(
        "Own a service in the settlement platform for one co-op term.",
        required=("Requirements: Go, PostgreSQL, and Linux fundamentals.",),
        preferred=("Nice to have: Kubernetes operations experience.",),
        notes=("Hourly compensation is $48/hr.",),
    )
    return (
        # Summer Internship, eligible, segment 3 -> 77 -> tailored_fast, quant resume.
        Posting(
            external_id="hs-quant-01",
            title="Summer Quantitative Developer Intern",
            url_posting="https://jobs.lever.co/halcyon-systems/7c1d9a40-quant-dev-intern",
            apply_url="https://jobs.lever.co/halcyon-systems/7c1d9a40-quant-dev-intern/apply",
            company_raw="Halcyon Systems",
            location_raw="Remote, United States",
            employment_type="Internship",
            workplace_type_raw="remote",
            published_at=_days(-1),
            published_at_precision="exact",
            deadline_at=_days(19),
            source_active=True,
            description_html=_description(
                "Build low latency market data decoders for the systematic trading desk.",
                required=(
                    "Required: strong C++ and Python.",
                    "Must have SQL fluency for research tooling.",
                ),
                preferred=("Preferred qualifications: Linux kernel bypass networking.",),
                notes=("Compensation is $75/hr plus a housing stipend.",),
            ),
            source_extra={"team": "Systematic Trading"},
        ),
        # Two distinct Lever requisitions under one title. Same employer,
        # conflicting scoped identifiers -> 'related', never an automatic merge.
        Posting(
            external_id="hs-rel-a1",
            title="Fall Backend Platform Co-op",
            url_posting="https://jobs.lever.co/halcyon-systems/a1f0c4d2-backend-platform-coop",
            apply_url="https://jobs.lever.co/halcyon-systems/a1f0c4d2-backend-platform-coop/apply",
            company_raw="Halcyon Systems",
            location_raw="Remote, United States",
            employment_type="Co-op",
            workplace_type_raw="remote",
            published_at=_days(-14),
            published_at_precision="date",
            deadline_at=_days(23),
            source_active=True,
            description_html=related_body,
            source_extra={"team": "Settlement", "shift": "A"},
        ),
        Posting(
            external_id="hs-rel-b2",
            title="Fall Backend Platform Co-op",
            url_posting="https://jobs.lever.co/halcyon-systems/b2e7d9f1-backend-platform-coop",
            apply_url="https://jobs.lever.co/halcyon-systems/b2e7d9f1-backend-platform-coop/apply",
            company_raw="Halcyon Systems",
            location_raw="Remote, United States",
            employment_type="Co-op",
            workplace_type_raw="remote",
            published_at=_days(-13),
            published_at_precision="date",
            deadline_at=_days(23),
            source_active=True,
            description_html=related_body,
            source_extra={"team": "Clearing", "shift": "B"},
        ),
        Label("hs-quant-01", "sponsorship_available", True),
        Label("hs-quant-01", "workplace_type", "remote"),
        Fact("Halcyon Systems", "one_shot_spent", False, "human"),
    )


def _feed_9004() -> tuple[Canonical, ...]:
    """Meridian Labs on Ashby — three of the ineligibility gates."""
    return (
        # Spring Co-op, eligible. Co-op slugs do not get relocation leniency,
        # so this one has to be in the home city to stay eligible.
        Posting(
            external_id="ml-spring-coop",
            title="Spring Robotics Software Co-op",
            url_posting="https://jobs.ashbyhq.com/meridian-labs/9f2a-spring-robotics-coop",
            apply_url="https://jobs.ashbyhq.com/meridian-labs/9f2a-spring-robotics-coop/application",
            company_raw="Meridian Labs",
            location_raw="San Diego, CA",
            requisition_id="ML-2210",
            employment_type="Co-op",
            workplace_type_raw="onsite",
            published_at=_days(-5),
            published_at_precision="date",
            deadline_at=_days(12),
            source_active=True,
            description_html=_description(
                "Six-month co-op on the manipulation controls stack.",
                required=("Requirements: C++ and Python, plus Linux fundamentals.",),
                preferred=("Bonus: Rust experience.",),
                notes=("Hourly compensation is $44/hr.",),
            ),
            source_extra={"team": "Manipulation"},
        ),
        # Seniority word in the title -> ineligible, routed to log_only.
        Posting(
            external_id="ml-senior",
            title="Senior Machine Learning Intern, Summer",
            url_posting="https://jobs.ashbyhq.com/meridian-labs/3b71-senior-ml-intern",
            apply_url="https://jobs.ashbyhq.com/meridian-labs/3b71-senior-ml-intern/application",
            company_raw="Meridian Labs",
            location_raw="Remote, United States",
            requisition_id="ML-3071",
            employment_type="Internship",
            workplace_type_raw="remote",
            published_at=_days(-17),
            published_at_precision="date",
            deadline_at=_days(30),
            source_active=True,
            description_html=_description(
                "Lead-track internship for returning interns on the perception team.",
                required=("Requirements: PyTorch and Python.",),
                notes=("Compensation is $60/hr.",),
            ),
            source_extra={"team": "Perception"},
        ),
        # Winter and Summer both stated -> conflicting seasons -> review type.
        Posting(
            external_id="ml-review-season",
            title="Winter / Summer Software Engineering Intern",
            url_posting="https://jobs.ashbyhq.com/meridian-labs/5c19-winter-summer-intern",
            apply_url="https://jobs.ashbyhq.com/meridian-labs/5c19-winter-summer-intern/application",
            company_raw="Meridian Labs",
            location_raw="San Diego, CA",
            requisition_id="ML-3388",
            employment_type="Internship",
            workplace_type_raw="hybrid",
            published_at=_days(-4),
            published_at_precision="date",
            source_active=True,
            description_html=_description(
                "One posting covering both the winter and summer cohorts.",
                required=("Requirements: Python and TypeScript.",),
                notes=("Compensation is $45/hr.",),
            ),
            source_extra={"team": "Tools"},
        ),
        # Co-op outside the home city -> location gate -> ineligible.
        Posting(
            external_id="ml-zurich",
            title="Fall Systems Engineering Co-op",
            url_posting="https://jobs.ashbyhq.com/meridian-labs/7d40-zurich-systems-coop",
            apply_url="https://jobs.ashbyhq.com/meridian-labs/7d40-zurich-systems-coop/application",
            company_raw="Meridian Labs",
            location_raw="Zurich, Switzerland",
            requisition_id="ML-4102",
            employment_type="Co-op",
            workplace_type_raw="onsite",
            published_at=_days(-21),
            published_at_precision="date",
            deadline_at=_days(15),
            source_active=True,
            description_html=_description(
                "Co-op with the European autonomy group.",
                required=("Requirements: C++ and Linux.",),
                notes=("Compensation is CHF-denominated.",),
            ),
            source_extra={"team": "Autonomy EU"},
        ),
        Label("ml-spring-coop", "workplace_type", "onsite"),
        Label("ml-review-season", "term_ambiguous", True),
    )


def _feed_9005() -> tuple[Canonical, ...]:
    """Borealis Health on Workable — cooldown, role family, stated closure."""
    return (
        # Role family gate: 'clinical' is outside the CS/Math families.
        Posting(
            external_id="bh-clinical",
            title="Clinical Research Intern",
            url_posting="https://apply.workable.com/borealis-health/j/8A31C0DE55/",
            apply_url="https://apply.workable.com/borealis-health/j/8A31C0DE55/apply/",
            company_raw="Borealis Health",
            location_raw="Boston, MA",
            employment_type="Internship",
            workplace_type_raw="onsite",
            published_at=_days(-8),
            published_at_precision="date",
            deadline_at=_days(18),
            source_active=True,
            description_html=_description(
                "Support trial data capture for the cardiology study group.",
                required=("Requirements: SQL and spreadsheet fluency.",),
                notes=("Compensation is $28/hr.",),
            ),
            source_extra={"team": "Clinical Ops"},
        ),
        # Stated closure with evidence -> status closed -> ineligible.
        Posting(
            external_id="bh-closed",
            title="Fall Health Data Engineering Co-op",
            url_posting="https://apply.workable.com/borealis-health/j/9B42D1EF66/",
            apply_url="https://apply.workable.com/borealis-health/j/9B42D1EF66/apply/",
            company_raw="Borealis Health",
            location_raw="Remote, United States",
            employment_type="Co-op",
            workplace_type_raw="remote",
            state="closed",
            closed_evidence="Employer marked the requisition filled in the source listing.",
            published_at=_days(-45),
            published_at_precision="date",
            deadline_at=_days(-6),
            source_active=False,
            description_html=_description(
                "Build the claims ingestion pipeline.",
                required=("Requirements: Python, SQL, and Airflow-style orchestration.",),
                notes=("Compensation was $47/hr.",),
            ),
            source_extra={"team": "Data Platform"},
        ),
        # Applies to every Borealis posting through the company cooldown gate.
        Fact("Borealis Health", "cooldown_until", _days(120).isoformat(), "human", _days(120)),
        Label("bh-clinical", "workplace_type", "onsite"),
    )


def _feed_9006() -> tuple[Canonical, ...]:
    """Vantage Quant on SmartRecruiters — the experience gate."""
    return (
        # Explicit 5-year minimum -> experience gate -> ineligible.
        Posting(
            external_id="vq-exp",
            title="Summer Trading Systems Intern",
            url_posting="https://jobs.smartrecruiters.com/VantageQuant/744000098001-summer-trading-systems-intern",
            apply_url="https://jobs.smartrecruiters.com/VantageQuant/744000098001-summer-trading-systems-intern/apply",
            company_raw="Vantage Quant",
            location_raw="Chicago, IL",
            employment_type="Internship",
            workplace_type_raw="onsite",
            published_at=_days(-19),
            published_at_precision="date",
            deadline_at=_days(21),
            source_active=True,
            description_html=_description(
                "Sit with the execution desk and own a latency-sensitive component.",
                required=(
                    "Requires a minimum of 5 years experience with C++ in production.",
                    "Must have Linux systems fluency.",
                ),
                notes=("Compensation is $90/hr.",),
            ),
            source_extra={"desk": "Execution"},
        ),
        # Summer Internship, eligible, segment 3 -> tailored_fast, quant resume.
        Posting(
            external_id="vq-summer",
            title="Summer Quantitative Research Intern",
            url_posting="https://jobs.smartrecruiters.com/VantageQuant/744000098044-summer-quantitative-research-intern",
            apply_url="https://jobs.smartrecruiters.com/VantageQuant/744000098044-summer-quantitative-research-intern/apply",
            company_raw="Vantage Quant",
            location_raw="Remote, United States",
            employment_type="Internship",
            workplace_type_raw="remote",
            published_at=_days(-2),
            published_at_precision="exact",
            # Deliberately outside the 7-day window: the top-up promotes this
            # one to the one_shot band, and Today checks deadline first.
            deadline_at=_days(26),
            source_active=True,
            description_html=_description(
                "Prototype signals on the mid-frequency research stack.",
                required=("Requirements: Python and SQL.",),
                preferred=("Preferred qualifications: C++ for backtest kernels.",),
                notes=(
                    "Compensation is $85/hr.",
                    "Open to students graduating in 2027 or 2028.",
                ),
            ),
            source_extra={"desk": "Research"},
        ),
        # Fall Internship, eligible, segment 3 -> 77 -> tailored_fast.
        # Left un-triaged and un-applied so Today can show the tailored window.
        Posting(
            external_id="vq-fall",
            title="Fall Quantitative Developer Internship",
            url_posting="https://jobs.smartrecruiters.com/VantageQuant/744000098077-fall-quantitative-developer-internship",
            apply_url="https://jobs.smartrecruiters.com/VantageQuant/744000098077-fall-quantitative-developer-internship/apply",
            company_raw="Vantage Quant",
            location_raw="Remote, United States",
            employment_type="Internship",
            workplace_type_raw="remote",
            published_at=_days(-12),
            published_at_precision="date",
            deadline_at=_days(30),
            source_active=True,
            description_html=_description(
                "Own a piece of the low latency order-entry path for one term.",
                required=("Requirements: C++ and Python.",),
                preferred=("Nice to have: Linux performance profiling.",),
                notes=("Compensation is $80/hr.",),
            ),
            source_extra={"desk": "Execution"},
        ),
        Label("vq-summer", "workplace_type", "remote"),
        Label("vq-fall", "workplace_type", "remote"),
        Fact("Vantage Quant", "headcount", 210, "source"),
    )


_ATLAS_SRE_BODY = _description(
    "Keep the regional grid telemetry service inside its error budget.",
    required=("Requirements: Go, Kubernetes, and Linux fundamentals.",),
    preferred=("Nice to have: Prometheus and Terraform exposure.",),
    notes=("Hourly compensation is $43/hr.",),
)


def _feed_9007(*, include_dropped: bool = True) -> tuple[Canonical, ...]:
    """Atlas Grid on Greenhouse.

    ``ag-dropped`` is withheld in round two so the lifecycle reconciler sees a
    genuine absence on a complete/closing feed and marks it unlisted.
    """
    postings: list[Canonical] = [
        # Same normalized title as the SmartRecruiters posting below, same
        # employer, no shared scoped identifier -> a real 'review' candidate.
        Posting(
            external_id="ag-sre-gh",
            title="Spring Site Reliability Engineering Co-op",
            url_posting="https://boards.greenhouse.io/atlasgrid/jobs/4820917",
            apply_url="https://boards.greenhouse.io/atlasgrid/jobs/4820917#app",
            company_raw="Atlas Grid",
            location_raw="United States (Remote)",
            employment_type="Co-op",
            workplace_type_raw="remote",
            published_at=_days(-7),
            published_at_precision="date",
            deadline_at=_days(28),
            source_active=True,
            description_html=_ATLAS_SRE_BODY,
            source_extra={"board": "greenhouse"},
        ),
    ]
    if include_dropped:
        postings.append(
            Posting(
                external_id="ag-dropped",
                title="Fall Network Systems Internship",
                url_posting="https://boards.greenhouse.io/atlasgrid/jobs/4820955",
                apply_url="https://boards.greenhouse.io/atlasgrid/jobs/4820955#app",
                company_raw="Atlas Grid",
                location_raw="Remote, United States",
                requisition_id="AG-7781",
                employment_type="Internship",
                workplace_type_raw="remote",
                published_at=_days(-10),
                published_at_precision="date",
                deadline_at=_days(35),
                source_active=True,
                description_html=_description(
                    "Instrument the substation control network.",
                    required=("Requirements: Python and Linux networking.",),
                    notes=("Compensation is $41/hr.",),
                ),
                source_extra={"board": "greenhouse"},
            )
        )
    postings.append(Label("ag-sre-gh", "workplace_type", "remote"))
    return tuple(postings)


def _feed_9008() -> tuple[Canonical, ...]:
    """Atlas Grid on SmartRecruiters — the 'review' half of the pair."""
    return (
        Posting(
            external_id="ag-sre-sr",
            title="Spring Site Reliability Engineering Co-op",
            url_posting="https://jobs.smartrecruiters.com/AtlasGrid/744000044120-spring-site-reliability-engineering-coop",
            apply_url="https://jobs.smartrecruiters.com/AtlasGrid/744000044120-spring-site-reliability-engineering-coop/apply",
            company_raw="Atlas Grid",
            location_raw="United States (Remote)",
            employment_type="Co-op",
            workplace_type_raw="remote",
            published_at=_days(-7),
            published_at_precision="date",
            deadline_at=_days(28),
            source_active=True,
            description_html=_ATLAS_SRE_BODY,
            source_extra={"board": "smartrecruiters"},
        ),
        Label("ag-sre-sr", "workplace_type", "remote"),
    )


def _feed_9009() -> tuple[Canonical, ...]:
    """Cobalt Foundry on Lever — graduation gate and the clearance penalty."""
    return (
        # Summer Internship, eligible, segment 2 -> tailored_fast.
        Posting(
            external_id="cf-embedded",
            title="Summer Embedded Systems Intern",
            url_posting="https://jobs.lever.co/cobalt-foundry/4e8b1c07-embedded-intern",
            apply_url="https://jobs.lever.co/cobalt-foundry/4e8b1c07-embedded-intern/apply",
            company_raw="Cobalt Foundry",
            location_raw="San Diego, CA",
            employment_type="Internship",
            workplace_type_raw="onsite",
            published_at=_days(-1),
            published_at_precision="exact",
            deadline_at=_days(24),
            source_active=True,
            description_html=_description(
                "Bring up firmware for the next-generation motor controller.",
                required=("Requirements: C, C++, and embedded Linux.",),
                preferred=("A plus: Rust on bare metal.",),
                notes=("Compensation is $47/hr.",),
            ),
            source_extra={"team": "Silicon"},
        ),
        # Explicit class-year range that excludes the profile -> ineligible.
        Posting(
            external_id="cf-grad",
            title="Spring Firmware Engineering Intern",
            url_posting="https://jobs.lever.co/cobalt-foundry/6f2a9d33-firmware-intern",
            apply_url="https://jobs.lever.co/cobalt-foundry/6f2a9d33-firmware-intern/apply",
            company_raw="Cobalt Foundry",
            location_raw="San Diego, CA",
            employment_type="Internship",
            workplace_type_raw="onsite",
            published_at=_days(-23),
            published_at_precision="date",
            deadline_at=_days(9),
            source_active=True,
            description_html=_description(
                "Spring rotation on the bootloader team.",
                required=("Requirements: C and Python.",),
                notes=("Open only to students graduating in 2025 or 2026.",),
            ),
            source_extra={"team": "Silicon"},
        ),
        # Active clearance is a soft negative: it adds a negative score
        # component without failing the eligibility gate.
        Posting(
            external_id="cf-clearance",
            title="Summer Systems Software Intern, Secure Platforms",
            url_posting="https://jobs.lever.co/cobalt-foundry/8c5e2b71-secure-systems-intern",
            apply_url="https://jobs.lever.co/cobalt-foundry/8c5e2b71-secure-systems-intern/apply",
            company_raw="Cobalt Foundry",
            location_raw="Remote, United States",
            employment_type="Internship",
            workplace_type_raw="remote",
            published_at=_days(-15),
            published_at_precision="date",
            deadline_at=_days(20),
            source_active=True,
            description_html=_description(
                "Harden the secure boot chain for defense-sector deployments.",
                required=("Requirements: C++, Rust, and Linux internals.",),
                notes=(
                    "This role requires an active security clearance.",
                    "Compensation is $55/hr.",
                ),
            ),
            source_extra={"team": "Secure Platforms"},
        ),
        Label("cf-embedded", "workplace_type", "onsite"),
        Label("cf-clearance", "clearance_required", True),
        Fact("Cobalt Foundry", "referral", "Dmitri Anand, Silicon", "human"),
    )


# --------------------------------------------------------------------------
# Round two: what a second manual fetch observes.
# Only the feeds that actually change need an entry; ``DemoGateway`` reuses a
# feed's last defined round for every fetch after it.
# --------------------------------------------------------------------------


def _feed_9001_round2() -> tuple[Canonical, ...]:
    """Northwind revises the compiler co-op: new deadline and new requirements."""
    first = list(_feed_9001())
    revised: list[Canonical] = []
    for item in first:
        if isinstance(item, Posting) and item.external_id == "nr-8802":
            from dataclasses import replace

            revised.append(
                replace(
                    item,
                    deadline_at=_days(5),  # pulled forward into the Today window
                    description_html=_description(
                        "Work on the ahead-of-time compiler that lowers motion-planning "
                        "graphs onto our accelerator. Scope now includes the runtime.",
                        required=(
                            "Required: strong C++ and LLVM internals.",
                            "Must have experience with Rust or a comparable systems language.",
                            "Requires familiarity with CUDA graph capture.",
                        ),
                        preferred=("Nice to have: GPU kernel scheduling background.",),
                        notes=("Salary range published per state pay-transparency law.",),
                    ),
                )
            )
        else:
            revised.append(item)
    return tuple(revised)


def _feed_9003_round2() -> tuple[Canonical, ...]:
    """Halcyon revises the quant internship body."""
    from dataclasses import replace

    revised: list[Canonical] = []
    for item in _feed_9003():
        if isinstance(item, Posting) and item.external_id == "hs-quant-01":
            revised.append(
                replace(
                    item,
                    description_html=_description(
                        "Build low latency market data decoders for the systematic "
                        "trading desk. The team has added an FPGA co-design track.",
                        required=(
                            "Required: strong C++ and Python.",
                            "Must have SQL fluency for research tooling.",
                            "Requires Rust for the tooling layer.",
                        ),
                        preferred=("Preferred qualifications: Linux kernel bypass networking.",),
                        notes=("Compensation is $75/hr plus a housing stipend.",),
                    ),
                )
            )
        else:
            revised.append(item)
    return tuple(revised)


def demo_rounds() -> dict[int, tuple[tuple[Canonical, ...], ...]]:
    """Map feed id -> the corpus it serves in each successive fetch round."""
    return {
        9001: (_feed_9001(), _feed_9001_round2()),
        9002: (_feed_9002(),),
        9003: (_feed_9003(), _feed_9003_round2()),
        9004: (_feed_9004(),),
        9005: (_feed_9005(),),
        9006: (_feed_9006(),),
        # Round two drops ag-dropped from a complete/closing feed.
        9007: (_feed_9007(), _feed_9007(include_dropped=False)),
        9008: (_feed_9008(),),
        9009: (_feed_9009(),),
        9010: ((),),  # never reached; DemoGateway raises for this feed
    }
