"""Explicit ATS closure and incomplete-detail protection."""

import json

import pytest

from xjobscore import silver
from xjobscore.net import Response
from xjobscore.records import Feed, Item
from xjobscore.sources import json_api


@pytest.mark.parametrize("source,field,opened,closed", [
    ("ashby", "isListed", True, False),
    ("bullhorn", "isOpen", True, False),
    ("smartrecruiters", "active", True, False),
    ("recruitee", "status", "published", "closed"),
    ("unstop", "status", "LIVE", "CLOSED"),
    ("bamboohr", "jobOpeningStatus", "Open", "Filled"),
])
def test_ats_status_requires_an_explicit_signal(source, field, opened, closed):
    feed = Feed(id=1, source=source, key="example")
    for value, expected in ((None, None), (opened, True), (closed, False)):
        posting, = json_api.parse(source, feed, Item("job", {field: value}), {})
        assert posting.source_active is expected
        assert silver._projection(posting)["status"] == ("closed" if expected is False else "active")


@pytest.mark.parametrize("source,field", [
    ("recruitee", "status"),
    ("unstop", "status"),
    ("bamboohr", "jobOpeningStatus"),
])
@pytest.mark.parametrize("value", ["", "unknown", "Draft", "On Hold"])
def test_unrecognized_or_nonterminal_status_does_not_close(source, field, value):
    feed = Feed(id=1, source=source, key="example")
    posting, = json_api.parse(source, feed, Item("job", {field: value}), {})
    assert posting.source_active is None
    assert silver._projection(posting)["status"] == "active"


@pytest.mark.parametrize("batch", [None, 2])
async def test_missing_detail_does_not_claim_a_complete_listing(monkeypatch, batch):
    config = {
        "list": {"url": "https://example.test/list", "items": "jobs", "id": "@id"},
        "detail": {
            "url": "https://example.test/{id}", "batch": batch,
            "payload": "jobs" if batch else "job",
        },
    }
    monkeypatch.setitem(json_api.CONFIGS, "missing-detail", config)

    class Net:
        async def get(self, url, **kwargs):
            if url.endswith("/list"):
                body = {"jobs": [{"id": "kept"}, {"id": "gone"}]}
            elif batch:
                body = {"jobs": [{"id": "kept"}]}
            else:
                body = {"job": {"id": "kept"} if url.endswith("/kept") else None}
            return Response(url=url, status=200, headers={}, body=json.dumps(body).encode())

    record = await json_api.harvest("missing-detail", Feed(id=1, source="missing-detail", key="test"), Net())
    assert record.truncated is True
    assert [item.external_id for item in record.items] == ["kept"]
