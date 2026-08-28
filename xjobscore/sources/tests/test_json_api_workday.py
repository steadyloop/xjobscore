"""`workday` -- the list leg plus a GATED detail leg.

Most of this file drives `ListOnlyNet`, whose `get` raises: those tests assert
the pagination and partition behaviour of the list leg, where a detail request
would mean the gate leaked. `DetailNet` at the bottom serves detail and covers
the gate itself and the two payload shapes that reach `fields` -- a skipped row
carrying its bare `jobPostings[]` payload, and an admitted row carrying the
detail response merged over that list row.
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
        raise AssertionError(f"workday must issue no detail request, got {url}")


@pytest.fixture
def workday_config(monkeypatch):
    config = copy.deepcopy(json_api.CONFIGS["workday"])
    config["list"]["body"]["limit"] = 2
    config["list"]["page"].update(size=2, step=2, concurrency=3)
    config["list"]["partition"]["window"] = 4
    monkeypatch.setitem(json_api.CONFIGS, "workday-test", config)
    return config


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def feed(**kwargs) -> Feed:
    return Feed(id=1, source="workday-test", key="tenant/wd1/Site", **kwargs)


async def harvest(net: ListOnlyNet):
    return await json_api.harvest("workday-test", feed(), net)


async def test_no_detail_request_is_issued(workday_config):
    jobs = [job(index) for index in range(3)]
    net = ListOnlyNet(jobs, {"category": {"A": [j["externalPath"] for j in jobs]}})

    record = await harvest(net)

    assert net.gets == []
    assert record.detail_fetched_count == 0
    assert record.reused_item_count == 0
    assert record.truncated is False


async def test_items_are_the_verbatim_list_rows(workday_config):
    jobs = [job(index) for index in range(3)]
    net = ListOnlyNet(jobs, {"category": {"A": [j["externalPath"] for j in jobs]}})

    record = await harvest(net)

    assert [item.external_id for item in record.items] == ["/job/0", "/job/1", "/job/2"]
    # No detail merge: the stored payload is the jobPostings[] element itself.
    assert record.items[0].payload == jobs[0]


async def test_recent_equal_total_stops_after_the_first_page(workday_config):
    jobs = [job(index) for index in range(3)]
    net = ListOnlyNet(jobs, {"category": {"A": [j["externalPath"] for j in jobs]}})

    record = await json_api.harvest(
        "workday-test",
        feed(list_total=3, last_full_poll_at=NOW),
        net,
        options=json_api.JsonApiHarvestOptions(now=NOW + timedelta(hours=23)),
    )

    assert record.count_unchanged is True
    assert record.list_total == 3
    assert record.items == []
    assert len(net.posts) == 1


async def test_changed_total_paginates_immediately(workday_config):
    jobs = [job(index) for index in range(3)]
    net = ListOnlyNet(jobs, {"category": {"A": [j["externalPath"] for j in jobs]}})

    record = await json_api.harvest(
        "workday-test",
        feed(list_total=2, last_full_poll_at=NOW),
        net,
        options=json_api.JsonApiHarvestOptions(now=NOW + timedelta(hours=1)),
    )

    assert [item.external_id for item in record.items] == ["/job/0", "/job/1", "/job/2"]
    assert record.full_listing is True
    assert record.list_total == 3
    assert len(net.posts) == 2


async def test_equal_total_forces_full_listing_at_24_hours(workday_config):
    jobs = [job(index) for index in range(3)]
    net = ListOnlyNet(jobs, {"category": {"A": [j["externalPath"] for j in jobs]}})

    record = await json_api.harvest(
        "workday-test",
        feed(list_total=3, last_full_poll_at=NOW),
        net,
        options=json_api.JsonApiHarvestOptions(now=NOW + timedelta(hours=24)),
    )

    assert len(record.items) == 3
    assert record.full_listing is True
    assert record.count_unchanged is False


async def test_capped_total_never_takes_count_shortcut(workday_config):
    jobs = [job(index) for index in range(6)]
    net = ListOnlyNet(
        jobs,
        {"category": {"A": [j["externalPath"] for j in jobs[:3]],
                      "B": [j["externalPath"] for j in jobs[3:5]],
                      "C": [jobs[5]["externalPath"]]}},
    )

    record = await json_api.harvest(
        "workday-test",
        feed(list_total=4, last_full_poll_at=NOW),
        net,
        options=json_api.JsonApiHarvestOptions(now=NOW + timedelta(hours=1)),
    )

    assert len(record.items) == 6
    assert record.full_listing is True
    assert record.result_window_truncated is True


async def test_capped_board_is_still_partitioned_and_deduplicated(workday_config):
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


def test_parse_maps_list_fields_and_synthesizes_the_posting_url(workday_config):
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
        "workday-test", feed(), Item(row["externalPath"], row), {}
    )[0]

    url = (
        "https://tenant.wd1.myworkdayjobs.com/Site"
        "/job/US-CA-Santa-Clara/Distinguished-Software-Architect_JR1980127"
    )
    assert posting.url_posting == url
    assert posting.apply_url == url
    assert posting.title == "Distinguished Software Architect"
    assert posting.job_type is None  # untyped, which is why the gate skipped it
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
    live = Feed(id=1, source="workday", key="tenant/wd1/Site")
    payload = {"title": "Engineer", "externalPath": "/job/0"}
    today = Record(items=[Item("/job/0", {**payload, "postedOn": "Posted Today"})])
    later = Record(items=[Item("/job/0", {**payload, "postedOn": "Posted Yesterday"})])

    first, second = router.prepare(live, today)[0], router.prepare(live, later)[0]

    assert first.data_hash == second.data_hash
    # Stripped from the hash only -- the payload stays verbatim and replayable.
    assert first.data["payload"]["postedOn"] == "Posted Today"
    assert router.unwrap(first.data)[0].payload == today.items[0].payload


# The gate and the two payload shapes -------------------------------------


class DetailNet(ListOnlyNet):
    """Serves detail, and records every path it was asked for."""

    def __init__(self, jobs, memberships=None, *, missing=()):
        super().__init__(jobs, memberships or {})
        self.missing = set(missing)

    async def get(self, url, **kwargs):
        self.gets.append({"url": url})
        path = url.split("/Site", 1)[1]
        if path in self.missing:
            raise AssertionError(f"detail fetched for a row that should 404: {path}")
        return response(url, {
            "jobPostingInfo": {
                "title": self.jobs[path]["title"],
                "location": "Austin, TX",
                "jobDescription": "<p>Build things</p>",
                "jobReqId": "R99",
                "timeType": "Part time",
                "startDate": "2026-08-01",
            },
        })


def typed(number: int, title: str) -> dict:
    return {"title": title, "externalPath": f"/job/{number}", "bulletFields": [f"R{number}"]}


async def test_detail_is_fetched_only_for_rows_the_job_type_table_types(workday_config):
    rows = [
        typed(0, "Software Engineer Intern"),      # intern  -> fetched
        typed(1, "Staff Software Engineer"),       # untyped -> skipped
        typed(2, "Chemist 1 New College Grad"),    # new_grad -> fetched
        typed(3, "Pharmacy Intern"),               # EXCLUDED -> skipped, on purpose
    ]
    net = DetailNet(rows, {"category": {"A": [r["externalPath"] for r in rows]}})

    record = await harvest(net)

    assert record.detail_fetched_count == 2
    assert record.detail_skipped_count == 2
    assert sorted(g["url"].split("/Site")[1] for g in net.gets) == ["/job/0", "/job/2"]
    # Every row still emits -- a skipped row is not a dropped row.
    assert len(record.items) == 4


async def test_the_gate_skips_a_title_less_stub_before_it_costs_a_request(workday_config):
    """Workday's orphaned index stubs carry only `externalPath` + `bulletFields`
    and 404 on detail. No rule in the table can match a row with no title, so
    the gate declines them for free."""
    stub = {"externalPath": "/job/9", "bulletFields": ["R9"]}
    net = DetailNet([stub], {"category": {"A": ["/job/9"]}}, missing={"/job/9"})

    record = await harvest(net)

    assert net.gets == []
    assert record.detail_skipped_count == 1
    assert record.items[0].payload == stub


async def test_identity_is_external_path_on_both_shapes(workday_config):
    """`merge` keeps the list row under the detail response, so the list leg's
    `id` survives. Without it a fetched row would fall back to a different
    identity scheme than a skipped one on the same feed."""
    rows = [typed(0, "Software Engineer Intern"), typed(1, "Staff Software Engineer")]
    net = DetailNet(rows, {"category": {"A": ["/job/0", "/job/1"]}})

    record = await harvest(net)

    assert sorted(item.external_id for item in record.items) == ["/job/0", "/job/1"]


def test_a_detail_merged_row_prefers_detail_where_it_is_better(workday_config):
    """Concrete `location` over an `N Locations` rollup, and detail's 98.9%-fill
    `timeType` over the list's 29.1%."""
    row = {
        "title": "Software Engineer Intern",
        "externalPath": "/job/Austin/SWE-Intern_R99",
        "locationsText": "3 Locations",
        "jobPostingInfo": {
            "title": "Software Engineer Intern",
            "location": "Austin, TX",
            "jobDescription": "<p>Build things</p>",
            "jobReqId": "R99",
            "timeType": "Part time",
            "startDate": "2026-08-01",
        },
    }

    posting = json_api.parse("workday-test", feed(), Item(row["externalPath"], row), {})[0]

    assert posting.location_raw == "Austin, TX"
    assert posting.employment_type == "Part time"
    assert posting.part_time == "yes"
    assert posting.description_html == "<p>Build things</p>"
    assert posting.requisition_id == "R99"
    assert posting.published_at_precision == "date"
    assert posting.job_type == "intern"
    # Templated from `externalPath`, which only survives because of `merge`.
    assert posting.url_posting.endswith("/job/Austin/SWE-Intern_R99")


def test_additional_locations_become_normalizer_extras(workday_config):
    """An `N Locations` row carries the other N-1 sites only on detail. They are
    appended to `locations[]`, never replacing the primary."""
    row = {
        "title": "Software Engineer Intern",
        "externalPath": "/job/Remote-Georgia-USA/SWE-Intern_R99",
        "locationsText": "3 Locations",
        "jobPostingInfo": {
            "location": "Remote, Georgia, USA",
            "additionalLocations": ["Remote, Ohio, USA", "Remote, Indiana, USA"],
        },
    }

    posting = json_api.parse("workday-test", feed(), Item(row["externalPath"], row), {})[0]

    assert posting.location_raw == "Remote, Georgia, USA"
    assert posting.location_normalization_extra == [
        "Remote, Ohio, USA", "Remote, Indiana, USA",
    ]


def test_a_row_without_detail_gets_an_empty_extras_list(workday_config):
    """`location_normalization_extra` backs a list[str] and has no absent-default
    in the adapter, so a skipped row must resolve to [] rather than None."""
    row = {"title": "Staff Engineer", "externalPath": "/job/Austin/Staff_R1",
           "locationsText": "Austin, TX"}

    posting = json_api.parse("workday-test", feed(), Item(row["externalPath"], row), {})[0]

    assert posting.location_normalization_extra == []
