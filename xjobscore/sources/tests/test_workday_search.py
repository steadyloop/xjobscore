"""Optional JSON search behavior. Production Workday now enumerates fully."""

import copy
import json as jsonlib
from datetime import datetime, timedelta, timezone

import pytest

from xjobscore.net import Response
from xjobscore.records import Feed
from xjobscore.sources import json_api


NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)
KEY = "example/wd1/careers"


class SearchNet:
    def __init__(self, selected=(0, 3, 5), *, fail_page=False):
        self.jobs = [
            {"title": "Intern" if i == 0 else f"Job {i}", "externalPath": f"/job/{i}"}
            for i in range(10)
        ]
        self.selected = selected
        self.fail_page = fail_page
        self.posts = []

    async def post(self, url, *, json=None, **kwargs):
        self.posts.append(dict(json))
        filtered = bool(json["searchText"] or json["appliedFacets"])
        if filtered and self.fail_page and json["offset"]:
            from xjobscore.net import NetError
            raise NetError("search page failed", url=url, status=503)
        rows = [self.jobs[i] for i in self.selected] if filtered else self.jobs
        offset = json["offset"]
        return Response(url=url, status=200, headers={}, body=jsonlib.dumps({
            "total": len(rows) if offset == 0 else 0,
            "jobPostings": rows[offset:offset + json["limit"]],
        }).encode())

    async def get(self, url, **kwargs):
        return Response(url=url, status=200, headers={}, body=b'{"jobPostingInfo": {"jobDescription": "Student role"}}')


@pytest.fixture
def configured(monkeypatch):
    config = copy.deepcopy(json_api.CONFIGS["workday"])
    config["list"]["body"]["limit"] = 2
    config["list"]["page"].update(size=2, step=2, concurrency=2)
    config["list"]["partition"]["window"] = 100
    # Exercise the generic optional search path, not Workday production policy.
    from xjobscore.sources.json_api_configs import _SHARED_JOB_TYPE
    config["detail"]["only"] = {"classify": [
        rule if rule["is"] is None else {**rule, "is": True}
        for rule in _SHARED_JOB_TYPE
    ]}
    config["list"]["count_probe"] = {"window_s": 86_400}
    config["list"]["search"] = {
        "parameter": "searchText", "refresh_s": 86_400,
        "query": "intern OR student",
    }
    monkeypatch.setitem(json_api.CONFIGS, "search-test", config)
    return config


async def fetch(net, *, key=KEY, last_full=NOW, total=9, **options):
    return await json_api.harvest(
        "search-test", Feed(id=1, source="search-test", key=key,
                            list_total=total, last_full_poll_at=last_full), net,
        options=json_api.JsonApiHarvestOptions(now=NOW + timedelta(hours=1), **options),
    )


async def test_changed_board_uses_search_and_keeps_unfiltered_total(configured):
    net = SearchNet()
    record = await fetch(net)
    assert {item.external_id for item in record.items} == {"/job/0", "/job/3", "/job/5"}
    assert record.filtered_listing and not record.full_listing and not record.truncated
    assert record.list_total == 10  # the count probe must use the board count
    assert len(net.posts) == 3  # one board probe + two search pages, versus five
    assert net.posts[0]["searchText"] == ""
    assert all(p["searchText"] == "intern OR student" for p in net.posts[1:])
    assert record.detail_fetched_count == 1


async def test_next_unchanged_board_still_costs_one_request(configured):
    net = SearchNet()
    record = await fetch(net, total=10)
    assert record.count_unchanged and not record.items
    assert len(net.posts) == 1 and net.posts[0]["searchText"] == ""


@pytest.mark.parametrize("kwargs", [
    {"last_full": None}, {"last_full": NOW - timedelta(days=1)},
    {"force_full": True}, {"force_unfiltered": True},
])
async def test_cold_expired_and_forced_boards_stay_unfiltered(configured, kwargs):
    net = SearchNet()
    record = await fetch(net, **kwargs)
    assert len(record.items) == 10 and record.full_listing and not record.filtered_listing
    assert len(net.posts) == 5 and all(p["searchText"] == "" for p in net.posts)


@pytest.mark.parametrize("selected", [(), tuple(range(9))])
async def test_empty_or_unhelpful_search_falls_back_to_board(configured, selected):
    net = SearchNet(selected)
    record = await fetch(net)
    assert len(record.items) == 10 and not record.filtered_listing and record.full_listing
    assert len(net.posts) == 6


async def test_query_that_loses_a_live_first_page_candidate_is_rejected(configured):
    net = SearchNet(selected=(3, 5))  # /job/0 is a live Intern on the board page
    record = await fetch(net)
    assert len(record.items) == 10 and record.full_listing and not record.filtered_listing
    assert sum(bool(p["searchText"]) for p in net.posts) == 1


@pytest.mark.parametrize("key", [KEY, "unknown/wd1/careers", "hpe/wd5/jobsathpe"])
async def test_same_query_is_used_on_every_board_without_manual_plans(configured, key):
    net = SearchNet()
    record = await fetch(net, key=key)
    assert record.filtered_listing and len(record.items) == 3
    assert all(p["appliedFacets"] == {} for p in net.posts)
    assert all(p["searchText"] == "intern OR student" for p in net.posts[1:])


async def test_failed_search_page_keeps_partial_results_without_completeness(configured):
    record = await fetch(SearchNet(fail_page=True))
    assert record.truncated and record.filtered_listing and not record.full_listing
    assert {item.external_id for item in record.items} == {"/job/0", "/job/3"}


@pytest.mark.parametrize("title", [
    "Internal Auditor", "Grade 5 Engineer", "PhD Research Intern",
    "Pharmacy Intern", "Nursing Student", "Graduate Research Assistant",
    "Student Recruitment Coordinator",
])
async def test_broad_search_hit_does_not_override_local_classification(configured, title):
    net = SearchNet()
    net.jobs[3]["title"] = title
    record = await fetch(net)
    hit = next(item for item in record.items if item.external_id == "/job/3")
    assert record.filtered_listing and record.detail_fetched_count == 1
    assert "jobPostingInfo" not in hit.payload
    assert json_api._value(configured["fields"]["job_type"], hit.payload, {"key": KEY}) is None
