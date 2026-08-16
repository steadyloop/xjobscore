"""Mechanism contracts for every source archetype the unified engine supports.

These are synthetic specifications, not production source registrations.  They
prove that new sources can remain configuration plus narrow semantic logic.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest

from xjobscore.net import NetError, Response
from xjobscore.records import Feed, Posting
from xjobscore.sources.unified_v2 import (
    AllOf,
    AdapterSpec,
    CanonicalSpec,
    DetailSpec,
    DiscoverySpec,
    Each,
    Equal,
    ExtractionSpec,
    Fallback,
    FindAll,
    Get,
    HtmlLinks,
    IfElse,
    Join,
    Matches,
    Normalize,
    PaginationSpec,
    Ref,
    RequestSpec,
    ResponseSpec,
    Truthy,
    bind,
)


def feed(*, config: dict | None = None, etag: str | None = None) -> Feed:
    return Feed(
        id=1,
        source="synthetic-archetype",
        key="fixture-board",
        config=config or {},
        etag=etag,
    )


def response(url: str, payload, *, content_type: str = "application/json") -> Response:
    body = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
    return Response(url=url, status=200, headers={"content-type": content_type}, body=body)


class RouteNet:
    def __init__(self, routes: dict[tuple[str, str], list[Response | Exception]]) -> None:
        self.routes = {key: list(values) for key, values in routes.items()}
        self.get_calls: list[dict] = []
        self.post_calls: list[dict] = []

    def _next(self, method: str, url: str) -> Response:
        value = self.routes[(method, url)].pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def get(self, url, *, params=None, headers=None, etag=None):
        self.get_calls.append({"url": url, "params": params, "headers": headers, "etag": etag})
        return self._next("GET", url)

    async def post(self, url, *, json=None, data=None, headers=None):
        self.post_calls.append({"url": url, "json": json, "data": data, "headers": headers})
        return self._next("POST", url)


def posting(fields: dict[str, object], *, include: object | None = None) -> tuple[CanonicalSpec, ...]:
    return (CanonicalSpec(kind="posting", fields=fields, include=include),)


@pytest.mark.asyncio
async def test_single_json_collection_is_configuration_only():
    url = "https://direct.invalid/jobs.json"
    spec = AdapterSpec(
        request=RequestSpec(method="GET", url=url),
        response=ResponseSpec(format="json"),
        extraction=ExtractionSpec(
            items=Ref("", scope="response"),
            external_id=Ref("id", scope="current"),
        ),
        outputs=posting(
            {
                "title": Ref("position"),
                "url_posting": Fallback((Ref("url"), Join(("https://direct.invalid/jobs/", Ref("id")), separator=""))),
                "company_raw": "Direct Employer",
                "description_html": Ref("overview"),
                "employment_type": Ref("availability"),
                "source_extra": {"team": Ref("team")},
            }
        ),
    )
    net = RouteNet({("GET", url): [response(url, [{"id": 7, "position": "Engineer", "overview": "<p>Build.</p>", "availability": "Internship", "team": "Software"}])]})

    record = await bind(spec).harvest(feed(), net)
    result = bind(spec).parse(feed(), record.items[0], record.envelope)[0]

    assert record.items[0].payload["id"] == 7
    assert result.title == "Engineer"
    assert result.url_posting == "https://direct.invalid/jobs/7"
    assert result.employment_type == "Internship"


@pytest.mark.asyncio
async def test_secondhand_json_uses_conditional_mapping_without_custom_transport():
    url = "https://board.invalid/listings.json"
    closed = Equal(Ref("active"), False)
    spec = AdapterSpec(
        request=RequestSpec(method="GET", url=url),
        response=ResponseSpec(),
        extraction=ExtractionSpec(Ref("", scope="response"), Ref("id", scope="current")),
        outputs=posting(
            {
                "title": Ref("title"),
                "url_posting": Ref("url"),
                "origin_url": Ref("url"),
                "company_raw": Ref("company"),
                "fidelity": "secondhand",
                "state": IfElse(closed, "closed", "open"),
                "closed_evidence": IfElse(closed, "source: active=false"),
                "source_active": Ref("active"),
            }
        ),
    )
    net = RouteNet({("GET", url): [response(url, [{"id": "row-1", "title": "Intern", "url": "https://origin.invalid/1", "company": "Example", "active": False}])]})
    adapter = bind(spec)

    record = await adapter.harvest(feed(), net)
    result = adapter.parse(feed(), record.items[0], {})[0]

    assert result.fidelity == "secondhand"
    assert result.state == "closed"
    assert result.closed_evidence == "source: active=false"
    assert result.description_html is None


@pytest.mark.asyncio
async def test_paginated_post_list_and_get_details_share_one_request_graph():
    list_url = "https://ats.invalid/jobs"
    detail_one = "https://ats.invalid/jobs/one"
    detail_two = "https://ats.invalid/jobs/two"
    spec = AdapterSpec(
        request=RequestSpec(
            method="POST",
            url=list_url,
            json={"limit": 1, "offset": Ref("cursor", scope="context")},
        ),
        response=ResponseSpec(),
        extraction=ExtractionSpec(
            items=Ref("jobPostings", scope="response"),
            external_id=Ref("externalPath", scope="current"),
        ),
        pagination=PaginationSpec(
            start=0,
            step=1,
            total=Ref("total", scope="response"),
            page_size=1,
            max_pages=5,
        ),
        detail=DetailSpec(
            request=RequestSpec(method="GET", url="https://ats.invalid/jobs/{externalPath}", conditional=False),
            response=ResponseSpec(),
            extraction=ExtractionSpec(
                items=Ref("", scope="response"),
                external_id=Fallback((Ref("job.id", scope="current"), Ref("external_id", scope="context"))),
            ),
            retain_list=True,
        ),
        outputs=posting(
            {
                "title": Ref("detail.job.title"),
                "url_posting": Ref("detail.job.url"),
                "company_raw": Ref("detail.company.name"),
                "description_html": Ref("detail.job.description"),
                "source_extra": {"list_external_path": Ref("list.externalPath")},
            }
        ),
    )
    net = RouteNet(
        {
            ("POST", list_url): [
                response(list_url, {"total": 2, "jobPostings": [{"externalPath": "one"}]}),
                response(list_url, {"total": 2, "jobPostings": [{"externalPath": "two"}]}),
            ],
            ("GET", detail_one): [response(detail_one, {"job": {"id": "req-1", "title": "One", "url": detail_one, "description": "First"}, "company": {"name": "Example"}})],
            ("GET", detail_two): [response(detail_two, {"job": {"id": "req-2", "title": "Two", "url": detail_two, "description": "Second"}, "company": {"name": "Example"}})],
        }
    )
    adapter = bind(spec)

    record = await adapter.harvest(feed(), net)
    results = [adapter.parse(feed(), item, {})[0] for item in record.items]

    assert [call["json"]["offset"] for call in net.post_calls] == [0, 1]
    assert [call["url"] for call in net.get_calls] == [detail_one, detail_two]
    assert [item.external_id for item in record.items] == ["req-1", "req-2"]
    assert record.items[0].payload["list"] == {"externalPath": "one"}
    assert record.items[0].payload["detail"]["job"]["id"] == "req-1"
    assert [result.title for result in results] == ["One", "Two"]
    assert results[0].source_extra == {"list_external_path": "one"}
    assert not record.truncated


@pytest.mark.asyncio
async def test_mid_pagination_failure_preserves_items_and_marks_snapshot_truncated():
    url = "https://paged.invalid/jobs"
    spec = AdapterSpec(
        request=RequestSpec(method="GET", url=url, params={"page": Ref("cursor", scope="context")}),
        response=ResponseSpec(),
        extraction=ExtractionSpec(Ref("rows", scope="response"), Ref("id", scope="current")),
        pagination=PaginationSpec(start=1, step=1, total=Ref("total", scope="response"), page_size=1),
        outputs=posting({"title": Ref("title"), "url_posting": Ref("url")}),
    )
    failure = NetError("fixture failure", url=url)
    net = RouteNet({("GET", url): [response(url, {"total": 2, "rows": [{"id": 1, "title": "One", "url": "https://paged.invalid/1"}]}), failure]})

    record = await bind(spec).harvest(feed(), net)

    assert [item.external_id for item in record.items] == ["1"]
    assert record.truncated is True


@pytest.mark.asyncio
async def test_html_link_discovery_and_jsonld_detail_are_declarative():
    board_url = "https://careers.invalid/jobs"
    detail_url = "https://careers.invalid/jobs/42/example"
    board = '<a href="/jobs/42/example">Role</a><a href="/jobs/42/example">duplicate</a>'
    detail = """<html><script type="application/ld+json">
    {"@context":"https://schema.org","@type":"JobPosting","identifier":"42",
     "title":"Platform Engineer","url":"https://careers.invalid/jobs/42/example",
     "description":"<p>Build systems.</p>","hiringOrganization":{"name":"Example"}}
    </script></html>"""
    spec = AdapterSpec(
        request=RequestSpec(method="GET", url=board_url),
        response=ResponseSpec(format="html"),
        extraction=ExtractionSpec(
            items=HtmlLinks(r"/jobs/(?P<id>\d+)/"),
            external_id=Ref("id", scope="current"),
        ),
        detail=DetailSpec(
            request=RequestSpec(method="GET", url="{url}", conditional=False),
            response=ResponseSpec(format="jsonld", schema_type="JobPosting"),
            extraction=ExtractionSpec(
                items=Ref("", scope="response"),
                external_id=Ref("external_id", scope="context"),
            ),
        ),
        outputs=posting(
            {
                "title": Ref("title"),
                "url_posting": Ref("url"),
                "company_raw": Ref("hiringOrganization.name"),
                "description_html": Ref("description"),
            }
        ),
    )
    net = RouteNet(
        {
            ("GET", board_url): [response(board_url, board, content_type="text/html")],
            ("GET", detail_url): [response(detail_url, detail, content_type="text/html")],
        }
    )
    adapter = bind(spec)

    record = await adapter.harvest(feed(), net)
    result = adapter.parse(feed(), record.items[0], {})[0]

    assert len(record.items) == 1
    assert record.items[0].external_id == "42"
    assert result.title == "Platform Engineer"
    assert result.company_raw == "Example"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "items", "external_id", "fields", "expected"),
    [
        (
            """<rss xmlns:g="urn:jobs"><channel><item><guid>rss-1</guid><title>RSS Role</title>
            <link>https://xml.invalid/rss-1</link><description>&amp;lt;p&amp;gt;RSS body&amp;lt;/p&amp;gt;</description>
            <pubDate>Mon, 10 Aug 2026 12:00:00 +0000</pubDate><g:employer>RSS Co</g:employer></item></channel></rss>""",
            FindAll("channel/item", scope="response"),
            Ref("guid", scope="current"),
            {
                "title": Ref("title"),
                "url_posting": Ref("link"),
                "company_raw": Ref("{urn:jobs}employer"),
                "description_html": Normalize(Ref("description"), using=("html_unescape",)),
                "published_at": Normalize(Ref("pubDate"), using=("rfc2822_datetime",)),
                "published_at_precision": "exact",
            },
            ("rss-1", "RSS Role", "https://xml.invalid/rss-1", "RSS Co"),
        ),
        (
            """<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>atom-1</id><title>Atom Role</title>
            <link rel="alternate" href="https://xml.invalid/atom-1"/><content>&lt;p&gt;Atom body&lt;/p&gt;</content>
            <published>2026-08-10T12:00:00+00:00</published></entry></feed>""",
            FindAll("{http://www.w3.org/2005/Atom}entry", scope="response"),
            Ref("{http://www.w3.org/2005/Atom}id", scope="current"),
            {
                "title": Ref("{http://www.w3.org/2005/Atom}title"),
                "url_posting": Ref("{http://www.w3.org/2005/Atom}link/@href"),
                "company_raw": "Atom Co",
                "description_html": Ref("{http://www.w3.org/2005/Atom}content"),
                "published_at": Normalize(Ref("{http://www.w3.org/2005/Atom}published"), using=("iso_datetime",)),
                "published_at_precision": "exact",
            },
            ("atom-1", "Atom Role", "https://xml.invalid/atom-1", "Atom Co"),
        ),
        (
            """<positions><position><id>xml-1</id><name>XML Role</name><subcompany>XML Co</subcompany>
            <office>Berlin</office><additionalOffices><office>Munich</office></additionalOffices>
            <createdAt>2026-08-10T12:00:00+00:00</createdAt></position></positions>""",
            FindAll("position", scope="response"),
            Ref("id", scope="current"),
            {
                "title": Ref("name"),
                "url_posting": Join(("https://xml.invalid/", Ref("id")), separator=""),
                "company_raw": Ref("subcompany"),
                "location_raw": Ref("office"),
                "locations_extra": Each(FindAll("additionalOffices/office"), Ref("", scope="current")),
                "published_at": Normalize(Ref("createdAt"), using=("iso_datetime",)),
                "published_at_precision": "exact",
            },
            ("xml-1", "XML Role", "https://xml.invalid/xml-1", "XML Co"),
        ),
    ],
    ids=("rss", "atom", "bespoke-xml"),
)
async def test_structured_document_archetypes_share_the_same_pipeline(body, items, external_id, fields, expected):
    url = "https://xml.invalid/feed"
    spec = AdapterSpec(
        request=RequestSpec(method="GET", url=url),
        response=ResponseSpec(format="xml"),
        extraction=ExtractionSpec(items=items, external_id=external_id),
        outputs=posting(fields),
    )
    net = RouteNet({("GET", url): [response(url, body, content_type="application/xml")]})
    adapter = bind(spec)

    record = await adapter.harvest(feed(), net)
    result = adapter.parse(feed(), record.items[0], {})[0]

    assert record.items[0].kind == "xml"
    assert (record.items[0].external_id, result.title, result.url_posting, result.company_raw) == expected
    assert result.published_at == datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    if expected[0] == "xml-1":
        assert result.location_raw == "Berlin"
        assert result.locations_extra == ["Munich"]


def _header_fields(value: object) -> dict[str, str | None]:
    if not isinstance(value, str):
        return {"company": None, "title": None, "location": None}
    parts = [part.strip() for part in re.sub(r"<[^>]+>", "", value).split("|")]
    return {
        "company": parts[0] if parts else None,
        "title": parts[1] if len(parts) > 1 else "Hiring",
        "location": parts[2] if len(parts) > 2 else None,
    }


@pytest.mark.asyncio
async def test_discovery_pagination_filtering_and_free_text_use_pipeline_plus_one_normalizer():
    api = "https://community.invalid/search"
    discovery = {"hits": [
        {"objectID": "old", "author": "someone", "title": "Monthly hiring (old)"},
        {"objectID": "story-9", "author": "publisher", "title": "Monthly hiring (August)"},
    ]}
    first_page = {
        "nbPages": 2,
        "hits": [
            {"objectID": "comment-1", "parent_id": "story-9", "comment_text": "Acme | Software Intern | Remote"},
            {"objectID": "nested", "parent_id": "comment-1", "comment_text": "ignore me"},
        ],
    }
    second_page = {
        "nbPages": 2,
        "hits": [
            {"objectID": "comment-1", "parent_id": "story-9", "comment_text": "duplicate"},
            {"objectID": "comment-2", "parent_id": "story-9", "comment_text": "Beta | Data Engineer | NYC"},
        ],
    }
    header = Normalize(Ref("comment_text"), using=("header_fields",))
    spec = AdapterSpec(
        discovery=DiscoverySpec(
            request=RequestSpec(method="GET", url=api, params={"kind": "thread"}, conditional=False),
            response=ResponseSpec(),
            items=Ref("hits", scope="response"),
            include=AllOf((
                Equal(Ref("author", scope="current"), "publisher"),
                Matches(Ref("title", scope="current"), r"^Monthly hiring"),
            )),
            values={
                "story_id": Ref("objectID", scope="current"),
                "story": Ref("", scope="current"),
            },
        ),
        request=RequestSpec(
            method="GET",
            url=api,
            params={
                "story": Ref("story_id", scope="context"),
                "page": Ref("cursor", scope="context"),
            },
        ),
        response=ResponseSpec(),
        extraction=ExtractionSpec(
            items=Ref("hits", scope="response"),
            external_id=Ref("objectID", scope="current"),
            include=AllOf((
                Equal(Ref("parent_id", scope="current"), Ref("story_id", scope="context")),
                Truthy(Ref("comment_text", scope="current")),
            )),
            envelope={"story": Ref("story", scope="context")},
        ),
        pagination=PaginationSpec(
            start=0,
            step=1,
            page_count=Ref("nbPages", scope="response"),
            max_pages=10,
        ),
        outputs=posting(
            {
                "title": Get(header, "title"),
                "url_posting": Join(("https://community.invalid/item/", Ref("external_id", scope="context")), separator=""),
                "company_raw": Get(header, "company"),
                "location_raw": Get(header, "location"),
                "description_html": Ref("comment_text"),
            },
            include=Truthy(Get(header, "company")),
        ),
    )
    net = RouteNet({("GET", api): [response(api, discovery), response(api, first_page), response(api, second_page)]})
    adapter = bind(spec, normalizers={"header_fields": _header_fields})

    record = await adapter.harvest(feed(etag='"old"'), net)
    results = [adapter.parse(feed(), item, record.envelope)[0] for item in record.items]

    assert [item.external_id for item in record.items] == ["comment-1", "comment-2"]
    assert [result.company_raw for result in results] == ["Acme", "Beta"]
    assert [result.title for result in results] == ["Software Intern", "Data Engineer"]
    assert record.envelope["story"]["objectID"] == "story-9"
    assert net.get_calls[0]["etag"] is None
    assert net.get_calls[1]["etag"] == '"old"'
    assert net.get_calls[2]["etag"] is None
    assert not record.truncated
