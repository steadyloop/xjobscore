"""Comeet -- one board, two key spellings.

Comeet's list endpoint needs a board id in the path and a public per-board API
token in the query. Those two values reach the adapter written either way:
keyscan promotes a discovered candidate to the composite `board-id/api-token`
(keyscan/probe.py `_probe_comeet`), while an operator-entered row spells the
board id alone and carries the API token in `token.config`.

`key_parts` splits the composite; `config`, applied last in `json_api.harvest`,
still wins for the operator row. Both must render the same URL -- an empty
`{token}` is a 404 from Comeet, which is how the composite rows failed silently
until this was added.
"""

from __future__ import annotations

import json

import pytest

from xjobscore.net import Response
from xjobscore.records import Feed
from xjobscore.sources import json_api

BOARD = "98.008"
API_TOKEN = "89833903C282AF8044C011303C283C2819C8"
URL = (
    f"https://www.comeet.co/careers-api/2.0/company/{BOARD}"
    f"/positions?token={API_TOKEN}&details=true"
)

POSITIONS = [
    {"uid": "p1", "name": "Process Engineer", "company_name": "Acme",
     "url_comeet_hosted_page": "https://www.comeet.com/jobs/acme/98.008/p1",
     "location": {"name": "Tel Aviv"}, "details": []},
]


class RecordingNet:
    """Serves one body for any URL and records what was asked for."""

    def __init__(self, body: list) -> None:
        self.body = body
        self.urls: list[str] = []

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        self.urls.append(url)
        return Response(url=url, status=200, headers={}, body=json.dumps(self.body).encode())


@pytest.mark.parametrize(
    ("key", "config"),
    [
        pytest.param(f"{BOARD}/{API_TOKEN}", {}, id="keyscan-composite"),
        pytest.param(BOARD, {"token": API_TOKEN}, id="operator-key-plus-config"),
    ],
)
@pytest.mark.asyncio
async def test_both_key_spellings_render_the_same_list_url(key, config):
    net = RecordingNet(POSITIONS)
    feed = Feed(id=1, source="comeet", key=key, config=config)

    record = await json_api.harvest("comeet", feed, net)

    assert net.urls == [URL]
    assert [item.external_id for item in record.items] == ["p1"]


@pytest.mark.asyncio
async def test_config_token_overrides_the_key_split():
    """A composite key plus a config token is the operator's answer, not the key's."""
    net = RecordingNet(POSITIONS)
    feed = Feed(
        id=1, source="comeet", key=f"{BOARD}/stale-token", config={"token": API_TOKEN}
    )

    await json_api.harvest("comeet", feed, net)

    assert net.urls == [URL]
