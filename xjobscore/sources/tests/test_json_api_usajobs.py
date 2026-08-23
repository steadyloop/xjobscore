"""USAJOBS Search API -- against a real archived response, no key required.

The fixture is a genuine 200 body (`SearchResultCountAll: 19`,
`SearchResultCount: 10`) captured from a historical query that itself used
`resultsperpage=10` -- an artifact of which query got archived, not of the
live API. This config always requests `ResultsPerPage=500`, so a real fetch
against 19 total results would return everything in one page. Driving the
fixture through the full `harvest()` pagination loop would therefore just
replay the same 10-item body until the `page.max` guard kicks in, producing
an artificial `truncated=True` that says nothing true about the source. So,
matching `test_json_api_headers.py`'s precedent for exactly this situation,
requests are built directly via `json_api._request` and parsing is tested
directly via `json_api.parse` against a hand-picked `Item` from the fixture
-- both real code paths, neither one needing pagination to run.

Every value here is unverified only in the sense that no `Authorization-Key`
has been minted yet (the feed remains disabled until the user registers one
at developer.usajobs.gov); the response shape,
field paths, and JobCategoryCode semantics are all confirmed against this
real capture and the live API reference page.
"""

from __future__ import annotations

import json as json_module
from pathlib import Path

from xjobscore.net import Response
from xjobscore.records import Feed, Item
from xjobscore.sources import json_api


FIXTURES = Path(__file__).parents[2] / "fixtures"
CONFIG = json_api.CONFIGS["usajobs"]


class FixtureNet:
    def __init__(self, payload: dict) -> None:
        self.response = Response(url="https://data.usajobs.gov/api/search", status=200, headers={}, body=json_module.dumps(payload).encode())
        self.get_calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None, etag=None):
        self.get_calls.append({"url": url, "params": params, "headers": headers, "etag": etag})
        return self.response


def fixture() -> dict:
    return json_module.loads((FIXTURES / "usajobs.json").read_text())


def feed_for(config: dict | None = None) -> Feed:
    return Feed(id=1, source="usajobs", key="usajobs", config=config or {})


async def test_usajobs_sends_authorization_key_and_user_agent_from_feed_config():
    net = FixtureNet(fixture())
    feed = feed_for({"api_key": "secret-key-123", "ua_email": "ops@example.com"})
    context = {"key": feed.key, "cursor": 1, "api_key": "secret-key-123", "ua_email": "ops@example.com"}

    await json_api._request(CONFIG["list"], feed, net, context, first=True)

    assert net.get_calls[0]["headers"] == {
        "Authorization-Key": "secret-key-123",
        "User-Agent": "ops@example.com",
    }


async def test_usajobs_filters_by_job_category_code_semicolon_delimited():
    """Series codes: 2210=IT, 1550=Computer Science, 1520=Mathematics. Multiple
    values are semicolon-delimited per the live API reference page, not a
    repeated key or JSON list."""
    net = FixtureNet(fixture())
    feed = feed_for()
    context = {"key": feed.key, "cursor": 1}

    await json_api._request(CONFIG["list"], feed, net, context, first=True)

    assert net.get_calls[0]["params"]["JobCategoryCode"] == "2210;1550;1520"


async def test_usajobs_requests_full_fields_and_500_results_per_page():
    net = FixtureNet(fixture())
    feed = feed_for()
    context = {"key": feed.key, "cursor": 1}

    await json_api._request(CONFIG["list"], feed, net, context, first=True)

    params = net.get_calls[0]["params"]
    assert params["Fields"] == "Full"
    assert params["ResultsPerPage"] == 500
    assert params["Page"] == 1


def test_usajobs_pagination_shape_is_500_per_page_capped_at_20():
    """20 pages at 500/page covers the API's 10,000-row query ceiling; the
    engine reports `truncated` if a board's own filtered result ever exceeds
    that, rather than silently dropping the rest."""
    page = CONFIG["list"]["page"]
    assert page == {
        "start": 1, "size": 500, "step": 1,
        "total": "@SearchResult.SearchResultCountAll", "max": 20,
    }


def _first_item() -> Item:
    row = fixture()["SearchResult"]["SearchResultItems"][0]
    return Item(external_id=str(row["MatchedObjectId"]), payload=row)


def test_usajobs_parse_reads_matched_object_descriptor_fields():
    feed = feed_for()
    item = _first_item()

    posting = json_api.parse("usajobs", feed, item, {})[0]

    assert posting.external_id == "859234800"
    assert posting.title == "Correctional Officer (Correctional Systems Officer)"
    assert posting.url_posting == "https://www.usajobs.gov:443/job/859234800"
    assert posting.company_raw == "Bureau of Prisons/Federal Prison System"
    assert posting.location_raw == "Waseca, Minnesota"
    assert posting.requisition_id == "WAS-2026-0019"


def test_usajobs_company_raw_falls_back_to_department_when_organization_absent():
    feed = feed_for()
    row = fixture()["SearchResult"]["SearchResultItems"][0]
    row = {**row, "MatchedObjectDescriptor": {**row["MatchedObjectDescriptor"], "OrganizationName": ""}}
    item = Item(external_id=str(row["MatchedObjectId"]), payload=row)

    posting = json_api.parse("usajobs", feed, item, {})[0]

    assert posting.company_raw == "Department of Justice"


def test_usajobs_major_duties_list_concatenates_into_description_html():
    """`UserArea.Details.MajorDuties` is a list[str], not a scalar --
    `PositionFormattedDescription` is deliberately not used, since on this
    response shape it carries no `Content` key."""
    feed = feed_for()
    item = _first_item()

    posting = json_api.parse("usajobs", feed, item, {})[0]

    assert posting.description_html.startswith("This position is located in the Correctional Systems Department")


def test_usajobs_position_start_and_end_dates_map_to_published_and_deadline():
    """`PositionStartDate`/`PositionEndDate` carry 4 fractional-second digits
    (`'2026-02-27T08:45:22.9570'`) -- parses on Python 3.12+, which
    `pyproject.toml` already requires."""
    feed = feed_for()
    item = _first_item()

    posting = json_api.parse("usajobs", feed, item, {})[0]

    assert posting.published_at is not None
    assert posting.published_at.year == 2026
    assert posting.published_at_precision == "exact"
    assert posting.deadline_at is not None
    assert posting.deadline_at.month == 3


def test_usajobs_extra_carries_hiring_path_and_summaries():
    feed = feed_for()
    item = _first_item()

    posting = json_api.parse("usajobs", feed, item, {})[0]

    assert posting.source_extra["hiring_path"] == ["public"]
    assert posting.source_extra["job_summary"]
    assert posting.source_extra["qualification_summary"]


def test_usajobs_is_registered_with_a_parser():
    from xjobscore import sources

    assert sources.get("usajobs") is not None
    assert sources.parser("usajobs") is not None
