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
from keyscan.core import Keyscan, KeyscanError, _Cancelled, selftest
from keyscan.probe import default_request, probe_rule
from keyscan.rules import BY_SOURCE, SOURCE_GROUPS, expand_sources, extract
from keyscan.verify import synthetic_token, verify_source, verify_sources


class FixtureScan(Keyscan):
    def _company_seeds(self):
        return [{"domain": "acme.test", "company_id": 999999, "company_name": "Acme"}]


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
    run_id = await scan.run(["board"])

    assert scan.status(run_id)["status"] == "completed"
    live = {(row["ats"], row["token"]) for row in scan.list_candidates(status="live")}
    assert ("greenhouse", "acme") in live
    entries, _ = scan.tail_log(run_id)
    assert any(entry["phase"] == "validate" for entry in entries)
    request = next(entry for entry in entries if entry.get("event") == "request_timing")
    assert request["outcome"] == "ok"
    assert {"elapsed_ms", "queue_ms", "throttle_delay_ms", "retry_delay_ms"} <= request.keys()
    assert any(entry.get("event") == "phase_timing" for entry in entries)


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
            "SELECT details FROM token WHERE ats = 'greenhouse' AND token = 'acme'"
        ).fetchone()["details"]
    assert json.loads(details)["company_name"] == "Acme Incorporated"


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
    assert [(row["ats"], row["token"]) for row in scan.list_candidates()] == [("lever", "acme")]
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
    rows = {(row["ats"], row["token"]): row for row in scan.list_candidates()}
    assert set(rows) == {
        ("greenhouse", "acme"),
        ("ashby", "OpenAI"),
        ("workday-list", "nvidia/wd5/NVIDIAExternalCareerSite"),
    }
    assert rows[("greenhouse", "acme")]["details"]["company_name"] == "Acme"

    # `method` records first-discovery provenance; a later probe does not
    # overwrite it.
    feed_url = "https://raw.githubusercontent.com/acme/board/dev/listings.json"
    with scan._connect() as con:
        methods = {row["method"] for row in con.execute("SELECT DISTINCT method FROM token")}
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
    run_id = await scan.run(["board"])
    assert scan.status(run_id)["status"] == "cancelled"
    assert scan.status(run_id)["checkpoint"]["board_index"] == 1
    await scan.resume(run_id)
    assert scan.status(run_id)["status"] == "completed"
    assert scan.list_candidates(status="live")[0]["token"] == "acme"


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
    assert (candidate["token"], candidate["status"], candidate["details"]["job_count"]) == (f"98.008/{api_token}", "live", 2)
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
        def _cc_scan_sources(self, crawl, rules, urls, loop):
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
    run_id = await scan.run(["commoncrawl"], crawls=1)
    status = scan.status(run_id)
    assert "CC-MAIN-TEST/greenhouse" in status["checkpoint"]["cc_done"]
    assert scan.list_candidates(source="greenhouse")[0]["status"] == "live"
    entries, _ = scan.tail_log(run_id)
    timing = next(entry for entry in entries if entry.get("event") == "operation_timing")
    assert timing["operation"] == "commoncrawl_merged_scan"
    assert timing["crawl"] == "CC-MAIN-TEST"


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
    here, so a candidate in the token table only got there via a checkpoint.
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
    # Every token is already in the token table, though `_cc_scan_sources`
    # itself never calls `_record_candidate` and no final loop ran afterward
    # -- the only path to the DB here was `on_candidates` at a checkpoint.
    tokens = {row["token"] for row in scan.list_candidates(source="greenhouse")}
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
    # than landing cleanly on 2/4/6. In production batch_files(8) is tiny next
    # to checkpoint_files(100), so this overshoot is a few percent, not 50%;
    # exercising it here at an exaggerated ratio is what proves a checkpoint
    # is never skipped outright even when a batch spans one.
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
    run_id = await scan.run(["board"])

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
                before = con.execute("SELECT count(*) FROM token WHERE checked_at IS NULL").fetchone()[0]
            swept.append(before)
            await super()._validate(run)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io":
            if request.url.path.endswith("/jobs"):
                return httpx.Response(200, json={"jobs": []})
            return httpx.Response(200, json={"name": "Acme"})
        return httpx.Response(404)

    scan = CountingSweep(tmp_path / "work", transport=transport(handler))
    await scan.run(["board"])

    assert swept == [0]
    assert scan.list_candidates(status="live")[0]["token"] == "acme"


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
    run_id = await scan.run(["board"])

    # One unprobeable token is a logged fact, not a failed scan.  It stays
    # unprobed, so the next run retries it.
    assert scan.status(run_id)["status"] == "completed"
    entries, _ = scan.tail_log(run_id)
    assert any(entry["message"] == "candidate probe failed" for entry in entries)
    with scan._connect() as con:
        row = con.execute("SELECT status, checked_at FROM token WHERE ats = 'greenhouse' AND token = 'acme'").fetchone()
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
        unprobed = con.execute("SELECT count(*) FROM token WHERE checked_at IS NULL").fetchone()[0]
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
    assert candidate["token"] == "paychex"
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
        result = await verify_source(BY_SOURCE["workday-list"], client=client, workdir=tmp_path, request_delay=0)

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
        result = await verify_source(BY_SOURCE["workday-list"], client=client, workdir=tmp_path, request_delay=0)

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
    await scan.run(["board"])

    rows = {row["ats"]: row["status"] for row in scan.list_candidates()}
    assert rows == {"lever": "live", "breezy": "unknown"}
    # breezy is discovered and stored, just never probed.
    assert not any("breezy" in host for host in probed)


@pytest.mark.asyncio
async def test_validation_stops_and_records_the_first_refusal(tmp_path: Path, monkeypatch):
    """A 429 ends validation for the run, and says so on the run row.

    Retrying a rate limit only deepens it, and every later probe would be
    rejected anyway -- so the run records who refused us and stops.
    """
    monkeypatch.setattr(keyscan_core, "BOARD_FEEDS", ({"repo": "acme/board", "path": "listings.json"},))
    listings = json.dumps([
        {"url": f"https://boards.greenhouse.io/acme{n}", "active": True} for n in range(6)
    ])
    probed: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("listings.json"):
            return httpx.Response(200, text=listings)
        probed.append(str(request.url))
        return httpx.Response(429, headers={"retry-after": "120"})

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["board"])

    blocked = scan.status(run_id)["blocked"]
    assert blocked["reason"] == "rate_limited"
    assert blocked["host"] == "boards-api.greenhouse.io"
    assert blocked["retry_after"] == "120"

    # Stopped rather than working through all six, and never retried the 429.
    assert len(probed) == 1

    # Nothing was probed successfully, so no token may carry a checked_at:
    # streaming validation only picks up rows where it is NULL, and stamping
    # one here would hide the token from every later run.
    rows = scan.list_candidates()
    assert len(rows) == 6
    assert all(row["checked_at"] is None and row["status"] == "unknown" for row in rows)

    entries, _ = scan.tail_log(run_id)
    assert any(entry.get("event") == "blocked" for entry in entries)


def test_initialize_adds_imported_at_to_an_existing_database_in_place(tmp_path: Path):
    """CREATE TABLE IF NOT EXISTS does not add columns to a table already on
    disk -- a real ``keyscan.sqlite`` predating the xjobscore-import feature
    must gain the column, and keep its rows, rather than needing a rebuild.
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
            created_at TEXT NOT NULL DEFAULT '',
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
    con.commit()
    con.close()

    scan = Keyscan(workdir)
    scan.initialize()

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    columns = {row["name"] for row in con.execute("PRAGMA table_info(token)")}
    assert "imported_at" in columns
    row = con.execute("SELECT * FROM token WHERE ats = 'greenhouse' AND token = 'acme'").fetchone()
    con.close()
    assert row["status"] == "live"
    assert row["imported_at"] is None
