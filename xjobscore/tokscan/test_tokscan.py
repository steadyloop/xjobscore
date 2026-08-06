from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
import tomllib

import duckdb
import httpx
import pytest

from xjobscore.tokscan.core import Tokscan, _Cancelled, selftest
from xjobscore.tokscan.rules import BY_SOURCE, extract


class FixtureScan(Tokscan):
    def _company_seeds(self):
        return [{"domain": "acme.test", "company_id": "acme", "company_name": "Acme"}]


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
    assert (candidate["source"], candidate["token"], candidate["company_id"]) == ("greenhouse", "acme", "acme")
    entries, _ = scan.tail_log(run_id)
    assert any(entry["phase"] == "validate" for entry in entries)
    request = next(entry for entry in entries if entry.get("event") == "request_timing")
    assert request["host"] == "acme.test"
    assert request["outcome"] == "ok"
    assert {"elapsed_ms", "queue_ms", "throttle_delay_ms", "retry_delay_ms"} <= request.keys()
    assert any(entry.get("event") == "phase_timing" for entry in entries)
    catalog = tomllib.loads((tmp_path / "catalogs" / "greenhouse.toml").read_text())
    assert catalog["feed"][0]["key"] == "acme"


@pytest.mark.asyncio
async def test_validation_distinguishes_ats_html_and_generic_html(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "acme.applytojob.com":
            return httpx.Response(200, text='<html><title>JazzHR Job Listings</title><form id="form_search_jobs"></form><a class="job_title_link" href="/apply/jobs/details/123">Role</a></html>')
        if request.url.host.endswith("teamtailor.com"):
            return httpx.Response(200, text="<html><body>generic careers shell</body></html>")
        return httpx.Response(404)

    scan = Tokscan(tmp_path / "work", transport=transport(handler))
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

    scan = Tokscan(tmp_path / "work", transport=transport(handler))
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
    scan = Tokscan(tmp_path / "work", transport=transport(handler))
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
    scan = Tokscan(tmp_path / "work", transport=transport(handler))
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
    scan = Tokscan(tmp_path / "work")
    scan._new_run(["company", "wayback"], 6)
    candidate_id = scan._record_candidate("greenhouse", "acme", "company", "https://boards.greenhouse.io/acme", company_id="acme")
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


def test_catalog_export_mirrors_source_catalog_schema(tmp_path: Path):
    scan = Tokscan(tmp_path / "work")
    scan._new_run(["company"], 6)
    candidate_id = scan._record_candidate(
        "jobvite", "acme", "company", "https://jobs.jobvite.com/acme",
        company_id="egnyte", company_name="Egnyte",
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
    assert feed["company_id"] == "egnyte"
    assert feed["company"] == "Egnyte"
    assert feed["domain"] == "egnyte.com"
    assert feed["status"] == "live"
    assert feed["enabled"] is True
    scan._finish("cancelled")


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
    scan = Tokscan(tmp_path / "work")
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


def test_rules_match_in_python_and_duckdb():
    assert selftest() == []


def test_rippling_locale_url_and_personio_workzag_xml():
    assert extract("https://ats.rippling.com/en-GB/aaca/jobs/123") == ("rippling", "aaca")
    assert extract("https://ats.rippling.com/aaca/jobs/123") == ("rippling", "aaca")
    from xjobscore.tokscan.core import _xml_job_count
    assert _xml_job_count('<workzag-jobs><position><id>1</id></position></workzag-jobs>') == 1


def test_trakstar_replaces_legacy_recruiterbox_urls():
    assert extract("https://acme.hire.trakstar.com/jobs/123") == ("trakstar", "acme")
    assert extract("https://acme.recruiterbox.com/") is None


@pytest.mark.asyncio
async def test_icims_vanity_token_retrieves_its_json_job_listings(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://careers.paychex.com/api/jobs?page=1"):
            return httpx.Response(200, json={"jobs": [{"data": {"req_id": "1"}}], "totalCount": 1})
        return httpx.Response(404)

    scan = Tokscan(tmp_path / "work", transport=transport(handler))
    scan._new_run(["company"], 6)
    await scan._discover_page(
        '<a href="https://careers.paychex.com/careers/jobs">Careers</a>',
        "https://paychex.com/careers",
        "company",
        {"company_id": "paychex", "company_name": "Paychex", "domain": "paychex.com"},
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
