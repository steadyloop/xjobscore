from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from xjobscore.inspector.network import InspectorNet
from xjobscore.net import NetError, Response, _HostPace


class InFlight:
    def __init__(self) -> None:
        self.count = 0
        self.maximum = 0


class ProbeNet(InspectorNet):
    def __init__(self, tracker: InFlight, shared) -> None:
        super().__init__(user_agent="test", shared_host_semaphores=shared)
        self.tracker = tracker
        # _request only needs a non-None marker because _request_one is replaced.
        self._client = object()

    async def _request_one(self, method, url, host, *, throttled, **kwargs):
        self.tracker.count += 1
        self.tracker.maximum = max(self.tracker.maximum, self.tracker.count)
        await asyncio.sleep(0.01)
        self.tracker.count -= 1
        return Response(url=url, status=200, headers={}, body=b"{}")


async def test_shared_host_semaphore_caps_multiple_inspector_clients():
    tracker = InFlight()
    shared = {"api.smartrecruiters.com": asyncio.Semaphore(8)}
    first = ProbeNet(tracker, shared)
    second = ProbeNet(tracker, shared)
    url = "https://api.smartrecruiters.com/v1/companies/demo/postings/1"

    await asyncio.gather(
        *(first._request("GET", url, concurrency=8) for _ in range(12)),
        *(second._request("GET", url, concurrency=8) for _ in range(12)),
    )

    assert tracker.maximum == 8


async def test_same_host_can_use_distinct_page_and_detail_limits():
    tracker = InFlight()
    net = ProbeNet(tracker, {})
    url = "https://tenant.wd1.myworkdayjobs.com/wday/cxs/tenant/site/jobs"

    await asyncio.gather(*(net._request("POST", url, concurrency=4) for _ in range(8)))
    assert tracker.maximum == 4

    tracker.maximum = 0
    await asyncio.gather(*(net._request("GET", url, concurrency=2) for _ in range(8)))
    assert tracker.maximum == 2
    assert set(net._host_semaphores) == {
        ("tenant.wd1.myworkdayjobs.com", 4),
        ("tenant.wd1.myworkdayjobs.com", 2),
    }


class ScriptedClient:
    """Stands in for httpx.AsyncClient with a fixed sequence of responses."""

    def __init__(self, responses: list[tuple[int, dict[str, str]]]) -> None:
        self._responses = list(responses)

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        status, headers = self._responses.pop(0)
        return httpx.Response(
            status,
            headers=headers,
            content=b"{}",
            request=httpx.Request(method, url),
        )


def test_host_pace_counts_a_burst_of_429s_as_one_window():
    pace = _HostPace()

    pace.throttled(None)
    assert pace.strikes == 1
    assert pace.interval == 0.125
    # Fifteen sibling slots hitting the same wall teach nothing new.
    pace.throttled(None)
    pace.throttled(None)
    assert pace.strikes == 1
    assert pace.interval == 0.125

    # A fresh window after the cooldown expired escalates.
    pace.cooldown_until = 0.0
    pace.throttled(None)
    assert pace.strikes == 2
    assert pace.interval == 0.25

    pace.succeeded()
    assert pace.strikes == 0
    assert pace.interval < 0.25  # decays toward unpaced


async def test_host_pace_reserve_spaces_starts_and_honors_cooldown():
    pace = _HostPace()
    pace.interval = 0.03
    started = time.monotonic()
    for _ in range(3):
        await pace.reserve()
    assert time.monotonic() - started >= 0.05  # starts spaced ~0.03s apart

    pace = _HostPace()
    pace.cooldown_until = time.monotonic() + 0.05
    waited = await pace.reserve()
    assert waited >= 0.04


async def test_429_retries_through_the_host_cooldown():
    net = InspectorNet(user_agent="test")
    net._client = ScriptedClient([(429, {"retry-after": "0.05"}), (200, {})])
    url = "https://tenant.wd1.myworkdayjobs.com/wday/cxs/tenant/site/jobs"

    response = await net._request("GET", url, concurrency=4)

    assert response.status == 200
    assert [event["attempt"] for event in net.events] == [1, 2]
    # The retry waited for the shared window rather than sleeping privately.
    assert net.events[1]["paced_ms"] >= 40
    pace = net._host_paces["tenant.wd1.myworkdayjobs.com"]
    assert pace.strikes == 0  # reset by the success
    assert pace.interval > 0  # spacing stays on until it decays away


async def test_shared_paces_carry_a_cooldown_across_clients():
    shared: dict[str, _HostPace] = {}
    url = "https://tenant.wd1.myworkdayjobs.com/wday/cxs/tenant/site/jobs"

    first = InspectorNet(user_agent="test", max_retries=0, shared_host_paces=shared)
    first._client = ScriptedClient([(429, {"retry-after": "0.2"})])
    with pytest.raises(NetError):
        await first._request("GET", url, concurrency=4)

    second = InspectorNet(user_agent="test", shared_host_paces=shared)
    second._client = ScriptedClient([(200, {})])
    response = await second._request("GET", url, concurrency=4)

    assert response.status == 200
    assert second.events[0]["paced_ms"] >= 150
