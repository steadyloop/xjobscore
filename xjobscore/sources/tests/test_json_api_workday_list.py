"""`workday-list` -- the Workday list leg, now the only Workday source.

The point of the source is what it does *not* do, so the fake net serves list
pages and turns any detail GET into a failure: a call means the config regrew a
`detail` block. These fakes used to be shared with the `workday` detail source's
tests and moved here when that source was removed.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from xjobscore import router
from xjobscore.net import Response
from xjobscore.records import Feed, Item, Record
from xjobscore.sources import json_api


def response(url: str, body: dict) -> Response:
    return Response(url=url, status=200, headers={}, body=json.dumps(body).encode())


def job(number: int) -> dict:
    return {
        "title": f"Job {number}",
        "externalPath": f"/job/{number}",
        "bulletFields": [f"R{number}"],
    }


class ListOnlyNet:
    def __init__(self, jobs: list[dict], memberships: dict[str, dict[str, list[str]]]) -> None:
        self.jobs = {job["externalPath"]: job for job in jobs}
        self.memberships = memberships
        self.posts: list[dict] = []
        self.gets: list[dict] = []

    def matching(self, applied: dict[str, list[str]]) -> list[dict]:
        if not applied:
            return list(self.jobs.values())
        selected = set(self.jobs)
        for parameter, values in applied.items():
            selected &= {
                path
                for value in values
                for path in self.memberships[parameter].get(value, [])
            }
        return [job for path, job in self.jobs.items() if path in selected]

    def facets(self, matching: list[dict]) -> list[dict]:
        paths = {job["externalPath"] for job in matching}
        return [
            {
                "facetParameter": parameter,
                "values": [
                    {
                        "id": value,
                        "count": len(paths & set(members)),
                        "descriptor": value,
                    }
                    for value, members in values.items()
                    if paths & set(members)
                ],
            }
            for parameter, values in self.memberships.items()
        ]

    async def post(self, url, *, json=None, data=None, headers=None, concurrency=1):
        body = json or {}
        applied = body.get("appliedFacets", {})
        matching = self.matching(applied)
        offset, limit = body.get("offset", 0), body.get("limit", 2)
        self.posts.append({"applied": applied, "offset": offset, "concurrency": concurrency})
        # Workday hides the true count at the configured window for any query.
        total = min(len(matching), 4)
        return response(
            url,
            {
                "total": total,
                "jobPostings": matching[offset : offset + limit],
                "facets": self.facets(matching),
            },
        )

    async def get(self, url, **kwargs):
        raise AssertionError(f"workday-list must issue no detail request, got {url}")


@pytest.fixture
def workday_list_config(monkeypatch):
    config = copy.deepcopy(json_api.CONFIGS["workday-list"])
    config["list"]["body"]["limit"] = 2
    config["list"]["page"].update(size=2, step=2, concurrency=3)
    config["list"]["partition"]["window"] = 4
    monkeypatch.setitem(json_api.CONFIGS, "workday-list-test", config)
    return config


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def feed(**kwargs) -> Feed:
    return Feed(id=1, source="workday-list-test", key="tenant/wd1/Site", **kwargs)


async def harvest(net: ListOnlyNet):
    return await json_api.harvest("workday-list-test", feed(), net)


async def test_no_detail_request_is_issued(workday_list_config):
    jobs = [job(index) for index in range(3)]
    net = ListOnlyNet(jobs, {"category": {"A": [j["externalPath"] for j in jobs]}})

    record = await harvest(net)

    assert net.gets == []
    assert record.detail_fetched_count == 0
    assert record.reused_item_count == 0
    assert record.truncated is False


async def test_items_are_the_verbatim_list_rows(workday_list_config):
    jobs = [job(index) for index in range(3)]
    net = ListOnlyNet(jobs, {"category": {"A": [j["externalPath"] for j in jobs]}})

    record = await harvest(net)

    assert [item.external_id for item in record.items] == ["/job/0", "/job/1", "/job/2"]
    # No detail merge: the stored payload is the jobPostings[] element itself.
    assert record.items[0].payload == jobs[0]


async def test_recent_equal_total_stops_after_the_first_page(workday_list_config):
    jobs = [job(index) for index in range(3)]
    net = ListOnlyNet(jobs, {"category": {"A": [j["externalPath"] for j in jobs]}})

    record = await json_api.harvest(
        "workday-list-test",
        feed(list_total=3, last_full_poll_at=NOW),
        net,
        options=json_api.JsonApiHarvestOptions(now=NOW + timedelta(hours=23)),
    )

    assert record.count_unchanged is True
    assert record.list_total == 3
    assert record.items == []
    assert len(net.posts) == 1


async def test_changed_total_paginates_immediately(workday_list_config):
    jobs = [job(index) for index in range(3)]
    net = ListOnlyNet(jobs, {"category": {"A": [j["externalPath"] for j in jobs]}})

    record = await json_api.harvest(
        "workday-list-test",
        feed(list_total=2, last_full_poll_at=NOW),
        net,
        options=json_api.JsonApiHarvestOptions(now=NOW + timedelta(hours=1)),
    )

    assert [item.external_id for item in record.items] == ["/job/0", "/job/1", "/job/2"]
    assert record.full_listing is True
    assert record.list_total == 3
    assert len(net.posts) == 2


async def test_equal_total_forces_full_listing_at_24_hours(workday_list_config):
    jobs = [job(index) for index in range(3)]
    net = ListOnlyNet(jobs, {"category": {"A": [j["externalPath"] for j in jobs]}})

    record = await json_api.harvest(
        "workday-list-test",
        feed(list_total=3, last_full_poll_at=NOW),
        net,
        options=json_api.JsonApiHarvestOptions(now=NOW + timedelta(hours=24)),
    )

    assert len(record.items) == 3
    assert record.full_listing is True
    assert record.count_unchanged is False


async def test_capped_total_never_takes_count_shortcut(workday_list_config):
    jobs = [job(index) for index in range(6)]
    net = ListOnlyNet(
        jobs,
        {"category": {"A": [j["externalPath"] for j in jobs[:3]],
                      "B": [j["externalPath"] for j in jobs[3:5]],
                      "C": [jobs[5]["externalPath"]]}},
    )

    record = await json_api.harvest(
        "workday-list-test",
        feed(list_total=4, last_full_poll_at=NOW),
        net,
        options=json_api.JsonApiHarvestOptions(now=NOW + timedelta(hours=1)),
    )

    assert len(record.items) == 6
    assert record.full_listing is True
    assert record.result_window_truncated is True


async def test_capped_board_is_still_partitioned_and_deduplicated(workday_list_config):
    jobs = [job(index) for index in range(6)]
    net = ListOnlyNet(
        jobs,
        {"category": {"A": [j["externalPath"] for j in jobs[:3]],
                      "B": [j["externalPath"] for j in jobs[3:5]],
                      "C": [jobs[5]["externalPath"]]}},
    )

    record = await harvest(net)

    assert [item.external_id for item in record.items] == [
        "/job/0", "/job/1", "/job/2", "/job/5", "/job/3", "/job/4",
    ]
    assert record.truncated is False
    assert record.result_window_truncated is True
    assert record.partition_count == 2
    assert net.gets == []


def test_parse_maps_list_fields_and_synthesizes_the_posting_url(workday_list_config):
    row = {
        "title": "Distinguished Software Architect",
        "externalPath": "/job/US-CA-Santa-Clara/Distinguished-Software-Architect_JR1980127",
        "locationsText": "US, CA, Santa Clara",
        "postedOn": "Posted Today",
        "bulletFields": ["JR1980127"],
        "timeType": "Full time",
        "remoteType": "Hybrid",
    }

    posting = json_api.parse(
        "workday-list-test", feed(), Item(row["externalPath"], row), {}
    )[0]

    url = (
        "https://tenant.wd1.myworkdayjobs.com/Site"
        "/job/US-CA-Santa-Clara/Distinguished-Software-Architect_JR1980127"
    )
    assert posting.url_posting == url
    assert posting.apply_url == url
    assert posting.title == "Distinguished Software Architect"
    assert posting.location_raw == "US, CA, Santa Clara"
    assert posting.employment_type == "Full time"
    assert posting.workplace_type_raw == "Hybrid"
    assert posting.fidelity == "origin"
    # The list leg simply has no path to these; they must stay empty rather
    # than be synthesized from a relative "Posted Today" bucket or the key.
    assert posting.description_html is None
    assert posting.published_at is None
    assert posting.published_at_precision == "unknown"
    assert posting.company_raw is None
    assert posting.requisition_id is None
    assert posting.source_active is None
    assert posting.source_extra["postedOn"] == "Posted Today"


def test_relative_posted_bucket_does_not_churn_the_bronze_hash():
    """`postedOn` re-buckets daily; without the VOLATILE entry every poll would
    rewrite the whole tenant."""
    live = Feed(id=1, source="workday-list", key="tenant/wd1/Site")
    payload = {"title": "Engineer", "externalPath": "/job/0"}
    today = Record(items=[Item("/job/0", {**payload, "postedOn": "Posted Today"})])
    later = Record(items=[Item("/job/0", {**payload, "postedOn": "Posted Yesterday"})])

    first, second = router.prepare(live, today)[0], router.prepare(live, later)[0]

    assert first.data_hash == second.data_hash
    # Stripped from the hash only -- the payload stays verbatim and replayable.
    assert first.data["payload"]["postedOn"] == "Posted Today"
    assert router.unwrap(first.data)[0].payload == today.items[0].payload
