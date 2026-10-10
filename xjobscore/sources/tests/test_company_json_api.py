from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from xjobscore.net import Response
from xjobscore.records import Feed
from xjobscore.sources import json_api


FIXTURES = Path(__file__).parents[3] / "fixtures"


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

    # `concurrency` is only passed through when a config raises it above 1, but
    # accepting it keeps a fanned-out source (apple) usable here.
    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        self.get_calls.append({"url": url, "params": params, "headers": headers, "etag": etag})
        return self.response

    async def post(self, url, *, json=None, headers=None, concurrency=1):
        self.post_calls.append({"url": url, "json": json, "headers": headers})
        return self.response


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def feed_for(name: str) -> Feed:
    return Feed(id=1, source=name, key=name, config={})


def with_total(name: str, total: int) -> dict:
    payload = fixture(name)
    if name == "amazon":
        payload["hits"] = total
    elif name == "apple":
        payload["res"]["totalRecords"] = total
    return payload


@pytest.mark.parametrize(
    ("name", "expected_title", "expected_url"),
    [
        ("amazon", "Software Development Engineer", "https://www.amazon.jobs/jobs/2345678/software-development-engineer"),
        ("apple", "Machine Learning Engineer - Siri", "https://jobs.apple.com/en-us/details/200500123/machine-learning-engineer-siri"),
    ],
)
async def test_fixture_contracts_produce_postings(name, expected_title, expected_url):
    net = FixtureNet(with_total(name, 1))
    feed = feed_for(name)

    record = await json_api.harvest(name, feed, net)

    assert record.http_status == 200
    assert record.truncated is False
    assert len(record.items) == 1
    postings = json_api.parse(name, feed, record.items[0], record.envelope)
    assert len(postings) == 1
    assert postings[0].title == expected_title
    assert postings[0].url_posting == expected_url


async def test_fixture_contracts_preserve_provider_specific_fields():
    apple_feed = feed_for("apple")
    apple_record = await json_api.harvest("apple", apple_feed, FixtureNet(with_total("apple", 1)))
    apple_posting = json_api.parse("apple", apple_feed, apple_record.items[0], apple_record.envelope)[0]
    assert apple_posting.location_raw == "Cupertino, Santa Clara Valley (SCV), United States"


async def test_requests_include_provider_query_and_configured_pagination_keys():
    amazon_net = FixtureNet(with_total("amazon", 1))
    await json_api.harvest("amazon", feed_for("amazon"), amazon_net)
    assert amazon_net.get_calls[0]["params"] == {
        "base_query": "",
        "offset": 0,
        "result_limit": 100,
        "is_intern[]": ["1"],
        "schedule_type_id[]": [
            "Flex-Time",
            "Part-Time",
            "Reduced-Time",
            "Flex-Time (<19 hours)",
            "Part-Time (20-29 hours)",
            "Reduced-Time (30-39 hours)",
            "Flex Time (<19 hours)",
            "Reduced Time (30-39 hours)",
        ],
        "employee_class[]": ["Seasonal/short-term"],
    }

    apple_net = FixtureNet(with_total("apple", 1))
    await json_api.harvest("apple", feed_for("apple"), apple_net)
    assert apple_net.post_calls[0]["json"]["filters"] == {
        "locations": ["postLocation-USA"],
    }

@pytest.mark.parametrize(
    ("name", "request_kind", "field", "first", "second"),
    [
        ("amazon", "get_calls", "offset", 0, 100),
        ("apple", "post_calls", "page", 1, 2),
    ],
)
async def test_pagination_advances_the_configured_parameter(name, request_kind, field, first, second):
    net = FixtureNet(with_total(name, 2))

    record = await json_api.harvest(name, feed_for(name), net)

    calls = getattr(net, request_kind)
    values = [call["params"][field] if request_kind == "get_calls" else call["json"][field] for call in calls]
    assert len(record.items) == 2
    assert values == [first, second]


def test_harvest_does_not_block_the_event_loop_in_fixture_contracts():
    """A tiny smoke check that fixture contracts stay fully async."""
    assert asyncio.iscoroutinefunction(json_api.harvest)
