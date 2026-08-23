from __future__ import annotations

import json

from xjobscore.net import NetError, Response
from xjobscore.records import Feed, Item
from xjobscore.sources import hn


STORY = {
    "hits": [{
        "objectID": "49156683",
        "author": "whoishiring",
        "title": "Ask HN: Who is hiring? (August 2026)",
    }]
}
TOP_LEVEL = {
    "objectID": "49234667",
    "parent_id": 49156683,
    "story_id": 49156683,
    "story_title": "Ask HN: Who is hiring? (August 2026)",
    "author": "kouiskas",
    "created_at": "2026-08-09T19:15:56Z",
    "comment_text": (
        "Symbolic.ai | Senior Fullstack Engineer | REMOTE (worldwide) | Full-time"
        "<p>We build tools for newsrooms. "
        '<a href="https://symbolic.ai/careers/senior-fullstack">Apply here</a>'
    ),
}
LOCATION_FIRST = {
    "objectID": "49232553",
    "parent_id": 49156683,
    "story_id": 49156683,
    "author": "JeremyTheo",
    "created_at": "2026-08-09T15:53:29Z",
    "comment_text": (
        "Cologne, Germany | UMH | Product Engineer | Full-time | 55 - 85k EUR | "
        "ONSITE (part remote)<p>We build industrial data infrastructure."
    ),
}
REPLY = {
    "objectID": "49234514",
    "parent_id": 49212742,
    "story_id": 49156683,
    "author": "ghost_ptr",
    "comment_text": "Where is the best place to apply?",
}


def response(payload: dict, *, status: int = 200, headers: dict | None = None) -> Response:
    return Response(
        url="https://hn.algolia.com/api/v1/search_by_date",
        status=status,
        headers=headers or {},
        body=json.dumps(payload).encode(),
    )


class FakeNet:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None, etag=None):
        self.calls.append({"url": url, "params": params, "etag": etag})
        if params["tags"] == "story,author_whoishiring":
            return response(STORY)
        return response(self.pages[params["page"]], headers={"etag": '"hn-etag"'})


async def test_harvest_discovers_latest_thread_paginates_and_keeps_only_top_level():
    pages = [
        {"hits": [TOP_LEVEL, REPLY], "nbPages": 2},
        {"hits": [LOCATION_FIRST], "nbPages": 2},
    ]
    net = FakeNet(pages)
    feed = Feed(id=1, source="hn", key="hn", config={"hits_per_page": 2}, etag="old")

    record = await hn.harvest(feed, net)

    assert [item.external_id for item in record.items] == ["49234667", "49232553"]
    assert record.envelope["story_id"] == "49156683"
    assert record.envelope["story_title"] == "Ask HN: Who is hiring? (August 2026)"
    assert record.etag == '"hn-etag"'
    assert not record.truncated
    assert net.calls[0]["params"]["query"] == "Ask HN: Who is hiring?"
    assert net.calls[1]["etag"] == "old"
    assert net.calls[2]["etag"] is None


async def test_discovery_reads_few_hits_and_search_decoration_is_never_stored():
    """`_highlightResult` is an empty-match copy of fields already present."""
    decorated = {
        **TOP_LEVEL,
        "_highlightResult": {
            "comment_text": {"matchLevel": "none", "matchedWords": [], "value": "…"}
        },
    }
    story = {
        "hits": [{**STORY["hits"][0], "_highlightResult": {"title": {"value": "…"}}}]
    }

    class Decorated(FakeNet):
        async def get(self, url, *, params=None, headers=None, etag=None):
            self.calls.append({"url": url, "params": params, "etag": etag})
            if params["tags"] == "story,author_whoishiring":
                return response(story)
            return response(self.pages[params["page"]])

    net = Decorated([{"hits": [decorated], "nbPages": 1}])
    feed = Feed(id=1, source="hn", key="hn", config={})

    record = await hn.harvest(feed, net)

    assert net.calls[0]["params"]["hitsPerPage"] == hn.DISCOVERY_HITS <= 5
    assert "_highlightResult" not in record.items[0].payload
    assert "_highlightResult" not in record.envelope["story"]
    # Everything the parser reads survives untouched.
    assert record.items[0].payload["comment_text"] == TOP_LEVEL["comment_text"]
    assert record.items[0].payload["story_id"] == TOP_LEVEL["story_id"]
    assert record.envelope["story_title"] == "Ask HN: Who is hiring? (August 2026)"


async def test_numeric_key_pins_a_thread_without_discovery():
    net = FakeNet([{"hits": [TOP_LEVEL], "nbPages": 1}])
    feed = Feed(id=1, source="hn", key="49156683", config={})

    record = await hn.harvest(feed, net)

    assert len(net.calls) == 1
    assert net.calls[0]["params"]["tags"] == "comment,story_49156683"
    assert len(record.items) == 1


async def test_page_cap_marks_the_record_truncated():
    net = FakeNet([{"hits": [TOP_LEVEL], "nbPages": 3}])
    feed = Feed(id=1, source="hn", key="49156683", config={"max_pages": 1})

    record = await hn.harvest(feed, net)

    assert record.truncated


async def test_not_modified_returns_no_items_and_keeps_validators():
    class NotModified(FakeNet):
        async def get(self, url, *, params=None, headers=None, etag=None):
            return Response(url=url, status=304, headers={"etag": '"same"'}, body=b"")

    feed = Feed(id=1, source="hn", key="49156683", config={}, etag='"same"')

    record = await hn.harvest(feed, NotModified([]))

    assert record.http_status == 304
    assert record.items == []
    assert record.etag == '"same"'


async def test_later_page_failure_preserves_earlier_comments_as_partial():
    class LaterPageFailure(FakeNet):
        async def get(self, url, *, params=None, headers=None, etag=None):
            if params.get("page") == 1:
                raise NetError("fixture failure", url=url, status=503)
            return await super().get(url, params=params, headers=headers, etag=etag)

    net = LaterPageFailure([{"hits": [TOP_LEVEL], "nbPages": 2}])
    feed = Feed(id=1, source="hn", key="49156683", config={})

    record = await hn.harvest(feed, net)

    assert [item.external_id for item in record.items] == ["49234667"]
    assert record.truncated


def test_parse_regular_company_first_header():
    feed = Feed(id=1, source="hn", key="hn", config={})
    posting = hn.parse(feed, Item("49234667", TOP_LEVEL), {"story_id": "49156683"})[0]

    assert posting.company_raw == "Symbolic.ai"
    assert posting.title == "Senior Fullstack Engineer"
    assert posting.location_raw == "REMOTE (worldwide)"
    assert posting.workplace_type_raw == "REMOTE (worldwide)"
    assert posting.employment_type == "Full-time"
    assert posting.apply_url == "https://symbolic.ai/careers/senior-fullstack"
    assert posting.url_posting == "https://news.ycombinator.com/item?id=49234667"
    assert posting.published_at is not None
    assert posting.published_at_precision == "exact"


def test_parse_location_first_header_finds_company_and_role():
    feed = Feed(id=1, source="hn", key="hn", config={})
    posting = hn.parse(feed, Item("49232553", LOCATION_FIRST), {})[0]

    assert posting.company_raw == "UMH"
    assert posting.title == "Product Engineer"
    assert posting.location_raw == "Cologne, Germany | ONSITE (part remote)"


def test_parse_uses_honest_fallback_when_no_role_is_stated():
    hit = {
        **TOP_LEVEL,
        "objectID": "1",
        "comment_text": "Acme | REMOTE | Full-time<p>We are growing our engineering team.",
    }
    feed = Feed(id=1, source="hn", key="hn", config={})

    posting = hn.parse(feed, Item("1", hit), {})[0]

    assert posting.company_raw == "Acme"
    assert posting.title == "Acme — hiring"


def test_hn_is_registered_for_harvest_and_replay():
    from xjobscore import sources

    assert sources.get("hn") is hn.harvest
    assert sources.parser("hn") is hn.parse
