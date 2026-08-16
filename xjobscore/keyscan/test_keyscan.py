from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
import tomllib

import duckdb
import httpx
import pytest

import xjobscore.keyscan.core as keyscan_core
from xjobscore.keyscan.core import Keyscan, KeyscanError, _Cancelled, selftest
from xjobscore.keyscan.rules import BY_SOURCE, extract


class FixtureScan(Keyscan):
    def _company_seeds(self):
        return [{"domain": "acme.test", "company_id": 999999, "company_name": "Acme"}]


def transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_company_run_validates_and_logs(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "acme.test" and request.url.path == "/":
            return httpx.Response(200, text='<a href="/careers">Careers</a><a href="https://boards.greenhouse.io/acme">Apply</a>')
        if request.url.host == "acme.test" and request.url.path == "/careers":
            return httpx.Response(200, text='<iframe src="https://boards.greenhouse.io/acme"></iframe>')
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, json={"jobs": []})
        return httpx.Response(404)

    scan = FixtureScan(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["company"])

    assert scan.status(run_id)["status"] == "completed"
    candidate = scan.list_candidates(validation="live")[0]
    assert (candidate["source"], candidate["token"], candidate["company_id"]) == ("greenhouse", "acme", 999999)
    entries, _ = scan.tail_log(run_id)
    assert any(entry["phase"] == "validate" for entry in entries)
    request = next(entry for entry in entries if entry.get("event") == "request_timing")
    assert request["host"] == "acme.test"
    assert request["outcome"] == "ok"
    assert {"elapsed_ms", "queue_ms", "throttle_delay_ms", "retry_delay_ms"} <= request.keys()
    assert any(entry.get("event") == "phase_timing" for entry in entries)
    catalog = next(entry for entry in entries if entry.get("message") == "catalog TOML written")
    assert catalog["feeds"] == 0
    assert catalog["skipped"] == 1


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
    candidate_id = scan._record_candidate(
        "greenhouse", "acme", "commoncrawl", "https://boards.greenhouse.io/acme",
    )
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        validation = await scan._probe(scan.list_candidates(source="greenhouse")[0])
        scan._set_validation(candidate_id, *validation)
    scan._finish("cancelled")

    candidate = scan.list_candidates(source="greenhouse")[0]
    assert candidate["company_name"] == "Acme Incorporated"
    assert validation[2]["company_name_source"] == "metadata_json"
    output = tmp_path / "candidate.jsonl"
    scan.export(output, accepted_only=False)
    exported = json.loads(output.read_text())
    probe_evidence = next(item for item in exported["evidence"] if item["method"] == "probe")
    assert probe_evidence["details"]["company_name"] == "Acme Incorporated"


@pytest.mark.asyncio
async def test_source_scoping_filters_page_discovery_and_validation(tmp_path: Path):
    requested_hosts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.host == "acme.test":
            return httpx.Response(
                200,
                text=(
                    '<a href="https://boards.greenhouse.io/acme">Greenhouse</a>'
                    '<a href="https://jobs.lever.co/acme">Lever</a>'
                ),
            )
        if request.url.host == "api.lever.co":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    scan = FixtureScan(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["company"], sources=["lever"])

    assert scan.status(run_id)["config"]["sources"] == ["lever"]
    assert [(row["source"], row["token"]) for row in scan.list_candidates()] == [("lever", "acme")]
    assert "boards-api.greenhouse.io" not in requested_hosts


def test_source_scoping_rejects_unknown_source(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    with pytest.raises(KeyscanError, match="unknown source"):
        scan._new_run(["company"], 1, ["not-an-ats"])


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
    run_id = scan._new_run(["company"], 6)
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        xml_id = scan._record_candidate("jazzhr", "acme", "company", "https://acme.applytojob.com/apply/", details={})
        html_id = scan._record_candidate("teamtailor", "acme", "company", "https://acme.teamtailor.com/jobs", details={})
        xml = await scan._probe(scan.list_candidates(source="jazzhr")[0])
        generic = await scan._probe(scan.list_candidates(source="teamtailor")[0])
        scan._set_validation(xml_id, *xml)
        scan._set_validation(html_id, *generic)
    scan._finish("cancelled")

    assert scan.list_candidates(source="jazzhr")[0]["validation"] == "live"
    assert scan.list_candidates(source="teamtailor")[0]["validation"] == "inconclusive"
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
    scan._new_run(["company"], 6)
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        scan._record_candidate("avature", "acme", "company", "https://acme.avature.net/careers", details={})
        scan._record_candidate("avature", "plain", "company", "https://plain.avature.net/careers", details={})
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
    scan._new_run(["company"], 6)
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        scan._record_candidate("dayforce", "acme", "company", "https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL", details={})
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
    scan._new_run(["company"], 6)
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        candidate_id = scan._record_candidate("comeet", "acme/98.008", "company", "https://www.comeet.com/jobs/acme/98.008", details={})
        validation, count, details = await scan._probe(scan.list_candidates(source="comeet")[0])
        scan._set_validation(candidate_id, validation, count, details)
    scan._finish("cancelled")

    candidate = scan.list_candidates(source="comeet")[0]
    assert (candidate["token"], candidate["validation"], candidate["job_count"]) == (f"98.008/{api_token}", "live", 2)
    assert candidate["canonical_url"] == "https://www.comeet.com/jobs/acme/98.008"


@pytest.mark.asyncio
async def test_evidence_dedupes_across_methods_and_export_is_stable(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    scan._new_run(["company", "wayback"], 6)
    candidate_id = scan._record_candidate("greenhouse", "acme", "company", "https://boards.greenhouse.io/acme", company_id=1)
    scan._record_candidate("greenhouse", "acme", "wayback", "https://boards.greenhouse.io/acme", referrer="https://acme.test/careers")
    scan._set_validation(candidate_id, "live", 2, {"responses": []})
    accepted = scan.decide(candidate_id, "accepted")
    assert accepted["decision"] == "accepted"
    assert len(scan.list_candidates()) == 1

    first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    assert scan.export(first) == 1
    assert scan.export(second) == 1
    assert first.read_bytes() == second.read_bytes()
    exported = json.loads(first.read_text())
    assert {item["method"] for item in exported["evidence"]} == {"company", "wayback", "probe"}
    scan._finish("cancelled")


def test_catalog_export_mirrors_registry_feed_schema(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    scan._new_run(["company"], 6)
    candidate_id = scan._record_candidate(
        "jobvite", "acme", "company", "https://jobs.jobvite.com/acme",
        company_id=649, company_name="Egnyte",
    )
    scan._set_validation(candidate_id, "live", 2, {"responses": []})
    output = tmp_path / "catalogs"

    assert scan.export_catalog(output) == {"files": 1, "feeds": 1}
    first = (output / "jobvite.toml").read_bytes()
    assert scan.export_catalog(output) == {"files": 1, "feeds": 1}
    assert (output / "jobvite.toml").read_bytes() == first
    document = tomllib.loads(first.decode())
    assert document["schema_version"] == 1
    assert document["source"] == "jobvite"
    feed = document["feed"][0]
    assert feed["key"] == "acme"
    assert feed["company_id"] == 649
    assert feed["company_name"] == "Egnyte"
    assert feed["domain"] == "egnyte.com"
    assert feed["verified"] is True
    assert feed["verified_at"]
    scan._finish("cancelled")


def test_default_catalog_export_appends_only_registry_valid_feeds(tmp_path: Path, monkeypatch):
    registry_catalog = tmp_path / "registry" / "ats-catalog"
    registry_catalog.mkdir(parents=True)
    existing = registry_catalog / "greenhouse.toml"
    existing.write_text(
        "schema_version = 1\nsource = \"greenhouse\"\n\n"
        "# operator-owned entry\n[[feed]]\n"
        "key = \"existing\"\ncompany_id = 649\ncompany_name = \"Egnyte\"\n"
        "domain = \"egnyte.com\"\nverified = true\nverified_at = \"2026-08-01\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(keyscan_core, "REGISTRY_CATALOG_DIR", registry_catalog)

    scan = Keyscan(tmp_path / "work")
    scan._new_run(["company"], 6)
    valid_id = scan._record_candidate(
        "greenhouse", "new-board", "company", "https://boards.greenhouse.io/new-board",
        company_id=649, company_name="Egnyte",
    )
    unknown_id = scan._record_candidate(
        "greenhouse", "unbound-board", "commoncrawl", "https://boards.greenhouse.io/unbound-board",
    )
    scan._set_validation(valid_id, "live", 3, {"responses": []})
    scan._set_validation(unknown_id, "live", 2, {"responses": []})
    scan._finish("cancelled")

    assert scan.export_catalog() == {"files": 1, "feeds": 1, "skipped": 1}
    document = tomllib.loads(existing.read_text())
    assert [feed["key"] for feed in document["feed"]] == ["existing", "new-board"]
    assert "# operator-owned entry" in existing.read_text()


def test_every_catalog_writer_emits_the_canonical_field_order(tmp_path: Path, monkeypatch):
    """All three writers agree with the registry's canonical field order.

    Keyscan appends straight into sources/registry/ats-catalog/*.toml, so a
    writer that orders fields its own way silently fails the registry's
    canonical-order test -- and only for the feeds keyscan added. The order is
    duplicated from sources/tests/test_registry_loader.py::_FEED_FIELD_ORDER
    rather than imported, so keyscan keeps no dependency on the core package.
    """
    canonical = ("key", "verified", "verified_at", "company_id", "company_name", "domain")

    registry_catalog = tmp_path / "registry" / "ats-catalog"
    registry_catalog.mkdir(parents=True)
    (registry_catalog / "greenhouse.toml").write_text(
        'schema_version = 1\nsource = "greenhouse"\n', encoding="utf-8"
    )
    monkeypatch.setattr(keyscan_core, "REGISTRY_CATALOG_DIR", registry_catalog)

    scan = Keyscan(tmp_path / "work")
    scan._new_run(["company", "commoncrawl"], 6)
    resolved = scan._record_candidate(
        "greenhouse", "resolved-board", "company",
        "https://boards.greenhouse.io/resolved-board",
        company_id=649, company_name="Egnyte",
    )
    unresolved = scan._record_candidate(
        "greenhouse", "unbound-board", "commoncrawl",
        "https://boards.greenhouse.io/unbound-board",
    )
    scan._set_validation(resolved, "live", 3, {"responses": []})
    scan._set_validation(unresolved, "live", 2, {"responses": []})
    scan._finish("cancelled")

    # 1. standalone export writes whole catalogs
    standalone = tmp_path / "catalogs"
    scan.export_catalog(standalone)
    feed = tomllib.loads((standalone / "greenhouse.toml").read_text())["feed"][0]
    assert tuple(key for key in feed if key != "config") == canonical

    # 2. registry append of a company-resolved candidate
    scan.export_catalog()
    appended = tomllib.loads((registry_catalog / "greenhouse.toml").read_text())["feed"]
    assert [row["key"] for row in appended] == ["resolved-board"]
    assert tuple(key for key in appended[0] if key != "config") == canonical

    # 3. registry append of an unresolved discovery placeholder
    scan.export_catalog(candidates=True)
    placeholder = next(
        row
        for row in tomllib.loads((registry_catalog / "greenhouse.toml").read_text())["feed"]
        if row["key"] == "unbound-board"
    )
    assert tuple(key for key in placeholder if key != "config") == canonical


def test_candidate_catalog_export_appends_disabled_unresolved_tokens(tmp_path: Path, monkeypatch):
    registry_catalog = tmp_path / "registry" / "ats-catalog"
    registry_catalog.mkdir(parents=True)
    catalog = registry_catalog / "greenhouse.toml"
    catalog.write_text('schema_version = 1\nsource = "greenhouse"\n', encoding="utf-8")
    monkeypatch.setattr(keyscan_core, "REGISTRY_CATALOG_DIR", registry_catalog)

    scan = Keyscan(tmp_path / "work")
    scan._new_run(["commoncrawl"], 6)
    scan._record_candidate(
        "greenhouse", "unbound-board", "commoncrawl", "https://boards.greenhouse.io/unbound-board",
    )
    scan._finish("cancelled")

    assert scan.export_catalog(candidates=True) == {"files": 1, "feeds": 1, "skipped": 0}
    assert scan.export_catalog(candidates=True) == {"files": 0, "feeds": 0, "skipped": 0}
    feed = tomllib.loads(catalog.read_text())["feed"][0]
    assert feed == {
        "key": "unbound-board", "verified": False, "verified_at": "",
        "company_id": 0, "company_name": "", "domain": "",
    }


@pytest.mark.asyncio
async def test_commoncrawl_checkpoint_can_resume_without_network_parquet(tmp_path: Path):
    class CrawlFixture(FixtureScan):
        def _cc_scan(self, crawl, rule, urls):
            return [{"token": "acme", "url_count": 3, "evidence_url": "https://boards.greenhouse.io/acme"}] if rule.source == "greenhouse" else []

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
    assert scan.list_candidates(source="greenhouse")[0]["validation"] == "live"
    entries, _ = scan.tail_log(run_id)
    timing = next(entry for entry in entries if entry.get("event") == "operation_timing")
    assert timing["operation"] == "commoncrawl_vendor_scan"
    assert timing["crawl"] == "CC-MAIN-TEST"


def test_commoncrawl_reads_a_persisted_synthetic_parquet(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    scan._new_run(["commoncrawl"], 1)
    output = scan._artifact() / "commoncrawl" / "CC-MAIN-TEST" / "greenhouse.parquet"
    output.parent.mkdir(parents=True)
    con = duckdb.connect()
    try:
        con.execute(
            f"COPY (SELECT 'acme' AS token, 3::BIGINT AS url_count, 'https://boards.greenhouse.io/acme' AS evidence_url) TO '{output}' (FORMAT PARQUET)"
        )
    finally:
        con.close()
    assert scan._cc_scan("CC-MAIN-TEST", BY_SOURCE["greenhouse"], []) == [
        {"token": "acme", "url_count": 3, "evidence_url": "https://boards.greenhouse.io/acme"}
    ]
    scan._finish("cancelled")


@pytest.mark.asyncio
async def test_cancelled_run_resumes_from_its_checkpoint(tmp_path: Path):
    class InterruptOnce(FixtureScan):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.interrupted = False

        async def _company(self, run):
            self._checkpoint(company_index=1)
            if not self.interrupted:
                self.interrupted = True
                raise _Cancelled()
            self._record_candidate("greenhouse", "acme", "company", "https://boards.greenhouse.io/acme")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, json={"jobs": []})
        return httpx.Response(404)

    scan = InterruptOnce(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["company"])
    assert scan.status(run_id)["status"] == "cancelled"
    await scan.resume(run_id)
    assert scan.status(run_id)["status"] == "completed"
    assert scan.list_candidates(validation="live")[0]["token"] == "acme"


@pytest.mark.asyncio
async def test_candidates_validate_during_discovery_not_only_after(tmp_path: Path):
    """The token must be live before the crawl that found it has finished."""
    live_while_crawling: list[bool] = []

    class SlowCrawl(FixtureScan):
        def _company_seeds(self):
            return [
                {"domain": "acme.test", "company_id": 1, "company_name": "Acme"},
                {"domain": "later.test", "company_id": 2, "company_name": "Later"},
            ]

        async def _discover_page(self, page, page_url, method, seed, referrer, selected_sources=None):
            await super()._discover_page(page, page_url, method, seed, referrer, selected_sources)
            if seed["domain"] == "acme.test":
                # Give the background validator room to probe before the
                # second seed is crawled.
                for _ in range(50):
                    await asyncio.sleep(0)
                    if self.list_candidates(validation="live"):
                        break
                live_while_crawling.append(bool(self.list_candidates(validation="live")))

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "acme.test":
            return httpx.Response(200, text='<a href="https://boards.greenhouse.io/acme">Apply</a>')
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, json={"jobs": [{"id": 1}]})
        return httpx.Response(404)

    scan = SlowCrawl(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["company"])

    assert any(live_while_crawling), "no candidate was live before discovery ended"
    assert scan.status(run_id)["status"] == "completed"
    candidate = scan.list_candidates(validation="live")[0]
    assert (candidate["token"], candidate["job_count"]) == ("acme", 1)


@pytest.mark.asyncio
async def test_streaming_validation_leaves_nothing_for_the_final_sweep(tmp_path: Path):
    swept: list[int] = []

    class CountingSweep(FixtureScan):
        async def _validate(self, run):
            before = {row["id"] for row in self.list_candidates(validation="unvalidated")}
            swept.append(len(before))
            await super()._validate(run)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "acme.test":
            return httpx.Response(200, text='<a href="https://boards.greenhouse.io/acme">Apply</a>')
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, json={"jobs": []})
        return httpx.Response(404)

    scan = CountingSweep(tmp_path / "work", transport=transport(handler))
    await scan.run(["company"])

    assert swept == [0]
    assert scan.list_candidates(validation="live")[0]["token"] == "acme"


@pytest.mark.asyncio
async def test_probe_failure_is_logged_without_aborting_the_run(tmp_path: Path):
    class BrokenProbe(FixtureScan):
        async def _probe(self, candidate):
            raise RuntimeError("probe exploded")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "acme.test":
            return httpx.Response(200, text='<a href="https://boards.greenhouse.io/acme">Apply</a>')
        return httpx.Response(404)

    scan = BrokenProbe(tmp_path / "work", transport=transport(handler))
    run_id = await scan.run(["company"])

    # One unprobeable token is a logged fact, not a failed crawl.  It stays
    # unvalidated, so the next run retries it.
    assert scan.status(run_id)["status"] == "completed"
    entries, _ = scan.tail_log(run_id)
    assert any(entry["message"] == "candidate probe failed" for entry in entries)
    assert scan.list_candidates()[0]["validation"] == "unvalidated"


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
    scan._new_run(["company"], 6)
    for index in range(3):
        scan._record_candidate(
            "greenhouse", f"acme-{index}", "company", f"https://boards.greenhouse.io/acme-{index}",
        )
    scan._finish("interrupted")
    assert len(scan.list_candidates(validation="unvalidated")) == 3

    run_id = await scan.run(["validate"])

    assert scan.status(run_id)["status"] == "completed"
    assert len(scan.list_candidates(validation="live")) == 3
    # Only vendor probe endpoints, never a company domain or an index.
    assert crawled and all(host.endswith("greenhouse.io") for host in crawled), crawled
    assert scan.candidates_since()["count"] == 3


def test_validated_seq_orders_polling_by_validation_not_discovery(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    scan._new_run(["company"], 6)
    first = scan._record_candidate("greenhouse", "early", "company", "https://boards.greenhouse.io/early")
    second = scan._record_candidate("lever", "late", "company", "https://jobs.lever.co/late")
    assert first < second

    # Validated in the reverse of discovery order, which is exactly what an
    # id-ordered cursor would get wrong.
    scan._set_validation(second, "live", 2, {"responses": []})
    scan._set_validation(first, "live", 1, {"responses": []})
    scan._finish("cancelled")

    page = scan.candidates_since(since_seq=0)
    assert [row["token"] for row in page["candidates"]] == ["late", "early"]
    assert page["more"] is False

    tail = scan.candidates_since(since_seq=page["candidates"][0]["validated_seq"])
    assert [row["token"] for row in tail["candidates"]] == ["early"]
    assert scan.candidates_since(since_seq=page["next_seq"])["candidates"] == []


def test_candidates_since_paginates_and_filters_to_live(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    scan._new_run(["company"], 6)
    for index in range(3):
        candidate_id = scan._record_candidate(
            "greenhouse", f"acme-{index}", "company", f"https://boards.greenhouse.io/acme-{index}",
        )
        scan._set_validation(candidate_id, "live" if index else "absent", index, {"responses": []})
    scan._finish("cancelled")

    first = scan.candidates_since(since_seq=0, limit=1)
    assert [row["token"] for row in first["candidates"]] == ["acme-1"]
    assert first["more"] is True
    second = scan.candidates_since(since_seq=first["next_seq"], limit=1)
    assert [row["token"] for row in second["candidates"]] == ["acme-2"]
    assert scan.candidates_since(since_seq=second["next_seq"], limit=1)["more"] is False
    assert {row["token"] for row in scan.candidates_since(validation=None)["candidates"]} == {
        "acme-0", "acme-1", "acme-2",
    }


def test_existing_work_database_gains_the_poll_cursor(tmp_path: Path):
    import sqlite3 as _sqlite3

    workdir = tmp_path / "work"
    workdir.mkdir()
    with _sqlite3.connect(workdir / "keyscan.sqlite") as con:
        con.executescript(
            """
            CREATE TABLE candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL, token TEXT NOT NULL,
                company_id INTEGER, company_name TEXT, canonical_url TEXT NOT NULL,
                validation TEXT NOT NULL DEFAULT 'unvalidated',
                decision TEXT NOT NULL DEFAULT 'pending', job_count INTEGER,
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                last_validated_at TEXT, UNIQUE(source, token)
            );
            INSERT INTO candidates(source, token, canonical_url, validation, job_count,
                                   first_seen_at, last_seen_at, last_validated_at)
            VALUES ('greenhouse', 'already-live', 'https://boards.greenhouse.io/already-live',
                    'live', 4, '2026-01-01', '2026-01-01', '2026-01-02'),
                   ('lever', 'never-probed', 'https://jobs.lever.co/never-probed',
                    'unvalidated', NULL, '2026-01-01', '2026-01-01', NULL);
            """
        )

    scan = Keyscan(workdir)
    scan.initialize()

    # Tokens validated before the upgrade are backfilled, so a poller starting
    # at zero receives them rather than only what is validated from now on.
    page = scan.candidates_since()
    assert [row["token"] for row in page["candidates"]] == ["already-live"]
    assert page["next_seq"] == 1
    assert scan.list_candidates(source="lever")[0]["validated_seq"] is None
    # Idempotent: a second open must not renumber or duplicate.
    Keyscan(workdir).initialize()
    assert scan.candidates_since()["next_seq"] == 1


def test_rules_match_in_python_and_duckdb():
    assert selftest() == []


def test_rippling_locale_url_and_personio_workzag_xml():
    assert extract("https://ats.rippling.com/en-GB/aaca/jobs/123") == ("rippling", "aaca")
    assert extract("https://ats.rippling.com/aaca/jobs/123") == ("rippling", "aaca")
    from xjobscore.keyscan.core import _xml_job_count
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
            return httpx.Response(200, json={"items": [{"TotalJobsCount": 2315, "requisitionList": [{}]}]})
        return httpx.Response(404)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    scan._new_run(["company"], 6)
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
            scan._record_candidate(source, token, "company", BY_SOURCE[source].board_url.format(**BY_SOURCE[source].urls(token)))
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
async def test_oleeo_requires_supported_atom_shape_and_exports_board_override(tmp_path: Path):
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
    scan._new_run(["company"], 6)
    candidate_id = scan._record_candidate(
        "oleeo", "asp/14", "company", "https://asp.tal.net/candidate/jobboard/vacancy/14",
        company_id=2491, company_name="Avon and Somerset Police",
    )
    async with httpx.AsyncClient(transport=scan.transport, follow_redirects=True) as client:
        scan._client = client
        validation = await scan._probe(scan.list_candidates(source="oleeo")[0])
        scan._set_validation(candidate_id, *validation)
    scan._finish("cancelled")

    assert validation[:2] == ("live", 1)
    output = tmp_path / "catalogs"
    scan.export_catalog(output)
    feed = tomllib.loads((output / "oleeo.toml").read_text())["feed"][0]
    assert feed["key"] == "asp"
    assert feed["config"]["url"].endswith("/vacancy/14/feed")
    assert feed["verified"] is True


def test_catalog_disables_tokens_missing_required_adapter_config(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    scan._new_run(["company"], 6)
    bullhorn_id = scan._record_candidate(
        "bullhorn", "44/CJAT1S", "company",
        "https://public-rest44.bullhornstaffing.com/rest-services/CJAT1S/query/JobBoardPost",
        company_id=695, company_name="ESS Clinical",
    )
    adp_id = scan._record_candidate(
        "adp", "d16ba13d-474e-4326-b628-74c87f0b289a/19000101_000001", "company",
        "https://workforcenow.adp.com/", company_id=1244, company_name="Look Trailers",
    )
    scan._set_validation(bullhorn_id, "live", 1, {"responses": []})
    scan._set_validation(adp_id, "live", 1, {"responses": []})
    scan._finish("cancelled")

    output = tmp_path / "catalogs"
    scan.export_catalog(output)
    bullhorn = tomllib.loads((output / "bullhorn.toml").read_text())["feed"][0]
    adp = tomllib.loads((output / "adp.toml").read_text())["feed"][0]
    assert bullhorn["config"] == {"company_raw": "ESS Clinical"}
    # A token the adapter cannot run is exported unverified, which is what now
    # keeps it out of loader.feeds(); the registry has no enabled flag.
    assert bullhorn["verified"] is False
    assert adp["config"] == {"company_raw": "Look Trailers"}
    assert adp["verified"] is True


@pytest.mark.asyncio
async def test_icims_vanity_token_retrieves_its_json_job_listings(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://careers.paychex.com/api/jobs?page=1"):
            return httpx.Response(200, json={"jobs": [{"data": {"req_id": "1"}}], "totalCount": 1})
        return httpx.Response(404)

    scan = Keyscan(tmp_path / "work", transport=transport(handler))
    scan._new_run(["company"], 6)
    await scan._discover_page(
        '<a href="https://careers.paychex.com/careers/jobs">Careers</a>',
        "https://paychex.com/careers",
        "company",
        {"company_id": 777, "company_name": "Paychex", "domain": "paychex.com"},
        "https://paychex.com/",
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
