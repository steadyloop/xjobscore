"""Fountain's public directory JSON, retained as a planned pointer source.

The fixture is a real response from RENFROE's Home Office board. It proves
that the shared JSON adapter can enumerate and identify the rows, while the
negative assertions pin why this source is not enabled: Fountain supplies no
description or date in this endpoint and its detail is HTML-only.
"""

from __future__ import annotations

import json
from pathlib import Path

from xjobscore.net import Response
from xjobscore.records import Feed
from xjobscore.sources import json_api


FIXTURE = Path(__file__).parents[3] / "fixtures" / "fountain.json"
KEY = "renfroe/renfroe-home-office/US"


class FixtureNet:
    def __init__(self) -> None:
        self.response = Response(
            url="https://fixture.invalid/jobs",
            status=200,
            headers={},
            body=FIXTURE.read_bytes(),
        )
        self.get_calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None, etag=None):
        self.get_calls.append({"url": url, "params": params, "headers": headers, "etag": etag})
        return self.response


def feed() -> Feed:
    return Feed(
        id=1,
        source="fountain",
        key=KEY,
        config={"company_raw": "RENFROE"},
    )


async def test_fountain_harvest_uses_all_three_key_parts_and_page_one():
    net = FixtureNet()

    record = await json_api.harvest("fountain", feed(), net)

    assert net.get_calls == [{
        "url": "https://web.fountain.com/c/renfroe/renfroe-home-office/US.json",
        "params": {"page": 1},
        "headers": None,
        "etag": None,
    }]
    assert not record.truncated
    assert len(record.items) == 6
    assert record.items[0].external_id == "9c922b84-dbce-4f0e-80eb-de0324eccb91"


def test_fountain_paginates_by_page_number_and_reported_total():
    assert json_api.CONFIGS["fountain"]["list"]["page"] == {
        "start": 1,
        "size": 25,
        "step": 1,
        "total": "@pagination.total_count",
        "max": 40,
    }


async def test_fountain_maps_the_directory_pointer_without_inventing_content():
    record = await json_api.harvest("fountain", feed(), FixtureNet())
    item = next(item for item in record.items if item.payload["title"] == "Senior Software Engineer")

    posting = json_api.parse("fountain", feed(), item, record.envelope)[0]

    assert posting.url_posting == "https://web.fountain.com/renfroe/apply/technical-lead?brand_id=all"
    assert posting.apply_url == posting.url_posting
    assert posting.company_raw == "RENFROE"
    assert posting.location_raw == "US"
    assert posting.employment_type == "permanent"
    assert posting.description_html is None
    assert posting.published_at is None
    assert posting.published_at_precision == "unknown"


def test_fountain_fixture_has_no_hidden_description_or_date_field():
    payload = json.loads(FIXTURE.read_text())

    assert payload["openings"]
    assert not any(
        "description" in key.lower() or "date" in key.lower()
        for row in payload["openings"]
        for key in row
    )


def test_fountain_adapter_is_registered():
    from xjobscore import sources

    assert sources.get("fountain") is not None
    assert sources.parser("fountain") is not None
