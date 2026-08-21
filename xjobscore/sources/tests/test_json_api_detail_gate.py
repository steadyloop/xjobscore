"""`detail.only` -- fetch detail pages for the rows a source actually wants.

A detail board pays one request per row, which is ~91% of a large run. A source
that only cares about some of those rows can gate the leg with an ordinary field
spec evaluated against the list row, so the rules read exactly like every other
config expression.

A gate is not a cap: a skipped row is still a posting, emitted from its list
payload the way a source with no detail leg emits every row. That is why it
reports `detail_skipped_count` and deliberately never sets `truncated` -- a
targeted harvest fetched precisely what it meant to.
"""

from __future__ import annotations

import json as json_module

import pytest

from xjobscore.net import Response
from xjobscore.records import Feed
from xjobscore.sources import json_api


def _response(url: str, body: dict) -> Response:
    return Response(url=url, status=200, headers={}, body=json_module.dumps(body).encode())


ROWS = [
    {"id": 1, "title": "Software Engineering Intern"},
    {"id": 2, "title": "Staff Software Engineer"},
    {"id": 3, "title": "Metrology Co-Op"},
    {"id": 4, "title": "International Tax Manager"},  # must not match bare /intern/
]


def _config(only=None) -> dict:
    detail = {"url": "https://board.invalid/postings/{id}", "id": "@id", "dedupe": "@id"}
    if only is not None:
        detail["only"] = only
    return {
        "list": {"url": "https://board.invalid/postings", "items": "content", "id": "@id"},
        "detail": detail,
        "fields": {"title": "@title"},
    }


class GateNet:
    def __init__(self, rows=ROWS):
        self.rows = rows
        self.detail_ids: list[int] = []

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        if url.endswith("/postings"):
            return _response(url, {"content": self.rows})
        detail_id = int(url.rsplit("/", 1)[1])
        self.detail_ids.append(detail_id)
        return _response(url, {"id": detail_id, "title": f"detail {detail_id}"})


async def _harvest(monkeypatch, net, only=None, rows=ROWS):
    monkeypatch.setitem(json_api.CONFIGS, "gate-test", _config(only))
    net.rows = rows
    feed = Feed(id=1, source="gate-test", key="board")
    return await json_api.harvest("gate-test", feed, net)


# The real shape: an ordered `classify` table, the same form every job_type rule
# in the catalog already uses.
INTERN_ONLY = {
    "classify": [
        {"from": "@title", "re": "intern(s|ship|ships)?", "is": True},
        {"from": "@title", "re": "co-?ops?", "is": True},
    ],
    "default": False,
}


@pytest.mark.asyncio
async def test_gate_fetches_detail_only_for_matching_rows(monkeypatch):
    net = GateNet()
    record = await _harvest(monkeypatch, net, INTERN_ONLY)

    # Only the intern and the co-op cost a request. "International" is rejected
    # by the word boundaries, which is the documented false positive.
    assert net.detail_ids == [1, 3]
    assert record.detail_fetched_count == 2
    assert record.detail_skipped_count == 2


@pytest.mark.asyncio
async def test_a_gated_row_is_still_a_posting_carrying_its_list_payload(monkeypatch):
    net = GateNet()
    record = await _harvest(monkeypatch, net, INTERN_ONLY)

    # Every row survives -- the gate spends requests, it does not drop postings.
    assert {item.external_id for item in record.items} == {"1", "2", "3", "4"}
    by_id = {item.external_id: item.payload for item in record.items}
    # Fetched rows carry the detail body...
    assert by_id["1"]["title"] == "detail 1"
    # ...skipped rows carry the list row verbatim.
    assert by_id["2"]["title"] == "Staff Software Engineer"


@pytest.mark.asyncio
async def test_a_gate_is_not_a_shortfall_so_it_never_truncates(monkeypatch):
    """`truncated` means "we wanted more and stopped". A gate wanted exactly
    this much, so folding it in would report every targeted run as partial."""
    net = GateNet()
    record = await _harvest(monkeypatch, net, INTERN_ONLY)

    assert record.truncated is False
    assert record.enumerated_item_count == 4


@pytest.mark.asyncio
async def test_a_gate_matching_nothing_issues_no_detail_request(monkeypatch):
    net = GateNet()
    record = await _harvest(monkeypatch, net, {"classify": [
        {"from": "@title", "re": "quantum-basket-weaving", "is": True},
    ], "default": False})

    assert net.detail_ids == []
    assert record.detail_skipped_count == 4
    assert record.truncated is False
    assert len(record.items) == 4


@pytest.mark.asyncio
async def test_the_gate_accepts_any_field_spec_not_just_classify(monkeypatch):
    """`only` goes through `_value`, so it is uniform with the rest of the
    config language rather than a bespoke mini-syntax."""
    net = GateNet()
    await _harvest(monkeypatch, net, {"eq": ["@title", "Staff Software Engineer"]})

    assert net.detail_ids == [2]


@pytest.mark.asyncio
async def test_a_source_without_a_gate_is_untouched(monkeypatch):
    """The key is absent from every config in the catalog today, so the loop
    must behave exactly as it did before `only` existed."""
    net = GateNet()
    record = await _harvest(monkeypatch, net, None)

    assert net.detail_ids == [1, 2, 3, 4]
    assert record.detail_fetched_count == 4
    assert record.detail_skipped_count == 0
    assert record.truncated is False


@pytest.mark.asyncio
async def test_dedupe_still_collapses_rows_before_the_gate_counts_them(monkeypatch):
    """Two list rows pointing at one detail page are one row, not two skips."""
    net = GateNet()
    record = await _harvest(
        monkeypatch, net, INTERN_ONLY,
        rows=[
            {"id": 7, "title": "Staff Engineer"},
            {"id": 7, "title": "Staff Engineer"},
            {"id": 8, "title": "Data Intern"},
        ],
    )

    assert net.detail_ids == [8]
    assert record.detail_skipped_count == 1
