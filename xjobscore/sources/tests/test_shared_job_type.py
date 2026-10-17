"""The shared title ladder on the adapters that had no job_type rule.

ADP and Pinpoint bind it as a JSON field rule, like Oracle and Eightfold;
JazzHR, SuccessFactors and Trakstar opt in with docxtract's
`"job_type": "shared"`; Dayforce, Zoho Recruit and HN call
`json_api.shared_job_type`. Each must type a title exactly as the ladder does,
exclusions included, and a docxtract board that did not opt in stays untyped.
"""

from __future__ import annotations

import pytest

from xjobscore import sources
from xjobscore.records import Feed, Item
from xjobscore.sources import json_api


def _rss(title: str) -> str:
    return f"<item><title>{title}</title><link>https://example.invalid/1</link><guid>1</guid></item>"


PAYLOADS = {
    "adp": (lambda title: {"itemID": "1", "requisitionTitle": title}, "cid/ccid"),
    "pinpoint": (lambda title: {"id": 1, "title": title}, "acme"),
    "jazzhr": (lambda title: {"@type": "JobPosting", "title": title}, "acme"),
    "successfactors": (_rss, "jobs.example.invalid"),
    "trakstar": (_rss, "acme"),
    "dayforce": (lambda title: {"jobTitle": title}, "acme"),
    "zohorecruit": (lambda title: {"id": "1", "Posting_Title": title}, "acme"),
    "hn": (lambda title: {"comment_text": f"Acme | {title} | New York, NY | ONSITE"}, "hn"),
}

TITLES = [
    ("Software Engineer Intern", "intern"),
    ("Mechanical Engineering Co-op - Summer 2027", "coop"),
    ("Research Intern - Machine Learning", "research"),
    ("Pharmacy Intern", None),
    ("PhD Machine Learning Engineer Intern", None),
    ("Senior Software Engineer", None),
]


def _posting(source: str, title: str):
    payload, key = PAYLOADS[source]
    feed = Feed(id=1, source=source, key=key, config={})
    [posting] = sources.parser(source)(feed, Item("1", payload(title)), {})
    return posting


@pytest.mark.parametrize("source", sorted(PAYLOADS))
@pytest.mark.parametrize(("title", "expected"), TITLES)
def test_shared_ladder_types_the_posting(source, title, expected):
    posting = _posting(source, title)

    assert posting.title == title
    assert posting.job_type == expected
    assert json_api.shared_job_type(title) == expected


def test_docxtract_board_without_the_opt_in_stays_untyped():
    feed = Feed(id=1, source="teamtailor", key="acme", config={})
    [posting] = sources.parser("teamtailor")(feed, Item("1", _rss("Software Engineer Intern")), {})

    assert posting.job_type is None
