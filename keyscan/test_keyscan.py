from __future__ import annotations

import asyncio
import contextlib
from functools import partial
import gzip
import json
from pathlib import Path
import sqlite3

import duckdb
import httpx
import pytest

import keyscan.core as keyscan_core
import keyscan.verify as keyscan_verify
from keyscan.__main__ import _progress_line
from keyscan.core import Keyscan, KeyscanError, _Cancelled, _select_crawls, selftest
from keyscan.name_boards import name_from_title
from keyscan.probe import default_request, probe_rule
from keyscan.rules import BY_SOURCE, SOURCE_GROUPS, expand_sources, extract, surt_range
from keyscan.verify import synthetic_token, verify_source, verify_sources


class FixtureScan(Keyscan):
    def _company_seeds(self):
        return [{"domain": "acme.test", "company_id": 999999, "company_name": "Acme"}]


def test_cli_progress_renders_milestones_without_network_noise():
    started = _progress_line({
        "at": "2026-08-21T12:30:00+00:00",
        "event": "crawl_start",
        "crawl": "CC-MAIN-2026-30",
        "snapshot": 1,
        "snapshots": 12,
        "files": 600,
        "vendors": 10,
    })
    assert started == (
        "[keyscan 12:30:00] CC-MAIN-2026-30 start | snapshot 1/12 | "
        "600 files | 10 vendors"
    )
    checkpoint = _progress_line({
        "at": "2026-08-21T12:34:56+00:00",
        "event": "scan_checkpoint",
        "crawl": "CC-MAIN-2026-30",
        "files": 104,
        "total_files": 600,
        "candidates": 1761,
        "vendors": 4,
        "took_ms": 323929,
    })
    assert checkpoint == (
        "[keyscan 12:34:56] CC-MAIN-2026-30 | files 104/600 (17%) | "
        "+1761 candidates | 4 vendors | 5m24s"
    )
    assert _progress_line({
        "at": "2026-08-21T12:34:56+00:00",
        "event": "request_timing",
        "message": "request timing",
        "host": "example.test",
    }) is None


def _flat(entry: dict) -> dict:
    """A log entry with its `detail` payload lifted, for assertions."""
    return {**entry, **(entry.get("detail") or {})}


def test_durable_log_event_is_forwarded_to_progress_observer(tmp_path: Path):
    events: list[dict] = []
    scan = Keyscan(tmp_path / "work", progress=events.append)
    scan._new_run(["commoncrawl"], 1, ["greenhouse"])
    scan._log(
        "info", "crawl", "scan checkpoint", event="scan_checkpoint",
        crawl="CC-MAIN-TEST", files=104, total_files=600,
        candidates=12, vendors=1, took_ms=1000,
    )
    scan._finish("interrupted")

    assert any(_flat(entry).get("event") == "scan_checkpoint" for entry in events)
    # Four fields carry every line; the rest is nested rather than merged in
    # beside them, so a detail can never shadow one of the four.
    assert all({"at", "level", "function", "message"} <= entry.keys() for entry in events)
    checkpoint = next(entry for entry in events if entry["message"] == "scan checkpoint")
    assert checkpoint["function"] == "crawl"
    assert checkpoint["detail"]["files"] == 104


def test_commoncrawl_date_range_selects_overlapping_crawl_weeks():
    collections = [
        {"id": "CC-MAIN-2026-30"},  # July 20-26
        {"id": "CC-MAIN-2026-25"},  # June 15-21
        {"id": "CC-MAIN-2026-21"},  # May 18-24
        {"id": "not-a-crawl"},
    ]
    assert _select_crawls(collections, {
        "date_from": "2026-06-21", "date_to": "2026-07-20",
    }) == ["CC-MAIN-2026-30", "CC-MAIN-2026-25"]


def test_commoncrawl_date_range_uses_exact_collection_coverage():
    collections = [{
        "id": "CC-MAIN-2026-30",
        "from": "2026-07-10T07:05:34",
        "to": "2026-07-23T01:13:28",
    }]
    # The ID resembles ISO week 30 (starting July 20), but the actual crawl
    # began ten days earlier; a July 10 range must still select it.
    assert _select_crawls(collections, {
        "date_from": "2026-07-10", "date_to": "2026-07-10",
    }) == ["CC-MAIN-2026-30"]


def test_select_crawls_prefers_explicit_crawl_ids_over_a_date_range():
    collections = [
        {"id": "CC-MAIN-2026-30"},
        {"id": "CC-MAIN-2026-25"},
        {"id": "CC-MAIN-2026-21"},
    ]
    # An id no longer in the live collection list is dropped rather than
    # scanned blind -- the picker's list can age relative to a launch.
    assert _select_crawls(collections, {
        "crawl_ids": ["CC-MAIN-2026-21", "CC-MAIN-2026-99"],
        "date_from": "2026-06-21", "date_to": "2026-07-20",
    }) == ["CC-MAIN-2026-21"]


def test_new_run_accepts_explicit_crawl_ids(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    with pytest.raises(KeyscanError, match="invalid crawl id"):
        scan._new_run(["commoncrawl"], crawl_ids=["not-a-crawl"])
    with pytest.raises(KeyscanError, match="not more than one"):
        scan._new_run(
            ["commoncrawl"], crawl_ids=["CC-MAIN-2026-30"],
            date_from="2026-01-01", date_to="2026-02-01",
        )

    run_id = scan._new_run(["commoncrawl"], crawl_ids=["CC-MAIN-2026-30", "CC-MAIN-2026-25"])
    assert scan.status(run_id)["config"] == {
        "archives": False,
        "crawl_ids": ["CC-MAIN-2026-30", "CC-MAIN-2026-25"],
        "sources": [],
    }
    scan._finish("cancelled")


def test_new_commoncrawl_run_requires_a_valid_complete_date_range(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    with pytest.raises(KeyscanError, match="both --from and --to"):
        scan._new_run(["commoncrawl"], date_from="2026-01-01")
    with pytest.raises(KeyscanError, match="must be YYYY-MM-DD"):
        scan._new_run(["commoncrawl"], date_from="2026/01/01", date_to="2026-02-01")
    with pytest.raises(KeyscanError, match="must not be later"):
        scan._new_run(["commoncrawl"], date_from="2026-02-01", date_to="2026-01-01")

    run_id = scan._new_run(
        ["commoncrawl"], date_from="2026-01-01", date_to="2026-02-01",
    )
    assert scan.status(run_id)["config"] == {
        "archives": False,
        "date_from": "2026-01-01",
        "date_to": "2026-02-01",
        "sources": [],
    }
    scan._finish("cancelled")


def transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_board_run_validates_and_logs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(keyscan_core, "BOARD_FEEDS", ({"repo": "acme/board", "path": "listings.json"},))

    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://raw.githubusercontent.com/acme/board/dev/listings.json":
            return httpx.Response(200, text=BOARD_LISTINGS)
        if request.url.host == "boards-api.greenhouse.io":
            if request.url.path.endswith("/jobs"):
                return httpx.Response(200, json={"jobs": []})
            return httpx.Response(200, json={"name": "Acme"})
        return httpx.Response(404)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["board", "validate"])

    assert scan.status(run_id)["status"] == "completed"
    live = {(row["ats"], row["key"]) for row in scan.list_candidates(status="live")}
    assert ("greenhouse", "acme") in live
    entries = [_flat(entry) for entry in scan.tail_log(run_id)[0]]
    assert any(entry["function"] == "validate" for entry in entries)
    # A request that succeeded is counted, not narrated: its only record is the
    # summary. That is the whole point -- 23,098 success lines across this
    # machine's runs said nothing a handful of summaries would not. A failure
    # still keeps its own line, because each one is separately actionable.
    summary = next(entry for entry in entries if entry.get("event") == "network_summary")
    failures = [entry for entry in entries if entry["message"] == "request failed"]
    assert summary["requests"] > len(failures)
    assert summary["failed"] == len(failures)
    assert summary["median_ms"] is not None
    assert any(entry.get("event") == "phase_timing" for entry in entries)


def test_avature_board_sighting_also_queues_the_companys_careers_board(tmp_path: Path):
    """Seeing `bupaanz/careersau` must not drop `bupaanz` (= /careers): the old
    rule probed it for every company, and it is often the main board."""
    scan = Keyscan(tmp_path / "work", transport=transport(lambda request: httpx.Response(404)))
    scan._new_run(["commoncrawl"], 1, ["avature"])
    scan._record_candidate(
        "avature", "Bupaanz/CareersAU", "cc", ["https://bupaanz.avature.net/careersau/JobDetail/1"],
    )
    scan._record_candidate("avature", "deloitteus/careers", "cc", ["https://deloitteus.avature.net/careers"])
    scan._finish("cancelled")

    found = {row["key"]: row["canonical_url"] for row in scan.list_candidates(source="avature")}
    assert found == {
        "bupaanz/careersau": "https://bupaanz.avature.net/careersau/SearchJobs",
        "bupaanz": "https://bupaanz.avature.net/careers/SearchJobs",
        "deloitteus": "https://deloitteus.avature.net/careers/SearchJobs",
    }


@pytest.mark.asyncio
async def test_probe_enriches_unbound_candidate_from_metadata(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": []})
        if request.url.path.endswith("/boards/acme"):
            return httpx.Response(200, json={"name": "Acme Incorporated"})
        return httpx.Response(404)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    scan._new_run(["commoncrawl"], 1, ["greenhouse"])
    key = scan._record_candidate(
        "greenhouse", "acme", "cc", ["https://boards.greenhouse.io/acme"],
    )
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        validation = await scan._probe(scan.list_candidates(source="greenhouse")[0])
        scan._set_validation(key, *validation)
    scan._finish("cancelled")

    candidate = scan.list_candidates(source="greenhouse")[0]
    assert candidate["details"]["company_name"] == "Acme Incorporated"
    assert validation[2]["company_name_source"] == "metadata_json"
    with scan._connect() as con:
        details = con.execute(
            "SELECT details FROM tenants WHERE ats = 'greenhouse' AND key = 'acme'"
        ).fetchone()["details"]
    assert json.loads(details)["company_name"] == "Acme Incorporated"


def test_workday_candidates_use_one_lowercase_list_identity(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    scan._new_run(["commoncrawl"], 1, ["workday"])

    first = scan._record_candidate(
        "workday",
        "NVIDIA/wd5/NVIDIAExternalCareerSite",
        "cc",
        ["https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"],
    )
    second = scan._record_candidate(
        "workday",
        "nvidia/wd5/nvidiaexternalcareersite",
        "board",
        ["https://nvidia.wd5.myworkdayjobs.com/en-US/nvidiaexternalcareersite"],
    )

    assert first == second == ("workday", "nvidia/wd5/nvidiaexternalcareersite")
    rows = scan.list_candidates(source="workday")
    assert len(rows) == 1
    assert rows[0]["key"] == "nvidia/wd5/nvidiaexternalcareersite"


@pytest.mark.asyncio
async def test_source_scoping_filters_discovery_and_validation(tmp_path: Path, monkeypatch):
    requested_hosts: list[str] = []
    monkeypatch.setattr(keyscan_core, "BOARD_FEEDS", ({"repo": "acme/board", "path": "listings.json"},))
    listings = json.dumps([
        {"url": "https://boards.greenhouse.io/acme", "company_name": "Acme", "active": True},
        {"url": "https://jobs.lever.co/acme", "company_name": "Acme", "active": True},
    ])

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if str(request.url) == "https://raw.githubusercontent.com/acme/board/dev/listings.json":
            return httpx.Response(200, text=listings)
        if request.url.host == "api.lever.co":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["board"], sources=["lever"])

    assert scan.status(run_id)["config"]["sources"] == ["lever"]
    assert [(row["ats"], row["key"]) for row in scan.list_candidates()] == [("lever", "acme")]
    assert "boards-api.greenhouse.io" not in requested_hosts


def test_source_scoping_rejects_unknown_source(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    with pytest.raises(KeyscanError, match="unknown source"):
        scan._new_run(["board"], 1, ["not-an-ats"])


def _source_selection(tmp_path: Path, sources: list[str]) -> tuple[list[str], str]:
    """Resolve `--sources` the way a real run does: expanded config -> rules -> SQL."""
    scan = Keyscan(tmp_path / "work")
    scan._new_run(["commoncrawl"], 1, sources)
    run = scan.status(scan._run_id)
    selected = scan._selected_sources(run)
    rules = [rule for rule in keyscan_core.RULES if selected is None or rule.source in selected]
    scan._finish("cancelled")
    scannable = [rule for rule in rules if rule.domains]
    return [rule.source for rule in rules], keyscan_core._merged_query(scannable, ["f.parquet"])


def test_a_source_group_is_exactly_its_expanded_member_list(tmp_path: Path):
    """`--sources tier1` must behave identically to naming its members.

    Order of the members must not matter either: selection runs off a set and
    the scan order comes from RULES, so the merged query has to come out
    byte-identical however the group was spelled.
    """
    members = list(SOURCE_GROUPS["tier1"])
    assert expand_sources(("tier1",)) == tuple(members)

    group_rules, group_sql = _source_selection(tmp_path / "a", ["tier1"])
    explicit_rules, explicit_sql = _source_selection(tmp_path / "b", members)
    reversed_rules, reversed_sql = _source_selection(tmp_path / "c", list(reversed(members)))
    # A group named alongside its own members must not double-count.
    dupes_rules, dupes_sql = _source_selection(tmp_path / "d", ["tier1"] + members)

    assert group_rules == explicit_rules == reversed_rules == dupes_rules
    assert group_sql == explicit_sql == reversed_sql == dupes_sql
    assert sorted(group_rules) == sorted(members)


def test_merged_query_narrows_each_branch_to_its_ranges_files():
    """With harvested footers, a branch lists only files its range can hit.

    Files outside every range would be pruned by DuckDB's zone maps anyway;
    narrowing here keeps them out of the SQL entirely, and a batch no branch
    can use produces no query at all (None), not an empty UNION.
    """
    greenhouse, ashby = BY_SOURCE["greenhouse"], BY_SOURCE["ashby"]
    gh_range = surt_range(greenhouse.domains[0])
    ashby_range = surt_range(ashby.domains[0])
    urls = ["a.parquet", "b.parquet"]

    narrowed = keyscan_core._merged_query(
        [greenhouse, ashby], urls,
        {gh_range: {"a.parquet", "b.parquet"}, ashby_range: {"b.parquet"}},
    )
    gh_branch, ashby_branch = [
        part for part in narrowed.split("UNION ALL") if "SELECT" in part
    ][:2]
    assert "'a.parquet'" in gh_branch and "'b.parquet'" in gh_branch
    assert "'a.parquet'" not in ashby_branch and "'b.parquet'" in ashby_branch

    # No mapping entry for a range means no file can hold it: branch dropped.
    only_gh = keyscan_core._merged_query(
        [greenhouse, ashby], urls, {gh_range: {"a.parquet"}},
    )
    assert "greenhouse" in only_gh and "ashby" not in only_gh

    # And a batch nothing overlaps is answered without DuckDB.
    assert keyscan_core._merged_query([greenhouse, ashby], urls, {}) is None

    # No mapping (unharvested crawl) keeps the old every-branch-every-file shape.
    full = keyscan_core._merged_query([greenhouse, ashby], urls)
    assert full.count("'a.parquet'") == full.count("'b.parquet'") == 2


BOARD_LISTINGS = json.dumps([
    {"url": "https://job-boards.greenhouse.io/acme", "company_name": "Acme", "title": "SWE Intern", "active": True},
    {"url": "https://jobs.ashbyhq.com/OpenAI/123", "company_name": "OpenAI", "title": "ML Intern", "active": True},
    {
        "url": "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
        "company_name": "NVIDIA", "title": "HW Intern", "active": True,
    },
    {"url": "https://www.tesla.com/careers/job/123", "company_name": "Tesla", "title": "Intern", "active": True},
    {
        "url": "https://boards.greenhouse.io/embed/job_app?token=1234567",
        "company_name": "Foo", "title": "Intern", "active": False,
    },
])


@pytest.mark.asyncio
async def test_board_run_extracts_tokens_from_listings(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(keyscan_core, "BOARD_FEEDS", ({"repo": "acme/board", "path": "listings.json"},))

    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://raw.githubusercontent.com/acme/board/dev/listings.json":
            return httpx.Response(200, text=BOARD_LISTINGS)
        return httpx.Response(404)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["board"])

    assert scan.status(run_id)["status"] == "completed"
    rows = {(row["ats"], row["key"]): row for row in scan.list_candidates()}
    assert set(rows) == {
        ("greenhouse", "acme"),
        # The listing spells it `OpenAI`; ashby ignores token casing, so it is
        # stored folded and cannot become a second row for the same board.
        ("ashby", "openai"),
        ("workday", "nvidia/wd5/nvidiaexternalcareersite"),
    }
    assert rows[("greenhouse", "acme")]["details"]["company_name"] == "Acme"

    # `method` records first-discovery provenance; a later probe does not
    # overwrite it.
    feed_url = "https://raw.githubusercontent.com/acme/board/dev/listings.json"
    with scan._connect() as con:
        methods = {row["method"] for row in con.execute("SELECT DISTINCT method FROM tenants")}
    assert methods == {f"board:{feed_url}"}


@pytest.mark.asyncio
async def test_board_run_skips_archives_by_default(tmp_path: Path, monkeypatch):
    requested: list[str] = []
    monkeypatch.setattr(
        keyscan_core, "BOARD_FEEDS",
        (
            {"repo": "acme/board", "path": "listings.json"},
            {"repo": "acme/board", "path": "archived/2025.json", "archive": True},
        ),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text="[]")

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["board"])

    assert scan.status(run_id)["status"] == "completed"
    assert requested == ["https://raw.githubusercontent.com/acme/board/dev/listings.json"]


@pytest.mark.asyncio
async def test_board_run_with_archives_reads_history_too(tmp_path: Path, monkeypatch):
    requested: list[str] = []
    monkeypatch.setattr(
        keyscan_core, "BOARD_FEEDS",
        (
            {"repo": "acme/board", "path": "listings.json"},
            {"repo": "acme/board", "path": "archived/2025.json", "archive": True},
        ),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text="[]")

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["board"], archives=True)

    assert scan.status(run_id)["status"] == "completed"
    assert scan.status(run_id)["config"]["archives"] is True
    assert set(requested) == {
        "https://raw.githubusercontent.com/acme/board/dev/listings.json",
        "https://raw.githubusercontent.com/acme/board/dev/archived/2025.json",
    }


@pytest.mark.asyncio
async def test_board_run_survives_malformed_json(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(keyscan_core, "BOARD_FEEDS", ({"repo": "acme/board", "path": "listings.json"},))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["board"])

    assert scan.status(run_id)["status"] == "completed"
    assert scan.list_candidates() == []
    entries, _ = scan.tail_log(run_id)
    assert any(
        entry["level"] == "warning" and "not valid JSON" in entry["message"] for entry in entries
    )


@pytest.mark.asyncio
async def test_board_run_resumes_from_its_checkpoint(tmp_path: Path):
    class InterruptOnce(Keyscan):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.interrupted = False

        async def _boards(self, run):
            self._checkpoint(board_index=1)
            if not self.interrupted:
                self.interrupted = True
                raise _Cancelled()
            self._record_candidate("greenhouse", "acme", "board:test", ["https://boards.greenhouse.io/acme"])

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io":
            # A live board with no open roles: /jobs is empty, but the board
            # metadata endpoint still names the company.  That name is what
            # separates a real empty board from a token that does not exist.
            if request.url.path.endswith("/jobs"):
                return httpx.Response(200, json={"jobs": []})
            return httpx.Response(200, json={"name": "Acme"})
        return httpx.Response(404)

    scan = InterruptOnce(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["board", "validate"])
    assert scan.status(run_id)["status"] == "cancelled"
    assert scan.status(run_id)["checkpoint"]["board_index"] == 1
    await scan.resume(run_id)
    assert scan.status(run_id)["status"] == "completed"
    assert scan.list_candidates(status="live")[0]["key"] == "acme"


@pytest.mark.asyncio
async def test_robots_reports_shared_blocks_and_per_tenant_unknown(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "job-boards.greenhouse.io"
        return httpx.Response(200, text="User-agent: CCBot\nDisallow: /\n")

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    results = await scan.robots(["greenhouse", "teamtailor"])

    assert results == [
        {"source": "greenhouse", "host": "job-boards.greenhouse.io", "status": "blocked"},
        {"source": "teamtailor", "host": "(per-tenant host)", "status": "unknown"},
    ]


@pytest.mark.asyncio
async def test_list_crawls_reports_real_windows_newest_first(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == keyscan_core.COLLINFO
        return httpx.Response(200, json=[
            {"id": "CC-MAIN-2026-21", "name": "May 2026 Index"},  # no from/to: falls back to the ISO week
            {
                "id": "CC-MAIN-2026-30", "name": "July 2026 Index",
                "from": "2026-07-10T07:05:34", "to": "2026-07-23T01:13:28",
            },
            {"id": "not-a-crawl", "name": "bogus"},  # unparseable either way: dropped
        ])

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    result = await scan.list_crawls()
    assert result["source"] == "live"
    assert result["error"] is None
    assert result["crawls"] == [
        {"id": "CC-MAIN-2026-30", "name": "July 2026 Index", "from": "2026-07-10", "to": "2026-07-23"},
        {"id": "CC-MAIN-2026-21", "name": "May 2026 Index", "from": "2026-05-18", "to": "2026-05-24"},
    ]


@pytest.mark.asyncio
async def test_list_crawls_falls_back_to_the_cache_when_unreachable(tmp_path: Path):
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=[{
                "id": "CC-MAIN-2026-30", "name": "July 2026 Index",
                "from": "2026-07-10T07:05:34", "to": "2026-07-23T01:13:28",
            }])
        raise httpx.ConnectError("network is down", request=request)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    live = await scan.list_crawls()
    assert live["source"] == "live"

    stale = await scan.list_crawls()
    assert stale["source"] == "cache"
    assert stale["error"] and "network is down" in stale["error"]
    assert stale["fetched_at"] == live["fetched_at"]
    assert stale["crawls"] == live["crawls"]


@pytest.mark.asyncio
async def test_list_crawls_raises_when_unreachable_and_never_cached(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network is down", request=request)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    with pytest.raises(KeyscanError, match="network is down"):
        await scan.list_crawls()


@pytest.mark.asyncio
async def test_validation_distinguishes_ats_html_and_generic_html(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "acme.applytojob.com":
            return httpx.Response(200, text='<html><title>JazzHR Job Listings</title><form id="form_search_jobs"></form><a class="job_title_link" href="/apply/jobs/details/123">Role</a></html>')
        if request.url.host.endswith("teamtailor.com"):
            return httpx.Response(200, text="<html><body>generic careers shell</body></html>")
        return httpx.Response(404)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    run_id = scan._new_run(["board"], 6)
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        xml_key = scan._record_candidate("jazzhr", "acme", "board:test", ["https://acme.applytojob.com/apply/"])
        html_key = scan._record_candidate("teamtailor", "acme", "board:test", ["https://acme.teamtailor.com/jobs"])
        xml = await scan._probe(scan.list_candidates(source="jazzhr")[0])
        generic = await scan._probe(scan.list_candidates(source="teamtailor")[0])
        scan._set_validation(xml_key, *xml)
        scan._set_validation(html_key, *generic)
    scan._finish("cancelled")

    assert scan.list_candidates(source="jazzhr")[0]["status"] == "live"
    assert scan.list_candidates(source="teamtailor")[0]["status"] == "unknown"
    assert scan.status(run_id)["status"] == "cancelled"


@pytest.mark.asyncio
async def test_avature_requires_vendor_and_jobboard_markers(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "acme.avature.net":
            return httpx.Response(200, text='<html><meta name="avature.portal.id" content="1"><title>Find Jobs</title></html>')
        if request.url.host == "plain.avature.net":
            return httpx.Response(200, text="<html><title>Find Jobs</title></html>")
        return httpx.Response(404)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    scan._new_run(["board"], 6)
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        scan._record_candidate("avature", "acme", "board:test", ["https://acme.avature.net/careers"])
        scan._record_candidate("avature", "plain", "board:test", ["https://plain.avature.net/careers"])
        live = await scan._probe(scan.list_candidates(source="avature")[0])
        rejected = await scan._probe(scan.list_candidates(source="avature")[1])
    scan._finish("cancelled")

    assert live[0] == "live"
    assert rejected[0] == "inconclusive"
    assert rejected[2]["reason"] == "missing_html_marker"


@pytest.mark.asyncio
async def test_dayforce_extracts_path_tenant_and_requires_board_markers(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "jobs.dayforcehcm.com" and request.url.path == "/en-US/acme/CANDIDATEPORTAL":
            return httpx.Response(200, text='<html><title>Job Board | Dayforce Jobs</title><script>var clientNamespace = "acme";</script></html>')
        if request.url.host == "jobs.dayforcehcm.com":
            return httpx.Response(404)
        return httpx.Response(404)

    assert extract("https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL") == ("dayforce", "acme")
    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    scan._new_run(["board"], 6)
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        scan._record_candidate("dayforce", "acme", "board:test", ["https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL"])
        validation = await scan._probe(scan.list_candidates(source="dayforce")[0])
    scan._finish("cancelled")

    assert validation[0] == "live"


@pytest.mark.asyncio
async def test_comeet_promotes_public_locator_to_adapter_composite(tmp_path: Path):
    api_token = "A1B2C3D4E5F6A7B8C9D0"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.comeet.com":
            return httpx.Response(200, text=f'<script>const jobs = "?token={api_token}";</script>')
        if request.url.host == "www.comeet.co" and request.url.path == "/careers-api/2.0/company/98.008/positions":
            assert request.url.params["token"] == api_token
            assert request.url.params["details"] == "true"
            return httpx.Response(200, json=[{"uid": "job-1"}, {"uid": "job-2"}])
        return httpx.Response(404)

    assert extract("https://www.comeet.co/jobs/acme/98.008") == ("comeet", "acme/98.008")
    assert extract("https://www.comeet.com/jobs/acme/98.008") == ("comeet", "acme/98.008")
    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    scan._new_run(["board"], 6)
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        key = scan._record_candidate("comeet", "acme/98.008", "board:test", ["https://www.comeet.com/jobs/acme/98.008"])
        validation, count, details = await scan._probe(scan.list_candidates(source="comeet")[0])
        scan._set_validation(key, validation, count, details)
    scan._finish("cancelled")

    candidate = scan.list_candidates(source="comeet")[0]
    assert (candidate["key"], candidate["status"], candidate["details"]["job_count"]) == (f"98.008/{api_token}", "live", 2)
    assert candidate["canonical_url"] == "https://www.comeet.com/jobs/acme/98.008"


@pytest.mark.asyncio
async def test_token_merges_sightings_across_methods(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    scan._new_run(["commoncrawl", "board"], 6)
    key = scan._record_candidate("greenhouse", "acme", "board:test", ["https://boards.greenhouse.io/acme"])
    scan._record_candidate(
        "greenhouse", "acme", "cc", ["https://boards.greenhouse.io/acme/careers"],
        details={"crawl": "CC-MAIN-TEST"},
    )
    scan._set_validation(key, "live", 2, {"responses": []})
    candidates = scan.list_candidates()
    assert len(candidates) == 1

    candidate = candidates[0]
    # `method` records first-discovery provenance only; the later `cc`
    # sighting merges its URL and details in without overwriting it.
    assert candidate["method"] == "board:test"
    assert candidate["status"] == "live"
    assert set(candidate["urls"]) == {
        "https://boards.greenhouse.io/acme", "https://boards.greenhouse.io/acme/careers",
    }
    assert candidate["details"]["crawl"] == "CC-MAIN-TEST"
    scan._finish("cancelled")


@pytest.mark.asyncio
async def test_commoncrawl_checkpoint_can_resume_without_network_parquet(tmp_path: Path):
    class CrawlFixture(FixtureScan):
        def _cc_scan_sources(self, crawl, rules, urls, loop, urls_for_range=None):
            return {
                rule.source: (
                    [{"token": "acme", "url_count": 3, "urls": ["https://boards.greenhouse.io/acme"]}]
                    if rule.source == "greenhouse" else []
                )
                for rule in rules
            }

    manifest = gzip.compress(b"crawl-data/CC-MAIN-TEST/cc-index/table/subset=warc/part.parquet\n")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://index.commoncrawl.org/collinfo.json"):
            return httpx.Response(200, json=[{"id": "CC-MAIN-TEST"}])
        if request.url.host == "data.commoncrawl.org":
            return httpx.Response(200, content=manifest)
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, json={"jobs": [{"id": 1}]})
        return httpx.Response(404)

    scan = CrawlFixture(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["commoncrawl", "validate"], crawls=1)
    status = scan.status(run_id)
    assert "CC-MAIN-TEST/greenhouse" in status["checkpoint"]["cc_done"]
    assert scan.list_candidates(source="greenhouse")[0]["status"] == "live"
    entries, _ = scan.tail_log(run_id)
    timing = next(entry for entry in map(_flat, entries) if entry.get("event") == "operation_timing")
    assert timing["operation"] == "commoncrawl_merged_scan"
    assert timing["crawl"] == "CC-MAIN-TEST"


@pytest.mark.asyncio
async def test_commoncrawl_attempts_later_snapshots_but_fails_an_incomplete_run(tmp_path: Path):
    attempted: list[str] = []

    class PartiallyFailingScan(FixtureScan):
        def _cc_scan_sources(self, crawl, rules, urls, loop, urls_for_range=None):
            attempted.append(crawl)
            if crawl == "CC-MAIN-BROKEN":
                raise duckdb.IOException("Could not resolve hostname")
            return {rule.source: [] for rule in rules}

    manifest = gzip.compress(b"crawl-data/test/cc-index/table/subset=warc/part.parquet\n")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://index.commoncrawl.org/collinfo.json"):
            return httpx.Response(200, json=[
                {"id": "CC-MAIN-BROKEN"},
                {"id": "CC-MAIN-GOOD"},
            ])
        if request.url.host == "data.commoncrawl.org":
            return httpx.Response(200, content=manifest)
        return httpx.Response(404)

    scan = PartiallyFailingScan(tmp_path / "work", transport=transport(handler))
    with pytest.raises(KeyscanError, match="CC-MAIN-BROKEN"):
        await scan.run(["commoncrawl"], crawls=2, sources=["greenhouse"])

    assert attempted == ["CC-MAIN-BROKEN", "CC-MAIN-GOOD"]
    status = scan.status()
    assert status["status"] == "failed"
    assert status["error"].startswith("KeyscanError: 1 Common Crawl snapshot(s) failed")
    assert status["checkpoint"]["cc_done"] == ["CC-MAIN-GOOD/greenhouse"]


def test_commoncrawl_reads_a_persisted_synthetic_parquet(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    scan._new_run(["commoncrawl"], 1)
    output = scan._artifact() / "commoncrawl" / "CC-MAIN-TEST" / "greenhouse.parquet"
    output.parent.mkdir(parents=True)
    con = duckdb.connect()
    try:
        con.execute(
            f"COPY (SELECT 'acme' AS token, 3::BIGINT AS url_count, "
            f"['https://boards.greenhouse.io/acme'] AS urls) TO '{output}' (FORMAT PARQUET)"
        )
    finally:
        con.close()
    # The cache file already exists, so the vendor is excluded from `pending`
    # and the batch loop never runs -- `loop` is never touched, a real one
    # isn't needed here.
    assert scan._cc_scan_sources("CC-MAIN-TEST", [BY_SOURCE["greenhouse"]], [], asyncio.new_event_loop()) == {
        "greenhouse": [{"token": "acme", "url_count": 3, "urls": ["https://boards.greenhouse.io/acme"]}]
    }
    scan._finish("cancelled")



def _synthetic_index(path: Path, rows: list[tuple[str, str]]) -> str:
    """Write a tiny stand-in for a Common Crawl index shard.

    Only the three columns the scan actually reads: `url`, `url_host_name` and
    `url_surtkey` (the sorted key the range predicate prunes on).
    """
    values = ", ".join(
        "('" + url + "', '" + host + "', '"
        + ",".join(reversed(host.split("."))) + ")/')"
        for url, host in rows
    )
    con = duckdb.connect()
    try:
        con.execute(
            f"COPY (SELECT * FROM (VALUES {values}) AS t(url, url_host_name, url_surtkey)) "
            f"TO '{path}' (FORMAT PARQUET)"
        )
    finally:
        con.close()
    return str(path)


def test_one_merged_scan_attributes_every_vendor_and_caches_each_separately(tmp_path: Path):
    """The whole point of merging: one pass over the index, N per-vendor caches."""
    shard = _synthetic_index(tmp_path / "index.parquet", [
        ("https://boards.greenhouse.io/acme", "boards.greenhouse.io"),
        ("https://boards.greenhouse.io/acme/jobs/1", "boards.greenhouse.io"),
        ("https://jobs.lever.co/globex", "jobs.lever.co"),
        ("https://example.com/careers", "example.com"),
    ])
    cache = tmp_path / "cache"
    rules = [BY_SOURCE["greenhouse"], BY_SOURCE["lever"]]

    results = keyscan_core.cc_scan_sources(
        "CC-MAIN-TEST", rules, [shard], lambda source: cache / f"{source}.parquet",
        target_mbps=0,
    )

    assert {s: [r["token"] for r in rows] for s, rows in results.items()} == {
        "greenhouse": ["acme"], "lever": ["globex"],
    }
    # greenhouse was seen twice, lever once -- proof the GROUP BY is per source.
    assert results["greenhouse"][0]["url_count"] == 2
    assert results["lever"][0]["url_count"] == 1
    # The merged result is split back into the per-vendor cache layout, and the
    # merged temp file is not left behind.
    assert (cache / "greenhouse.parquet").exists()
    assert (cache / "lever.parquet").exists()
    assert not list(cache.glob(".merged-*"))


@pytest.mark.asyncio
async def test_candidates_are_recorded_at_checkpoints_not_only_at_the_end(tmp_path: Path):
    """The merge silently cost `_validation_worker` its overlap: nothing used
    to reach `_record_candidate` until the entire 600-file scan returned, so
    "streaming" validation only ever started once discovery was fully done.
    `on_candidates` exists to fix that -- this proves it, by calling
    `_cc_scan_sources` exactly as `_commoncrawl` does (real event loop, real
    `call_soon_threadsafe`) and nothing else: no final re-recording loop runs
    here, so a candidate in the tenants table only got there via a checkpoint.
    """
    shards = [
        _synthetic_index(tmp_path / f"s{n}.parquet",
                          [(f"https://boards.greenhouse.io/co{n}", "boards.greenhouse.io")])
        for n in range(9)  # 9 files: default batch_files=8 makes two batches, [8, 1]
    ]
    scan = Keyscan(tmp_path / "work", checkpoint_files=4, target_mbps=0)
    scan._new_run(["commoncrawl"], 1)

    loop = asyncio.get_running_loop()
    records = await asyncio.to_thread(
        scan._cc_scan_sources, "CC-MAIN-TEST", [BY_SOURCE["greenhouse"]], shards, loop,
    )
    scan._finish("cancelled")

    # The pure scan result covers all 9 -- unaffected by when recording ran.
    assert {r["token"] for r in records["greenhouse"]} == {f"co{n}" for n in range(9)}
    # Every token is already in the tenants table, though `_cc_scan_sources`
    # itself never calls `_record_candidate` and no final loop ran afterward
    # -- the only path to the DB here was `on_candidates` at a checkpoint.
    tokens = {row["key"] for row in scan.list_candidates(source="greenhouse")}
    assert tokens == {f"co{n}" for n in range(9)}


@pytest.mark.asyncio
async def test_a_probe_fires_before_the_merged_scan_finishes(tmp_path: Path, monkeypatch):
    """The end-to-end claim, not just the recording half: a real HTTP probe
    must go out to the vendor while `_cc_scan_sources` is still running, using
    the actual validation workers `Keyscan.run()` starts -- not a stand-in.

    `CC_CDN` is patched to a local directory so DuckDB reads real files with
    no network involved, while httpx (mocked) still serves the manifest calls
    and the one probe this test cares about.
    """
    data_dir = tmp_path / "data"
    (data_dir / "cc-index" / "table" / "subset=warc").mkdir(parents=True)
    shard = _synthetic_index(
        data_dir / "cc-index" / "table" / "subset=warc" / "part-00000.parquet",
        [("https://boards.greenhouse.io/acme", "boards.greenhouse.io")],
    )
    monkeypatch.setattr(keyscan_core, "CC_CDN", str(data_dir) + "/")
    manifest = gzip.compress(b"cc-index/table/subset=warc/part-00000.parquet\n")

    probed = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://index.commoncrawl.org/collinfo.json"):
            return httpx.Response(200, json=[{"id": "CC-MAIN-TEST"}])
        if request.url.host == "data.commoncrawl.org":
            return httpx.Response(200, content=manifest)
        if request.url.host == "boards-api.greenhouse.io":
            probed.set()
            # Never resolves during the scan: proves the probe went out while
            # `_cc_scan_sources` was still mid-flight, not merely queued.
            await asyncio.sleep(10)
            return httpx.Response(200, json={"jobs": [{"id": 1}]})
        return httpx.Response(404)

    scan = Keyscan(tmp_path / "work", transport=transport(handler), checkpoint_files=1, target_mbps=0)
    run_task = asyncio.create_task(scan.run(["commoncrawl", "validate"], crawls=1, sources=["greenhouse"]))
    try:
        await asyncio.wait_for(probed.wait(), timeout=5)
    finally:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task

    assert probed.is_set()


def test_pacing_waits_long_enough_to_hold_the_target_rate():
    """Pure arithmetic, no network: the sleep that holds `target_bps`."""
    mbps = 60.0 * 1_000_000
    # 60 Mbit moved in 0.2s -- a 60 Mbps budget needs a full second, so wait 0.8s.
    assert keyscan_core._pace_seconds(7_500_000, 0.2, mbps) == pytest.approx(0.8)
    # Already at or under the target: nothing to wait for.
    assert keyscan_core._pace_seconds(7_500_000, 5.0, mbps) == 0.0
    assert keyscan_core._pace_seconds(0, 1.0, mbps) == 0.0
    # target 0 disables pacing outright.
    assert keyscan_core._pace_seconds(10_000_000, 0.0, 0) == 0.0
    # Unreadable link counters fall back to a 50% duty cycle rather than to
    # no limit at all -- failing slow is the safe direction here.
    assert keyscan_core._pace_seconds(None, 3.0, mbps) == 3.0


def test_duckdb_http_settings_use_seconds_and_a_bounded_retry_window(monkeypatch):
    class FakeConnection:
        def __init__(self):
            self.statements: list[str] = []

        def execute(self, statement):
            self.statements.append(statement)
            return self

    con = FakeConnection()
    monkeypatch.setattr(keyscan_core.duckdb, "connect", lambda: con)

    assert keyscan_core._duckdb_http() is con
    assert "SET http_timeout=120" in con.statements
    assert "SET http_retries=3" in con.statements
    assert "SET http_retry_backoff=2" in con.statements
    assert "SET http_retry_wait_ms=250" in con.statements
    assert "SET http_timeout=120000" not in con.statements


def test_failed_remote_batch_reopens_connection_without_replaying_completed_batch(
    tmp_path: Path, monkeypatch,
):
    first = ("greenhouse", "acme", 1, ["https://boards.greenhouse.io/acme"])
    second = ("greenhouse", "globex", 1, ["https://boards.greenhouse.io/globex"])

    class Result:
        def __init__(self, rows=()):
            self.rows = list(rows)

        def fetchall(self):
            return self.rows

    class FakeConnection:
        def __init__(self, generation: int):
            self.generation = generation
            self.remote_batches: list[str] = []
            self.restored: list[tuple] = []
            self.closed = False

        def execute(self, statement):
            if statement.startswith("INSERT INTO _batches"):
                shard = "first" if "first.parquet" in statement else "second"
                self.remote_batches.append(shard)
                if self.generation == 1 and shard == "second":
                    raise duckdb.IOException("Could not resolve hostname")
                return Result([first] if shard == "first" else [second])
            if statement.startswith("COPY"):
                (tmp_path / "cache" / "greenhouse.part").touch()
            return Result()

        def executemany(self, _statement, rows):
            self.restored.extend(rows)
            return self

        def close(self):
            self.closed = True

    connections: list[FakeConnection] = []

    def connect():
        con = FakeConnection(len(connections) + 1)
        connections.append(con)
        return con

    monkeypatch.setattr(keyscan_core, "_duckdb_http", connect)
    monkeypatch.setattr(keyscan_core, "_read_cache", lambda _target: [])
    retries: list[dict] = []
    candidates: list[tuple] = []

    keyscan_core.cc_scan_sources(
        "CC-MAIN-TEST", [BY_SOURCE["greenhouse"]],
        ["first.parquet", "second.parquet"],
        lambda source: tmp_path / "cache" / f"{source}.parquet",
        target_mbps=0, batch_files=1, checkpoint_files=1, batch_backoff=(0.0,),
        on_candidates=candidates.extend, on_retry=retries.append,
    )

    assert len(connections) == 2
    assert connections[0].remote_batches == ["first", "second"]
    assert connections[0].closed
    assert connections[1].restored == [first]
    assert connections[1].remote_batches == ["second"]
    assert candidates == [first, second]
    assert retries == [{
        "files": 1,
        "batch_files": 1,
        "attempt": 2,
        "max_attempts": keyscan_core.BATCH_ATTEMPTS,
        "backoff_s": 0.0,
        "error": "IOException: Could not resolve hostname",
    }]


def test_batching_matches_an_unbatched_scan(tmp_path: Path):
    """Batching may change throughput; it must not change results.

    `url_count` has to sum across batches and `urls` has to re-take the five
    smallest overall, so a token split across batch boundaries still lands
    exactly as a single pass would have written it.
    """
    shards = [
        _synthetic_index(tmp_path / f"i{n}.parquet", rows)
        for n, rows in enumerate([
            [("https://boards.greenhouse.io/acme/jobs/5", "boards.greenhouse.io"),
             ("https://boards.greenhouse.io/acme/jobs/6", "boards.greenhouse.io")],
            [("https://boards.greenhouse.io/acme", "boards.greenhouse.io"),
             ("https://jobs.lever.co/globex", "jobs.lever.co")],
            [("https://boards.greenhouse.io/acme/jobs/1", "boards.greenhouse.io")],
        ])
    ]
    rules = [BY_SOURCE["greenhouse"], BY_SOURCE["lever"]]

    def scan(cache_name: str, batch_files: int):
        cache = tmp_path / cache_name
        return keyscan_core.cc_scan_sources(
            "CC-MAIN-TEST", rules, shards, lambda s: cache / f"{s}.parquet",
            target_mbps=0, batch_files=batch_files,
        )

    one_pass = scan("whole", 99)     # every shard in a single batch
    batched = scan("split", 1)       # one shard per batch

    assert batched == one_pass
    gh = batched["greenhouse"][0]
    assert gh["url_count"] == 4                       # 2 + 1 + 1, summed across batches
    assert gh["urls"][0] == "https://boards.greenhouse.io/acme"   # smallest overall


def test_checkpoint_fires_every_n_files_with_per_window_stats(tmp_path: Path):
    """The only visibility into a merged scan from outside the worker thread.

    Without this, a scan across hundreds of files logs nothing until the
    whole thing returns -- which at 60 Mbps over 600 files is well over an
    hour of silence. `checkpoint_files` must be independent of `batch_files`:
    a checkpoint can land mid-batch-count, so this uses values with no common
    multiple below the total to prove they aren't secretly coupled.
    """
    shards = [
        _synthetic_index(tmp_path / f"s{n}.parquet", [
            (f"https://boards.greenhouse.io/co{n}", "boards.greenhouse.io"),
            (f"https://jobs.lever.co/co{n}", "jobs.lever.co"),
        ])
        for n in range(7)
    ]
    cache = tmp_path / "cache"
    rules = [BY_SOURCE["greenhouse"], BY_SOURCE["lever"]]
    checkpoints: list[dict] = []

    keyscan_core.cc_scan_sources(
        "CC-MAIN-TEST", rules, shards, lambda s: cache / f"{s}.parquet",
        target_mbps=0, batch_files=3, checkpoint_files=2,
        on_checkpoint=checkpoints.append,
    )

    # Batches are size 3 against a checkpoint interval of 2, so a batch always
    # overshoots the next boundary: each batch's *entire* haul reports as one
    # checkpoint (files_done checked once per batch, not once per file), which
    # is what pushes the windows to sizes 3/3/1 -- files=[3, 6, 7] -- rather
    # than landing cleanly on 2/4/6. Production now runs the two equal
    # (CHECKPOINT_FILES = BATCH_FILES), so a batch lands exactly on a boundary
    # and there is no overshoot at all; exercising it here at a ratio that
    # cannot divide evenly is what proves the two knobs stay independent and
    # that a checkpoint is never skipped outright even when a batch spans one.
    assert [c["files"] for c in checkpoints] == [3, 6, 7]
    # Each company appears under both vendors in every file, so an N-file
    # window finds N companies x 2 vendors = 2N fresh (source, token) pairs.
    assert [c["candidates"] for c in checkpoints] == [6, 6, 2]
    assert [c["vendors"] for c in checkpoints] == [2, 2, 2]
    assert all(c["total_files"] == 7 for c in checkpoints)
    assert all(isinstance(c["took_ms"], int) and c["took_ms"] >= 0 for c in checkpoints)


def test_a_cancelled_scan_leaves_no_partial_cache(tmp_path: Path):
    """Cancelling mid-scan must abort, not half-write a vendor's cache file."""
    shards = [
        _synthetic_index(tmp_path / f"c{n}.parquet",
                         [("https://boards.greenhouse.io/acme", "boards.greenhouse.io")])
        for n in range(4)
    ]
    cache = tmp_path / "cache"
    calls = {"n": 0}

    def should_cancel():
        calls["n"] += 1
        if calls["n"] > 2:
            raise _Cancelled()

    with pytest.raises(_Cancelled):
        keyscan_core.cc_scan_sources(
            "CC-MAIN-TEST", [BY_SOURCE["greenhouse"]], shards,
            lambda s: cache / f"{s}.parquet",
            target_mbps=0, batch_files=1, should_cancel=should_cancel,
        )
    assert not (cache / "greenhouse.parquet").exists()
    assert not list(cache.glob("*.part"))


def test_a_cached_vendor_is_not_rescanned(tmp_path: Path):
    """A vendor already cached for this crawl must cost no further index reads."""
    shard = _synthetic_index(tmp_path / "index.parquet", [
        ("https://boards.greenhouse.io/acme", "boards.greenhouse.io"),
        ("https://jobs.lever.co/globex", "jobs.lever.co"),
    ])
    cache = tmp_path / "cache"
    target_for = lambda source: cache / f"{source}.parquet"
    rules = [BY_SOURCE["greenhouse"], BY_SOURCE["lever"]]
    keyscan_core.cc_scan_sources("CC-MAIN-TEST", rules, [shard], target_for, target_mbps=0)

    # Re-running with the shard removed still returns both vendors from cache;
    # had either been re-scanned, the missing file would raise.
    Path(shard).unlink()
    again = keyscan_core.cc_scan_sources("CC-MAIN-TEST", rules, [shard], target_for, target_mbps=0)
    assert {s: [r["token"] for r in rows] for s, rows in again.items()} == {
        "greenhouse": ["acme"], "lever": ["globex"],
    }


@pytest.mark.asyncio
async def test_candidates_validate_during_discovery_not_only_after(tmp_path: Path):
    """The token must be live before the scan that found it has finished."""
    live_while_crawling: list[bool] = []

    class SlowCrawl(Keyscan):
        async def _boards(self, run):
            self._record_candidate("greenhouse", "acme", "board:test", ["https://boards.greenhouse.io/acme"])
            # Give the background validator room to probe before discovery ends.
            for _ in range(50):
                await asyncio.sleep(0)
                if self.list_candidates(status="live"):
                    break
            live_while_crawling.append(bool(self.list_candidates(status="live")))

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, json={"jobs": [{"id": 1}]})
        return httpx.Response(404)

    scan = SlowCrawl(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["board", "validate"])

    assert any(live_while_crawling), "no candidate was live before discovery ended"
    assert scan.status(run_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_streaming_validation_leaves_nothing_for_the_final_sweep(tmp_path: Path):
    swept: list[int] = []

    class CountingSweep(Keyscan):
        async def _boards(self, run):
            self._record_candidate("greenhouse", "acme", "board:test", ["https://boards.greenhouse.io/acme"])

        async def _validate(self, run):
            with self._connect() as con:
                before = con.execute("SELECT count(*) FROM tenants WHERE checked_at IS NULL").fetchone()[0]
            swept.append(before)
            await super()._validate(run)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io":
            if request.url.path.endswith("/jobs"):
                return httpx.Response(200, json={"jobs": []})
            return httpx.Response(200, json={"name": "Acme"})
        return httpx.Response(404)

    scan = CountingSweep(tmp_path / "work", transport=transport(handler))
    await scan.run(["board", "validate"])

    assert swept == [0]
    assert scan.list_candidates(status="live")[0]["key"] == "acme"


@pytest.mark.asyncio
async def test_probe_failure_is_logged_without_aborting_the_run(tmp_path: Path):
    class BrokenProbe(Keyscan):
        async def _boards(self, run):
            self._record_candidate("greenhouse", "acme", "board:test", ["https://boards.greenhouse.io/acme"])

        async def _probe(self, candidate):
            raise RuntimeError("probe exploded")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    scan = BrokenProbe(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["board", "validate"])

    # One unprobeable token is a logged fact, not a failed scan.  It stays
    # unprobed, so the next run retries it.
    assert scan.status(run_id)["status"] == "completed"
    entries, _ = scan.tail_log(run_id)
    assert any(entry["message"] == "candidate probe failed" for entry in entries)
    with scan._connect() as con:
        row = con.execute("SELECT status, checked_at FROM tenants WHERE ats = 'greenhouse' AND key = 'acme'").fetchone()
    assert row["status"] == "unknown" and row["checked_at"] is None


@pytest.mark.asyncio
async def test_validate_method_drains_a_backlog_without_crawling(tmp_path: Path):
    crawled: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        crawled.append(request.url.host)
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, json={"jobs": [{"id": 1}]})
        return httpx.Response(404)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    # A backlog left behind by an earlier, unvalidated discovery run.
    scan._new_run(["board"], 6)
    for index in range(3):
        scan._record_candidate(
            "greenhouse", f"acme-{index}", "board:test", [f"https://boards.greenhouse.io/acme-{index}"],
        )
    scan._finish("interrupted")
    with scan._connect() as con:
        unprobed = con.execute("SELECT count(*) FROM tenants WHERE checked_at IS NULL").fetchone()[0]
    assert unprobed == 3

    run_id = await scan.run(["validate"])

    assert scan.status(run_id)["status"] == "completed"
    assert len(scan.list_candidates(status="live")) == 3
    # Only vendor probe endpoints, never a company domain or an index.
    assert crawled and all(host.endswith("greenhouse.io") for host in crawled), crawled





def test_rules_match_in_python_and_duckdb():
    assert selftest() == []


def test_rippling_locale_url_and_personio_workzag_xml():
    assert extract("https://ats.rippling.com/en-GB/aaca/jobs/123") == ("rippling", "aaca")
    assert extract("https://ats.rippling.com/aaca/jobs/123") == ("rippling", "aaca")
    from keyscan.core import _xml_job_count
    assert _xml_job_count('<workzag-jobs><position><id>1</id></position></workzag-jobs>') == 1


def test_trakstar_replaces_legacy_recruiterbox_urls():
    assert extract("https://acme.hire.trakstar.com/jobs/123") == ("trakstar", "acme")
    assert extract("https://acme.recruiterbox.com/") is None


def test_new_rules_emit_exact_adapter_keys():
    assert extract(
        "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
        "?ccId=19000101_000001&lang=en_US&cid=d16ba13d-474e-4326-b628-74c87f0b289a"
    ) == ("adp", "d16ba13d-474e-4326-b628-74c87f0b289a/19000101_000001")
    assert extract(
        "https://recruiting2.ultipro.com/ARU1000ARUP/JobBoard/"
        "62cc791d-612e-42e6-909f-0de27efe2038/OpportunityDetail"
    ) == (
        "ukg",
        "recruiting2.ultipro.com/ARU1000ARUP/62cc791d-612e-42e6-909f-0de27efe2038",
    )
    assert extract(
        "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_45001/job/1"
    ) == ("oracle", "eeho.fa.us2/CX_45001")
    assert extract(
        "https://apply.careers.microsoft.com/careers/job/1?location=US&domain=microsoft.com"
    ) == ("eightfold", "apply.careers.microsoft.com/microsoft.com")


@pytest.mark.asyncio
async def test_new_json_rules_use_required_request_contracts(tmp_path: Path):
    seen: dict[str, httpx.Request] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.host] = request
        if request.url.host == "workforcenow.adp.com":
            return httpx.Response(200, json={"jobRequisitions": [{}], "meta": {"totalNumber": 51}})
        if request.url.host == "albatross.recruitcrm.io":
            return httpx.Response(200, json={"data": {"jobs": [{}, {}], "account": {"title": "Acme"}}})
        if request.url.host == "recruiting2.ultipro.com":
            return httpx.Response(200, json={"opportunities": [{}], "totalCount": 153})
        if request.url.host == "eeho.fa.us2.oraclecloud.com":
            # oracle now confirms the site is one the tenant actually lists
            # before it trusts the tenant-scoped requisition count.
            if "recruitingCESites" in request.url.path:
                return httpx.Response(200, json={"count": 1, "items": [{"SiteNumber": "CX_45001"}]})
            return httpx.Response(200, json={"items": [{"TotalJobsCount": 2315, "requisitionList": [{}]}]})
        return httpx.Response(404)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    scan._new_run(["board"], 6)
    tokens = {
        "adp": "d16ba13d-474e-4326-b628-74c87f0b289a/19000101_000001",
        "recruitcrm": "Acme_jobs",
        "ukg": "recruiting2.ultipro.com/ARU1000ARUP/62cc791d-612e-42e6-909f-0de27efe2038",
        "oracle": "eeho.fa.us2/CX_45001",
    }
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        results = {}
        for source, token in tokens.items():
            scan._record_candidate(source, token, "board:test", [BY_SOURCE[source].board_url.format(**BY_SOURCE[source].urls(token))])
            results[source] = await scan._probe(scan.list_candidates(source=source)[0])
    scan._finish("cancelled")

    assert {source: result[:2] for source, result in results.items()} == {
        "adp": ("live", 51),
        "recruitcrm": ("live", 2),
        "ukg": ("live", 153),
        "oracle": ("live", 2315),
    }
    recruitcrm = seen["albatross.recruitcrm.io"]
    assert recruitcrm.method == "POST"
    assert recruitcrm.headers["origin"] == "https://careers.recruitcrm.io"
    assert seen["recruiting2.ultipro.com"].method == "POST"


@pytest.mark.asyncio
async def test_oleeo_requires_supported_atom_shape(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "asp.tal.net":
            return httpx.Response(
                200,
                text=(
                    '<feed xmlns="http://www.w3.org/2005/Atom">'
                    '<entry><id>1</id><content type="text">Role</content></entry></feed>'
                ),
            )
        return httpx.Response(404)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    scan._new_run(["board"], 6)
    key = scan._record_candidate(
        "oleeo", "asp/14", "board:test", ["https://asp.tal.net/candidate/jobboard/vacancy/14"],
        details={"company_name": "Avon and Somerset Police"},
    )
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        validation = await scan._probe(scan.list_candidates(source="oleeo")[0])
        scan._set_validation(key, *validation)
    scan._finish("cancelled")

    assert validation[:2] == ("live", 1)



@pytest.mark.asyncio
async def test_icims_vanity_token_retrieves_its_json_job_listings(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://careers.paychex.com/api/jobs?page=1"):
            return httpx.Response(200, json={"jobs": [{"data": {"req_id": "1"}}], "totalCount": 1})
        return httpx.Response(404)

    assert extract("https://careers.paychex.com/careers/jobs") == ("icims", "paychex")

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    scan._new_run(["board"], 6)
    scan._record_candidate(
        "icims", "paychex", "board:test", ["https://careers.paychex.com/careers/jobs"],
        details={"company_name": "Paychex"},
    )
    candidate = scan.list_candidates(source="icims")[0]
    assert candidate["key"] == "paychex"
    assert candidate["canonical_url"] == "https://careers.paychex.com/api/jobs?page=1"
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        validation, count, _ = await scan._probe(candidate)
    scan._finish("cancelled")

    assert (validation, count) == ("live", 1)
    assert extract("https://careers-paychex.icims.com/jobs/1") is None


# ----------------------------------------------------------------------
# verify
# ----------------------------------------------------------------------

_CC_MANIFEST = gzip.compress(b"crawl-data/CC-MAIN-TEST/cc-index/table/subset=warc/part.parquet\n")


def _probe(rule, token, handler):
    async def run():
        async with httpx.AsyncClient(transport=transport(handler)) as client:
            return await probe_rule(rule, token, request=partial(default_request, client), controls={})
    return run()


@pytest.mark.asyncio
async def test_workday_disabled_site_is_absent_but_a_waf_403_is_not():
    """Workday answers a disabled site with a JSON 403 (S22). That is the board
    being gone, not us being refused -- unlike a WAF's HTML 403."""
    async def disabled(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errorCode": "S22", "httpStatus": 403, "message": "permission denied"})

    async def waf(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<html>cloudflare</html>")

    status, count, details = await _probe(BY_SOURCE["workday"], "acme/wd1/old", disabled)
    assert (status, count, details["reason"]) == ("absent", None, "site_disabled")

    status, _, details = await _probe(BY_SOURCE["workday"], "acme/wd1/old", waf)
    assert (status, details["reason"]) == ("inconclusive", "blocked")

    async def private(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errorCode": "S99"})

    status, _, details = await _probe(BY_SOURCE["workday"], "acme/wd1/old", private)
    assert (status, details["reason"]) == ("inconclusive", "http_403")


@pytest.mark.asyncio
async def test_workday_empty_site_is_live_but_smartrecruiters_zero_is_not():
    async def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 0, "jobPostings": [], "facets": [], "userAuthenticated": False})

    assert (await _probe(BY_SOURCE["workday"], "acme/wd1/site", empty))[:2] == ("live", 0)

    async def sr_empty(request: httpx.Request) -> httpx.Response:
        # The departments check that would settle it answers neither way;
        # test_probe_dns covers the cases where it does.
        if request.url.path.endswith("/departments"):
            return httpx.Response(500)
        return httpx.Response(200, json={"totalFound": 0, "content": []})

    status, _, details = await _probe(BY_SOURCE["smartrecruiters"], "nobody", sr_empty)
    assert (status, details["reason"]) == ("inconclusive", "zero_count")


@pytest.mark.asyncio
async def test_workday_422_finds_the_tenant_on_another_shard():
    """A wrong `wdN` host answers 422 and does not redirect; the probe asks the
    other shards and names the token that works."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "acme.wd3.myworkdayjobs.com":
            return httpx.Response(200, json={"total": 4, "jobPostings": []})
        return httpx.Response(422, json={"errorCode": "S1"})

    status, _, details = await _probe(BY_SOURCE["workday"], "acme/wd5/site", handler)
    assert (status, details["reason"], details["moved_to"]) == ("absent", "wrong_shard", "acme/wd3/site")
    hosts = [url.split("/")[2] for url, _ in details["responses"]]
    assert hosts == ["acme.wd5.myworkdayjobs.com", "acme.wd1.myworkdayjobs.com", "acme.wd3.myworkdayjobs.com"]


@pytest.mark.asyncio
async def test_workday_422_on_every_shard_is_a_missing_tenant():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422)

    status, _, details = await _probe(BY_SOURCE["workday"], "gone/wd1/site", handler)
    assert (status, details["reason"]) == ("absent", "tenant_not_found")
    assert "moved_to" not in details
    assert len(details["responses"]) == len(BY_SOURCE["workday"].shards)


@pytest.mark.asyncio
async def test_workday_shard_search_stops_on_a_refusal():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.startswith("acme.wd1."):
            return httpx.Response(429)
        return httpx.Response(422)

    status, _, details = await _probe(BY_SOURCE["workday"], "acme/wd5/site", handler)
    assert (status, details["reason"]) == ("inconclusive", "http_429")


@pytest.mark.asyncio
async def test_oracle_rejects_a_site_its_tenant_does_not_list():
    """recruitingCEJobRequisitions ignores `siteNumber` and answers 200 with the
    tenant's whole requisition list, so a bogus site used to look live. Only the
    site-scoped endpoint can tell, and it must run *before* the job probe."""
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if "recruitingCESites" in request.url.path:
            return httpx.Response(200, json={"count": 0, "items": []})
        raise AssertionError("job endpoint must not be reached for an unlisted site")

    status, count, details = await _probe(BY_SOURCE["oracle"], "eeho.fa.us2/NOPE", handler)

    assert (status, count) == ("absent", None)
    assert details["reason"] == "board_not_registered"
    assert not any("JobRequisitions" in path for path in seen)


@pytest.mark.asyncio
async def test_oracle_still_counts_jobs_for_a_listed_site():
    async def handler(request: httpx.Request) -> httpx.Response:
        if "recruitingCESites" in request.url.path:
            return httpx.Response(200, json={"count": 1, "items": [{"SiteNumber": "CX_1"}]})
        return httpx.Response(200, json={"items": [{"TotalJobsCount": 81}]})

    assert (await _probe(BY_SOURCE["oracle"], "eeho.fa.us2/CX_1", handler))[:2] == ("live", 81)


@pytest.mark.asyncio
async def test_avature_probes_the_board_path_in_its_token():
    """The board lives at a customer-named path, not always `/careers`; the
    probe must fetch that path, and its control must stay on the same host."""
    seen: list[str] = []
    board = (
        '<html><script>avature.portal.init()</script>'
        '<a href="/careersau/JobDetail/Carer/1">Search jobs</a></html>'
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/careersau/SearchJobs":
            return httpx.Response(200, text=board)
        return httpx.Response(404)

    status, _count, _details = await _probe(BY_SOURCE["avature"], "bupaanz/careersau", handler)

    assert status == "live"
    assert seen[0] == "https://bupaanz.avature.net/careersau/SearchJobs"
    assert seen[1].startswith("https://bupaanz.avature.net/keyscan-token-does-not-exist")


@pytest.mark.asyncio
async def test_avature_bare_token_is_the_careers_board():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://deloitteus.avature.net/careers/SearchJobs"
        return httpx.Response(404)

    assert (await _probe(BY_SOURCE["avature"], "deloitteus", handler))[0] == "absent"


def test_ashby_board_name_with_a_space_is_not_cut_at_the_space():
    """`Citizen%20Health` used to become `citizen` -- another company's board."""
    assert extract("https://jobs.ashbyhq.com/Citizen%20Health/eaa76517") == ("ashby", "Citizen%20Health")
    assert keyscan_core._canonical_token("ashby", "Citizen%20Health") == "citizen health"


@pytest.mark.asyncio
async def test_ashby_probe_sends_the_space_encoded():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path == b"/posting-api/job-board/citizen%20health"
        return httpx.Response(200, json={"jobs": [{}, {}, {}]})

    assert (await _probe(BY_SOURCE["ashby"], "citizen health", handler))[:2] == ("live", 3)


def test_avature_careers_board_token_is_stored_bare_and_lowercase():
    """Mirrors feed_identity: `company/careers` is the bare key's board."""
    assert keyscan_core._canonical_token("avature", "deloitteus/careers") == "deloitteus"
    assert keyscan_core._canonical_token("avature", "Bupaanz/CareersAU") == "bupaanz/careersau"
    assert keyscan_core._canonical_token("avature", "Acme/ja_jp/Careers") == "acme/ja_JP/careers"


@pytest.mark.asyncio
async def test_oracle_accepts_a_site_named_by_its_vanity_url_name():
    """`/sites/Aerospace` names the site by SiteURLName, not SiteNumber. A
    SiteNumber-only check called every such board absent."""
    queries: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if "recruitingCESites" in request.url.path:
            query = request.url.params["q"]
            queries.append(query)
            matched = query == "SiteURLName='aerospace'"
            return httpx.Response(200, json={"count": int(matched), "items": [{}] if matched else []})
        return httpx.Response(200, json={"items": [{"TotalJobsCount": 827}]})

    status, count, _ = await _probe(BY_SOURCE["oracle"], "icfcjb.fa.ocs/aerospace", handler)

    assert (status, count) == ("live", 827)
    assert queries == ["SiteNumber='aerospace'", "SiteURLName='aerospace'"]


@pytest.mark.asyncio
async def test_oracle_site_number_match_skips_the_vanity_lookup():
    queries: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if "recruitingCESites" in request.url.path:
            queries.append(request.url.params["q"])
            return httpx.Response(200, json={"count": 1, "items": [{}]})
        return httpx.Response(200, json={"items": [{"TotalJobsCount": 81}]})

    assert (await _probe(BY_SOURCE["oracle"], "eeho.fa.us2/cx_1", handler))[0] == "live"
    assert queries == ["SiteNumber='cx_1'"]


@pytest.mark.asyncio
async def test_oracle_failed_vanity_lookup_is_inconclusive_not_absent():
    """A zero on SiteNumber is only half the answer; if the SiteURLName lookup
    then fails, the site may still be real."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if "recruitingCESites" in request.url.path:
            if request.url.params["q"].startswith("SiteNumber="):
                return httpx.Response(200, json={"count": 0, "items": []})
            return httpx.Response(500)
        raise AssertionError("job endpoint must not be reached when the check failed")

    status, _count, details = await _probe(BY_SOURCE["oracle"], "icfcjb.fa.ocs/aerospace", handler)

    assert status == "inconclusive"
    assert details["reason"] == "exists_check_failed"


@pytest.mark.asyncio
async def test_unreachable_exists_check_is_inconclusive_not_absent():
    """A failed check means "unknown", never "not registered" -- falling through
    to the job endpoint would resurrect the very bug this pre-check exists for."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if "recruitingCESites" in request.url.path:
            return httpx.Response(500)
        raise AssertionError("job endpoint must not be reached when the check failed")

    status, _count, details = await _probe(BY_SOURCE["oracle"], "eeho.fa.us2/CX_1", handler)

    assert status == "inconclusive"
    assert details["reason"] == "exists_check_failed"


@pytest.mark.asyncio
async def test_successfactors_rejects_the_unknown_company_stub():
    """An unknown company answers 200 with a ~3KB error stub rather than a 404,
    so without the marker every slug -- real or not -- classified as live."""
    stub = "<html><body><h1>SuccessFactors</h1><p>An error occurred while processing your request.</p></body></html>"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=stub)

    status, _count, details = await _probe(
        BY_SOURCE["successfactors"], "career4.successfactors.com/nope", handler
    )

    assert status == "absent"
    assert details["reason"] == "absent_marker"


def _cc_ok(request: httpx.Request) -> httpx.Response | None:
    """Answer verify's crawl-manifest requests; return None for anything else
    so a test's own handler can take over."""
    if request.url == httpx.URL("https://index.commoncrawl.org/collinfo.json"):
        return httpx.Response(200, json=[{"id": "CC-MAIN-TEST"}])
    if request.url.host == "data.commoncrawl.org":
        return httpx.Response(200, content=_CC_MANIFEST)
    return None


def test_synthetic_token_preserves_composite_host_components():
    assert synthetic_token("acme") == "keyscan-verify-does-not-exist-9f4d2a"
    assert synthetic_token("nvidia/wd5/NVIDIAExternalCareerSite") == "nvidia/wd5/keyscan-verify-does-not-exist-9f4d2a"


@pytest.mark.asyncio
async def test_verify_stops_after_one_live_and_one_dead(tmp_path: Path, monkeypatch):
    tokens = [
        {"token": "acme", "url_count": 5, "urls": []},
        {"token": "beta", "url_count": 1, "urls": []},
        {"token": "gamma", "url_count": 1, "urls": []},
    ]
    monkeypatch.setattr(keyscan_verify, "cc_scan", lambda crawl, rule, urls, target: tokens)

    async def handler(request: httpx.Request) -> httpx.Response:
        stub = _cc_ok(request)
        if stub is not None:
            return stub
        if request.url.path.endswith("/acme"):
            return httpx.Response(200, json={"jobs": [{"id": 1}]})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await verify_source(BY_SOURCE["ashby"], client=client, workdir=tmp_path, request_delay=0)

    assert result["outcome"] == "verified"
    assert result["live"]["token"] == "acme"
    assert result["dead"]["token"] == "beta"
    assert result["probes"] == 3  # acme (live) + beta (dead) + synthetic -- gamma never touched
    assert result["discrimination"] == "pass_consistent"
    assert not any("gamma" in a["url"] for a in result["attempts"])


@pytest.mark.asyncio
async def test_verify_reports_all_inconclusive_when_the_rule_never_discriminates(tmp_path: Path, monkeypatch):
    tokens = [{"token": f"t{i}", "url_count": 1, "urls": []} for i in range(5)]
    monkeypatch.setattr(keyscan_verify, "cc_scan", lambda crawl, rule, urls, target: tokens)

    async def handler(request: httpx.Request) -> httpx.Response:
        stub = _cc_ok(request)
        if stub is not None:
            return stub
        return httpx.Response(200, json={"unexpected": "shape"})

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await verify_source(BY_SOURCE["ashby"], client=client, workdir=tmp_path, max_probes=3, request_delay=0)

    assert result["probes"] == 4  # 3 real (capped) + 1 synthetic
    assert result["live"] is None
    assert result["dead"] is None
    assert result["outcome"] == "all_inconclusive"
    assert result["discrimination"] == "warn_inconclusive_control"


@pytest.mark.asyncio
async def test_verify_flags_a_synthetic_token_that_probes_live(tmp_path: Path, monkeypatch):
    """A rule that returns `live` for a deliberately-bogus token false-positives on
    everything -- this is the critical failure verify exists to catch."""
    tokens = [{"token": "acme", "url_count": 1, "urls": []}, {"token": "beta", "url_count": 1, "urls": []}]
    monkeypatch.setattr(keyscan_verify, "cc_scan", lambda crawl, rule, urls, target: tokens)

    async def handler(request: httpx.Request) -> httpx.Response:
        stub = _cc_ok(request)
        if stub is not None:
            return stub
        if synthetic_token("acme") in request.url.path:
            return httpx.Response(200, json={"jobs": [{"id": 1}]})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await verify_source(BY_SOURCE["ashby"], client=client, workdir=tmp_path, max_probes=2, request_delay=0)

    assert result["live"] is None
    assert result["dead"]["token"] == "acme"
    assert result["synthetic"]["status"] == "live"
    assert result["discrimination"] == "fail_synthetic_live"
    assert result["outcome"] == "verified"


@pytest.mark.asyncio
async def test_verify_stops_the_vendor_on_a_429(tmp_path: Path, monkeypatch):
    tokens = [{"token": "acme", "url_count": 1, "urls": []}, {"token": "beta", "url_count": 1, "urls": []}]
    monkeypatch.setattr(keyscan_verify, "cc_scan", lambda crawl, rule, urls, target: tokens)

    async def handler(request: httpx.Request) -> httpx.Response:
        stub = _cc_ok(request)
        if stub is not None:
            return stub
        return httpx.Response(429, headers={"Retry-After": "30"})

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await verify_source(BY_SOURCE["ashby"], client=client, workdir=tmp_path, request_delay=0)

    assert result["outcome"] == "rate_limited"
    assert result["probes"] == 1
    assert "synthetic" not in result


@pytest.mark.asyncio
async def test_verify_stops_the_vendor_on_a_waf_block(tmp_path: Path, monkeypatch):
    tokens = [{"token": "acme", "url_count": 1, "urls": []}]
    monkeypatch.setattr(keyscan_verify, "cc_scan", lambda crawl, rule, urls, target: tokens)

    async def handler(request: httpx.Request) -> httpx.Response:
        stub = _cc_ok(request)
        if stub is not None:
            return stub
        return httpx.Response(403, text="<html>Attention Required! | Cloudflare</html>")

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await verify_source(BY_SOURCE["ashby"], client=client, workdir=tmp_path, request_delay=0)

    assert result["outcome"] == "blocked"
    assert "cloudflare" in result["detail"]


@pytest.mark.asyncio
async def test_verify_does_not_abort_a_multi_host_vendor_on_one_blocked_tenant(tmp_path: Path, monkeypatch):
    """Workday gives every tenant its own host; one oddly-configured tenant 403ing
    must not read as evidence the whole vendor is blocking us."""
    tokens = [
        {"token": "bad/wd1/badsite", "url_count": 1, "urls": []},
        {"token": "good/wd2/goodsite", "url_count": 1, "urls": []},
        {"token": "dead/wd3/deadsite", "url_count": 1, "urls": []},
    ]
    monkeypatch.setattr(keyscan_verify, "cc_scan", lambda crawl, rule, urls, target: tokens)

    async def handler(request: httpx.Request) -> httpx.Response:
        stub = _cc_ok(request)
        if stub is not None:
            return stub
        url = str(request.url)
        if url == "https://bad.wd1.myworkdayjobs.com/wday/cxs/bad/badsite/jobs":
            return httpx.Response(403, text="<html>cloudflare</html>")
        if url == "https://good.wd2.myworkdayjobs.com/wday/cxs/good/goodsite/jobs":
            return httpx.Response(200, json={"jobs": [{"id": 1}]})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await verify_source(BY_SOURCE["workday"], client=client, workdir=tmp_path, request_delay=0)

    assert result["outcome"] == "verified"
    assert result["live"]["token"] == "good/wd2/goodsite"
    assert result["dead"]["token"] == "dead/wd3/deadsite"


@pytest.mark.asyncio
async def test_verify_escalates_when_a_per_tenant_vendor_blocks_every_host(tmp_path: Path, monkeypatch):
    """Distinct blocked hosts alone are not the signal -- Workday has thousands
    of tenants and some always run their own WAF. What marks a block on *us* is
    403s with no success in between, so escalation waits for the third."""
    tokens = [
        {"token": f"bad{n}/wd{n}/site{n}", "url_count": 1, "urls": []}
        for n in range(1, 7)
    ]
    monkeypatch.setattr(keyscan_verify, "cc_scan", lambda crawl, rule, urls, target: tokens)

    async def handler(request: httpx.Request) -> httpx.Response:
        stub = _cc_ok(request)
        if stub is not None:
            return stub
        return httpx.Response(403, text="<html>cloudflare</html>")

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await verify_source(BY_SOURCE["workday"], client=client, workdir=tmp_path, request_delay=0)

    assert result["outcome"] == "blocked"
    assert result["probes"] == 5
    assert "synthetic" not in result


@pytest.mark.asyncio
async def test_verify_probes_past_blocked_tenants_once_one_answers(tmp_path: Path, monkeypatch):
    """Measured on Eightfold: 5 of 14 tenants 403 while 6 serve us normally. At
    that rate three blocks in a row happen by chance, so a run of 403s only
    means we are blocked if nothing has ever answered."""
    tokens = [
        {"token": f"{name}.eightfold.ai/{name}.com", "url_count": 1, "urls": []}
        for name in ("blocka", "blockb", "blockc", "good", "gone")
    ]
    monkeypatch.setattr(keyscan_verify, "cc_scan", lambda crawl, rule, urls, target: tokens)

    async def handler(request: httpx.Request) -> httpx.Response:
        stub = _cc_ok(request)
        if stub is not None:
            return stub
        host = request.url.host
        if host.startswith("block"):
            return httpx.Response(403, text="<html>cloudflare</html>")
        if host.startswith("good"):
            return httpx.Response(200, json={"data": {"count": 7}})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await verify_source(BY_SOURCE["eightfold"], client=client, workdir=tmp_path, request_delay=0)

    # Three consecutive 403s came first; aborting there would have hidden both
    # the live board and the dead one waiting behind them.
    assert result["outcome"] == "verified"
    assert result["live"]["token"] == "good.eightfold.ai/good.com"
    assert result["dead"]["token"] == "gone.eightfold.ai/gone.com"


@pytest.mark.asyncio
async def test_verify_reports_icims_as_unverifiable(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("icims has no CC path; verify must not make any request")

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await verify_source(BY_SOURCE["icims"], client=client, workdir=tmp_path)

    assert result == {"source": "icims", "outcome": "no_cc_path"}


@pytest.mark.asyncio
async def test_verify_reports_unreachable_manifest_as_cc_unavailable(tmp_path: Path, monkeypatch):
    """A throttled manifest must not be reported as "this rule finds nothing".

    Both end with zero tokens, but only one is a verdict on the rule. Reading
    a skipped scan as a rule failure is how a healthy vendor gets written off.
    """
    monkeypatch.setattr(
        keyscan_verify, "cc_scan",
        lambda *a, **k: pytest.fail("scan must not run when the manifest is unavailable"),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://index.commoncrawl.org/collinfo.json"):
            return httpx.Response(200, json=[{"id": "CC-MAIN-TEST"}])
        return httpx.Response(503)  # the throttle that silently skipped four vendors

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await verify_source(BY_SOURCE["ashby"], client=client, workdir=tmp_path, request_delay=0)

    assert result["outcome"] == "cc_unavailable"
    assert result["crawls_scanned"] == []
    assert "http_503" in result["detail"]


@pytest.mark.asyncio
async def test_verify_reports_no_tokens_only_after_a_real_scan(tmp_path: Path, monkeypatch):
    """The other half of the pair: the scan ran, and genuinely found nothing."""
    monkeypatch.setattr(keyscan_verify, "cc_scan", lambda crawl, rule, urls, target: [])

    async def handler(request: httpx.Request) -> httpx.Response:
        stub = _cc_ok(request)
        if stub is not None:
            return stub
        raise AssertionError("no probe should happen with zero tokens")

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await verify_source(BY_SOURCE["ashby"], client=client, workdir=tmp_path, request_delay=0)

    assert result["outcome"] == "no_tokens_found"
    assert result["crawls_scanned"] == ["CC-MAIN-TEST"]


@pytest.mark.asyncio
async def test_verify_sources_expands_groups_and_runs_sequentially(tmp_path: Path, monkeypatch):
    seen: list[str] = []

    async def fake_verify_source(rule, **kwargs):
        seen.append(rule.source)
        return {"source": rule.source, "outcome": "no_cc_path"}

    monkeypatch.setattr(keyscan_verify, "verify_source", fake_verify_source)
    results = await verify_sources(("tier1",), workdir=tmp_path)

    assert seen == list(expand_sources(("tier1",)))
    assert [r["source"] for r in results] == seen


@pytest.mark.asyncio
async def test_verify_sources_rejects_unknown_names(tmp_path: Path):
    with pytest.raises(KeyscanError):
        await verify_sources(("not-a-real-vendor",), workdir=tmp_path)


@pytest.mark.asyncio
async def test_validation_skips_sources_whose_rule_is_unverified(tmp_path: Path, monkeypatch):
    """A rule that cannot tell a real board from a fake one must not vote.

    oracle and successfactors each marked *every* token live before they were
    fixed, so an unverified rule is worse than no answer: the token stays
    `unknown` rather than being recorded as a false positive.
    """
    monkeypatch.setattr(keyscan_core, "BOARD_FEEDS", ({"repo": "acme/board", "path": "listings.json"},))
    listings = json.dumps([
        {"url": "https://jobs.lever.co/acme", "company_name": "Acme", "active": True},
        {"url": "https://acme.breezy.hr/p/1", "company_name": "Acme", "active": True},
    ])
    probed: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("listings.json"):
            return httpx.Response(200, text=listings)
        probed.append(request.url.host)
        return httpx.Response(200, json=[{"id": 1, "text": "Engineer"}])

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    await scan.run(["board", "validate"])

    rows = {row["ats"]: row["status"] for row in scan.list_candidates()}
    assert rows == {"lever": "live", "breezy": "unknown"}
    # breezy is discovered and stored, just never probed.
    assert not any("breezy" in host for host in probed)


@pytest.mark.asyncio
async def test_validation_paces_a_refusing_host_and_keeps_going(tmp_path: Path, monkeypatch):
    """A 429 paces the one host that sent it; the rest of the sweep continues.

    The old behaviour stopped the whole run on the first refusal, so one touchy
    vendor froze every other token. Now a 429 opens that host's cooldown and the
    sweep carries on. The token actually refused is left unchecked by streaming
    validation (NULL checked_at), so the explicit sweep reprobes it once the host
    has cooled -- here it then validates too, so all six end up live instead of
    one 429 freezing the run.
    """
    monkeypatch.setattr(keyscan_core, "BOARD_FEEDS", ({"repo": "acme/board", "path": "listings.json"},))
    listings = json.dumps([
        {"url": f"https://boards.greenhouse.io/acme{n}", "active": True} for n in range(6)
    ])
    # Requests to greenhouse's host are serialised by the per-host lock, so the
    # single 429 lands on whichever board is probed first; the retry-after keeps
    # the resulting cooldown short enough for a test.
    refusals = {"count": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("listings.json"):
            return httpx.Response(200, text=listings)
        if request.url.host == "boards-api.greenhouse.io":
            if refusals["count"] == 0:
                refusals["count"] = 1
                return httpx.Response(429, headers={"retry-after": "0.2"})
            if request.url.path.endswith("/jobs"):
                return httpx.Response(200, json={"jobs": []})
            return httpx.Response(200, json={"name": "Acme"})
        return httpx.Response(404)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["board", "validate"])

    # The run finished rather than being stopped by the refusal.
    assert scan.status(run_id)["status"] == "completed"

    # A 429 really did happen and was recorded as a diagnostic (only that).
    assert refusals["count"] == 1
    blocked = scan.status(run_id)["blocked"]
    assert blocked["reason"] == "rate_limited"
    assert blocked["host"] == "boards-api.greenhouse.io"

    # All six validated: the five that never saw the 429 directly, plus the one
    # that did -- reprobed by the sweep after the host's cooldown lifted. The old
    # run-wide abort would have left every one of them frozen at `unknown`.
    rows = scan.list_candidates()
    assert len(rows) == 6
    assert all(row["status"] == "live" and row["checked_at"] is not None for row in rows)

    entries, _ = scan.tail_log(run_id)
    assert any(_flat(entry).get("event") == "blocked" for entry in entries)


def test_initialize_adds_new_columns_to_an_existing_database_in_place(tmp_path: Path):
    """CREATE TABLE IF NOT EXISTS does not add columns to a table already on
    disk -- a real ``keyscan.sqlite`` predating either schema addition must
    gain both columns and keep its rows rather than needing a rebuild.
    """
    workdir = tmp_path / "work"
    workdir.mkdir()
    db_path = workdir / "keyscan.sqlite"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            methods_json TEXT NOT NULL DEFAULT '[]',
            config_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL CHECK (status IN ('running','interrupted','cancelled','completed','failed')),
            phase TEXT NOT NULL DEFAULT '',
            checkpoint_json TEXT NOT NULL DEFAULT '{}',
            counts_json TEXT NOT NULL DEFAULT '{}',
            artifact_dir TEXT NOT NULL DEFAULT '',
            pid INTEGER,
            error TEXT,
            started_at TEXT NOT NULL DEFAULT '',
            finished_at TEXT
        );
        CREATE TABLE token (
            ats           TEXT NOT NULL,
            token         TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'unknown'
                CHECK (status IN ('unknown','live','dead')),
            canonical_url TEXT NOT NULL,
            urls          TEXT NOT NULL DEFAULT '[]',
            method        TEXT,
            run_id        TEXT REFERENCES runs(id) ON DELETE SET NULL,
            details       TEXT NOT NULL DEFAULT '{}',
            discovered_at TEXT NOT NULL,
            checked_at    TEXT,
            PRIMARY KEY (ats, token)
        );
        """
    )
    con.execute(
        "INSERT INTO token(ats, token, status, canonical_url, discovered_at) "
        "VALUES ('greenhouse', 'acme', 'live', 'https://boards.greenhouse.io/acme', '2026-01-01T00:00:00+00:00')"
    )
    con.execute(
        """INSERT INTO token(
               ats, token, status, canonical_url, urls, details,
               discovered_at, checked_at
           ) VALUES (
               'workday-list', 'NVIDIA/wd5/NVIDIAExternalCareerSite', 'live',
               'https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite',
               '["old"]', '{"verdict":"live","job_count":42}',
               '2025-12-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00'
           )"""
    )
    con.execute(
        """INSERT INTO token(
               ats, token, status, canonical_url, urls, details, discovered_at
           ) VALUES (
               'workday', 'nvidia/wd5/nvidiaexternalcareersite', 'unknown',
               'https://nvidia.wd5.myworkdayjobs.com/en-US/nvidiaexternalcareersite',
               '["new"]', '{"company_name":"NVIDIA"}',
               '2026-01-01T00:00:00+00:00'
           )"""
    )
    con.commit()
    con.close()

    scan = Keyscan(workdir)
    scan.initialize()

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    tenant_columns = {row["name"] for row in con.execute("PRAGMA table_info(tenants)")}
    run_columns = {row["name"] for row in con.execute("PRAGMA table_info(runs)")}
    assert "imported_at" in tenant_columns
    assert "key" in tenant_columns and "token" not in tenant_columns
    assert "blocked_json" in run_columns
    row = con.execute("SELECT * FROM tenants WHERE ats = 'greenhouse' AND key = 'acme'").fetchone()
    workday_rows = con.execute(
        "SELECT * FROM tenants WHERE ats IN ('workday', 'workday-list')"
    ).fetchall()
    # The retired label is the interim one now: `workday-list` was renamed back
    # to `workday` on 2026-08-26 when the gated detail leg landed.
    with pytest.raises(sqlite3.IntegrityError, match="renamed back to workday"):
        con.execute(
            """INSERT INTO tenants(ats, key, canonical_url, discovered_at)
               VALUES ('workday-list', 'acme/wd1/jobs', 'https://example.test', '2026-01-01')"""
        )
    con.close()
    assert row["status"] == "live"
    assert row["imported_at"] is None
    assert len(workday_rows) == 1
    migrated = workday_rows[0]
    assert migrated["ats"] == "workday"
    assert migrated["key"] == "nvidia/wd5/nvidiaexternalcareersite"
    assert migrated["status"] == "live"
    assert migrated["checked_at"] == "2026-01-02T00:00:00+00:00"
    assert migrated["discovered_at"] == "2025-12-01T00:00:00+00:00"
    assert json.loads(migrated["urls"]) == ["old", "new"]
    assert json.loads(migrated["details"])["company_name"] == "NVIDIA"
    assert json.loads(migrated["details"])["job_count"] == 42


def _titled(title: str) -> str:
    return f"<html><head><title>{title}</title></head><body></body></html>"


@pytest.mark.parametrize(
    "title, expected",
    [
        # The three vendors, as each actually titles its board.
        ("Eliza Jobs", "Eliza"),                       # ashby
        ("Zopa", "Zopa"),                              # lever
        ("Intertek USA", "Intertek USA"),              # oracle
        ("daa All Open Jobs", "daa"),
        ("BTN Career Sites", "BTN"),
        ("STACK Infrastructure APAC Jobs", "STACK Infrastructure APAC"),
        ("Barnes &amp; Thornburg LLP", "Barnes & Thornburg LLP"),
        # The suffix needs a separator in front of it. Without one the
        # alternation matches inside a word and an employer is renamed by its
        # own name: "Snagajob" came back as "Snaga".
        ("Snagajob", "Snagajob"),
        ("Career Group", "Career Group"),
        # Found by walking the live boards in slices of ten. Oracle names the
        # *site*, and wraps the boilerplate on either side of the employer.
        ("Careers at CBIZ", "CBIZ"),
        ("Onity External Career Site", "Onity"),
        ("Pittsburg State University Career Site Minimal", "Pittsburg State University"),
        # ...but "Global" is the employer's word, not Oracle's.
        ("TWE Global Careers", "TWE Global"),
        ("ESM Vacancies", "ESM"),
        # A title built inside a JavaScript string keeps its escapes.
        (r"Macy\'s", "Macy's"),
        # The required separator again, on a live lever board: not "Go".
        ("Gojob", "Gojob"),
    ],
)
def test_board_title_yields_the_employer(title, expected):
    # Oracle is the free-text vendor, so it carries every furniture case.
    assert name_from_title(title, "oracle")[0] == expected


@pytest.mark.parametrize(
    "title",
    [
        "Jobs",
        "Careers",
        # Oracle's untouched default names the product, not the customer.
        "Talent Acquisition",
        "Not found \u2013 404 error",
        "Just a moment...",
        "Attention Required! | Cloudflare",
        "",
        # Oracle's other default, and an ashby board whose owner set no name.
        "Candidate Experience site",
        "Jobs",
        # The whole title was furniture; what survives names no employer.
        "Global Career Site",
        "Sitio de experiencia de candidatos",
    ],
)
def test_a_title_that_names_no_employer_is_refused(title):
    assert name_from_title(title, "oracle")[0] is None


def test_no_title_element_at_all():
    # Workday ships the board as an empty shell and fills it in from script.
    assert name_from_title("", "oracle") == (None, "none")


@pytest.mark.parametrize(
    "title, ats, expected",
    [
        # Ashby publishes a format; anything that is not that format is
        # refused rather than guessed at.
        ("Eliza Jobs", "ashby", "Eliza"),
        ("Jobs", "ashby", None),
        ("TowardJobs", "ashby", None),
        # Lever prints the account name and nothing else, so stripping there
        # can only damage a real one. Both of these are employers.
        ("TowardJobs", "lever", "TowardJobs"),
        ("Gojob", "lever", "Gojob"),
        ("Zopa", "lever", "Zopa"),
    ],
)
def test_each_vendor_gets_its_own_contract(title, ats, expected):
    assert name_from_title(title, ats)[0] == expected


@pytest.mark.parametrize(
    "title, ats, confidence",
    [
        ("Eliza Jobs", "ashby", "exact"),
        ("Zopa", "lever", "exact"),
        ("Intertek USA", "oracle", "exact"),
        # Furniture had to be removed to reach the name: right far more often
        # than not, but not provable for a title nobody has seen.
        ("Careers at CBIZ", "oracle", "stripped"),
        ("Onity External Career Site", "oracle", "stripped"),
        ("Jobs", "ashby", "none"),
    ],
)
def test_confidence_says_whether_anything_was_guessed(title, ats, confidence):
    assert name_from_title(title, ats)[1] == confidence


@pytest.mark.parametrize(
    "raw, expected",
    [
        # A title assembled in a JavaScript string keeps that string's escapes.
        (r"Macy\'s", "Macy's"),
        (r"Carri\u00E8re SNCF", "Carrière SNCF"),
    ],
)
def test_javascript_escapes_are_undone(raw, expected):
    assert name_from_title(raw, "oracle")[0] == expected


# ----------------------------------------------------------------------
# enum
# ----------------------------------------------------------------------

def test_words_to_tokens_takes_the_first_label_and_dedupes():
    from keyscan.enumerate import words_to_tokens

    tokens, dropped = words_to_tokens(["AMD.com", "www.spglobal.com", "amd", "# note", "", "bad word", "sig.com"])
    assert tokens == ["amd", "spglobal", "sig"]
    assert dropped == 1


@pytest.mark.asyncio
async def test_enum_writes_only_live_guesses_and_treats_nxdomain_as_absent(tmp_path: Path, monkeypatch):
    from keyscan import enumerate as enum_mod

    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "careers.amd.com":
            return httpx.Response(200, json={"jobs": [{"data": {"req_id": "1"}}], "totalCount": 3})
        if host == "careers.flaky.com":
            return httpx.Response(503)
        raise httpx.ConnectError("nxdomain", request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        enum_mod.httpx, "AsyncClient",
        lambda **kw: real_client(transport=transport(handler), **kw),
    )
    scan = Keyscan(tmp_path / "work")
    scan.initialize()
    scan._new_run(["board"], 6)
    scan._record_candidate("icims", "known", "board:test", ["https://careers.known.com/careers/jobs"])
    scan._finish("cancelled")

    result = await enum_mod.enumerate_tokens(
        str(scan.db_path), "icims", ["amd.com", "nope.com", "flaky.com", "known.com"],
    )
    assert result["known"] == 1
    assert result["probed"] == 3
    assert result["absent"] == 1
    assert result["live"] == [{"key": "amd", "name": None, "job_count": 3}]
    assert result["inconclusive"] == [{"key": "flaky", "reason": "http_503"}]
    assert result["written"] == 1

    rows = {row["key"]: row for row in scan.list_candidates(source="icims")}
    assert set(rows) == {"known", "amd"}
    assert rows["amd"]["status"] == "live"
    assert rows["amd"]["method"] == "enum"
    assert rows["amd"]["canonical_url"] == "https://careers.amd.com/api/jobs?page=1"


@pytest.mark.asyncio
async def test_discovery_without_validate_leaves_tokens_unprobed(tmp_path: Path, monkeypatch) -> None:
    """Validation is opt-in: a plain board run records tokens and probes none."""
    monkeypatch.setattr(keyscan_core, "BOARD_FEEDS", ({"repo": "acme/board", "path": "listings.json"},))
    probed: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://raw.githubusercontent.com/acme/board/dev/listings.json":
            return httpx.Response(200, text=BOARD_LISTINGS)
        probed.append(request.url.host)
        return httpx.Response(200, json={"jobs": [], "name": "Acme"})

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["board"])

    assert scan.status(run_id)["status"] == "completed"
    rows = scan.list_candidates()
    assert ("greenhouse", "acme") in {(row["ats"], row["key"]) for row in rows}
    assert {row["status"] for row in rows} == {"unknown"}
    assert probed == []


def _scanned_fixture(attempted: list[tuple[str, str]]):
    class Fixture(FixtureScan):
        def _cc_scan_sources(self, crawl, rules, urls, loop, urls_for_range=None):
            attempted.extend((crawl, rule.source) for rule in rules)
            return {rule.source: [] for rule in rules}

    manifest = gzip.compress(b"crawl-data/CC-MAIN-2026-39/cc-index/table/subset=warc/part.parquet\n")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://index.commoncrawl.org/collinfo.json"):
            return httpx.Response(200, json=[{"id": "CC-MAIN-2026-39"}])
        if request.url.host == "data.commoncrawl.org":
            return httpx.Response(200, content=manifest)
        return httpx.Response(404)

    return Fixture, transport(handler)


@pytest.mark.asyncio
async def test_a_pair_scanned_by_an_earlier_run_is_skipped(tmp_path: Path):
    attempted: list[tuple[str, str]] = []
    Fixture, handler = _scanned_fixture(attempted)
    scan = Fixture(tmp_path / "work", transport=handler)
    await scan.run(["commoncrawl"], crawl_ids=["CC-MAIN-2026-39"], sources=["greenhouse"])
    assert attempted == [("CC-MAIN-2026-39", "greenhouse")]

    # A second run over the same pair -- plus one new vendor -- scans only the new one.
    await scan.run(["commoncrawl"], crawl_ids=["CC-MAIN-2026-39"], sources=["greenhouse", "lever"])
    assert attempted == [("CC-MAIN-2026-39", "greenhouse"), ("CC-MAIN-2026-39", "lever")]


@pytest.mark.asyncio
async def test_rescan_ignores_earlier_runs(tmp_path: Path):
    attempted: list[tuple[str, str]] = []
    Fixture, handler = _scanned_fixture(attempted)
    scan = Fixture(tmp_path / "work", transport=handler)
    await scan.run(["commoncrawl"], crawl_ids=["CC-MAIN-2026-39"], sources=["greenhouse"])
    await scan.run(["commoncrawl"], crawl_ids=["CC-MAIN-2026-39"], sources=["greenhouse"], rescan=True)
    assert attempted == [("CC-MAIN-2026-39", "greenhouse")] * 2


@pytest.mark.asyncio
async def test_a_changed_rule_makes_its_scanned_pairs_due_again(tmp_path: Path):
    attempted: list[tuple[str, str]] = []
    Fixture, handler = _scanned_fixture(attempted)
    scan = Fixture(tmp_path / "work", transport=handler)
    await scan.run(["commoncrawl"], crawl_ids=["CC-MAIN-2026-39"], sources=["greenhouse"])
    with sqlite3.connect(scan.db_path) as con:
        con.execute("UPDATE scanned SET rule_hash = 'stale'")
    await scan.run(["commoncrawl"], crawl_ids=["CC-MAIN-2026-39"], sources=["greenhouse"])
    assert attempted == [("CC-MAIN-2026-39", "greenhouse")] * 2


@pytest.mark.asyncio
async def test_a_failed_scan_leaves_no_scanned_row(tmp_path: Path):
    class Failing(FixtureScan):
        def _cc_scan_sources(self, crawl, rules, urls, loop, urls_for_range=None):
            raise duckdb.IOException("HTTP 403")

    _, handler = _scanned_fixture([])
    scan = Failing(tmp_path / "work", transport=handler)
    with pytest.raises(KeyscanError):
        await scan.run(["commoncrawl"], crawl_ids=["CC-MAIN-2026-39"], sources=["greenhouse"])
    with sqlite3.connect(scan.db_path) as con:
        assert con.execute("SELECT count(*) FROM scanned").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_a_crawl_footers_rule_out_is_marked_scanned(tmp_path: Path):
    attempted: list[tuple[str, str]] = []
    Fixture, handler = _scanned_fixture(attempted)
    scan = Fixture(tmp_path / "work", transport=handler)
    scan.initialize()
    # One harvested file whose only row group sits nowhere near greenhouse.
    with sqlite3.connect(scan.db_path) as con:
        con.execute(
            "INSERT INTO footers VALUES ('CC-MAIN-2026-39', "
            "'crawl-data/CC-MAIN-2026-39/cc-index/table/subset=warc/part.parquet', 0, 'aa,', 'ab,', 1, 't')"
        )
    await scan.run(["commoncrawl"], crawl_ids=["CC-MAIN-2026-39"], sources=["greenhouse"])
    assert attempted == []
    with sqlite3.connect(scan.db_path) as con:
        assert con.execute("SELECT ats, crawl FROM scanned").fetchall() == [("greenhouse", "CC-MAIN-2026-39")]


@pytest.mark.asyncio
async def test_run_counts_separate_new_tokens_from_resightings(tmp_path: Path):
    class Fixture(FixtureScan):
        tokens = ["acme"]

        def _cc_scan_sources(self, crawl, rules, urls, loop, urls_for_range=None):
            return {rule.source: [
                {"token": token, "url_count": 1, "urls": [f"https://boards.greenhouse.io/{token}"]}
                for token in self.tokens
            ] for rule in rules}

    _, handler = _scanned_fixture([])
    scan = Fixture(tmp_path / "work", transport=handler)
    first = await scan.run(["commoncrawl"], crawl_ids=["CC-MAIN-2026-39"], sources=["greenhouse"])
    assert scan.status(first)["counts"]["found"] == 1
    assert scan.status(first)["counts"]["added"] == 1

    Fixture.tokens = ["acme", "globex"]
    second = await scan.run(
        ["commoncrawl"], crawl_ids=["CC-MAIN-2026-39"], sources=["greenhouse"], rescan=True,
    )
    assert scan.status(second)["counts"]["found"] == 2
    assert scan.status(second)["counts"]["added"] == 1


def test_run_ids_are_utc_start_seconds_and_never_collide(tmp_path: Path):
    import re

    scan = Keyscan(tmp_path / "work")
    first = scan._new_run(["board"])
    scan._finish("completed")
    second = scan._new_run(["board"])
    assert re.fullmatch(r"\d{8}-\d{6}", first)
    assert second > first
