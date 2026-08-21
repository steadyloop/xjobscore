"""`sources.reusing`: the wiring between `run.poll`'s prior-item read and the
json-api engine's revision-checked detail reuse.

The engine's own reuse semantics (cache hit/miss, dedupe, truncation) are
covered by `test_json_api_detail_concurrency.py`. This file is only about the
seam: does `reusing` fall back to plain `get` when a source hasn't opted in
or there is nothing to reuse yet, and does its `options` actually reach
`json_api.harvest` and skip a detail request end to end.
"""

from __future__ import annotations

import json as json_module

import pytest

from xjobscore.net import Response
from xjobscore.records import Feed, Item
from xjobscore import sources
from xjobscore.sources import json_api

pytestmark = pytest.mark.asyncio

CONFIG = {
    "list": {
        "url": "https://board.invalid/postings",
        "items": "content",
        "id": "@id",
    },
    "detail": {
        "url": "https://board.invalid/postings/{id}",
        "id": "@id",
        "dedupe": "@id",
    },
    "fields": {"title": "@title"},
}


def _response(url: str, body: dict) -> Response:
    return Response(url=url, status=200, headers={}, body=json_module.dumps(body).encode())


class FakeNet:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.detail_calls: list[str] = []

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        if url.endswith("/postings"):
            return _response(url, {"content": self.rows})
        detail_id = url.rsplit("/", 1)[1]
        self.detail_calls.append(detail_id)
        row = next(r for r in self.rows if r["id"] == detail_id)
        return _response(url, {**row, "body": f"fresh {detail_id}"})


@pytest.fixture(autouse=True)
def fake_source(monkeypatch):
    """A throwaway JSON-API source opted into revision reuse, so the fallback
    branches (unopted source, no priors) have a real opted-in source to
    contrast against without touching the real smartrecruiters config."""
    monkeypatch.setitem(json_api.SPECS, "reuse-test", CONFIG)
    # `SOURCES` is built once at import time from `json_api.SPECS`, so
    # patching `SPECS` alone leaves the registry's own lookup (`get`, used by
    # `reusing`'s fallback) unaware of this source -- mirror the same binding
    # `sources/__init__.py` does for every real json-api entry.
    monkeypatch.setitem(sources.SOURCES, "reuse-test", json_api.harvester("reuse-test"))
    monkeypatch.setitem(sources.REVISION_FIELDS, "reuse-test", ("rev",))
    yield


async def test_unopted_source_falls_back_to_plain_get():
    prior = {"x": Item("x", {"rev": "1"})}
    assert sources.reusing("greenhouse", prior) is sources.get("greenhouse")


async def test_no_prior_items_falls_back_to_plain_get():
    assert sources.reusing("reuse-test", None) is sources.get("reuse-test")


async def test_revision_match_skips_its_detail_request():
    rows = [{"id": "1", "rev": "same"}, {"id": "2", "rev": "changed"}]
    prior = {
        "1": Item("1", {"id": "1", "rev": "same", "body": "cached 1"}),
        "2": Item("2", {"id": "2", "rev": "old", "body": "cached 2"}),
    }
    net = FakeNet(rows)
    feed = Feed(id=1, source="reuse-test", key="board", config={})

    record = await sources.reusing("reuse-test", prior)(feed, net)

    # Only the changed row costs a request; the matching one is served from
    # `prior` verbatim -- this is the whole point of the wiring.
    assert net.detail_calls == ["2"]
    payloads = {item.external_id: item.payload["body"] for item in record.items}
    assert payloads == {"1": "cached 1", "2": "fresh 2"}
