"""Domain guesses match the page, and probing has no retry or body download."""

import asyncio
import json
from pathlib import Path
import subprocess

import httpx
import pytest

from xjobscore import company_domain
from xjobscore.records import Feed

REACHABLE = company_domain._reachable


def test_guesses_match_the_actual_page_function():
    names = [
        None, "", "Masimo Corporation", "LMAX Group", "Vaayu Tech",
        "Acme, Inc.", "Johnson & Johnson", "Ｃｏｍｐａｎｙ ＡＣＭＥ",
        "Éco Company", "Acme_co", "incorporated", "İNC Ltd", "Société Incé",
    ]
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["node", "--input-type=module", "-e", """
        import { guessDomain } from './xjobscore/console/static/companies/model.mjs';
        console.log(JSON.stringify(JSON.parse(process.argv[1]).map(guessDomain)));
        """, json.dumps(names)],
        cwd=root, check=True, capture_output=True, text=True,
    )
    assert [company_domain.guess_domain(name) for name in names] == json.loads(result.stdout)


@pytest.mark.parametrize("source,kind", [
    (source, "ATS") for source in company_domain.EXCLUDED_ATS
] + [("lever", "Direct"), ("lever", "Aggregator")])
async def test_excluded_sources_and_non_ats_need_no_database(source, kind):
    assert not await company_domain.try_for_feed(
        object(), Feed(id=1, source=source, key="acme", type=kind)
    )


@pytest.fixture
def mock_http(monkeypatch):
    real_client = httpx.AsyncClient
    calls, options = [], []

    def install(handler):
        async def recorded(request):
            calls.append(request)
            return await handler(request)

        def client(**kwargs):
            options.append(kwargs)
            return real_client(transport=httpx.MockTransport(recorded), **kwargs)

        monkeypatch.setattr(company_domain.httpx, "AsyncClient", client)
        return calls, options

    return install


@pytest.mark.parametrize("status,accepted", [
    (200, True), (204, True), (299, True), (301, False),
    (403, False), (404, False), (429, False), (500, False),
])
async def test_only_2xx_is_accepted_without_retries(mock_http, status, accepted):
    class UnreadBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("the website body must not be downloaded")
            yield b""

    async def handler(request):
        return httpx.Response(status, stream=UnreadBody())

    calls, options = mock_http(handler)
    assert await REACHABLE("acme.com") is accepted
    assert len(calls) == 1
    assert str(calls[0].url) == "https://acme.com/"
    assert options[0]["max_redirects"] == 5
    assert options[0]["timeout"] == 5.0


@pytest.mark.parametrize("destination,accepted", [
    ("https://www.acme.com/", True),
    ("https://brand.example/", True),
    ("https://jobs.lever.co/acme", False),
])
async def test_redirects_are_followed_but_board_hosts_are_refused(mock_http, destination, accepted):
    async def handler(request):
        if str(request.url) == "https://acme.com/":
            return httpx.Response(302, headers={"Location": destination})
        return httpx.Response(200)

    calls, _ = mock_http(handler)
    assert await REACHABLE("acme.com") is accepted
    assert len(calls) == 2


async def test_redirect_limit_is_final(mock_http):
    async def handler(request):
        return httpx.Response(302, headers={"Location": "https://acme.com/loop"})

    calls, _ = mock_http(handler)
    assert not await REACHABLE("acme.com")
    assert len(calls) == 6  # Initial request plus five redirects, no retry.


async def test_dns_or_connection_failure_is_not_retried(mock_http):
    async def handler(request):
        raise httpx.ConnectError("DNS failed", request=request)

    calls, _ = mock_http(handler)
    assert not await REACHABLE("acme.com")
    assert len(calls) == 1


async def test_total_timeout_bounds_the_whole_attempt(mock_http, monkeypatch):
    async def handler(request):
        await asyncio.Event().wait()

    calls, _ = mock_http(handler)
    monkeypatch.setattr(company_domain, "TIMEOUT_S", 0.01)
    assert not await REACHABLE("acme.com")
    assert len(calls) == 1
