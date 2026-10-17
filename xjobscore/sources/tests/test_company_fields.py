"""Boards that describe their employer hand it to silver, not just the posting.

Each case parses the vendor's recorded payload: what the company says about
itself lands on `company_description`, a link to its own site on
`company_domain`. Silver decides what is fit to keep (see store.fill_company).
"""

from __future__ import annotations

import json
from pathlib import Path

from xjobscore.records import Feed, Item
from xjobscore.sources import json_api

FIXTURES = Path(__file__).parents[3] / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _parse(name: str, payload: dict, envelope: dict | None = None, **config):
    feed = Feed(id=1, source=name, key="acme", config=config)
    return json_api.parse(name, feed, Item(external_id="1", payload=payload), envelope or {})[0]


def test_workable_keeps_the_account_description():
    listing = _load("workable")
    envelope = {"name": listing["name"], "description": listing["description"]}
    posting = _parse("workable", listing["jobs"][0], envelope)

    assert posting.company_description.startswith("Here at Hugging Face")
    assert posting.company_domain is None


def test_smartrecruiters_keeps_the_company_section():
    detail = _load("smartrecruiters_detail")
    detail["jobAd"]["sections"]["companyDescription"]["text"] = "<p>Visa moves money.</p>"
    posting = _parse("smartrecruiters", detail)

    assert posting.company_description == "<p>Visa moves money.</p>"
    # The job's own text still carries it, as before.
    assert "Visa moves money." in posting.description_html


def test_smartrecruiters_without_a_company_section_says_nothing():
    posting = _parse("smartrecruiters", _load("smartrecruiters_detail"))

    assert not posting.company_description


def test_rippling_points_at_the_board_url_and_names_the_company():
    detail = _load("rippling_detail")
    posting = _parse("rippling", detail)

    assert posting.company_domain == detail["board"]["boardURL"]
    assert posting.company_raw == "Rippling"


def test_rippling_never_names_a_company_after_the_page_heading():
    detail = _load("rippling_detail")
    detail.pop("companyName")
    posting = _parse("rippling", detail)

    assert posting.company_raw == detail["board"]["companyName"]
    assert posting.company_raw != detail["board"]["title"]


def test_gem_reads_the_company_url():
    detail = _load("gem_detail")
    payload = detail[0]["data"]["oatsExternalJobPosting"] if isinstance(detail, list) else detail
    payload = {**payload, "companyUrl": "https://www.civrobotics.com/"}
    posting = _parse("gem", payload, company_raw="Civ Robotics")

    assert posting.company_domain == "https://www.civrobotics.com/"


def test_greenhouse_reads_the_job_link_as_the_company_site():
    posting = _parse("greenhouse", _load("greenhouse")["jobs"][0])

    # On the employer's own site here; silver refuses a greenhouse.io link.
    assert posting.company_domain == "https://stripe.com/jobs/search?gh_jid=7954688"


def test_recruitee_reads_the_careers_link_as_the_company_site():
    posting = _parse("recruitee", _load("recruitee")["offers"][0])

    assert posting.company_domain == "https://transperfect.com/o/director-business-development-5"
