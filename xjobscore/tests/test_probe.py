"""The probe channel's only job is telling three things apart.

A 404 read as a timeout costs one closure. A 429 read as a 404 fabricates one,
and nothing downstream ever revisits a fabricated closure -- `status` is
monotonic, so the row is wrong permanently. These tests exist for that second
failure mode.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from xjobscore import probe
from xjobscore.net import NetError, Response

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
URL = "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs/123"


class _Net:
    """Stands in for Net.get, which raises on every status except 2xx/304."""

    def __init__(self, outcome):
        self._outcome = outcome
        self.calls: list[tuple[str, int]] = []

    async def get(self, url, *, concurrency=1, **kwargs):
        self.calls.append((url, concurrency))
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _response(status: int, url: str = URL) -> Response:
    return Response(url=url, status=status, headers={}, body=b"{}")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410])
async def test_gone_status_is_closure_evidence(status):
    net = _Net(NetError(f"HTTP {status}", url=URL, status=status))

    result = await probe.probe(net, URL)

    assert result.outcome == "absent"
    assert result.status == status


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 403])
async def test_blocked_or_broken_is_never_closure(status):
    """The whole point of the third outcome.

    429 and 5xx reach here only after Net exhausted its retries, and 403 says
    we were refused. All four are facts about the request, not the job.
    """
    net = _Net(NetError(f"HTTP {status}", url=URL, status=status))

    result = await probe.probe(net, URL)

    assert result.outcome == "inconclusive"
    assert result.status == status


@pytest.mark.asyncio
async def test_transport_failure_is_never_closure():
    """A NetError with no status at all: DNS, timeout, connection reset."""
    net = _Net(NetError("timed out", url=URL, status=None))

    result = await probe.probe(net, URL)

    assert result.outcome == "inconclusive"
    assert result.status is None


@pytest.mark.asyncio
async def test_a_resolving_url_is_live():
    net = _Net(_response(200))

    result = await probe.probe(net, URL)

    assert result.outcome == "live"
    assert result.status == 200


@pytest.mark.asyncio
async def test_final_url_survives_a_redirect():
    """Net follows redirects, so a 302 arrives as a 200 somewhere else.

    Recorded rather than judged: a redirect to a careers index is closure
    evidence and a redirect to another posting is a URL migration, and telling
    them apart needs per-vendor rules that do not exist yet.
    """
    landed = "https://job-boards.greenhouse.io/anthropic"
    net = _Net(_response(200, url=landed))

    result = await probe.probe(net, URL)

    assert result.outcome == "live"
    assert result.url == URL
    assert result.final_url == landed


@pytest.mark.asyncio
async def test_tombstone_carries_the_status_it_saw():
    net = _Net(NetError("HTTP 404", url=URL, status=404))
    result = await probe.probe(net, URL)

    payload = probe.tombstone(result, probed_at=NOW)

    assert payload["_probe"]["outcome"] == "absent"
    assert payload["_probe"]["http_status"] == 404
    assert payload["_probe"]["probed_at"] == NOW.isoformat()


@pytest.mark.asyncio
async def test_probe_many_passes_concurrency_through():
    """Every board of one ATS shares a hostname; at concurrency 1 Net would
    serialize the whole pass at one request per second."""
    net = _Net(_response(200))

    results = await probe.probe_many(net, [URL, URL + "4"], concurrency=4)

    assert [one.outcome for one in results] == ["live", "live"]
    assert {call[1] for call in net.calls} == {4}
