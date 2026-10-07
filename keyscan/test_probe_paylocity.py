"""Paylocity must distinguish recruiting boards from generic error HTML."""

import asyncio
from dataclasses import replace
import json
from functools import partial

import httpx
import pytest

from keyscan.probe import HTMLControlCache, default_request, probe_rule
from keyscan.rules import BY_SOURCE


PAYLOCITY = BY_SOURCE["paylocity"]
# The shared HTML-control path is exercised separately from Paylocity's
# board-data path, which no longer makes a control request.
RULE = replace(PAYLOCITY, source="html-test")
BOARD = '<html><title>Acme - Job Opportunities</title><body>0 of 0 Job Opportunities</body></html>'
ERROR = '<html><title>Error</title><body>Unable to load this page.</body></html>'


@pytest.mark.parametrize("body, expected", [(BOARD, "live"), (ERROR, "inconclusive")])
async def test_html_requires_board_evidence_even_if_control_404s(body, expected):
    def handler(request):
        if request.url.path.endswith(RULE.html_control_token):
            return httpx.Response(404)
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        status, _, _ = await probe_rule(RULE, "acme", request=partial(default_request, client))
    assert status == expected


@pytest.mark.parametrize("code, reason", [(429, "http_429"), (403, "blocked"), (503, "http_503")])
async def test_control_refusal_is_not_live_and_is_not_cached(code, reason):
    controls = HTMLControlCache()
    refused = True
    control_requests = 0

    async def request(method, url, **kwargs):
        nonlocal control_requests
        if url.endswith(RULE.html_control_token):
            control_requests += 1
            if refused:
                return httpx.Response(code)
            return httpx.Response(200, text=ERROR)
        return httpx.Response(200, text=BOARD)

    status, _, details = await probe_rule(RULE, "acme", request=request, controls=controls)
    assert status == "inconclusive"
    assert details["reason"] == reason
    assert details["stage"] == "html_control"
    assert details["responses"][-1][1] == code
    assert not controls

    refused = False
    status, _, _ = await probe_rule(RULE, "acme", request=request, controls=controls)
    assert status == "live"
    assert control_requests == 2


async def test_control_transport_failure_is_not_live():
    async def request(method, url, **kwargs):
        if url.endswith(RULE.html_control_token):
            return None
        return httpx.Response(200, text=BOARD)

    status, _, details = await probe_rule(RULE, "acme", request=request)
    assert status == "inconclusive"
    assert details["reason"] == "transport"
    assert details["stage"] == "html_control"


@pytest.mark.parametrize("control_code", [200, 404])
async def test_concurrent_checks_share_one_answered_control(control_code):
    controls = HTMLControlCache()
    control_requests = 0

    async def request(method, url, **kwargs):
        nonlocal control_requests
        if url.endswith(RULE.html_control_token):
            control_requests += 1
            await asyncio.sleep(0)
            return httpx.Response(control_code, text=ERROR)
        return httpx.Response(200, text=BOARD)

    results = await asyncio.gather(*(
        probe_rule(RULE, token, request=request, controls=controls)
        for token in ["acme", "other", "third", "fourth"]
    ))
    assert [result[0] for result in results] == ["live"] * 4
    assert control_requests == 1
    assert not controls.pending


async def test_concurrent_checks_share_a_failed_control_but_retry_later():
    controls = HTMLControlCache()
    control_requests = 0

    async def request(method, url, **kwargs):
        nonlocal control_requests
        if url.endswith(RULE.html_control_token):
            control_requests += 1
            await asyncio.sleep(0)
            return httpx.Response(429)
        return httpx.Response(200, text=BOARD)

    results = await asyncio.gather(*(
        probe_rule(RULE, token, request=request, controls=controls)
        for token in ["acme", "other"]
    ))
    assert [result[0] for result in results] == ["inconclusive"] * 2
    assert control_requests == 1
    await probe_rule(RULE, "third", request=request, controls=controls)
    assert control_requests == 2


async def test_control_cache_keeps_different_tenant_hosts_separate():
    rule = replace(RULE, source="regional", board_url="https://{t0}.example.test/{t1}")
    controls = HTMLControlCache()
    seen = []

    async def request(method, url, **kwargs):
        seen.append(url)
        if url.endswith(rule.html_control_token):
            return httpx.Response(200, text=ERROR)
        return httpx.Response(200, text=BOARD)

    for token in ["one/board", "two/board"]:
        status, _, _ = await probe_rule(rule, token, request=request, controls=controls)
        assert status == "live"
    assert len(controls) == 2
    assert sum(url.endswith(rule.html_control_token) for url in seen) == 2


async def test_a_matching_generic_page_is_inconclusive():
    # A vendor-branded shell can contain the words "Job Opportunities" too.
    async def request(method, url, **kwargs):
        return httpx.Response(200, text=BOARD)

    status, _, details = await probe_rule(RULE, "00000000-0000-4000-8000-000000000002", request=request)
    assert status == "inconclusive"
    assert details["reason"] == "generic_html"


async def test_paylocity_job_not_found_is_dead_despite_http_200():
    seen = []

    async def request(method, url, **kwargs):
        seen.append(url)
        return httpx.Response(200, text='<html><title>Job Not Found</title></html>')

    status, _, details = await probe_rule(PAYLOCITY, "acme", request=request)
    assert status == "absent"
    assert details["reason"] == "absent_marker"
    assert len(seen) == 1


@pytest.mark.parametrize("jobs", [[], [{"JobId": 123}]])
@pytest.mark.parametrize("liveness_only", [False, True])
async def test_paylocity_checks_board_identity_and_jobs_in_one_request(jobs, liveness_only):
    token = "c47e27a2-5dd2-408a-9ef0-c799cbdd5796"
    payload = {"Jobs": jobs, "LeadJoinUrl": f"/Recruiting/PublicLeads/New/{token}"}
    body = BOARD.replace("</body>", f"<script>window.pageData = {json.dumps(payload)};</script></body>")
    seen = []

    async def request(method, url, **kwargs):
        seen.append((method, url))
        return httpx.Response(200, text=body)

    status, count, _ = await probe_rule(PAYLOCITY, token, request=request, liveness_only=liveness_only)
    assert (status, count) == ("live", len(jobs))
    assert seen == [("GET", PAYLOCITY.board_url.format(token=token))]


@pytest.mark.parametrize("data, reason", [
    ('{"Jobs":[],"LeadJoinUrl":"/Recruiting/PublicLeads/New/another-board"}', "board_identity_mismatch"),
    ('{"Jobs":[],"LeadJoinUrl":null}', "board_identity_mismatch"),
    ('{"Jobs":{},"LeadJoinUrl":"/Recruiting/PublicLeads/New/acme"}', "missing_board_data"),
    ('{"Jobs":[', "missing_board_data"),
    ('null', "missing_board_data"),
])
async def test_paylocity_rejects_missing_malformed_or_other_board_data(data, reason):
    async def request(method, url, **kwargs):
        return httpx.Response(200, text=BOARD + f"<script>window.pageData = {data};</script>")

    status, _, details = await probe_rule(PAYLOCITY, "acme", request=request)
    assert status == "inconclusive"
    assert details["reason"] == reason


@pytest.mark.parametrize("body", [BOARD, ERROR, "<html>Cloudflare: Just a moment...</html>"])
async def test_paylocity_does_not_accept_title_only_or_error_pages(body):
    async def request(method, url, **kwargs):
        return httpx.Response(200, text=body)

    status, _, _ = await probe_rule(PAYLOCITY, "acme", request=request)
    assert status == "inconclusive"
