from dataclasses import asdict
import json

import pytest

from xjobscore.company_name import MAX_DESCRIPTION_LENGTH, enrich_posting, extract_company_name, plain_text
from xjobscore.records import Feed, Posting


@pytest.mark.parametrize("description,expected", [
    ("Cobalt Orchard Labs is hiring engineering interns.", "Cobalt Orchard Labs"),
    ("POLITICO is seeking exceptional college students.", "POLITICO"),
    ("At moss.ag, we build outdoor robots.", "moss.ag"),
    ("Here at fairlife, we make dairy products.", "fairlife"),
    ("At Neogen, we are committed to developing future talent.", "Neogen"),
    ("Company: Cedar Robotics", "Cedar Robotics"),
    ("Company Name:\nNorthwind Labs", "Northwind Labs"),
    ("Employer: Café Étoile", "Café Étoile"),
    ("About Cedar Robotics\nWe build robots.", "Cedar Robotics"),
    ("Mueller Water Products, Inc. is a leading manufacturer of valves.", "Mueller Water Products, Inc"),
    ("Auto-Owners Insurance, a top-rated insurance carrier, is seeking an intern.", "Auto-Owners Insurance"),
    ("Hormel Foods Corporation, based in Austin, Minnesota, is a global branded food company.", "Hormel Foods Corporation"),
    ("Boardwalk Pipelines, LP, is an equal opportunity employer.", "Boardwalk Pipelines, LP"),
    ("A. Morton Thomas and Associates, Inc. is seeking interns.", "A. Morton Thomas and Associates, Inc"),
    ("The City of Baltimore is an Equal Opportunity Employer.", "The City of Baltimore"),
    ("Thank you for your interest in Cedar Robotics. We build robots.", "Cedar Robotics"),
    ("Employees at Bernalillo County enjoy working together.", "Bernalillo County"),
    ("Join Cedar Robotics and build robots.", "Cedar Robotics"),
    ("Join Cedar Robotics.", "Cedar Robotics"),
    ("Our client Acme Industries is seeking an intern. Beacon Staffing is an equal opportunity employer.", "Acme Industries"),
    ("Our client, Acme Industries, is hiring interns.", "Acme Industries"),
    ("Our client is Acme Industries. We are recruiting an intern.", "Acme Industries"),
    ("Client Company: Acme Industries\nBeacon Staffing is hiring.", "Acme Industries"),
    ("Students will be employed by Paramont Manufacturing LLC. EO is an equal opportunity employer.", "Paramont Manufacturing LLC"),
    ("Justice Resource Institute (JRI) supports families. JRI is hiring interns.", "Justice Resource Institute"),
    ("Acme LLC is hiring interns. Acme is a leading company.", "Acme LLC"),
    ("<h2>About Cedar Robotics</h2><p>Cedar Robotics is hiring interns.</p>", "Cedar Robotics"),
    ("<p>At Holland &amp; Knight, we support clients.</p>", "Holland & Knight"),
])
def test_extracts_employer_bearing_statements(description, expected):
    result = extract_company_name(description)
    assert result.status == "matched"
    assert result.name == expected
    assert result.evidence
    # Evidence/offsets are available to a reviewer and survive JSON storage.
    body = plain_text(description)
    assert all(body[e.start:e.end] for e in result.evidence)
    json.dumps(asdict(result))


@pytest.mark.parametrize("description", [
    "", None, "We are seeking a talented intern.", "Join our team!",
    "About Us\nWe are seeking an intern.", "About Company:\nWe are growing.",
    "About the Internship\nLearn about our work.", "ABOUT THE APPLICATION PROCESS\nApply today.",
    "The pay range listed for this position is the base hourly wage range the organization provides.",
    "We use Microsoft Azure, Amazon Web Services, and Salesforce.",
    "Responsibilities:\nSupport Owensboro Health's mission.",
    "Guilford attracts those who are seeking a professional challenge.",
    "<script>At FakeCo, we build robots.</script><style>.FakeCo {}</style><p>We are hiring interns.</p>",
    "The intern is a team leader for this project.",
    "Housing and Residence Life is an integral part of the University of North Florida.",
    "Not Acme is hiring interns.",
])
def test_does_not_promote_generic_text_or_tool_mentions(description):
    result = extract_company_name(description)
    assert result.name is None


@pytest.mark.parametrize("description,reason", [
    ("Acme is hiring interns. Beta Labs is hiring interns.", "conflicting_employer_names"),
    ("Our client is a leading manufacturer. Beacon Staffing is an equal opportunity employer.", "unnamed_or_unconfirmed_client"),
    ("Our client Acme is hiring interns. Our client Beta Labs is seeking interns.", "conflicting_employer_names"),
    ("Acme Child is hiring interns. Acme Parent is an equal opportunity employer.", "conflicting_employer_names"),
    ("At Alpha Corp, Beta Labs, and Gamma Inc, we welcome interns.", "multiple_named_employers_in_statement"),
])
def test_conflicting_or_unidentified_employers_abstain(description, reason):
    result = extract_company_name(description)
    assert result.status == "ambiguous"
    assert result.name is None
    assert result.reason == reason


def posting(raw=None, description="Cobalt Orchard Labs is hiring interns."):
    return Posting(external_id="1", title="Intern", url_posting="https://jobs.example/1", company_raw=raw,
                   description_html=description, source_extra={"original": 42})


def test_disabled_integration_is_identity_preserving():
    original = posting()
    assert enrich_posting(Feed(id=1, source="lever", key="cobalt"), original) is original


def test_suggest_mode_retains_native_name_and_records_evidence():
    original = posting("Native Employer")
    result = enrich_posting(Feed(id=1, source="lever", key="cobalt", config={"company_from_description": "suggest"}), original)
    assert result.company_raw == "Native Employer"
    assert result.source_extra["original"] == 42
    assert result.source_extra["company_name_from_description"]["name"] == "Cobalt Orchard Labs"
    assert "company_name_from_description" not in original.source_extra


@pytest.mark.parametrize("raw", [None, "cobalt"])
def test_fallback_fills_missing_or_slug_name(raw):
    result = enrich_posting(Feed(id=1, source="lever", key="cobalt", config={"company_from_description": "fallback"}), posting(raw))
    assert result.company_raw == "Cobalt Orchard Labs"


@pytest.mark.parametrize("kwargs,raw", [
    ({}, "Native Employer"),
    ({"company_id": 42, "company_needs_fill": False}, "cobalt"),
    ({"config": {"company_from_description": "fallback", "company_raw_spec": None}}, None),
])
def test_native_verified_and_explicit_routing_win(kwargs, raw):
    fields = {"id": 1, "source": "lever", "key": "cobalt", "config": {"company_from_description": "fallback"}, **kwargs}
    assert enrich_posting(Feed(**fields), posting(raw)).company_raw == raw


@pytest.mark.parametrize("description", [
    "About Cobalt Orchard Labs\nWe build robots.",
    "Acme is hiring interns. Beta Labs is hiring interns.",
    "We use Microsoft Azure.",
    "Cobalt Orchard Labs is hiring interns. " + "x" * MAX_DESCRIPTION_LENGTH,
])
def test_fallback_requires_strong_unambiguous_untruncated_evidence(description):
    assert enrich_posting(Feed(id=1, source="lever", key="cobalt", config={"company_from_description": "fallback"}), posting(description=description)).company_raw is None


def test_short_form_and_article_do_not_create_false_conflicts():
    result = extract_company_name("The Cedar Robotics is hiring interns. Cedar Robotics is a leading company.")
    assert result.status == "matched"


def test_ambiguous_abbreviation_is_not_expanded():
    result = extract_company_name("Cedar Robotics (CR) builds robots. Cobalt Robotics (CR) builds sensors. CR is hiring interns.")
    assert result.name == "CR"
