"""Oracle Recruiting Cloud -- paginated list + batched detail fetch.

Oracle's Candidate Experience API caps `limit` at 200 and requires it (with
`offset`) inside the `finder=` value, not as a top-level query param, so it
uses the engine's generic `page` machinery same as any other paginated board.
Its detail endpoint additionally batches: `Id="a,b,c"` returns several
requisitions from one request, verified live up to ~360 ids. That is new
engine behavior (`detail.batch` / `detail.match` in `json-api.py`), so this
file exercises it directly rather than through `SequencedFixtureNet`, which
only knows a single list call followed by a single detail call.

`RoutedFixtureNet` dispatches by URL instead of by call order: `finder=
findReqs` calls serve `list_bodies[offset // 200]` (pages 2+ are fetched
concurrently, so arrival order is not guaranteed); anything else is a detail
batch, whose `Id="..."` ids are parsed out of the URL and handed to
`detail_responder`. `fail_at` counts calls in issue order, which the engine
keeps deterministic (requests are recorded before the fake first awaits).
`delays` maps a URL substring to seconds so a test can force out-of-order
completion; the always-taken `sleep(0)` makes overlap observable in
`max_active` either way.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from xjobscore.net import NetError, Response
from xjobscore.records import Feed
from xjobscore.sources import json_api

FEED_KEY = "eeho.fa.us2/CX_45001"


class RoutedFixtureNet:
    def __init__(
        self,
        list_bodies,
        detail_responder,
        *,
        fail_at: int | None = None,
        delays: dict[str, float] | None = None,
        page_step: int = 200,
    ) -> None:
        self.list_bodies = list(list_bodies)
        self.detail_responder = detail_responder
        self.fail_at = fail_at
        self.delays = delays or {}
        self.page_step = page_step
        self.get_calls: list[dict] = []
        self.active = 0
        self.max_active = 0

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        self.get_calls.append(
            {"url": url, "params": params, "headers": headers, "etag": etag, "concurrency": concurrency}
        )
        if self.fail_at is not None and len(self.get_calls) == self.fail_at:
            raise NetError("boom", url=url)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(next((s for needle, s in self.delays.items() if needle in url), 0))
        self.active -= 1
        if "finder=findReqs" in url:
            offset = int(re.search(r"offset=(\d+)", url).group(1))
            body = self.list_bodies[offset // self.page_step]
        else:
            ids = re.search(r'Id="([^"]*)"', url).group(1).split(",")
            body = self.detail_responder(ids)
        return Response(url=url, status=200, headers={}, body=json.dumps(body).encode())


def _row(id_: str, *, title: str = "Title") -> dict:
    return {"Id": id_, "Title": title, "PostedDate": "2026-07-30"}


def _list_body(rows: list[dict], *, total: int) -> dict:
    return {"items": [{"TotalJobsCount": total, "requisitionList": rows}]}


def _detail_entry(id_: str, *, description: str = "<p>desc</p>") -> dict:
    # Deliberately no "Title" here: merge must let the list row's Title
    # survive when the detail entry doesn't repeat it.
    return {"Id": id_, "ExternalDescriptionStr": description, "PrimaryLocation": "Remote"}


def _detail_body(entries: list[dict]) -> dict:
    return {"items": entries}


async def _harvest(net: RoutedFixtureNet, *, config: dict | None = None):
    feed = Feed(id=1, source="oracle", key=FEED_KEY, config=config or {})
    return feed, await json_api.harvest("oracle", feed, net)


def _list_calls(net: RoutedFixtureNet) -> list[dict]:
    return [c for c in net.get_calls if "finder=findReqs" in c["url"]]


def _detail_calls(net: RoutedFixtureNet) -> list[dict]:
    return [c for c in net.get_calls if "finder=ById" in c["url"]]


async def test_oracle_paginates_by_total_jobs_count_not_hasmore(monkeypatch):
    """`hasMore` describes Oracle's outer 1-item collection, not the board --
    pagination has to be driven off TotalJobsCount instead."""
    # Shrink Oracle's 200-row pages to 2 so the fixtures stay readable. It is
    # an offset board, so a page's worth of rows is both the cursor stride
    # (`step`) and the page length (`size`) -- patching one without the other
    # would describe a board that does not exist.
    monkeypatch.setitem(json_api.CONFIGS["oracle"]["list"]["page"], "step", 2)
    monkeypatch.setitem(json_api.CONFIGS["oracle"]["list"]["page"], "size", 2)
    net = RoutedFixtureNet(
        [
            _list_body([_row("1"), _row("2")], total=5),
            _list_body([_row("3"), _row("4")], total=5),
            _list_body([_row("5")], total=5),
        ],
        detail_responder=lambda ids: _detail_body([_detail_entry(i) for i in ids]),
        page_step=2,
    )
    feed = Feed(id=1, source="oracle", key=FEED_KEY, config={}, etag="cached-etag")
    record = await json_api.harvest("oracle", feed, net)

    list_calls = _list_calls(net)
    assert len(list_calls) == 3
    assert "offset=0," in list_calls[0]["url"]
    assert "offset=2," in list_calls[1]["url"]
    assert "offset=4," in list_calls[2]["url"]
    # Conditional GET only applies to the first request of a poll.
    assert list_calls[0]["etag"] == "cached-etag"
    assert list_calls[1]["etag"] is None
    assert list_calls[2]["etag"] is None
    assert len(record.items) == 5
    assert record.truncated is False
    assert record.envelope["TotalJobsCount"] == 5


async def test_oracle_matches_batched_detail_response_by_id_not_position():
    rows = [_row(str(i)) for i in (10, 20, 30)]
    net = RoutedFixtureNet(
        [_list_body(rows, total=3)],
        detail_responder=lambda ids: _detail_body(
            [_detail_entry(i, description=f"<p>{i}</p>") for i in reversed(ids)]
        ),
    )
    _, record = await _harvest(net)

    detail_calls = _detail_calls(net)
    assert len(detail_calls) == 1
    assert 'Id="10,20,30"' in detail_calls[0]["url"]

    by_id = {item.external_id: item.payload for item in record.items}
    assert set(by_id) == {"10", "20", "30"}
    assert by_id["10"]["ExternalDescriptionStr"] == "<p>10</p>"
    assert by_id["30"]["ExternalDescriptionStr"] == "<p>30</p>"
    # merge: list-row fields (Title) survive alongside detail fields.
    assert all(payload["Title"] == "Title" for payload in by_id.values())


async def test_oracle_detail_chunks_at_the_configured_batch_size(monkeypatch):
    monkeypatch.setitem(json_api.CONFIGS["oracle"]["detail"], "batch", 2)
    rows = [_row(str(i)) for i in (1, 2, 3, 4, 5)]
    net = RoutedFixtureNet(
        [_list_body(rows, total=5)],
        detail_responder=lambda ids: _detail_body([_detail_entry(i) for i in ids]),
    )
    _, record = await _harvest(net)

    detail_calls = _detail_calls(net)
    assert len(detail_calls) == 3
    ids_per_call = [re.search(r'Id="([^"]*)"', c["url"]).group(1).split(",") for c in detail_calls]
    assert ids_per_call == [["1", "2"], ["3", "4"], ["5"]]
    assert len(record.items) == 5


async def test_oracle_partial_batch_failure_returns_completed_chunks_truncated(monkeypatch):
    monkeypatch.setitem(json_api.CONFIGS["oracle"]["detail"], "batch", 2)
    rows = [_row(str(i)) for i in (1, 2, 3, 4)]
    net = RoutedFixtureNet(
        [_list_body(rows, total=4)],
        detail_responder=lambda ids: _detail_body([_detail_entry(i) for i in ids]),
        fail_at=3,  # call 1 = list, call 2 = detail batch [1,2] ok, call 3 = detail batch [3,4] fails
    )
    _, record = await _harvest(net)

    assert record.truncated is True
    assert {item.external_id for item in record.items} == {"1", "2"}


async def test_oracle_dedupes_ids_repeated_across_pages():
    net = RoutedFixtureNet(
        [
            _list_body([_row("1"), _row("2")], total=3),
            _list_body([_row("2"), _row("3")], total=3),  # "2" repeated at the page boundary
        ],
        detail_responder=lambda ids: _detail_body([_detail_entry(i) for i in ids]),
    )
    _, record = await _harvest(net)

    detail_calls = _detail_calls(net)
    assert len(detail_calls) == 1
    assert 'Id="1,2,3"' in detail_calls[0]["url"]
    assert len(record.items) == 3


async def test_oracle_skips_rows_missing_from_the_detail_response():
    """A requisition withdrawn between the list and detail pass simply isn't
    in the batch response. Incomplete details must not establish disappearance."""
    rows = [_row("1"), _row("2"), _row("3")]
    net = RoutedFixtureNet(
        [_list_body(rows, total=3)],
        detail_responder=lambda ids: _detail_body([_detail_entry(i) for i in ids if i != "2"]),
    )
    _, record = await _harvest(net)

    assert {item.external_id for item in record.items} == {"1", "3"}
    assert record.truncated is True


async def test_oracle_pages_fetch_concurrently_and_assemble_in_offset_order(monkeypatch):
    """Pages 2+ go out together; a slow middle page must not reorder rows."""
    # Shrink Oracle's 200-row pages to 2 so the fixtures stay readable. It is
    # an offset board, so a page's worth of rows is both the cursor stride
    # (`step`) and the page length (`size`) -- patching one without the other
    # would describe a board that does not exist.
    monkeypatch.setitem(json_api.CONFIGS["oracle"]["list"]["page"], "step", 2)
    monkeypatch.setitem(json_api.CONFIGS["oracle"]["list"]["page"], "size", 2)
    net = RoutedFixtureNet(
        [
            _list_body([_row("1"), _row("2")], total=5),
            _list_body([_row("3"), _row("4")], total=5),
            _list_body([_row("5")], total=5),
        ],
        detail_responder=lambda ids: _detail_body([_detail_entry(i) for i in ids]),
        delays={"offset=2,": 0.05},  # page 2 completes after page 3
        page_step=2,
    )
    _, record = await _harvest(net)

    assert net.max_active >= 2  # pages 2 and 3 were in flight together
    assert [item.external_id for item in record.items] == ["1", "2", "3", "4", "5"]
    assert record.truncated is False


async def test_oracle_requests_carry_the_configured_concurrency_to_net():
    """The cap itself is enforced by Net's per-host semaphore; the engine's
    contract is passing the configured value through on every fanned-out
    request. The first list request stays on the throttled path."""
    net = RoutedFixtureNet(
        [
            _list_body([_row("1"), _row("2")], total=3),
            _list_body([_row("3")], total=3),
        ],
        detail_responder=lambda ids: _detail_body([_detail_entry(i) for i in ids]),
    )
    await _harvest(net)

    first_list, *rest = net.get_calls
    assert "offset=0," in first_list["url"] and first_list["concurrency"] == 1
    assert rest  # a fanned-out page and at least one detail batch
    assert all(call["concurrency"] == 4 for call in rest)


async def test_oracle_failed_page_mid_fanout_truncates_with_no_items(monkeypatch):
    # Shrink Oracle's 200-row pages to 2 so the fixtures stay readable. It is
    # an offset board, so a page's worth of rows is both the cursor stride
    # (`step`) and the page length (`size`) -- patching one without the other
    # would describe a board that does not exist.
    monkeypatch.setitem(json_api.CONFIGS["oracle"]["list"]["page"], "step", 2)
    monkeypatch.setitem(json_api.CONFIGS["oracle"]["list"]["page"], "size", 2)
    net = RoutedFixtureNet(
        [
            _list_body([_row("1"), _row("2")], total=5),
            _list_body([_row("3"), _row("4")], total=5),
            _list_body([_row("5")], total=5),
        ],
        detail_responder=lambda ids: _detail_body([_detail_entry(i) for i in ids]),
        fail_at=2,  # call 1 = page offset 0; calls 2-3 = fanned pages
        page_step=2,
    )
    _, record = await _harvest(net)

    assert record.truncated is True
    assert record.items == []  # detail pass still owed, list-only rows dropped


async def test_oracle_detail_chunks_fetch_concurrently(monkeypatch):
    monkeypatch.setitem(json_api.CONFIGS["oracle"]["detail"], "batch", 2)
    rows = [_row(str(i)) for i in (1, 2, 3, 4)]
    net = RoutedFixtureNet(
        [_list_body(rows, total=4)],
        detail_responder=lambda ids: _detail_body([_detail_entry(i) for i in ids]),
        delays={"finder=ById": 0.02},
    )
    _, record = await _harvest(net)

    assert net.max_active >= 2  # both chunks in flight together
    assert {item.external_id for item in record.items} == {"1", "2", "3", "4"}


async def test_oracle_parse_maps_merged_list_and_detail_fields():
    rows = [_row("500", title="Staff Engineer")]
    net = RoutedFixtureNet(
        [_list_body(rows, total=1)],
        detail_responder=lambda ids: _detail_body(
            [_detail_entry(i, description="<p>Build things.</p>") for i in ids]
        ),
    )
    feed, record = await _harvest(net, config={"company_raw": "Oracle"})
    assert len(record.items) == 1

    posting = json_api.parse("oracle", feed, record.items[0], record.envelope)[0]
    assert posting.title == "Staff Engineer"
    assert posting.description_html == "<p>Build things.</p>"
    assert posting.requisition_id == "500"
    assert posting.url_posting == (
        "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_45001/job/500/"
    )
