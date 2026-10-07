from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

import keyscan.wayback as wayback
from keyscan.core import Keyscan, KeyscanError
from keyscan.rules import BY_SOURCE
from keyscan.wayback import WaybackClient, WaybackRefused, rule_token


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Pacing and backoff sleep for real in production; never in tests."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(wayback, "pause", fake_sleep)
    return slept


def cdx(handler):
    """A transport that answers CDX queries with `handler(params)` -> Response."""

    async def route(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "web.archive.org"
        return handler(dict(request.url.params))

    return httpx.MockTransport(route)


def lines(*urls: str, resume: str | None = None) -> httpx.Response:
    body = "\n".join(urls) + "\n"
    if resume:
        body += f"\n{resume}\n"
    return httpx.Response(200, text=body)


def scanned(scan: Keyscan) -> list[tuple[str, str]]:
    with sqlite3.connect(scan.db_path) as con:
        return sorted(con.execute("SELECT ats, crawl FROM scanned").fetchall())


# --- rule_token -------------------------------------------------------------

@pytest.mark.parametrize("url, token", [
    ("https://jobs.lever.co/palantir/abc-123", "palantir"),
    ("https://jobs.lever.co:443/bisnow", "bisnow"),
    ("https://jobs.eu.lever.co/glow.co", "glow.co"),
    ("https://JOBS.LEVER.CO/Academy", None),  # STOP word, as in the crawl
    ("https://jobs.lever.co/RaptorMaps", "RaptorMaps"),  # case kept
    ("https://www.lever.co/blog", None),
    ("https://api.lever.co/v0/postings/acme", None),
])
def test_rule_token_applies_only_that_rules_patterns(url, token):
    assert rule_token(BY_SOURCE["lever"], url) == token


def test_rule_token_ignores_other_vendors_urls():
    assert rule_token(BY_SOURCE["lever"], "https://boards.greenhouse.io/stripe") is None
    assert rule_token(BY_SOURCE["greenhouse"], "https://boards.greenhouse.io/stripe") == "stripe"


# --- client -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_follows_resume_keys_and_groups_by_token():
    seen: list[dict] = []

    def handler(params):
        seen.append(params)
        if "resumeKey" not in params:
            return lines(
                "https://jobs.lever.co/acme",
                "https://jobs.lever.co/acme/1",
                "https://www.lever.co/pricing",
                resume="KEY1",
            )
        return lines("https://jobs.lever.co/acme/2", "https://jobs.lever.co/globex")

    client = WaybackClient(user_agent="test", transport=cdx(handler))
    found = await client.scan(BY_SOURCE["lever"], "lever.co", 2026)
    await client.close()

    assert {token: entry.count for token, entry in found.items()} == {"acme": 3, "globex": 1}
    assert found["acme"].urls[0] == "https://jobs.lever.co/acme"
    assert [p.get("resumeKey") for p in seen] == [None, "KEY1"]
    assert seen[0]["from"] == seen[0]["to"] == "2026"
    assert seen[0]["matchType"] == "domain" and seen[0]["collapse"] == "urlkey"


@pytest.mark.asyncio
async def test_client_spaces_requests_apart(no_waiting):
    calls = iter([lines("x", resume="K"), lines("y")])
    client = WaybackClient(user_agent="test", transport=cdx(lambda params: next(calls)), gap=4.0)
    await client.scan(BY_SOURCE["lever"], "lever.co", 2026)
    await client.close()
    # The second request waited out (nearly) the whole gap.
    assert no_waiting and 3.5 < no_waiting[-1] <= 4.0


@pytest.mark.asyncio
async def test_retry_after_is_honoured_then_the_page_is_retried(no_waiting):
    calls = iter([
        httpx.Response(429, headers={"Retry-After": "17"}),
        lines("https://jobs.lever.co/acme"),
    ])
    client = WaybackClient(user_agent="test", transport=cdx(lambda params: next(calls)), gap=0)
    found = await client.scan(BY_SOURCE["lever"], "lever.co", 2026)
    await client.close()
    assert "acme" in found
    assert 17.0 in no_waiting


@pytest.mark.asyncio
async def test_exhausted_backoff_raises():
    client = WaybackClient(
        user_agent="test", transport=cdx(lambda params: httpx.Response(503)), gap=0, backoff=(1.0, 2.0),
    )
    with pytest.raises(WaybackRefused):
        await client.scan(BY_SOURCE["lever"], "lever.co", 2026)
    await client.close()
    assert client.requests == 3


# --- runs -------------------------------------------------------------------

def lever_archive(asked: list[tuple[str, str]] | None = None, fail_years: set[str] = frozenset()):
    def handler(params):
        if asked is not None:
            asked.append((params["url"], params["from"]))
        if params["from"] in fail_years:
            return httpx.Response(503)
        return lines(f"https://jobs.lever.co/acme-{params['from']}", "https://jobs.lever.co/globex")

    return cdx(handler)


@pytest.mark.asyncio
async def test_wayback_run_records_tokens_and_marks_pairs(tmp_path: Path):
    scan = Keyscan(tmp_path / "work", transport=lever_archive())
    run_id = await scan.run(["wayback"], sources=["lever"], years=[2025, 2026])

    status = scan.status(run_id)
    assert status["status"] == "completed"
    assert status["counts"]["found"] == 3 and status["counts"]["added"] == 3
    tokens = {row["key"]: row for row in scan.list_candidates(source="lever")}
    assert set(tokens) == {"acme-2025", "acme-2026", "globex"}
    assert tokens["acme-2026"]["method"] == "wayback"
    assert tokens["acme-2026"]["details"]["wayback_year"] == 2026
    assert scanned(scan) == [("lever", "wayback:2025"), ("lever", "wayback:2026")]


@pytest.mark.asyncio
async def test_finished_pairs_are_skipped_unless_rescan(tmp_path: Path):
    asked: list[tuple[str, str]] = []
    scan = Keyscan(tmp_path / "work", transport=lever_archive(asked))
    await scan.run(["wayback"], sources=["lever"], years=[2026])
    await scan.run(["wayback"], sources=["lever"], years=[2025, 2026])
    assert asked == [("lever.co", "2026"), ("lever.co", "2025")]

    await scan.run(["wayback"], sources=["lever"], years=[2026], rescan=True)
    assert asked[-1] == ("lever.co", "2026")


@pytest.mark.asyncio
async def test_a_refused_pair_fails_the_run_without_a_mark_and_resume_finishes_it(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setattr(wayback, "BACKOFF_SECONDS", (1.0,))
    scan = Keyscan(tmp_path / "work", transport=lever_archive(fail_years={"2025"}))
    with pytest.raises(KeyscanError, match="wayback:2025/lever"):
        await scan.run(["wayback"], sources=["lever"], years=[2025, 2026])

    run_id = scan.status()["id"]
    assert scan.status(run_id)["status"] == "failed"
    # 2026 still completed and is marked; 2025 is not.
    assert scanned(scan) == [("lever", "wayback:2026")]

    asked: list[tuple[str, str]] = []
    scan.transport = lever_archive(asked)
    await scan.resume(run_id)
    assert asked == [("lever.co", "2025")]
    assert scanned(scan) == [("lever", "wayback:2025"), ("lever", "wayback:2026")]


@pytest.mark.asyncio
async def test_wayback_marks_never_count_as_crawl_snapshots(tmp_path: Path):
    scan = Keyscan(tmp_path / "work", transport=lever_archive())
    await scan.run(["wayback"], sources=["lever"], years=[2026])
    pairs = scan._scanned_pairs([BY_SOURCE["lever"]])
    assert pairs == {"wayback:2026/lever"}
    assert not any(pair.startswith("CC-MAIN") for pair in pairs)


def test_wayback_years_default_and_validation(tmp_path: Path):
    scan = Keyscan(tmp_path / "work")
    run_id = scan._new_run(["wayback"], sources=["lever"])
    years = scan.status(run_id)["config"]["wayback_years"]
    assert years[0] == 2013 and years == sorted(years)
    scan._finish("completed")
    with pytest.raises(KeyscanError):
        scan._new_run(["wayback"], years=[1990])
