from __future__ import annotations

import copy
import json

import pytest

from xjobscore.net import Response
from xjobscore.records import Feed, Item
from xjobscore.sources import json_api


def response(url: str, body: dict) -> Response:
    return Response(url=url, status=200, headers={}, body=json.dumps(body).encode())


class WorkdayNet:
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

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        path = url[url.index("/job/") :]
        row = self.jobs[path]
        req = row["bulletFields"][0]
        self.gets.append({"path": path, "concurrency": concurrency})
        return response(
            url,
            {
                "jobPostingInfo": {
                    "title": row["title"],
                    "jobReqId": req,
                    "externalUrl": f"https://tenant.wd1.myworkdayjobs.com/Site{path}",
                    "jobDescription": f"detail {req}",
                }
            },
        )


def job(number: int) -> dict:
    return {
        "title": f"Job {number}",
        "externalPath": f"/job/{number}",
        "bulletFields": [f"R{number}"],
    }


@pytest.fixture
def workday_config(monkeypatch):
    config = copy.deepcopy(json_api.CONFIGS["workday"])
    config["list"]["body"]["limit"] = 2
    config["list"]["page"].update(size=2, step=2, concurrency=3)
    config["list"]["partition"]["window"] = 4
    config["detail"]["concurrency"] = 2
    monkeypatch.setitem(json_api.CONFIGS, "workday-test", config)
    return config


async def harvest(net: WorkdayNet, *, options=None):
    feed = Feed(id=1, source="workday-test", key="tenant/wd1/Site")
    return await json_api.harvest("workday-test", feed, net, options=options)


async def test_capped_workday_board_is_partitioned_and_deduplicated(workday_config):
    jobs = [job(index) for index in range(6)]
    net = WorkdayNet(
        jobs,
        {"category": {"A": [j["externalPath"] for j in jobs[:3]],
                      "B": [j["externalPath"] for j in jobs[3:5]],
                      "C": [jobs[5]["externalPath"]]}},
    )

    record = await harvest(net)

    assert [item.external_id for item in record.items] == ["R0", "R1", "R2", "R5", "R3", "R4"]
    assert record.truncated is False
    assert record.result_window_truncated is True
    assert record.enumerated_item_count == 6
    assert record.partition_count == 2
    assert record.detail_fetched_count == 6
    assert {tuple(call["applied"]["category"]) for call in net.posts if call["applied"]} == {
        ("A", "C"), ("B",)
    }
    assert all(call["concurrency"] == 3 for call in net.posts[1:])
    assert all(call["concurrency"] == 2 for call in net.gets)


async def test_asymmetric_workday_revision_reuses_only_exact_matches(workday_config):
    jobs = [job(index) for index in range(3)]
    net = WorkdayNet(jobs, {"category": {"A": [j["externalPath"] for j in jobs]}})
    prior = {
        "/job/0": Item("R0", {"jobPostingInfo": {"title": "Job 0", "jobReqId": "R0"}}),
        "/job/1": Item("R1", {"jobPostingInfo": {"title": "Old title", "jobReqId": "R1"}}),
    }

    record = await harvest(
        net,
        options=json_api.JsonApiHarvestOptions(
            list_revision_fields=("title", "bulletFields.0"),
            detail_revision_fields=("jobPostingInfo.title", "jobPostingInfo.jobReqId"),
            prior_items=prior,
        ),
    )

    assert record.reused_item_count == 1
    assert record.detail_fetched_count == 2
    assert [call["path"] for call in net.gets] == ["/job/1", "/job/2"]
    assert record.items[0] is prior["/job/0"]


async def test_unpartitionable_capped_window_is_partial(workday_config):
    jobs = [job(index) for index in range(5)]
    net = WorkdayNet(jobs, {})

    record = await harvest(net)

    assert record.truncated is True
    assert record.items == []
    assert record.enumerated_item_count == 0
    assert record.result_window_truncated is True


async def test_oversized_facet_recursively_partitions_by_another_dimension(workday_config):
    jobs = [job(index) for index in range(6)]
    paths = [item["externalPath"] for item in jobs]
    net = WorkdayNet(
        jobs,
        {
            "category": {"All": paths},
            "type": {"X": paths[:3], "Y": paths[3:]},
        },
    )

    record = await harvest(net)

    assert {item.external_id for item in record.items} == {f"R{i}" for i in range(6)}
    assert record.truncated is False
    assert record.partition_count == 3
    assert any(call["applied"] == {"category": ["All"], "type": ["X"]}
               for call in net.posts)
    assert any(call["applied"] == {"category": ["All"], "type": ["Y"]}
               for call in net.posts)


async def test_overlapping_group_falls_back_to_individual_values(workday_config):
    jobs = [job(index) for index in range(5)]
    paths = [item["externalPath"] for item in jobs]
    net = WorkdayNet(
        jobs,
        {"category": {"A": paths[:2], "B": paths[1:3], "C": paths[3:]}},
    )

    record = await harvest(net)

    assert {item.external_id for item in record.items} == {f"R{i}" for i in range(5)}
    assert record.truncated is False
    assert record.partition_count == 4
    applied = [call["applied"].get("category") for call in net.posts if call["applied"]]
    assert ["A", "B"] in applied  # discarded overlapping group probe
    assert ["A"] in applied and ["B"] in applied  # exhaustive fallback
