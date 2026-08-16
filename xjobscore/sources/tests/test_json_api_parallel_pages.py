"""Opt-in parallel pagination in the json-api engine.

A `page` config with `"concurrency": N` fetches every page after the first at
once (the first page stays sequential -- it establishes total, etag, and
envelope). Rows must come back in page order regardless of completion order, a
failed page truncates to the contiguous prefix of successes, and configs whose
cursors cannot be precomputed fall back to the sequential loop.

Both cursor shapes are covered, because they disagree about `step`: an offset
board (talentreef, oracle) advances by a page's worth of items, so `step` and
`size` are equal, while a page-number board (apple) advances by 1 and carries
`size` rows. How many requests remain is always a `size` question.
"""

from __future__ import annotations

import asyncio
import json as json_module

import pytest

from xjobscore.net import NetError, Response
from xjobscore.records import Feed
from xjobscore.sources import json_api


PAGE_SIZE = 2

CONFIG = {
    "list": {
        "method": "POST",
        "url": "https://board.invalid/search",
        "body": {"size": PAGE_SIZE, "from": "$cursor"},
        "items": "items",
        "id": "@id",
        "page": {"total": "@total", "size": PAGE_SIZE, "step": PAGE_SIZE, "max": 5, "concurrency": 3},
    },
    "fields": {"title": "@title"},
}


class PagedNet:
    """Serves offset pages of `total` synthetic rows, watching for overlap."""

    def __init__(self, total, *, fail_from=None):
        self.total = total
        self.fail_from = fail_from
        self.calls: list[dict] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def post(self, url, *, json=None, headers=None, concurrency=1):
        offset = json["from"]
        self.calls.append({"from": offset, "concurrency": concurrency})
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.01)  # let gathered peers overlap
        self.in_flight -= 1
        if offset == self.fail_from:
            raise NetError("HTTP 500", url=url, status=500)
        ids = range(offset, min(offset + PAGE_SIZE, self.total))
        body = {
            "total": self.total,
            "items": [{"id": i, "title": f"job {i}"} for i in ids],
        }
        return Response(url=url, status=200, headers={}, body=json_module.dumps(body).encode())


async def harvest(net, config=CONFIG, *, options=None):
    feed = Feed(id=1, source="ptest", key="ptest", config={})
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(json_api.CONFIGS, "ptest", config)
        return await json_api.harvest("ptest", feed, net, options=options)


async def test_parallel_pages_fire_concurrently_and_preserve_offset_order():
    net = PagedNet(total=7)
    record = await harvest(net)

    assert record.truncated is False
    assert [item.external_id for item in record.items] == [str(i) for i in range(7)]
    assert [call["from"] for call in net.calls] == [0, 2, 4, 6]
    assert net.calls[0]["concurrency"] == 1
    assert all(call["concurrency"] == 3 for call in net.calls[1:])
    assert net.max_in_flight >= 2


async def test_failure_mid_fanout_returns_contiguous_prefix_truncated():
    net = PagedNet(total=8, fail_from=4)
    record = await harvest(net)

    assert record.truncated is True
    # Offset 6 succeeded, but the walk stops at the first failure -- no gaps.
    assert [item.external_id for item in record.items] == ["0", "1", "2", "3"]


async def test_max_cap_marks_truncated():
    net = PagedNet(total=100)
    config = {**CONFIG, "list": {**CONFIG["list"], "page": {**CONFIG["list"]["page"], "max": 3}}}
    record = await harvest(net, config)

    assert record.truncated is True
    assert [item.external_id for item in record.items] == [str(i) for i in range(6)]
    assert [call["from"] for call in net.calls] == [0, 2, 4]


class PagedDetailNet(PagedNet):
    """PagedNet plus a per-id detail GET, for sources that page and detail."""

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        detail_id = int(url.rsplit("/", 1)[1])
        return Response(
            url=url, status=200, headers={},
            body=json_module.dumps({"id": detail_id, "body": f"detail {detail_id}"}).encode(),
        )


DETAIL_CONFIG = {
    **CONFIG,
    "detail": {"url": "https://board.invalid/postings/{id}", "id": "@id"},
}


async def test_capped_parallel_pagination_still_fetches_detail_for_collected_rows():
    # A `page.max` cap is our own cost decision, not a vendor error -- the
    # rows collected before it bit are complete and real, so they must not
    # ship without descriptions. Regression for the bug where the "capped"
    # branch returned list-only rows, silently skipping the detail stage.
    net = PagedDetailNet(total=100)
    config = {
        **DETAIL_CONFIG,
        "list": {**DETAIL_CONFIG["list"], "page": {**DETAIL_CONFIG["list"]["page"], "max": 3}},
    }
    record = await harvest(net, config)

    assert record.truncated is True
    assert [item.external_id for item in record.items] == [str(i) for i in range(6)]
    assert all(item.payload["body"] == f"detail {i}" for i, item in enumerate(record.items))


async def test_sequential_max_cap_still_fetches_detail_for_collected_rows():
    # Same bug, sequential path: page.concurrency omitted so pagination stays
    # in the `while` loop instead of `_paginate_parallel`.
    net = PagedDetailNet(total=100)
    config = {
        **DETAIL_CONFIG,
        "list": {
            **DETAIL_CONFIG["list"],
            "page": {**DETAIL_CONFIG["list"]["page"], "max": 3, "concurrency": 1},
        },
    }
    record = await harvest(net, config)

    assert record.truncated is True
    assert [item.external_id for item in record.items] == [str(i) for i in range(6)]
    assert all(item.payload["body"] == f"detail {i}" for i, item in enumerate(record.items))


async def test_ineligible_configs_stay_sequential():
    # `step: "count"` cannot precompute offsets, so the fan-out gate must not
    # fire even though `concurrency` is configured.
    net = PagedNet(total=7)
    config = {**CONFIG, "list": {**CONFIG["list"], "page": {**CONFIG["list"]["page"], "step": "count"}}}
    record = await harvest(net, config)

    assert [item.external_id for item in record.items] == [str(i) for i in range(7)]
    assert all(call["concurrency"] == 1 for call in net.calls)
    assert net.max_in_flight == 1


async def test_one_run_options_override_dynamic_step_and_concurrency():
    net = PagedNet(total=7)
    config = {
        **CONFIG,
        "list": {
            **CONFIG["list"],
            "page": {**CONFIG["list"]["page"], "step": "count", "concurrency": 1},
        },
    }
    options = json_api.JsonApiHarvestOptions(page_step=PAGE_SIZE, page_concurrency=8)

    record = await harvest(net, config, options=options)

    assert [item.external_id for item in record.items] == [str(i) for i in range(7)]
    assert [call["from"] for call in net.calls] == [0, 2, 4, 6]
    assert all(call["concurrency"] == 8 for call in net.calls[1:])


async def test_a_config_without_size_cannot_fan_out():
    # Remaining-page arithmetic divides by `size`; without one there is no
    # honest answer, so the gate must keep such a config sequential.
    net = PagedNet(total=7)
    page = {k: v for k, v in CONFIG["list"]["page"].items() if k != "size"}
    config = {**CONFIG, "list": {**CONFIG["list"], "page": page}}
    record = await harvest(net, config)

    assert [item.external_id for item in record.items] == [str(i) for i in range(7)]
    assert all(call["concurrency"] == 1 for call in net.calls)


# -- page-number cursors (apple) -------------------------------------------
#
# `step` is 1 because the cursor counts pages, while each page still carries
# `size` rows. Counting remaining requests off `step` would ask for one page
# per outstanding *item*.

PAGE_NUMBER_CONFIG = {
    "list": {
        "method": "POST",
        "url": "https://board.invalid/search",
        "body": {"page": "$cursor"},
        "items": "items",
        "id": "@id",
        "page": {"total": "@total", "start": 1, "size": PAGE_SIZE, "step": 1,
                 "max": 50, "concurrency": 3},
    },
    "fields": {"title": "@title"},
}


class PageNumberNet(PagedNet):
    """Serves 1-based numbered pages of `total` synthetic rows."""

    async def post(self, url, *, json=None, headers=None, concurrency=1):
        page = json["page"]
        offset = (page - 1) * PAGE_SIZE
        return await super().post(url, json={"from": offset}, headers=headers,
                                  concurrency=concurrency)


async def test_page_number_fanout_counts_requests_by_size_not_step():
    net = PageNumberNet(total=7)
    record = await harvest(net, PAGE_NUMBER_CONFIG)

    assert record.truncated is False
    assert [item.external_id for item in record.items] == [str(i) for i in range(7)]
    # ceil(7/2) = 4 pages. Dividing the 5 outstanding rows by step=1 instead
    # would have asked for pages 2..6 -- two of them past the end of the board.
    assert [call["from"] for call in net.calls] == [0, 2, 4, 6]
    assert net.calls[0]["concurrency"] == 1
    assert all(call["concurrency"] == 3 for call in net.calls[1:])
    assert net.max_in_flight >= 2
