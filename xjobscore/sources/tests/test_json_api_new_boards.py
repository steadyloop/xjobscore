"""Bullhorn, Recruit CRM, Gem, and ADP -- against saved live payloads.

Two request shapes: Bullhorn and Recruit CRM are single-request boards, same
as the existing `test_json_api_boards.py` sources. Gem and ADP both fan out to
a detail request, so they get their own `SequencedFixtureNet` that serves the
list body first and the detail body on every request after -- the same
list-then-detail pattern `test_docxtract_html_links.py` uses for `html_links`,
adapted for POST (Gem) and GET (ADP) rather than only GET.

Bullhorn's `id` is a bare integer; the engine's `_item_id` coerces it with
`str()`, matching how TalentReef types `positionId` the same way. Gem's
`{id}` in `url_posting` resolves against the payload (the base64 GraphQL node
id), not context -- there is no `id` in context at `fields`-evaluation time.
ADP's `$skip` is 1-based and the off-by-one there is the whole integration, so
the pagination-params test is the load-bearing one for that source.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xjobscore.net import Response
from xjobscore.records import Feed
from xjobscore.sources import json_api


FIXTURES = Path(__file__).parents[2] / "fixtures"


class FixtureNet:
    """Serves one saved body to every request; records what was sent."""

    def __init__(self, payload) -> None:
        self.response = Response(url="https://fixture.invalid/jobs", status=200, headers={}, body=json.dumps(payload).encode())
        self.get_calls: list[dict] = []
        self.post_calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        self.get_calls.append({"url": url, "params": params, "headers": headers, "etag": etag})
        return self.response

    async def post(self, url, *, json=None, headers=None, concurrency=1):
        self.post_calls.append({"url": url, "json": json, "headers": headers})
        return self.response


class SequencedFixtureNet:
    """Serves the list body first, the detail body to every request after."""

    def __init__(self, list_payload, detail_payload) -> None:
        self.list_response = Response(url="https://fixture.invalid/list", status=200, headers={}, body=json.dumps(list_payload).encode())
        self.detail_response = Response(url="https://fixture.invalid/detail", status=200, headers={}, body=json.dumps(detail_payload).encode())
        self.get_calls: list[dict] = []
        self.post_calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        self.get_calls.append({"url": url, "params": params, "headers": headers, "etag": etag})
        return self.list_response if len(self.get_calls) == 1 else self.detail_response

    async def post(self, url, *, json=None, headers=None, concurrency=1):
        self.post_calls.append({"url": url, "json": json, "headers": headers})
        return self.list_response if len(self.post_calls) == 1 else self.detail_response


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


SINGLE_REQUEST_KEYS = {
    "bullhorn": "44/CJAT1S",
    "recruitcrm": "Oasis_Protocol_Foundation_jobs",
}
SINGLE_REQUEST_CONFIG = {
    "bullhorn": {
        "company_raw": "ESS Clinical",
        "board_url": "https://essclinical.com/wp-content/plugins/bullhorn-oscp/#/jobs/",
    },
}
SEQUENCED_KEYS = {
    "gem": "gem",
    "adp": "d16ba13d-474e-4326-b628-74c87f0b289a/19000101_000001",
}
SEQUENCED_CONFIG = {
    "gem": {"company_raw": "Gem"},
    "adp": {"company_raw": "Look Trailers"},
}


async def harvest_single(name: str):
    net = FixtureNet(fixture(name))
    feed = Feed(id=1, source=name, key=SINGLE_REQUEST_KEYS[name], config=SINGLE_REQUEST_CONFIG.get(name, {}))
    record = await json_api.harvest(name, feed, net)
    assert len(record.items) == 1
    return net, json_api.parse(name, feed, record.items[0], record.envelope)[0]


async def harvest_sequenced(name: str):
    net = SequencedFixtureNet(fixture(name), fixture(f"{name}_detail"))
    feed = Feed(id=1, source=name, key=SEQUENCED_KEYS[name], config=SEQUENCED_CONFIG.get(name, {}))
    record = await json_api.harvest(name, feed, net)
    assert len(record.items) == 1
    return net, json_api.parse(name, feed, record.items[0], record.envelope)[0]


@pytest.mark.parametrize("name", sorted({**SINGLE_REQUEST_KEYS, **SEQUENCED_KEYS}))
async def test_boards_are_registered_for_harvest_and_replay(name):
    from xjobscore import sources

    assert sources.get(name) is not None
    assert sources.parser(name) is not None


# -- Bullhorn ----------------------------------------------------------------


async def test_bullhorn_query_jobboardpost_not_search_jobboardpost():
    """`/search/JobBoardPost` 400s ("Unknown search entity"); the real entity
    is reached through `/query/JobBoardPost`."""
    net, _ = await harvest_single("bullhorn")

    assert net.get_calls[0]["url"] == (
        "https://public-rest44.bullhornstaffing.com/rest-services/CJAT1S/query/JobBoardPost"
    )


async def test_bullhorn_requests_total_matched_and_open_only():
    net, _ = await harvest_single("bullhorn")

    params = net.get_calls[0]["params"]
    assert params["where"] == "isOpen=true"
    assert params["showTotalMatched"] == "true"
    assert params["start"] == 0
    # 500 is Bullhorn's own clamp (doc/integration-plan.md:221); asking for it
    # is what keeps a 792-row board at 2 requests instead of 8.
    assert params["count"] == 500


async def test_bullhorn_fields_are_inline_no_detail_fetch():
    net, posting = await harvest_single("bullhorn")

    assert net.get_calls == [net.get_calls[0]]  # exactly one request total
    assert posting.external_id == "4830"
    assert posting.description_html is not None and len(posting.description_html) > 100
    assert posting.company_raw == "ESS Clinical"
    assert posting.url_posting == "https://essclinical.com/wp-content/plugins/bullhorn-oscp/#/jobs/4830"


async def test_bullhorn_dates_are_epoch_milliseconds_not_iso():
    _, posting = await harvest_single("bullhorn")

    assert posting.published_at is not None
    assert posting.published_at.year == 2026
    assert posting.published_at_precision == "exact"


async def test_bullhorn_source_active_reflects_is_open():
    _, posting = await harvest_single("bullhorn")

    assert posting.source_active is True


# -- Recruit CRM ---------------------------------------------------------------


async def test_recruitcrm_sends_origin_and_referer_headers():
    """Without these the endpoint answers 200 with a soft access-denied body
    rather than an HTTP error -- the header IS the auth."""
    net, _ = await harvest_single("recruitcrm")

    headers = net.post_calls[0]["headers"]
    assert headers["Origin"] == "https://careers.recruitcrm.io"
    assert headers["Referer"] == "https://careers.recruitcrm.io/"


async def test_recruitcrm_no_batch_param_returns_jobs_inline():
    net, posting = await harvest_single("recruitcrm")

    assert "batch" not in net.post_calls[0]["url"]
    assert posting.external_id == "16436639290320017453gNO"
    assert posting.description_html is not None and len(posting.description_html) > 100


async def test_recruitcrm_company_raw_falls_back_to_envelope_account_title():
    """The row itself carries an empty `companyname`; the employer name lives
    only on the response root."""
    _, posting = await harvest_single("recruitcrm")

    assert posting.company_raw == "Oasis Protocol Foundation"


async def test_recruitcrm_has_no_date_field_anywhere_in_the_schema():
    """Not a config gap -- there is no per-job date field on this source."""
    _, posting = await harvest_single("recruitcrm")

    assert posting.published_at is None
    assert posting.published_at_precision == "unknown"


# -- Gem -----------------------------------------------------------------------


async def test_gem_list_query_is_a_separate_operation_from_detail():
    net, _ = await harvest_sequenced("gem")

    assert len(net.post_calls) == 2
    assert net.post_calls[0]["json"][0]["operationName"] == "JobBoardList"
    assert net.post_calls[1]["json"][0]["operationName"] == "ExternalJobPostingQuery"
    assert net.post_calls[1]["json"][0]["variables"] == {"boardId": "gem", "extId": "4965519002"}


async def test_gem_description_and_dates_come_from_the_detail_fetch():
    _, posting = await harvest_sequenced("gem")

    assert posting.external_id == "4965519002"
    assert posting.title == "Software Engineer"
    assert posting.description_html is not None and len(posting.description_html) > 1000
    assert posting.published_at is not None
    assert posting.published_at_precision == "exact"
    assert posting.requisition_id == "95"


async def test_gem_url_posting_templates_id_from_the_payload_not_context():
    _, posting = await harvest_sequenced("gem")

    assert posting.url_posting == "https://jobs.gem.com/gem/T2F0c0pvYlBvc3Q6MTExOTE="


async def test_gem_location_comes_from_the_list_locations_list():
    _, posting = await harvest_sequenced("gem")

    assert posting.location_raw == "San Francisco"
    assert posting.employment_type == "FULL_TIME"
    assert posting.workplace_type_raw == "HYBRID"


# -- ADP -------------------------------------------------------------------


async def test_adp_top_is_20_and_skip_is_1_based():
    net, _ = await harvest_sequenced("adp")

    params = net.get_calls[0]["params"]
    assert params["$top"] == 20
    assert params["$skip"] == 1


async def test_adp_detail_url_carries_the_itemid_and_both_key_parts():
    net, _ = await harvest_sequenced("adp")

    assert net.get_calls[1]["url"] == (
        "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/"
        "job-requisitions/9201454482567_1"
        "?cid=d16ba13d-474e-4326-b628-74c87f0b289a&ccId=19000101_000001"
    )


async def test_adp_description_only_available_after_detail_fetch():
    _, posting = await harvest_sequenced("adp")

    assert posting.description_html is not None and len(posting.description_html) > 1000
    assert posting.requisition_id == "3181"


async def test_adp_uses_postdate_not_the_second_less_customfield_date():
    _, posting = await harvest_sequenced("adp")

    assert posting.published_at is not None
    assert posting.published_at.second == 0
    assert posting.published_at_precision == "exact"


async def test_adp_company_raw_comes_from_feed_config():
    _, posting = await harvest_sequenced("adp")

    assert posting.company_raw == "Look Trailers"
