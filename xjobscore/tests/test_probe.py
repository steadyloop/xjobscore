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


@pytest.mark.parametrize("message", [
    "This job has expired",
    "This position is no longer active",
    "This role has been filled",
    "We are no longer accepting applications",
    "Applications for this position are closed",
])
async def test_closed_page_message_is_distinct_from_a_dead_link(message):
    response = Response(url=URL, status=200, headers={}, body=f"<h1>{message}</h1>".encode())
    result = await probe.probe(_Net(response), URL, page=True)
    assert result.outcome == "page_closed"
    assert result.detail == message
    assert result.status == 200


async def test_job_page_redirect_is_page_closure_under_product_policy():
    landed = "https://job-boards.greenhouse.io/anthropic?error=true"
    result = await probe.probe(_Net(_response(200, landed)), URL, page=True)
    assert result.outcome == "page_closed"
    assert result.final_url == landed
    assert result.detail == "job page redirected"


async def test_closed_strings_inside_scripts_do_not_close_an_open_job():
    response = Response(url=URL, status=200, headers={}, body=(
        b'<script>const translations = {closed: "This job has expired"};</script>'
        b'<h1>Engineer</h1><button>Apply</button>'
    ))
    assert (await probe.probe(_Net(response), URL, page=True)).outcome == "live"


@pytest.mark.parametrize("status", [403, 429, 500])
async def test_failed_page_request_does_not_become_redirect_closure(status):
    net = _Net(NetError("refused", url="https://example.com/challenge", status=status))
    assert (await probe.probe(net, URL, page=True)).outcome == "inconclusive"


async def test_empty_javascript_shell_does_not_claim_the_job_is_live():
    response = Response(url=URL, status=200, headers={}, body=b"<title>Jobs</title><div id='app'></div>")
    assert (await probe.probe(_Net(response), URL, page=True)).outcome == "inconclusive"


async def test_accepting_applications_until_role_is_filled_is_not_a_closure():
    response = Response(url=URL, status=200, headers={}, body=b"<p>Apply until this position is filled.</p>")
    assert (await probe.probe(_Net(response), URL, page=True)).outcome == "live"


def test_page_closure_tombstone_keeps_channel_and_evidence():
    from xjobscore.records import PAGE_CLOSED, Item
    result = probe.Probe("page_closed", URL, URL, 200, "This job has expired")
    payload = probe.tombstone(result, probed_at=NOW)
    posting, = probe.parse_tombstone(Item("123", payload), payload["_probe"])
    assert posting.closed_evidence == PAGE_CLOSED
    assert payload["_probe"]["detail"] == "This job has expired"
    assert payload["_probe"]["probed_at"] == NOW.isoformat()
