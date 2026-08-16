"""Request-header support in the json-api engine.

`net.get`/`net.post` have always accepted `headers=`, but `_request` never
passed them through -- the gap that blocked USAJOBS (needs an
`Authorization-Key` header carrying a free API key). Headers resolve through
the same `_render`/`$`-context machinery as `params` and `body`, so a
`feed.config` value (an API key, a contact email) reaches the wire the same
way a Comeet token already does.
"""

from __future__ import annotations

import json as json_module

from xjobscore.net import Response
from xjobscore.records import Feed
from xjobscore.sources import json_api


class FixtureNet:
    def __init__(self, payload: dict) -> None:
        self.response = Response(url="https://fixture.invalid/jobs", status=200, headers={}, body=json_module.dumps(payload).encode())
        self.get_calls: list[dict] = []
        self.post_calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None, etag=None):
        self.get_calls.append({"url": url, "params": params, "headers": headers, "etag": etag})
        return self.response

    async def post(self, url, *, json=None, headers=None):
        self.post_calls.append({"url": url, "json": json, "headers": headers})
        return self.response


async def test_get_request_sends_headers_resolved_from_feed_config():
    net = FixtureNet({"items": []})
    feed = Feed(id=1, source="x", key="acme", config={"api_key": "secret-123"})
    request = {
        "url": "https://api.example.com/jobs",
        "headers": {"Authorization-Key": "$api_key", "User-Agent": "xjobscore (ops@example.com)"},
    }

    await json_api._request(request, feed, net, {"key": feed.key, "api_key": "secret-123"}, first=True)

    assert net.get_calls[0]["headers"] == {
        "Authorization-Key": "secret-123",
        "User-Agent": "xjobscore (ops@example.com)",
    }


async def test_post_request_sends_headers_too():
    net = FixtureNet({"items": []})
    feed = Feed(id=1, source="x", key="acme", config={})
    request = {"method": "POST", "url": "https://api.example.com/jobs", "body": {}, "headers": {"X-Token": "fixed"}}

    await json_api._request(request, feed, net, {"key": feed.key}, first=True)

    assert net.post_calls[0]["headers"] == {"X-Token": "fixed"}


async def test_no_headers_key_means_no_headers_kwarg_reaches_net():
    """Every existing config omits `headers` -- must stay a no-op for them."""
    net = FixtureNet({"items": []})
    feed = Feed(id=1, source="x", key="acme", config={})
    request = {"url": "https://api.example.com/jobs"}

    await json_api._request(request, feed, net, {"key": feed.key}, first=True)

    assert net.get_calls[0]["headers"] is None
