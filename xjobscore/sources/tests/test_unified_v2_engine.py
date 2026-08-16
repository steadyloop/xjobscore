"""Source-neutral contract tests for the parallel unified engine."""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone

import pytest

from xjobscore import sources
from xjobscore.net import Response
from xjobscore.records import Fact, Feed, Item, Label, Posting, Target
from xjobscore.sources import unified_v2
from xjobscore.sources.unified_v2 import (
    AdapterSpec,
    CanonicalSpec,
    Each,
    ExtractionSpec,
    Fallback,
    Join,
    Normalize,
    Ref,
    RequestSpec,
    ResponseSpec,
    bind,
)


class FixtureNet:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.get_calls: list[dict] = []
        self.post_calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None, etag=None):
        self.get_calls.append({"url": url, "params": params, "headers": headers, "etag": etag})
        return self.response

    async def post(self, url, *, json=None, data=None, headers=None):
        self.post_calls.append({"url": url, "json": json, "data": data, "headers": headers})
        return self.response


def response(payload, *, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    body = b"" if status == 304 else json.dumps(payload).encode()
    return Response(
        url="https://fixture.invalid/result",
        status=status,
        headers=headers or {},
        body=body,
    )


def feed(*, config: dict | None = None, etag: str | None = None) -> Feed:
    return Feed(
        id=1,
        source="synthetic",
        key="board-key",
        config=config or {},
        etag=etag,
    )


def posting_spec(*, request: RequestSpec | None = None) -> AdapterSpec:
    return AdapterSpec(
        request=request
        or RequestSpec(
            method="GET",
            url="https://fixture.invalid/{key}/jobs",
            params={"page": Ref("page", scope="context")},
            headers={"X-Fixture": Ref("token", scope="context")},
        ),
        response=ResponseSpec(),
        extraction=ExtractionSpec(
            items=Ref("entries", scope="response"),
            external_id=Ref("id", scope="current"),
            envelope={"meta": Ref("meta", scope="response")},
        ),
        outputs=(
            CanonicalSpec(
                kind="posting",
                fields={
                    "title": Ref("title"),
                    "url_posting": Join(("https://jobs.invalid/", Ref("id")), separator=""),
                    "company_raw": Fallback((Ref("company"), Ref("company", scope="envelope"))),
                    "description_html": Normalize(Ref("content"), using=("html_unescape",)),
                    "published_at": Normalize(Ref("published"), using=("iso_datetime",)),
                    "published_at_precision": "exact",
                    "locations_extra": Each(
                        Ref("offices"),
                        Ref("name", scope="current"),
                    ),
                    "source_active": Ref("active"),
                    "source_extra": {
                        "category": Ref("category"),
                        "response_total": Ref("meta.total", scope="envelope"),
                    },
                },
            ),
        ),
    )


async def test_json_pipeline_preserves_raw_items_and_maps_canonical_output():
    payload = {
        "entries": [
            {
                "id": 17,
                "title": "Fixture Engineer",
                "company": "Fixture Co",
                "content": "&lt;p&gt;Build the fixture.&lt;/p&gt;",
                "published": "2026-08-09T12:30:00Z",
                "offices": [{"name": "Remote"}, {"name": None}],
                "active": False,
                "category": "Engineering",
            }
        ],
        "meta": {"total": 1},
    }
    net = FixtureNet(
        response(
            payload,
            headers={
                "etag": '"fixture-etag"',
                "last-modified": "Sun, 09 Aug 2026 12:30:00 GMT",
            },
        )
    )
    fixture_feed = feed(config={"page": 3, "token": "fixture-token"}, etag='"old"')
    adapter = bind(posting_spec())

    record = await adapter.harvest(fixture_feed, net)
    postings = adapter.parse(fixture_feed, record.items[0], record.envelope)

    assert net.get_calls == [
        {
            "url": "https://fixture.invalid/board-key/jobs",
            "params": {"page": 3},
            "headers": {"X-Fixture": "fixture-token"},
            "etag": '"old"',
        }
    ]
    assert record.http_status == 200
    assert record.etag == '"fixture-etag"'
    assert record.envelope == {"meta": {"total": 1}}
    assert record.items[0].external_id == "17"
    assert record.items[0].payload == payload["entries"][0]

    assert len(postings) == 1
    posting = postings[0]
    assert isinstance(posting, Posting)
    assert posting.external_id == "17"
    assert posting.title == "Fixture Engineer"
    assert posting.url_posting == "https://jobs.invalid/17"
    assert posting.company_raw == "Fixture Co"
    assert posting.description_html == "<p>Build the fixture.</p>"
    assert posting.published_at == datetime(2026, 8, 9, 12, 30, tzinfo=timezone.utc)
    assert posting.published_at_precision == "exact"
    assert posting.locations_extra == ["Remote"]
    assert posting.source_active is False
    assert posting.source_extra == {"category": "Engineering", "response_total": 1}


async def test_not_modified_stops_before_response_decoding():
    net = FixtureNet(response(None, status=304, headers={"etag": '"same"'}))
    fixture_feed = feed(etag='"same"')

    record = await bind(posting_spec()).harvest(fixture_feed, net)

    assert record.items == []
    assert record.envelope == {}
    assert record.http_status == 304
    assert record.etag == '"same"'
    assert net.get_calls[0]["etag"] == '"same"'


async def test_extraction_ignores_non_objects_and_rows_without_ids():
    net = FixtureNet(
        response(
            {
                "entries": [None, "not-an-object", {"id": None}, {"id": 0, "title": "Zero"}],
                "meta": None,
            }
        )
    )

    record = await bind(posting_spec()).harvest(feed(config={"page": 0, "token": "x"}), net)

    assert [item.external_id for item in record.items] == ["0"]
    assert record.envelope == {}


async def test_post_request_resolves_body_headers_and_explicit_url_override():
    request = RequestSpec(
        method="POST",
        url="https://fixture.invalid/default",
        json={"cursor": Ref("cursor", scope="context")},
        headers={"Authorization": Ref("token", scope="context")},
        url_override_key="endpoint",
    )
    adapter = bind(posting_spec(request=request))
    net = FixtureNet(response({"entries": [], "meta": {"total": 0}}))

    record = await adapter.harvest(
        feed(
            config={
                "cursor": 4,
                "token": "secret",
                "endpoint": "https://override.invalid/{key}/jobs",
            }
        ),
        net,
    )

    assert record.items == []
    assert net.post_calls == [
        {
            "url": "https://override.invalid/board-key/jobs",
            "json": {"cursor": 4},
            "data": None,
            "headers": {"Authorization": "secret"},
        }
    ]


def test_canonical_mapping_supports_every_existing_output_type():
    spec = AdapterSpec(
        request=RequestSpec(method="GET", url="https://fixture.invalid"),
        response=ResponseSpec(),
        extraction=ExtractionSpec(items=Ref("rows", scope="response"), external_id=Ref("id", scope="current")),
        outputs=(
            CanonicalSpec(
                kind="posting",
                fields={
                    "title": Ref("title"),
                    "url_posting": Ref("url"),
                    "published_at_precision": "exact",
                },
            ),
            CanonicalSpec(kind="target", fields={"source": "next", "key": Ref("target")}),
            CanonicalSpec(kind="label", fields={"name": "fixture", "value": Ref("label")}),
            CanonicalSpec(kind="fact", fields={"company_raw": Ref("company"), "name": "fixture", "value": True}),
        ),
    )
    adapter = bind(spec)
    fixture_item = Item(
        external_id="job-1",
        payload={
            "title": "Fixture",
            "url": "https://fixture.invalid/job-1",
            "target": "board-2",
            "label": "yes",
            "company": "Fixture Co",
        },
    )

    outputs = adapter.parse(feed(), fixture_item, {})

    assert [type(output) for output in outputs] == [Posting, Target, Label, Fact]
    assert outputs[0].published_at_precision == "unknown"
    assert outputs[1].key == "board-2"
    assert outputs[2].external_id == "job-1"
    assert outputs[3].company_raw == "Fixture Co"


def test_invalid_pipeline_shapes_fail_when_bound():
    with pytest.raises(ValueError, match="both json and data"):
        bind(
            posting_spec(
                request=RequestSpec(
                    method="POST",
                    url="https://fixture.invalid",
                    json={},
                    data={},
                )
            )
        )


def test_import_has_no_source_registry_or_source_specs():
    sources_before = dict(sources.SOURCES)
    parsers_before = dict(sources.PARSERS)

    importlib.reload(unified_v2)

    assert sources.SOURCES == sources_before
    assert sources.PARSERS == parsers_before
    assert not hasattr(unified_v2, "SPECS")
    assert not hasattr(unified_v2, "REGISTRY")
