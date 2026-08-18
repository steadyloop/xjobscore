"""Simplify's shared listings.json filtered to its current Summer 2027 README."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from xjobscore.records import Feed, Item
from xjobscore.sources import docxtract


NOW = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)


def feed() -> Feed:
    return Feed(id=1, source="simplify", key="SimplifyJobs/Summer2027-Internships")


def payload(**overrides):
    row = {
        "id": "listing-1",
        "title": "Software Engineer Intern",
        "url": "https://jobs.example.test/intern",
        "company_name": "Example",
        "locations": ["Remote"],
        "active": True,
        "is_visible": True,
        "terms": ["Summer 2027"],
        "date_posted": int((NOW - timedelta(days=10)).timestamp()),
        "date_updated": int(NOW.timestamp()),
        "source": "Simplify",
        "category": "Software",
        "sponsorship": "Other",
        "degrees": ["Bachelor's"],
        "company_url": "https://simplify.jobs/c/example",
    }
    row.update(overrides)
    return row


def test_simplify_current_summer_listing_parses_as_secondhand(monkeypatch):
    monkeypatch.setattr(docxtract, "datetime", _Clock)

    postings = docxtract.parse(
        "simplify", feed(), Item(external_id="listing-1", payload=payload()), {}
    )

    assert len(postings) == 1
    posting = postings[0]
    assert posting.fidelity == "secondhand"
    assert posting.state == "open"
    assert posting.source_active is True
    assert posting.title == "Software Engineer Intern"


@pytest.mark.parametrize(
    "overrides",
    [
        {"active": False},
        {"active": None},
        {"is_visible": False},
        {"company_url": "https://simplify.jobs/c/Jerry"},
        {"terms": ["Fall 2027"]},
        {"date_posted": 1777618800},
        {"date_posted": int((NOW - timedelta(days=60)).timestamp())},
        {"date_posted": "not-a-timestamp"},
    ],
)
def test_simplify_excludes_rows_outside_the_current_readme(monkeypatch, overrides):
    monkeypatch.setattr(docxtract, "datetime", _Clock)

    assert docxtract.parse(
        "simplify",
        feed(),
        Item(external_id="listing-1", payload=payload(**overrides)),
        {},
    ) == []


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)
