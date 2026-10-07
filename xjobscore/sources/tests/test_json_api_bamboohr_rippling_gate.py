"""BambooHR and Rippling: closing the "no vendor validator" gap.

Neither vendor sends an ETag, Last-Modified, or a cache-control that would let
`net.py`'s conditional GET help (verified live against real tenants), and both
have a per-posting detail leg -- so every 15-minute poll of a dormant board
cost `1 + N` requests. Two independent, additive fixes:

- `sources.REVISION_FIELDS` / `REVISION_REFRESH_WINDOWS`: the same
  revision-tuple reuse Gem already uses, so an unchanged row's detail request
  is skipped entirely. `test_reusing.py` covers the seam generically; this
  file is about whether the *real* field choices for these two vendors are
  actually safe -- verified against live payload shapes, not assumed. Two
  traps a naive choice would have hit: BambooHR's `employmentType` is null on
  every row of the tenant probed (`_revision` treats one null as "never
  reusable" for the whole tuple), and Rippling's `department` /
  `workLocation` have different shapes on the list side vs. the detail side,
  so comparing them whole would never match.
- BambooHR's `count_probe`: its list envelope carries `meta.totalCount`, the
  same shape Workday's probe already reads, bounding the *list* GET itself
  the way Workday's does (BambooHR has no true pagination -- `result` is
  always the whole board in one response).
"""

from __future__ import annotations

import copy
import json as json_module
from datetime import datetime, timedelta, timezone

import pytest

from xjobscore.net import Response
from xjobscore.records import Feed, Item
from xjobscore import sources
from xjobscore.sources import json_api

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def response(url: str, body: dict) -> Response:
    return Response(url=url, status=200, headers={}, body=json_module.dumps(body).encode())


# -- BambooHR --------------------------------------------------------------

# Field shapes as measured live against prentkeromich.bamboohr.com (13
# postings): list rows carry a 2-key `location` dict, the detail payload's
# `location` carries 4 (postalCode, addressCountry added) -- never `==`.
BAMBOO_LIST_ROW = {
    "id": "474",
    "jobOpeningName": "Shipping Clerk- Internal Only",
    "departmentId": "18923",
    "departmentLabel": "Shipping",
    "employmentStatusLabel": "Full Time",
    "employmentType": None,
    "location": {"city": "Wooster", "state": "Ohio"},
    "atsLocation": {"country": None, "state": None, "province": None, "city": None},
    "isRemote": None,
    "locationType": "0",
}
BAMBOO_DETAIL_PAYLOAD = {
    "jobOpeningShareUrl": "https://prentkeromich.bamboohr.com/careers/474",
    "jobOpeningName": "Shipping Clerk- Internal Only",
    "jobOpeningStatus": "Open",
    "departmentId": "18923",
    "departmentLabel": "Shipping",
    "employmentStatusLabel": "Full Time",
    "employmentType": None,
    "location": {
        "city": "Wooster",
        "state": "Ohio",
        "postalCode": "44691",
        "addressCountry": "United States",
    },
    "atsLocation": {"country": None, "countryId": None, "state": None, "city": None},
    "description": "<p>Old description</p>",
    "compensation": "$15 - $19",
    "datePosted": "2026-06-10",
    "locationType": "0",
}


class BambooNet:
    def __init__(self, rows: list[dict], detail_by_id: dict[str, dict]):
        self.rows = rows
        self.detail_by_id = detail_by_id
        self.detail_calls: list[str] = []
        self.list_calls = 0

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        if url.endswith("/careers/list"):
            self.list_calls += 1
            return response(url, {"meta": {"totalCount": len(self.rows)}, "result": self.rows})
        posting_id = url.rsplit("/", 2)[1]
        self.detail_calls.append(posting_id)
        return response(url, {"meta": {}, "result": {"jobOpening": self.detail_by_id[posting_id]}})


def bamboo_feed(**kwargs) -> Feed:
    return Feed(id=1, source="bamboohr", key="prentkeromich", **kwargs)


async def test_revision_fields_skip_detail_for_an_unchanged_row():
    prior = {"474": Item("474", BAMBOO_DETAIL_PAYLOAD)}
    net = BambooNet([BAMBOO_LIST_ROW], {"474": BAMBOO_DETAIL_PAYLOAD})

    record = await sources.reusing("bamboohr", prior, now=NOW)(
        bamboo_feed(last_full_poll_at=NOW), net
    )

    assert net.detail_calls == []
    assert record.items[0].payload["description"] == "<p>Old description</p>"


async def test_revision_fields_refetch_when_a_tracked_field_changes():
    prior = {"474": Item("474", BAMBOO_DETAIL_PAYLOAD)}
    changed_row = {**BAMBOO_LIST_ROW, "jobOpeningName": "Shipping Clerk II"}
    fresh_detail = {**BAMBOO_DETAIL_PAYLOAD, "jobOpeningName": "Shipping Clerk II"}
    net = BambooNet([changed_row], {"474": fresh_detail})

    record = await sources.reusing("bamboohr", prior, now=NOW)(
        bamboo_feed(last_full_poll_at=NOW), net
    )

    assert net.detail_calls == ["474"]
    assert record.items[0].payload["jobOpeningName"] == "Shipping Clerk II"


async def test_null_employment_type_does_not_disable_reuse():
    """The trap a naive field list would hit: `employmentType` is null on
    every row of the tenant this was verified against, and `_revision`
    invalidates the whole tuple on one None component. It must not be in the
    field list, or reuse never fires for this vendor at all."""
    assert "employmentType" not in sources.REVISION_FIELDS["bamboohr"]
    assert BAMBOO_LIST_ROW["employmentType"] is None

    prior = {"474": Item("474", BAMBOO_DETAIL_PAYLOAD)}
    net = BambooNet([BAMBOO_LIST_ROW], {"474": BAMBOO_DETAIL_PAYLOAD})

    record = await sources.reusing("bamboohr", prior, now=NOW)(
        bamboo_feed(last_full_poll_at=NOW), net
    )

    assert net.detail_calls == []
    assert record.reused_item_count == 1


async def test_mismatched_location_shape_is_excluded_from_revision_fields():
    """The other trap: `location` differs in key set between list and
    detail (detail adds postalCode/addressCountry), so it would never
    compare equal -- confirmed unequal here, and confirmed absent from the
    field list a dotted `location.city`/`location.state` is used instead."""
    assert BAMBOO_LIST_ROW["location"] != BAMBOO_DETAIL_PAYLOAD["location"]
    assert "location" not in sources.REVISION_FIELDS["bamboohr"]
    assert "location.city" in sources.REVISION_FIELDS["bamboohr"]


async def test_window_expiry_forces_a_full_detail_pass():
    prior = {"474": Item("474", BAMBOO_DETAIL_PAYLOAD)}
    net = BambooNet([BAMBOO_LIST_ROW], {"474": BAMBOO_DETAIL_PAYLOAD})

    record = await sources.reusing("bamboohr", prior, now=NOW + timedelta(hours=24))(
        bamboo_feed(last_full_poll_at=NOW), net
    )

    assert net.detail_calls == ["474"]
    assert record.full_listing is True


async def test_full_listing_carries_list_total_for_the_count_probe_baseline():
    """Regression for the json_api.py fix: before it, a detail-leg source's
    `_finish()` never set `list_total`, so a forced full pass would persist
    `list_total=NULL` and `count_probe` could never find a baseline to
    compare against on the next poll."""
    prior = {"474": Item("474", BAMBOO_DETAIL_PAYLOAD)}
    net = BambooNet([BAMBOO_LIST_ROW], {"474": BAMBOO_DETAIL_PAYLOAD})

    record = await sources.reusing("bamboohr", prior, now=NOW + timedelta(hours=24))(
        bamboo_feed(last_full_poll_at=NOW), net
    )

    assert record.full_listing is True
    assert record.list_total == 1


async def test_count_probe_skips_the_list_fetch_entirely_within_window():
    net = BambooNet([BAMBOO_LIST_ROW], {"474": BAMBOO_DETAIL_PAYLOAD})

    record = await json_api.harvest(
        "bamboohr",
        bamboo_feed(list_total=1, last_full_poll_at=NOW),
        net,
        options=json_api.JsonApiHarvestOptions(now=NOW + timedelta(hours=23)),
    )

    assert record.count_unchanged is True
    assert record.items == []
    assert net.detail_calls == []


async def test_count_probe_falls_through_when_total_changed():
    net = BambooNet([BAMBOO_LIST_ROW], {"474": BAMBOO_DETAIL_PAYLOAD})

    record = await json_api.harvest(
        "bamboohr",
        bamboo_feed(list_total=2, last_full_poll_at=NOW),
        net,
        options=json_api.JsonApiHarvestOptions(now=NOW + timedelta(hours=1)),
    )

    assert record.count_unchanged is False
    assert len(record.items) == 1


# -- Rippling ----------------------------------------------------------------

# Field shapes as measured live against api.rippling.com/.../board/rippling
# (734 postings): the list row's `department` is `{id, label}`; the detail
# response's root `department` is `{name, base_department, department_tree}`
# -- a completely different shape, never `==`. `workLocation` (singular)
# doesn't even exist at the detail root at all (`workLocations`, plural).
RIPPLING_LIST_ROW = {
    "uuid": "2f0674e6-f01f-4ecd-b459-e947241c211f",
    "name": "Account Executive - Accountants Channel (East Coast)",
    "department": {"id": "Sales", "label": "Sales"},
    "url": "https://ats.rippling.com/rippling/jobs/2f0674e6-f01f-4ecd-b459-e947241c211f",
    "workLocation": {"label": "Remote (Connecticut, US)", "id": "Remote (Connecticut, US)"},
}
RIPPLING_DETAIL_PAYLOAD = {
    "uuid": "2f0674e6-f01f-4ecd-b459-e947241c211f",
    "name": "Account Executive - Accountants Channel (East Coast)",
    "description": {"role": "Old role text", "company": "About us"},
    "workLocations": [{"label": "Remote (Connecticut, US)"}],
    "department": {
        "name": "Channel Sales Account Executives",
        "base_department": "Sales",
        "department_tree": ["Sales", "Channel Sales Account Executives"],
    },
    "employmentType": {"label": "Full-time"},
    "createdOn": "2026-01-05T00:00:00Z",
    "url": "https://ats.rippling.com/rippling/jobs/2f0674e6-f01f-4ecd-b459-e947241c211f",
    "logo": None,
}


class RipplingNet:
    def __init__(self, rows: list[dict], detail_by_id: dict[str, dict]):
        self.rows = rows
        self.detail_by_id = detail_by_id
        self.detail_calls: list[str] = []

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        if url.endswith("/jobs"):
            return response(url, self.rows)
        posting_id = url.rsplit("/", 1)[1]
        self.detail_calls.append(posting_id)
        return response(url, self.detail_by_id[posting_id])


def rippling_feed(**kwargs) -> Feed:
    return Feed(id=1, source="rippling", key="rippling", **kwargs)


async def test_revision_fields_skip_detail_for_an_unchanged_posting():
    posting_id = RIPPLING_LIST_ROW["uuid"]
    prior = {posting_id: Item(posting_id, RIPPLING_DETAIL_PAYLOAD)}
    net = RipplingNet([RIPPLING_LIST_ROW], {posting_id: RIPPLING_DETAIL_PAYLOAD})

    record = await sources.reusing("rippling", prior, now=NOW)(
        rippling_feed(last_full_poll_at=NOW), net
    )

    assert net.detail_calls == []
    assert record.items[0].payload["description"]["role"] == "Old role text"


async def test_mismatched_department_and_worklocation_are_excluded():
    """The trap: both fields exist on both sides but differ in shape (or key
    name), so comparing them whole would never match -- confirmed unequal
    here, and confirmed absent from the field list."""
    assert RIPPLING_LIST_ROW["department"] != RIPPLING_DETAIL_PAYLOAD["department"]
    assert "workLocation" not in RIPPLING_DETAIL_PAYLOAD
    assert set(sources.REVISION_FIELDS["rippling"]) == {"uuid", "name", "url"}


async def test_revision_fields_refetch_when_the_title_changes():
    posting_id = RIPPLING_LIST_ROW["uuid"]
    prior = {posting_id: Item(posting_id, RIPPLING_DETAIL_PAYLOAD)}
    changed_row = {**RIPPLING_LIST_ROW, "name": "Senior Account Executive"}
    fresh_detail = {**RIPPLING_DETAIL_PAYLOAD, "name": "Senior Account Executive"}
    net = RipplingNet([changed_row], {posting_id: fresh_detail})

    record = await sources.reusing("rippling", prior, now=NOW)(
        rippling_feed(last_full_poll_at=NOW), net
    )

    assert net.detail_calls == [posting_id]


async def test_window_expiry_forces_a_full_detail_pass():
    posting_id = RIPPLING_LIST_ROW["uuid"]
    prior = {posting_id: Item(posting_id, RIPPLING_DETAIL_PAYLOAD)}
    net = RipplingNet([RIPPLING_LIST_ROW], {posting_id: RIPPLING_DETAIL_PAYLOAD})

    record = await sources.reusing("rippling", prior, now=NOW + timedelta(hours=24))(
        rippling_feed(last_full_poll_at=NOW), net
    )

    assert net.detail_calls == [posting_id]
    assert record.full_listing is True
