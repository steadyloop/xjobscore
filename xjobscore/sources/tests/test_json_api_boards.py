"""Public JSON boards added from the 2026-08 adapter recon, against saved payloads.

Five configs, three shapes the engine already knew and one it had not been asked
for: `talentreef` unwraps an Elasticsearch `_source` and builds its posting URL
from two numbers, because the `url` the documents carry is empty on about a
third of them and relative on the rest.

The absent fields are asserted too. Recruitee and Unstop both stamp timestamps
the config vocabulary cannot reshape ("2026-08-06 15:57:36 UTC",
"… GMT+0530"), so those must stay in `source_extra` and leave `published_at`
unset rather than raise inside `parse`.
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
    def __init__(self, payload: dict) -> None:
        self.response = Response(
            url="https://fixture.invalid/jobs",
            status=200,
            headers={},
            body=json.dumps(payload).encode(),
        )
        self.get_calls: list[dict] = []
        self.post_calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        self.get_calls.append({
            "url": url, "params": params, "headers": headers, "etag": etag,
            "concurrency": concurrency,
        })
        return self.response

    async def post(self, url, *, json=None, headers=None):
        self.post_calls.append({"url": url, "json": json, "headers": headers})
        return self.response


KEYS = {
    "recruitee": "transperfect",
    "pinpoint": "workwithus",
    "manatal": "manatal",
    "unstop": "unstop",
    "talentreef": "talentreef",
    # host/companyCode/boardGuid: a UKG board answers on exactly one shard host.
    "ukg": "recruiting.ultipro.com/UNI1076UNFI/86df2700-c124-49b9-b096-7cacea55e9dd",
}

# Boards that name no employer anywhere in the payload take one from the catalog.
FEED_CONFIG = {"ukg": {"company_raw": "United Natural Foods"}}


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def feed_for(name: str) -> Feed:
    return Feed(id=1, source=name, key=KEYS[name], config=FEED_CONFIG.get(name, {}))


async def harvest_one(name: str) -> tuple[FixtureNet, object]:
    net = FixtureNet(fixture(name))
    feed = feed_for(name)
    record = await json_api.harvest(name, feed, net)
    assert len(record.items) == 1
    return net, json_api.parse(name, feed, record.items[0], record.envelope)[0]


@pytest.mark.parametrize("name", sorted(KEYS))
async def test_boards_are_registered_for_harvest_and_replay(name):
    from xjobscore import sources

    assert sources.get(name) is not None
    assert sources.parser(name) is not None


@pytest.mark.parametrize(
    ("name", "external_id", "title", "url_posting"),
    [
        ("recruitee", "2702830", "Director, Business Development",
         "https://transperfect.com/o/director-business-development-5"),
        ("pinpoint", "520855", "Product Reliability Engineer ",
         "https://workwithus.pinpointhq.com/en/postings/0e968d34-78ce-4d50-bc33-1f3f28e816c6"),
        ("manatal", "1333846", "Business Development Internship (12PM - 9PM)",
         "https://www.careers-page.com/manatal/job/L8597V4V"),
        ("unstop", "1731836", "Campus Ambassador Internship",
         "https://unstop.com/internships/campus-ambassador-internship-smarted-innovations-1731836"),
        ("talentreef", "11086949", "Clerk",
         "https://apply.jobappnetwork.com/clients/13757/posting/11086949/en"),
        ("ukg", "0eae2087-d555-4853-aee3-1ed23f6e4067",
         "Senior Manager, Finance and Grants, Family Planning 2030",
         "https://recruiting.ultipro.com/UNI1076UNFI/JobBoard/"
         "86df2700-c124-49b9-b096-7cacea55e9dd/OpportunityDetail"
         "?opportunityId=0eae2087-d555-4853-aee3-1ed23f6e4067"),
    ],
)
async def test_fixture_contracts_produce_identified_postings(name, external_id, title, url_posting):
    _, posting = await harvest_one(name)

    assert posting.external_id == external_id
    assert posting.title == title
    assert posting.url_posting == url_posting
    assert posting.description_html


@pytest.mark.parametrize(
    ("name", "company_raw", "location_raw"),
    [
        # The employer names itself on the row ...
        ("recruitee", "TransPerfect", "Paris, Île-de-France, France"),
        ("unstop", "SmartED Innovations", "Bangalore"),
        ("talentreef", "Niemann Foods", "Macon, MO"),
        # ... or it does not, and the board slug is all there is.
        ("pinpoint", "workwithus", "Remote"),
        ("manatal", "manatal", "Bangkok, Bangkok, Thailand"),
        # ... or the key is two opaque ids, so the catalog supplies the name.
        ("ukg", "United Natural Foods", "Washington, DC"),
    ],
)
async def test_employer_and_location_resolve(name, company_raw, location_raw):
    _, posting = await harvest_one(name)

    assert posting.company_raw == company_raw
    assert posting.location_raw == location_raw


async def test_talentreef_unwraps_elasticsearch_source_and_types_its_ids():
    net, posting = await harvest_one("talentreef")

    body = net.post_calls[0]["json"]
    assert body["from"] == 0 and body["size"] == 1000
    assert body["sort"] == [{"createdDate": "desc"}, {"jobId": "desc"}]
    # fixture total is 1, so the configured fan-out never fires.
    assert len(net.post_calls) == 1
    # positionId is a JSON number on the wire; Posting types it as text.
    assert posting.requisition_id == "52359"
    assert posting.published_at is not None
    assert posting.published_at_precision == "date"
    # `brand` is the storefront, `clientName` the operator who is actually
    # hiring -- which is why company_raw prefers clientName and keeps brand.
    assert posting.company_raw == "Niemann Foods"
    assert posting.source_extra["brand"] == "Haymakers"


async def test_ukg_splits_its_three_part_key_into_the_request():
    net, posting = await harvest_one("ukg")

    assert net.post_calls[0]["url"] == (
        "https://recruiting.ultipro.com/UNI1076UNFI/JobBoard/"
        "86df2700-c124-49b9-b096-7cacea55e9dd/JobBoardView/LoadSearchResults"
    )
    search = net.post_calls[0]["json"]["opportunitySearch"]
    assert search["Skip"] == 0 and search["Top"] == 50
    assert posting.requisition_id == "SENIO002183"
    assert posting.published_at_precision == "exact"


async def test_ukg_description_is_only_the_teaser_the_list_returns():
    """There is no JSON detail endpoint, so content is knowingly truncated.

    If a JSON detail route ever appears, this is the assertion that should fail.
    """
    _, posting = await harvest_one("ukg")

    assert posting.description_html.endswith("…")
    assert len(posting.description_html) < 1000


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("manatal", {"page": 1, "page_size": 50}),
        ("unstop", {"opportunity": "internships", "page": 1, "per_page": 200}),
    ],
)
async def test_paginated_boards_start_on_the_configured_first_page(name, params):
    net, _ = await harvest_one(name)

    assert net.get_calls[0]["params"] == params


async def test_unstop_keeps_its_1000_item_cap_in_five_concurrent_pages():
    payload = fixture("unstop")
    payload["data"]["total"] = 10_000
    net = FixtureNet(payload)

    record = await json_api.harvest("unstop", feed_for("unstop"), net)

    assert record.truncated is True
    assert [call["params"]["page"] for call in net.get_calls] == [1, 2, 3, 4, 5]
    assert [call["params"]["per_page"] for call in net.get_calls] == [200] * 5
    assert [call["concurrency"] for call in net.get_calls] == [1, 4, 4, 4, 4]


@pytest.mark.parametrize(
    ("name", "raw_field", "raw_value"),
    [
        ("recruitee", "published_at", "2026-08-06 15:57:36 UTC"),
        ("unstop", "approved_date", "2026-08-07 02:20:57 GMT+0530"),
    ],
)
async def test_unparseable_timestamps_are_kept_raw_not_mapped(name, raw_field, raw_value):
    """Neither string survives `datetime.fromisoformat`, so neither may reach a date field."""
    _, posting = await harvest_one(name)

    assert posting.source_extra[raw_field] == raw_value


async def test_recruitee_leaves_published_at_unset_rather_than_guessing():
    _, posting = await harvest_one("recruitee")

    assert posting.published_at is None
    assert posting.published_at_precision == "unknown"
    # Unstop's registration window *is* ISO, so it does get a timestamp.
    _, unstop = await harvest_one("unstop")
    assert unstop.published_at is not None
    assert unstop.published_at_precision == "exact"


async def test_boards_that_state_their_own_status_report_it():
    _, recruitee = await harvest_one("recruitee")
    _, unstop = await harvest_one("unstop")

    assert recruitee.source_active is True
    assert unstop.source_active is True
