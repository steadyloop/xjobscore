"""Unit tests for the inspector's token validator.

These exercise the status mapping and the per-vendor nuances the validator now
inherits from ``keyscan.probe`` -- the behaviours the old in-house probe got
wrong (zero-count boards, region fallbacks, the Comeet handshake). No database
is required: each test drives a mocked HTTP transport and passes ``pool=None``.
"""

from __future__ import annotations

import httpx
import pytest

from xjobscore.inspector import validator


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


@pytest.mark.asyncio
async def test_live_board_maps_to_live():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": [{"id": 1}]})
        return httpx.Response(200, json={"name": "Acme"})

    async with _client(handler) as client:
        result = await validator.validate_token("greenhouse", "acme", client=client)
    assert result["status"] == "live"


@pytest.mark.asyncio
async def test_all_404_maps_to_dead():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with _client(handler) as client:
        result = await validator.validate_token("greenhouse", "gone", client=client)
    assert result["status"] == "dead"


@pytest.mark.asyncio
async def test_zero_count_without_name_is_unknown_not_dead():
    # A live board with no open roles returns an empty list; with no company
    # name to confirm the id resolved, keyscan calls this inconclusive. The old
    # validator called it "dead" and deleted the feed -- the bug this fixes.
    #
    # smartrecruiters is the vendor this guard exists for, and the reason it is
    # excluded from the liveness-only shortcut: a nonexistent company id
    # answers 200 with this exact payload, so the verdict is only in the body.
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"totalFound": 0, "content": []})

    async with _client(handler) as client:
        result = await validator.validate_token("smartrecruiters", "empty", client=client)
    assert result["status"] == "unknown"
    assert result["details"]["reason"] == "zero_count"


@pytest.mark.asyncio
async def test_region_fallback_is_honoured():
    # Primary US host 404s; the EU fallback is live. Must be "live", not "dead".
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.eu.lever.co":
            return httpx.Response(200, json=[{"id": 1}])
        return httpx.Response(404)

    async with _client(handler) as client:
        result = await validator.validate_token("lever", "acme", client=client)
    assert result["status"] == "live"


@pytest.mark.asyncio
async def test_comeet_two_step_handshake_resolves():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.comeet.com":  # board page carries the api token
            return httpx.Response(200, text='<html><a href="/api?token=abcdef0123456789abcd">x</a></html>')
        if request.url.host == "www.comeet.co":  # positions API
            return httpx.Response(200, json=[{"uid": "x"}])
        return httpx.Response(404)

    async with _client(handler) as client:
        result = await validator.validate_token("comeet", "acme/A5.004", client=client)
    assert result["status"] == "live"


@pytest.mark.asyncio
async def test_persistent_http_error_is_unknown():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with _client(handler) as client:
        result = await validator.validate_token("greenhouse", "flaky", client=client)
    assert result["status"] == "unknown"


@pytest.mark.asyncio
async def test_unknown_vendor_is_unknown():
    async def handler(request: httpx.Request) -> httpx.Response:  # never called
        return httpx.Response(200)

    async with _client(handler) as client:
        result = await validator.validate_token("not-a-vendor", "x", client=client)
    assert result["status"] == "unknown"
    assert result["details"]["reason"] == "unknown_ats_not-a-vendor"


@pytest.mark.asyncio
@pytest.mark.parametrize("ats", ["ashby", "greenhouse"])
async def test_liveness_only_vendors_probe_with_head(ats: str):
    # The Tokens page stores nothing but `status`, so these three ask for the
    # status code alone rather than pulling a whole board down to count it.
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(200)

    async with _client(handler) as client:
        result = await validator.validate_token(ats, "acme", client=client)
    assert result["status"] == "live"
    assert [method for method, _ in seen] == ["HEAD"]


@pytest.mark.asyncio
async def test_head_404_is_still_dead():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with _client(handler) as client:
        result = await validator.validate_token("ashby", "gone", client=client)
    assert result["status"] == "dead"


@pytest.mark.asyncio
async def test_live_but_empty_board_is_live_under_head():
    # A HEAD rule only opts in where a nonexistent board 404s, so a 200 settles
    # it: an empty-but-real board reports live instead of falling to the
    # zero-count guard. That is the right answer to "does this board exist".
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    async with _client(handler) as client:
        result = await validator.validate_token("ashby", "no-openings", client=client)
    assert result["status"] == "live"


@pytest.mark.asyncio
async def test_gem_keeps_the_full_probe():
    # gem must NOT take a HEAD shortcut. Its API answers 200 with a body of
    # `[]` both for a real board with no openings (eqp, spherecast-ai) and for
    # one whose public page is a hard 404 (athumanresources-com,
    # brahma-consulting-group) -- the board record outlives the board. Only the
    # body separates them, so HEAD would report every one of them live.
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(200, json=[])

    async with _client(handler) as client:
        result = await validator.validate_token("gem", "unpublished", client=client)
    assert seen == ["GET"]
    assert result["status"] == "unknown"
    assert result["details"]["reason"] == "zero_count"


@pytest.mark.asyncio
async def test_lever_light_url_asks_for_one_posting():
    # 5.9 MB -> 12 KB on a 308-posting board. Still a GET, because the EU
    # fallback needs a 404 it can tell apart from a body.
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[{"id": 1}])

    async with _client(handler) as client:
        result = await validator.validate_token("lever", "acme", client=client)
    assert result["status"] == "live"
    assert seen == ["https://api.lever.co/v0/postings/acme?mode=json&limit=1"]


@pytest.mark.asyncio
async def test_workable_probe_does_not_ask_for_descriptions():
    # `details=true` costs 580 KB vs 42.6 KB and the probe reads neither the
    # descriptions nor anything else it adds.
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"name": "Acme", "jobs": [{"shortcode": "x"}]})

    async with _client(handler) as client:
        result = await validator.validate_token("workable", "acme", client=client)
    assert result["status"] == "live"
    assert "details=true" not in seen[0]


@pytest.mark.asyncio
async def test_batch_preserves_order_and_shares_client():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": [{"id": 1}]})
        return httpx.Response(200, json={"name": "Acme"})

    async with _client(handler) as client:
        results = await validator.validate_tokens(
            [("greenhouse", "a"), ("greenhouse", "b")], client=client
        )
    assert [r["token"] for r in results] == ["a", "b"]
    assert all(r["status"] == "live" for r in results)
