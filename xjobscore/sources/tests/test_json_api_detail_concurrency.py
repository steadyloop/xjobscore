"""Opt-in concurrent detail fetches in the json-api engine.

A `detail` config with `"concurrency": N` fires every per-item request at
once, bounded by Net's per-host semaphore. Items must come back in list-row
order regardless of completion order, a failed detail costs only its own row
and flags `truncated`, dedupe still fetches each id once, and configs
without the key keep the serial loop -- including with fake nets whose `get`
does not accept a `concurrency` kwarg.
"""

from __future__ import annotations

import asyncio
import json as json_module

import pytest

from xjobscore.net import NetError, Response
from xjobscore.records import Feed, Item
from xjobscore.sources import json_api


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
        "concurrency": 3,
    },
    "fields": {"title": "@title"},
}


def _response(url: str, body: dict) -> Response:
    return Response(url=url, status=200, headers={}, body=json_module.dumps(body).encode())


class DetailNet:
    """Serves one list page of `ids`, then a detail per id, watching overlap."""

    def __init__(self, ids, *, fail_id=None):
        self.ids = ids
        self.fail_id = fail_id
        self.calls: list[dict] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        self.calls.append({"url": url, "concurrency": concurrency})
        if url.endswith("/postings"):
            return _response(url, {"content": [{"id": i} for i in self.ids]})
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.01)  # let gathered peers overlap
        self.in_flight -= 1
        detail_id = int(url.rsplit("/", 1)[1])
        if detail_id == self.fail_id:
            raise NetError("HTTP 500", url=url, status=500)
        return _response(url, {"id": detail_id, "title": f"job {detail_id}"})


class SerialNet(DetailNet):
    """FixtureNet's signature: no `concurrency` kwarg at all."""

    async def get(self, url, *, params=None, headers=None, etag=None):
        return await super().get(url, params=params, headers=headers, etag=etag)


async def harvest(net, config=CONFIG, *, options=None):
    feed = Feed(id=1, source="dtest", key="dtest", config={})
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(json_api.CONFIGS, "dtest", config)
        return await json_api.harvest("dtest", feed, net, options=options)


def detail_calls(net):
    return [call for call in net.calls if not call["url"].endswith("/postings")]


async def test_detail_fetches_overlap_and_preserve_row_order():
    net = DetailNet(ids=range(6))
    record = await harvest(net)

    assert record.truncated is False
    assert [item.external_id for item in record.items] == [str(i) for i in range(6)]
    assert net.calls[0]["concurrency"] == 1
    assert all(call["concurrency"] == 3 for call in detail_calls(net))
    assert net.max_in_flight >= 2


async def test_failed_detail_skips_its_row_and_keeps_the_rest():
    net = DetailNet(ids=range(6), fail_id=3)
    record = await harvest(net)

    assert record.truncated is True
    # The fan-out issued and paid for all six requests before any failed, so
    # dropping the survivors would discard work already done. Only row 3 is
    # lost; `truncated` is how the shortfall stays visible.
    assert [item.external_id for item in record.items] == ["0", "1", "2", "4", "5"]


async def test_dedupe_fetches_each_id_once():
    net = DetailNet(ids=[0, 1, 0, 2, 1])
    record = await harvest(net)

    assert [item.external_id for item in record.items] == ["0", "1", "2"]
    assert len(detail_calls(net)) == 3


async def test_default_stays_serial_and_works_without_concurrency_kwarg():
    net = SerialNet(ids=range(4))
    config = {**CONFIG, "detail": {k: v for k, v in CONFIG["detail"].items() if k != "concurrency"}}
    record = await harvest(net, config)

    assert record.truncated is False
    assert [item.external_id for item in record.items] == [str(i) for i in range(4)]
    assert net.max_in_flight == 1


# -- detail.max ------------------------------------------------------------
#
# The counterpart of page.max for the detail leg. A detail board pays one
# request per row, so without a cap the only bound on a poll is the vendor's
# row count.


async def test_detail_max_caps_requests_and_reports_truncated():
    net = DetailNet(ids=range(10))
    config = {**CONFIG, "detail": {**CONFIG["detail"], "max": 4}}
    record = await harvest(net, config)

    assert len(detail_calls(net)) == 4
    assert [item.external_id for item in record.items] == ["0", "1", "2", "3"]
    # A capped poll must never read as a complete one.
    assert record.truncated is True


async def test_detail_max_above_the_row_count_changes_nothing():
    net = DetailNet(ids=range(3))
    config = {**CONFIG, "detail": {**CONFIG["detail"], "max": 50}}
    record = await harvest(net, config)

    assert len(detail_calls(net)) == 3
    assert record.truncated is False


async def test_detail_without_max_is_unbounded_as_before():
    net = DetailNet(ids=range(7))
    record = await harvest(net)

    assert len(detail_calls(net)) == 7
    assert record.truncated is False


# -- console-only revision reuse ---------------------------------------


class RevisionNet(DetailNet):
    def __init__(self, rows, *, fail_id=None):
        super().__init__([], fail_id=fail_id)
        self.rows = rows

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        if url.endswith("/postings"):
            self.calls.append({"url": url, "concurrency": concurrency})
            return _response(url, {"content": self.rows})
        detail_id = int(url.rsplit("/", 1)[1])
        row = next(row for row in self.rows if row["id"] == detail_id)
        response = await super().get(
            url, params=params, headers=headers, etag=etag, concurrency=concurrency
        )
        payload = {**response.json(), **row, "body": f"fresh {detail_id}"}
        return _response(url, payload)


def reuse_options(prior_items):
    return json_api.JsonApiHarvestOptions(
        detail_concurrency=8,
        revision_fields=("jobAdId", "releasedDate"),
        prior_items=prior_items,
    )


async def test_revision_matches_reuse_verbatim_and_only_misses_hit_detail():
    rows = [
        {"id": 0, "jobAdId": "ad-0", "releasedDate": "2026-08-01"},
        {"id": 1, "jobAdId": "ad-1-new", "releasedDate": "2026-08-02"},
        {"id": 2, "jobAdId": "ad-2", "releasedDate": None},
        {"id": 4, "jobAdId": "ad-4", "releasedDate": "2026-08-04"},
    ]
    prior = {
        "0": Item("0", {**rows[0], "body": "cached 0"}),
        "1": Item("1", {**rows[1], "jobAdId": "ad-1-old", "body": "cached 1"}),
        "2": Item("2", {**rows[2], "body": "cached 2"}),
        # A prior posting absent from the current active list must disappear.
        "3": Item("3", {"id": 3, "jobAdId": "ad-3", "releasedDate": "2026-07-01"}),
    }
    net = RevisionNet(rows)

    record = await harvest(net, options=reuse_options(prior))

    assert record.truncated is False
    assert [item.external_id for item in record.items] == ["0", "1", "2", "4"]
    assert record.items[0] is prior["0"]
    assert record.items[0].payload["body"] == "cached 0"
    assert record.reused_item_count == 1
    assert [call["url"].rsplit("/", 1)[1] for call in detail_calls(net)] == ["1", "2", "4"]
    assert all(call["concurrency"] == 8 for call in detail_calls(net))


async def test_cached_rows_survive_a_failed_miss():
    rows = [
        {"id": 0, "jobAdId": "ad-0", "releasedDate": "2026-08-01"},
        {"id": 1, "jobAdId": "ad-1", "releasedDate": "2026-08-02"},
        {"id": 2, "jobAdId": "ad-2", "releasedDate": "2026-08-03"},
    ]
    prior = {
        "0": Item("0", {**rows[0], "body": "cached 0"}),
        "2": Item("2", {**rows[2], "body": "cached 2"}),
    }
    net = RevisionNet(rows, fail_id=1)

    record = await harvest(net, options=reuse_options(prior))

    assert record.truncated is True
    # Row 1 is the only miss and it fails; rows 0 and 2 were never fetched at
    # all, so a failure between them must not cost us the reuse.
    assert [item.external_id for item in record.items] == ["0", "2"]
    assert record.reused_item_count == 2


async def test_without_prior_items_force_full_fetches_every_detail():
    rows = [
        {"id": i, "jobAdId": f"ad-{i}", "releasedDate": "2026-08-01"}
        for i in range(3)
    ]
    net = RevisionNet(rows)

    record = await harvest(net, options=reuse_options(None))

    assert record.reused_item_count == 0
    assert len(detail_calls(net)) == 3


async def test_conditional_not_modified_stops_after_the_list_request():
    class NotModifiedNet:
        def __init__(self):
            self.calls = []

        async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
            self.calls.append({"url": url, "etag": etag, "concurrency": concurrency})
            return Response(
                url=url,
                status=304,
                headers={"etag": 'W/"same"'},
                body=b"",
            )

    net = NotModifiedNet()
    feed = Feed(id=1, source="dtest", key="dtest", config={}, etag='W/"same"')
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(json_api.CONFIGS, "dtest", CONFIG)
        record = await json_api.harvest(
            "dtest", feed, net, options=reuse_options({})
        )

    assert record.http_status == 304
    assert record.items == []
    assert len(net.calls) == 1
    assert net.calls[0]["etag"] == 'W/"same"'
