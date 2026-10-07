"""Teamtailor liveness comes from RSS, including empty boards and redirects."""

from functools import partial

import httpx
import pytest

from keyscan.probe import default_request, probe_rule
from keyscan.rules import BY_SOURCE


@pytest.mark.parametrize("liveness_only", [False, True])
@pytest.mark.parametrize("items", ["", "<item><title>Engineer</title></item>"])
async def test_teamtailor_uses_rss_instead_of_redirected_html(liveness_only, items):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/jobs.rss":
            return httpx.Response(200, text=f'<rss version="2.0"><channel>{items}</channel></rss>')
        return httpx.Response(301, headers={"location": "https://employer.example/jobs"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        status, count, _ = await probe_rule(
            BY_SOURCE["teamtailor"], "triggerfish",
            request=partial(default_request, client), liveness_only=liveness_only,
        )
    assert (status, count) == ("live", int(bool(items)))
    suffix = "?per_page=1" if liveness_only else ""
    assert seen == [f"https://triggerfish.teamtailor.com/jobs.rss{suffix}"]


@pytest.mark.parametrize("body", [
    "<html><body>Generic careers page</body></html>",
    '<rss version="2.0"><channel>',  # truncated XML
    "<jobs/>",  # another feed format is not Teamtailor RSS
])
async def test_teamtailor_rejects_non_rss_or_malformed_feed(body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=body),
    )) as client:
        status, count, _ = await probe_rule(
            BY_SOURCE["teamtailor"], "acme", request=partial(default_request, client),
            liveness_only=True,
        )
    assert (status, count) == ("inconclusive", None)


@pytest.mark.parametrize("code, expected", [(404, "absent"), (410, "absent"), (429, "inconclusive")])
async def test_teamtailor_missing_and_throttled_feeds(code, expected):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(code),
    )) as client:
        status, _, details = await probe_rule(
            BY_SOURCE["teamtailor"], "acme", request=partial(default_request, client),
            liveness_only=True,
        )
    assert status == expected
    assert "refusal_scope" not in details  # a direct vendor 429 still cools
    if code == 429:
        assert details["reason"] == "http_429"
