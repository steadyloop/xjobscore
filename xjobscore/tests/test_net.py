"""Unit tests for `Net`'s vendor-wide concurrency cap (`_VENDOR_CAPS`).

`Net._host_semaphores` is keyed by `(netloc, concurrency)`, which is exactly
right for a vendor that answers on one hostname but fragments for a
multi-tenant vendor that hands every customer its own hostname (Workday: 87
feeds, 77 distinct hostnames, each independently capped at 32 -- nothing
bounds the vendor total). `_VENDOR_CAPS` adds an opt-in, suffix-matched cap
shared across every hostname under that suffix.

All fakes here avoid the network entirely: `ProbeNet` overrides
`_request_one` (the same technique `xjobscore/inspector/test_network.py`
uses for `InspectorNet`) so only the semaphore machinery in `Net._request`
is exercised, and `ScriptedClient` stands in for `httpx.AsyncClient` where a
test needs the real `_request_one`/`_throttle` path.
"""

from __future__ import annotations

import asyncio
import time

import httpx

import pytest

from xjobscore import net as net_module
from xjobscore.net import (
    _PACE_CEIL_S,
    _PACE_HALFLIFE_S,
    Net,
    Response,
    _HostPace,
    _VENDOR_CAPS,
    _vendor_key,
    vendor_cap,
)


class InFlight:
    """Tracks concurrent in-flight calls, overall and per host."""

    def __init__(self) -> None:
        self.count = 0
        self.maximum = 0
        self.host_count: dict[str, int] = {}
        self.host_maximum: dict[str, int] = {}

    def enter(self, host: str) -> None:
        self.count += 1
        self.maximum = max(self.maximum, self.count)
        self.host_count[host] = self.host_count.get(host, 0) + 1
        self.host_maximum[host] = max(self.host_maximum.get(host, 0), self.host_count[host])

    def exit(self, host: str) -> None:
        self.count -= 1
        self.host_count[host] -= 1


class ProbeNet(Net):
    """A `Net` whose `_request_one` records overlap instead of calling httpx."""

    def __init__(self, tracker: InFlight, *, delay: float = 0.01) -> None:
        super().__init__(user_agent="test")
        self.tracker = tracker
        self.delay = delay
        # `_request` only checks this is not None; `_request_one` is replaced
        # entirely below, so no real client is required.
        self._client = object()

    async def _request_one(self, method, url, host, *, throttled, **kwargs):
        self.tracker.enter(host)
        await asyncio.sleep(self.delay)
        self.tracker.exit(host)
        return Response(url=url, status=200, headers={}, body=b"{}")


async def test_vendor_cap_bounds_many_hostnames_under_one_vendor():
    """The actual regression: 30 Workday tenants, one vendor-wide cap.

    Each hostname's own semaphore would allow 32 in flight (the catalog's
    real Workday width), but the vendor cap for myworkdayjobs.com is 24, and
    that is what must bind across all of them combined.
    """
    tracker = InFlight()
    net = ProbeNet(tracker)
    hostnames = [f"tenant{i}.wd1.myworkdayjobs.com" for i in range(30)]

    await asyncio.gather(
        *(
            net._request("GET", f"https://{host}/wday/cxs/tenant/site/jobs", concurrency=32)
            for host in hostnames
            for _ in range(3)
        )
    )

    assert tracker.maximum == _VENDOR_CAPS["myworkdayjobs.com"] == 24


async def test_host_without_capped_suffix_is_unaffected():
    """No suffix in `_VENDOR_CAPS` matches greenhouse.io, so distinct hosts
    there never share a cap -- today's behaviour, unchanged."""
    tracker = InFlight()
    net = ProbeNet(tracker)
    hostnames = [f"job{i}.greenhouse.io" for i in range(20)]

    await asyncio.gather(
        *(net._request("GET", f"https://{host}/postings", concurrency=8) for host in hostnames)
    )

    assert tracker.maximum == 20


def test_suffix_matching_does_not_over_match():
    assert _vendor_key("bamboohr.com") == "bamboohr.com"
    assert _vendor_key("acme.bamboohr.com") == "bamboohr.com"
    assert _vendor_key("acme.bamboohr.com:443") == "bamboohr.com"
    # A hostname that merely ends with the suffix's characters, but not on a
    # dot boundary, must not match.
    assert _vendor_key("notbamboohr.com") is None
    assert _vendor_key("bamboohr.com.evil.example") is None


async def test_vendor_cap_composes_with_a_tighter_per_host_cap():
    """A single host's own concurrency width, when narrower than the vendor
    cap, still binds -- the vendor semaphore only adds an outer ceiling."""
    tracker = InFlight()
    net = ProbeNet(tracker)
    host = "acme.bamboohr.com"

    await asyncio.gather(*(net._request("GET", f"https://{host}/v1/jobs", concurrency=3) for _ in range(10)))

    assert tracker.host_maximum[host] == 3
    assert tracker.maximum == 3  # well under bamboohr.com's vendor cap of 8


async def test_vendor_cap_binds_below_the_sum_of_per_host_caps():
    """Two hosts, each with its own width of 5 (10 combined demand), under a
    vendor cap of 8: the vendor total is what must bind, not either host's
    individual semaphore."""
    tracker = InFlight()
    net = ProbeNet(tracker)
    host_a, host_b = "acme.bamboohr.com", "beta.bamboohr.com"

    await asyncio.gather(
        *(net._request("GET", f"https://{host_a}/v1/jobs", concurrency=5) for _ in range(10)),
        *(net._request("GET", f"https://{host_b}/v1/jobs", concurrency=5) for _ in range(10)),
    )

    assert tracker.maximum == _VENDOR_CAPS["bamboohr.com"] == 8
    # Per-host semaphores are unaffected in kind -- each host's own cap of 5
    # is still a real, distinct semaphore.
    assert set(net._host_semaphores) == {(host_a, 5), (host_b, 5)}
    assert tracker.host_maximum[host_a] <= 5
    assert tracker.host_maximum[host_b] <= 5


async def test_vendor_cap_applies_to_the_concurrency_one_throttled_path():
    """`concurrency` defaults to 1 (the `_throttle` path), which has no
    per-host semaphore at all -- the vendor cap must still bind there."""
    tracker = InFlight()
    net = ProbeNet(tracker)
    hostnames = [f"tenant{i}.bamboohr.com" for i in range(20)]

    await asyncio.gather(*(net._request("GET", f"https://{host}/v1/jobs") for host in hostnames))

    assert tracker.maximum == _VENDOR_CAPS["bamboohr.com"] == 8


class ScriptedClient:
    """Stands in for `httpx.AsyncClient`, always answering 200 immediately."""

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        return httpx.Response(200, content=b"{}", request=httpx.Request(method, url))


async def test_min_interval_throttle_still_applies_under_a_vendor_cap():
    """The 1-request-per-`min_interval_s` pacing on the `concurrency == 1`
    path must still run for a host under a capped vendor suffix -- the
    vendor semaphore wraps it, it does not replace it."""
    net = Net(user_agent="test", min_interval_s=0.05)
    net._client = ScriptedClient()
    host = "acme.bamboohr.com"

    started = time.monotonic()
    for _ in range(3):
        await net._request("GET", f"https://{host}/v1/jobs")
    elapsed = time.monotonic() - started

    assert elapsed >= 0.09  # ~2 gaps of 0.05s across 3 sequential requests


# -- _HostPace recovery ----------------------------------------------------
#
# `succeeded` used to decay `interval` a fixed 2% per call, which made
# recovery proportional to throughput -- and throughput is itself inversely
# proportional to `interval`. A host paced to 0.5 req/s therefore healed ~16x
# slower than one paced to 8 req/s, so the pacer crawled back slowest exactly
# when it was throttled hardest. These pin the time-based rule that replaced
# it: the clock is what recovers, not the request count.


def _drive(pace, seconds, calls, clock):
    """Run `calls` successes spread evenly across `seconds` of fake time."""
    step = seconds / calls
    for _ in range(calls):
        clock["t"] += step
        pace.succeeded()


def test_recovery_depends_on_elapsed_time_not_success_count(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(net_module.time, "monotonic", lambda: clock["t"])

    intervals = []
    for calls in (240, 15):  # 8 req/s vs 0.5 req/s across the same 30s
        clock["t"] = 1000.0
        pace = _HostPace()
        pace.interval = 2.0
        pace.last_decay = clock["t"]
        _drive(pace, _PACE_HALFLIFE_S, calls, clock)
        intervals.append(pace.interval)

    busy, idle = intervals
    # A 16x difference in request rate must not change the recovery at all.
    assert busy == pytest.approx(idle, rel=1e-9)
    # One half-life of elapsed time halves the spacing, by definition.
    assert busy == pytest.approx(1.0, rel=1e-9)


def test_recovery_from_the_ceiling_is_bounded_by_wall_time(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(net_module.time, "monotonic", lambda: clock["t"])

    pace = _HostPace()
    pace.throttled(None)
    pace.interval = _PACE_CEIL_S  # worst case: fully throttled at 0.5 req/s
    pace.last_decay = clock["t"]

    # Even at the ceiling's miserly 0.5 req/s, two half-lives of successful
    # traffic must quarter the spacing. The old per-success rule needed ~276s.
    _drive(pace, 2 * _PACE_HALFLIFE_S, 30, clock)
    assert pace.interval == pytest.approx(_PACE_CEIL_S / 4, rel=1e-9)


def test_a_burst_of_successes_cannot_buy_back_time_never_spent(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(net_module.time, "monotonic", lambda: clock["t"])

    pace = _HostPace()
    pace.interval = 1.0
    pace.last_decay = clock["t"]
    for _ in range(1000):  # no clock movement at all
        pace.succeeded()
    assert pace.interval == pytest.approx(1.0, rel=1e-9)


# ------------------------------------------------------- client construction


async def test_client_is_opened_with_explicit_connection_limits(monkeypatch):
    """httpx defaults to 100 connections; the scheduler's summed per-vendor
    widths exceed that, and the surplus would queue invisibly inside httpx."""
    opened: list[dict] = []

    class RecordingClient:
        def __init__(self, **kwargs) -> None:
            opened.append(kwargs)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(net_module.httpx, "AsyncClient", RecordingClient)

    async with Net(user_agent="t"):
        pass
    assert opened[-1]["limits"].max_connections == 256
    assert opened[-1]["limits"].max_keepalive_connections == 64

    async with Net(user_agent="t", max_connections=10, max_keepalive_connections=3):
        pass
    assert opened[-1]["limits"].max_connections == 10
    assert opened[-1]["limits"].max_keepalive_connections == 3


def test_vendor_cap_reads_the_table_request_enforces():
    """`sources.width` sizes worker pools off this, so it must match
    `_vendor_key`'s dot-boundary matching exactly."""
    assert vendor_cap("acme.bamboohr.com") == _VENDOR_CAPS["bamboohr.com"] == 8
    assert vendor_cap("x.oraclecloud.com") == 12
    assert vendor_cap("tenant.wd5.myworkdayjobs.com") == 24
    assert vendor_cap("notbamboohr.com") is None       # dot boundary, not substring
    assert vendor_cap("api.ashbyhq.com") is None       # single-host vendor: uncapped here
