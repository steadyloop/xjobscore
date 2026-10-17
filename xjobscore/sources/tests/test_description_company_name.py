"""Description-name fallback preserves native and shared-board routing."""
import pytest

from xjobscore.records import Feed, Item
from xjobscore.sources import json_api


def parse(source="lever", *, config=None, payload=None, **feed_fields):
    feed = Feed(id=1, source=source, key="cobalt", config=config or {}, **feed_fields)
    if payload is None:
        payload = {"text": "Software Engineering Intern", "hostedUrl": "https://jobs.lever.co/cobalt/1",
                   "descriptionPlain": "Cobalt Orchard Labs is hiring engineering interns.",
                   "description": "<p>Cobalt Orchard Labs is hiring engineering interns.</p>", "categories": {}}
    return json_api.parse(source, feed, Item(external_id="1", payload=payload), {})[0]


def test_default_parse_does_not_change_slug_or_add_suggestions():
    result = parse()
    assert result.company_raw == "cobalt"
    assert "company_name_from_description" not in result.source_extra


def test_suggest_mode_is_visible_in_parser_output_without_rerouting():
    result = parse(config={"company_from_description": "suggest"})
    assert result.company_raw == "cobalt"
    assert result.source_extra["company_name_from_description"]["name"] == "Cobalt Orchard Labs"


def test_fallback_uses_description_and_keeps_posting_identity():
    result = parse(config={"company_from_description": "fallback"})
    assert result.company_raw == "Cobalt Orchard Labs"
    assert result.external_id == "1"
    assert result.title == "Software Engineering Intern"
    assert result.url_posting == "https://jobs.lever.co/cobalt/1"


@pytest.mark.parametrize("override", [None, "Known Routed Employer"])
def test_explicit_company_raw_spec_remains_authoritative(override):
    result = parse(config={"company_from_description": "fallback", "company_raw_spec": override})
    assert result.company_raw == override


def test_verified_feed_is_not_rerouted():
    result = parse(config={"company_from_description": "fallback"}, company_id=42, company_needs_fill=False)
    assert result.company_raw == "cobalt"


def test_structured_greenhouse_name_wins_over_description():
    result = parse("greenhouse", config={"company_from_description": "fallback"}, payload={
        "id": 1, "title": "Intern", "absolute_url": "https://jobs.example/1", "company_name": "Native Employer",
        "content": "<p>Cobalt Orchard Labs is hiring interns.</p>"})
    assert result.company_raw == "Native Employer"


def test_closed_probe_is_not_processed_as_description():
    from xjobscore.records import Feed, Item
    parsed = json_api.parse("lever", Feed(id=1, source="lever", key="cobalt", config={"company_from_description": "fallback"}),
                            Item(external_id="1", payload={"_probe": {"outcome": "page_closed", "url": "https://jobs.example/1", "detail": "this job is closed"}}), {})
    assert parsed
    assert all("company_name_from_description" not in p.source_extra for p in parsed)
