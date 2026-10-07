from __future__ import annotations

import json
from pathlib import Path

from xjobscore.net import Response
from xjobscore.records import Feed, Item
from xjobscore.sources import janestreet


FIXTURES = Path(__file__).parents[2] / "fixtures"


def fixture(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())


def feed(*, etag: str | None = None) -> Feed:
    return Feed(
        id=1,
        source="janestreet",
        key="janestreet",
        config={},
        etag=etag,
    )


class FakeNet:
    def __init__(self, payload: list[dict], *, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None, etag=None):
        self.calls.append({"url": url, "etag": etag})
        body = b"" if self.status == 304 else json.dumps(self.payload).encode()
        return Response(
            url=url,
            status=self.status,
            headers={"etag": '"jane-etag"'},
            body=body,
        )


async def test_harvest_uses_main_as_the_only_listing_feed():
    jobs = fixture("janestreet_main.json")
    net = FakeNet(jobs)
    source_feed = feed(etag='"old-etag"')

    record = await janestreet.harvest(source_feed, net)

    assert net.calls == [{"url": janestreet.MAIN, "etag": source_feed.etag}]
    assert [item.external_id for item in record.items] == [str(job["id"]) for job in jobs]
    assert record.envelope == {}
    assert record.etag == '"jane-etag"'


async def test_harvest_preserves_main_conditional_get_behavior():
    source_feed = feed(etag='"jane-etag"')
    net = FakeNet([], status=304)

    record = await janestreet.harvest(source_feed, net)

    assert len(net.calls) == 1
    assert record.http_status == 304
    assert record.items == []
    assert record.etag == '"jane-etag"'


def test_parse_does_not_treat_the_closed_archive_as_main_job_status():
    job = {
        "id": 123,
        "position": "Software Engineer",
        "team": "Software Engineering",
        "city": "NYC",
        "duration": "May-August",
        "availability": "Summer Internship",
        "overview": "<p>Open internship</p>",
    }
    stale_envelope = {
        "internships": [
            {
                "position": "Software Engineer",
                "team": "Software Engineering",
                "location": "NYC",
                "duration": "May-August",
                "type": "Internship",
                "status": "closed",
            }
        ]
    }

    posting = janestreet.parse(feed(), Item("123", job), stale_envelope)[0]

    assert posting.state == "open"
    assert posting.source_active is None
    assert posting.employment_type == "Summer Internship"
    assert posting.url_posting == "https://www.janestreet.com/join-jane-street/position/123/"
