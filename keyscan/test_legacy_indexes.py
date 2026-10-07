"""Unsupported archives must not become empty scans or network failures."""

from functools import partial
import gzip

import httpx
import pytest

from keyscan.__main__ import _progress_line
from keyscan.core import COLLINFO, LEGACY_CRAWLS, LEGACY_INDEX_REASON, Keyscan, KeyscanError


@pytest.mark.asyncio
async def test_shared_ui_cli_list_excludes_legacy_live_and_from_old_cache(tmp_path):
    supported = {
        "id": "CC-MAIN-2026-30", "name": "July 2026 Index",
        "from": "2026-07-10T07:05:34", "to": "2026-07-23T01:13:28",
    }
    legacy = [
        {"id": crawl, "name": crawl, "from": "2008-01-01", "to": "2012-12-31"}
        for crawl in LEGACY_CRAWLS
    ]
    offline = False

    def handler(request):
        if offline:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json=[supported, *legacy])

    scan = Keyscan(tmp_path, transport=httpx.MockTransport(handler))
    live = await scan.list_crawls()
    assert [row["id"] for row in live["crawls"]] == [supported["id"]]
    # Simulate a cache written by a version that exposed ARC collections.
    with scan._connect() as db:
        db.executemany(
            "INSERT INTO crawls_cache(id, name, from_date, to_date, fetched_at) VALUES (?, ?, ?, ?, ?)",
            [(row["id"], row["name"], row["from"], row["to"], live["fetched_at"]) for row in legacy],
        )
    offline = True
    cached = await scan.list_crawls()
    assert cached["source"] == "cache"
    assert cached["crawls"] == live["crawls"]


def test_footer_harvest_skips_legacy_without_requests(tmp_path, monkeypatch):
    def unexpected(request):
        pytest.fail(f"Legacy crawl caused a request: {request.url}")

    monkeypatch.setattr(httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(unexpected)))
    scan = Keyscan(tmp_path)
    events = []
    result = scan.harvest_footers(sorted(LEGACY_CRAWLS), on_event=events.append)

    assert result["complete"] == []
    assert result["harvested"] == {}
    assert set(result["no_index"]) == LEGACY_CRAWLS
    assert result["skipped"] == dict.fromkeys(LEGACY_CRAWLS, LEGACY_INDEX_REASON)
    assert all(event["event"] == "unsupported_index" for event in events)
    assert all("2013 onward" in _progress_line(event) for event in events)
    with scan._connect() as db:
        assert db.execute("SELECT count(*) FROM footer_crawls").fetchone()[0] == 0


def test_missing_modern_manifest_is_not_classified_as_legacy(tmp_path, monkeypatch):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(handler)))
    events = []
    result = Keyscan(tmp_path).harvest_footers(
        ["CC-MAIN-2012", "CC-MAIN-2026-30"], on_event=events.append,
    )
    assert len(requested) == 1
    assert "CC-MAIN-2026-30" in requested[0]
    assert "HTTP 404" in result["skipped"]["CC-MAIN-2026-30"]
    assert events[-1]["event"] == "no_index"
    assert result["complete"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("modern_status", [None, 200, 404])
async def test_crawl_skips_legacy_without_marking_it_scanned(tmp_path, modern_status):
    requested = []
    modern = "CC-MAIN-2026-30"
    collections = [{"id": "CC-MAIN-2012"}]
    if modern_status is not None:
        collections.insert(0, {"id": modern})

    def handler(request):
        requested.append(str(request.url))
        if str(request.url) == COLLINFO:
            return httpx.Response(200, json=collections)
        assert modern in str(request.url)
        return httpx.Response(modern_status, content=gzip.compress(b""))

    scan = Keyscan(tmp_path, transport=httpx.MockTransport(handler))
    run_id = scan._new_run(["commoncrawl"], crawls=2, sources=["greenhouse"])
    if modern_status == 200:
        await scan._run(run_id)
    else:
        expected = "No selected crawl" if modern_status is None else "snapshot\\(s\\) failed"
        with pytest.raises(KeyscanError, match=expected):
            await scan._run(run_id)
    status = scan.status(run_id)
    assert status["status"] == ("completed" if modern_status == 200 else "failed")
    assert all("CC-MAIN-2012" not in url for url in requested)
    assert not any("CC-MAIN-2012" in marker for marker in status["checkpoint"].get("cc_done", []))
    events = [{**entry, **entry.get("detail", {})} for entry in scan.tail_log(run_id)[0]]
    skipped = next(entry for entry in events if entry.get("event") == "unsupported_index")
    assert skipped["reason"] == LEGACY_INDEX_REASON
    with scan._connect() as db:
        assert db.execute("SELECT count(*) FROM scanned WHERE crawl = 'CC-MAIN-2012'").fetchone()[0] == 0
