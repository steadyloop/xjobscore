"""parse_record classification, resolution preview, and failure isolation."""

from __future__ import annotations

import pytest

from xjobscore import sources
from xjobscore.inspector.parsing import parse_record
from xjobscore.records import Fact, Feed, Item, Label, Posting, Target


def ats_feed(**overrides) -> Feed:
    values = dict(
        id=1,
        source="parse-test",
        key="acme",
        config={},
        source_type="ATS",
        company_id=42,
        company_name="Acme",
    )
    values.update(overrides)
    return Feed(**values)


def posting(**overrides) -> Posting:
    values = dict(
        external_id="job-1",
        title="Engineer",
        url_posting="https://jobs.example/1",
    )
    values.update(overrides)
    return Posting(**values)


@pytest.fixture
def register(monkeypatch):
    def _register(parse):
        monkeypatch.setitem(sources.PARSERS, "parse-test", parse)

    return _register


def no_alias(_raw: str) -> None:
    return None


def test_missing_parser_is_a_state_not_an_error():
    outcome = parse_record(
        ats_feed(source="never-registered"), [Item("a", {})], {}, no_alias
    )

    assert outcome["status"] == "no_parser"
    assert outcome["items"] == []
    assert outcome["summary"]["postings"] == 0


def test_per_item_errors_do_not_hide_other_items(register):
    def parse(_feed, item, _envelope):
        if item.external_id == "bad":
            raise ValueError("malformed payload")
        return [posting(external_id=item.external_id, company_raw="Acme")]

    register(parse)
    outcome = parse_record(
        ats_feed(), [Item("good", {}), Item("bad", {})], {}, no_alias
    )

    good, bad = outcome["items"]
    assert outcome["status"] == "ok"
    assert good["status"] == "ok" and len(good["canonicals"]) == 1
    assert bad["status"] == "error"
    assert bad["error_class"] == "ValueError"
    assert "malformed" in bad["error_detail"]
    assert outcome["summary"]["items_parsed"] == 1
    assert outcome["summary"]["item_errors"] == 1


def test_non_posting_canonicals_are_unsupported_but_rendered(register):
    def parse(_feed, _item, _envelope):
        return [
            Target(source="lever", key="new-board", reason="discovered_board"),
            Label(external_id="job-1", name="term", value="internship"),
            Fact(company_raw="Acme", name="headcount", value=10),
        ]

    register(parse)
    outcome = parse_record(ats_feed(), [Item("a", {})], {}, no_alias)

    entries = outcome["items"][0]["canonicals"]
    assert [entry["type"] for entry in entries] == ["Target", "Label", "Fact"]
    assert all(entry["classification"] == "unsupported" for entry in entries)
    assert entries[0]["data"]["key"] == "new-board"
    assert outcome["summary"]["unsupported"] == 3
    assert outcome["summary"]["targets"] == 1
    assert outcome["summary"]["labels"] == 1
    assert outcome["summary"]["facts"] == 1


def test_secondhand_postings_never_reach_resolution(register):
    def parse(_feed, _item, _envelope):
        return [posting(fidelity="secondhand", company_raw="Acme")]

    register(parse)
    outcome = parse_record(ats_feed(), [Item("a", {})], {}, no_alias)

    entry = outcome["items"][0]["canonicals"][0]
    assert entry["classification"] == "secondhand"
    assert "company_resolution" not in entry
    assert outcome["summary"]["secondhand"] == 1
    assert outcome["summary"]["postings"] == 0


def test_company_resolution_prefers_alias_mirror(register):
    def parse(_feed, _item, _envelope):
        return [posting(company_raw="Acme Inc")]

    def resolve(raw):
        assert raw == "Acme Inc"
        return (7, "Acme Corporation")

    register(parse)
    outcome = parse_record(ats_feed(), [Item("a", {})], {}, resolve)

    resolution = outcome["items"][0]["canonicals"][0]["company_resolution"]
    assert resolution["status"] == "resolved"
    assert resolution["company_id"] == 7
    assert resolution["company_name"] == "Acme Corporation"
    assert outcome["summary"]["resolved"] == 1


def test_ats_feed_falls_back_to_feed_company(register):
    def parse(_feed, _item, _envelope):
        return [posting(company_raw="Unknown Startup")]

    register(parse)
    outcome = parse_record(ats_feed(), [Item("a", {})], {}, no_alias)

    resolution = outcome["items"][0]["canonicals"][0]["company_resolution"]
    assert resolution["status"] == "fallback"
    assert resolution["company_id"] == 42
    assert resolution["company_name"] == "Acme"
    assert outcome["summary"]["fallback_company"] == 1


def test_board_feed_without_alias_would_park(register):
    def parse(_feed, _item, _envelope):
        return [posting(company_raw="Unknown Startup")]

    register(parse)
    board = ats_feed(source_type="Board", company_id=None, company_name=None)
    outcome = parse_record(board, [Item("a", {})], {}, no_alias)

    resolution = outcome["items"][0]["canonicals"][0]["company_resolution"]
    assert resolution["status"] == "would_park"
    assert resolution["company_raw"] == "Unknown Startup"
    assert outcome["summary"]["would_park"] == 1


def test_none_title_is_reported_not_raised(register):
    def parse(_feed, _item, _envelope):
        return [posting(title=None, company_raw="Acme")]

    register(parse)
    outcome = parse_record(ats_feed(), [Item("a", {})], {}, no_alias)

    entry = outcome["items"][0]["canonicals"][0]
    assert entry["title_normalized"] is None
    assert "None" in entry["title_warning"]
    assert outcome["items"][0]["status"] == "ok"


def test_normalized_title_folds_punctuation(register):
    def parse(_feed, _item, _envelope):
        return [posting(title="Sr. ML — Engineer", company_raw="Acme")]

    register(parse)
    outcome = parse_record(ats_feed(), [Item("a", {})], {}, no_alias)

    assert outcome["items"][0]["canonicals"][0]["title_normalized"] == "sr ml engineer"
