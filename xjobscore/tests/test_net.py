"""Unit tests for `Net`'s vendor-wide concurrency cap (`_VENDOR_CAPS`).

`Net._host_semaphores` is keyed by `(netloc, concurrency)`, which is exactly
right for a vendor that answers on one hostname but fragments for a
multi-tenant vendor that hands every customer its own hostname (Workday: 87
feeds, 77 distinct hostnames, each independently capped at 32 -- nothing
bounds the vendor total). `_VENDOR_CAPS` adds an opt-in, suffix-matched cap
shared across every hostname under that suffix.

All fakes here avoid the network entirely: `ProbeNet` is a real `Net` whose
`httpx.AsyncClient` is a `TrackingClient`, so the host semaphores, vendor
slots and rate budgets all run and only the wire is fake; `ScriptedClient`
stands in for `httpx.AsyncClient` where a test needs scripted replies.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

import httpx

import pytest

from xjobscore import net as net_module
from xjobscore.net import (
    _PACE_CEIL_S,
    _PACE_HALFLIFE_S,
    Net,
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


class TrackingClient:
    """Stands in for `httpx.AsyncClient`, recording overlap on the wire."""

    def __init__(self, tracker: InFlight, delay: float) -> None:
        self.tracker = tracker
        self.delay = delay

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        host = urlsplit(url).netloc
        self.tracker.enter(host)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.tracker.exit(host)
        return httpx.Response(200, content=b"{}", request=httpx.Request(method, url))


class ProbeNet(Net):
    """A real `Net` -- host semaphores, vendor slots and all -- whose wire is
    a `TrackingClient`. The vendor slot is taken around the wire call itself,
    so the overlap it records is exactly what `_VENDOR_CAPS` must bound.

    Rate budgets are off: these tests are about concurrency, and a budget
    would only make them slow. `test_rate_budget_*` cover the budget.
    """

    def __init__(self, tracker: InFlight, *, delay: float = 0.01) -> None:
        super().__init__(user_agent="test", vendor_rates={}, host_rates={})
        self.tracker = tracker
        self._client = TrackingClient(tracker, delay)


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

    assert tracker.maximum == _VENDOR_CAPS["wd1.myworkdayjobs.com"] == 24


async def test_each_workday_data_center_has_its_own_cap():
    """Workday limits per data center, so wd1 and wd3 tenants must not queue
    on one shared cap: together they run to the sum of their two caps."""
    tracker = InFlight()
    net = ProbeNet(tracker)
    hostnames = [f"tenant{i}.{dc}.myworkdayjobs.com" for dc in ("wd1", "wd3") for i in range(30)]

    await asyncio.gather(
        *(
            net._request("GET", f"https://{host}/wday/cxs/tenant/site/jobs", concurrency=32)
            for host in hostnames
            for _ in range(3)
        )
    )

    assert tracker.maximum == (
        _VENDOR_CAPS["wd1.myworkdayjobs.com"] + _VENDOR_CAPS["wd3.myworkdayjobs.com"]
    )


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


def test_longest_suffix_wins():
    assert _vendor_key("acme.wd1.myworkdayjobs.com") == "wd1.myworkdayjobs.com"
    assert _vendor_key("acme.wd103.myworkdayjobs.com") == "myworkdayjobs.com"
    assert _vendor_key("acme.xwd1.myworkdayjobs.com") == "myworkdayjobs.com"


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
    assert vendor_cap("x.oraclecloud.com") == 48
    assert vendor_cap("tenant.wd5.myworkdayjobs.com") == 24
    assert vendor_cap("tenant.wd3.myworkdayjobs.com") == 16
    # An unmeasured data center falls back to the shared suffix.
    assert vendor_cap("tenant.wd12.myworkdayjobs.com") == _VENDOR_CAPS["myworkdayjobs.com"]
    assert vendor_cap("notbamboohr.com") is None       # dot boundary, not substring
    assert vendor_cap("api.ashbyhq.com") is None       # single-host vendor: uncapped here


def test_retry_after_zero_is_no_hint():
    """Workable's 429 says `Retry-After: 0`; taken literally it retried at once
    and burned every retry in a burst. Zero or junk falls back to backoff."""
    from xjobscore.net import _retry_after_seconds

    assert _retry_after_seconds("0") is None
    assert _retry_after_seconds("not a date") is None
    assert _retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") is None  # already past
    assert _retry_after_seconds("2.5") == 2.5


# --------------------------------------------- vendor slots are wire-time only


class HostScriptedClient:
    """Answers per host from a script of (status, headers), then 200s."""

    def __init__(self, scripts: dict[str, list[tuple[int, dict]]]) -> None:
        self.scripts = {host: list(script) for host, script in scripts.items()}

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        script = self.scripts.get(urlsplit(url).netloc) or []
        status, headers = script.pop(0) if script else (200, {})
        return httpx.Response(
            status, headers=headers, content=b"{}", request=httpx.Request(method, url)
        )


async def _elapsed(coro) -> float:
    started = time.monotonic()
    await coro
    return time.monotonic() - started


async def test_a_tenant_in_429_cooldown_does_not_hold_its_vendors_slots(monkeypatch):
    """The 2026-10-05 stall: the vendor slot was taken before the host's
    cooldown was waited out, so a tenant sitting in a 429 cooldown parked the
    vendor's slots and every other tenant queued behind it (a 2-request
    Workday board waited 267s behind Target). With a one-slot vendor, the
    second tenant must go straight through while the first one cools."""
    monkeypatch.setitem(net_module._VENDOR_CAPS, "bamboohr.com", 1)
    net = Net(user_agent="test", vendor_rates={})
    net._client = HostScriptedClient({"slow.bamboohr.com": [(429, {"retry-after": "0.4"})]})

    cooling = asyncio.create_task(
        net._request("GET", "https://slow.bamboohr.com/v1/jobs", concurrency=4)
    )
    await asyncio.sleep(0.05)  # the 429 has landed; `slow` is in its cooldown
    other = await _elapsed(
        net._request("GET", "https://fast.bamboohr.com/v1/jobs", concurrency=4)
    )
    response = await cooling

    assert other < 0.2
    assert response.status == 200  # and the cooled tenant still finishes


async def test_retry_backoff_does_not_hold_the_vendor_slot(monkeypatch):
    """Same failure through the 5xx path: backoff sleeps after the wire call,
    so it must happen with the vendor slot already released."""
    monkeypatch.setitem(net_module._VENDOR_CAPS, "bamboohr.com", 1)

    async def short_backoff(self, attempt, retry_after):
        await asyncio.sleep(0.4)

    monkeypatch.setattr(Net, "_sleep_before_retry", short_backoff)
    net = Net(user_agent="test", vendor_rates={})
    net._client = HostScriptedClient({"flaky.bamboohr.com": [(503, {})]})

    backing_off = asyncio.create_task(
        net._request("GET", "https://flaky.bamboohr.com/v1/jobs", concurrency=4)
    )
    await asyncio.sleep(0.05)
    other = await _elapsed(
        net._request("GET", "https://fast.bamboohr.com/v1/jobs", concurrency=4)
    )
    response = await backing_off

    assert other < 0.2
    assert response.status == 200


# ------------------------------------------------------------- rate budgets


async def test_rate_budget_bounds_starts_across_every_tenant_host():
    """A vendor budget binds across hostnames, like the vendor cap: 8 starts
    against a 20 req/s budget with a burst of 2 need at least 6/20 = 0.3s, no
    matter that each went to a different tenant with its own semaphore."""
    net = Net(user_agent="test", vendor_rates={"myworkdayjobs.com": (20.0, 2)})
    net._client = ScriptedClient()
    hosts = [f"tenant{i}.wd1.myworkdayjobs.com" for i in range(8)]

    elapsed = await _elapsed(
        asyncio.gather(
            *(net._request("POST", f"https://{host}/wday/cxs/t/s/jobs", concurrency=4) for host in hosts)
        )
    )

    assert elapsed >= 0.28


async def test_rate_budget_only_applies_to_its_vendor():
    net = Net(user_agent="test", vendor_rates={"myworkdayjobs.com": (1.0, 1)})
    net._client = ScriptedClient()

    elapsed = await _elapsed(
        asyncio.gather(
            *(net._request("GET", f"https://job{i}.greenhouse.io/x", concurrency=4) for i in range(10))
        )
    )

    assert elapsed < 0.2


async def test_rate_budget_serves_waiters_in_arrival_order(monkeypatch):
    clock = {"t": 100.0}
    slept: list[float] = []
    monkeypatch.setattr(net_module.time, "monotonic", lambda: clock["t"])

    async def fake_sleep(seconds):
        slept.append(round(seconds, 6))

    monkeypatch.setattr(net_module.asyncio, "sleep", fake_sleep)
    budget = net_module._RateBudget(rate=2.0, burst=2)

    waits = [await budget.take() for _ in range(5)]

    # Two from the burst, then one every 1/rate: each later caller queues
    # behind the debt of the ones before it.
    assert waits == [0.0, 0.0, 0.5, 1.0, 1.5]
    assert slept == [0.5, 1.0, 1.5]


async def test_rate_budgets_of_two_data_centers_do_not_share():
    """10 starts each to wd1 and wd3 under a 20 req/s budget with a burst of
    10: each data center fits in its own burst, so nothing waits. One shared
    budget would make the second ten wait ~0.5s."""
    net = Net(
        user_agent="test",
        vendor_rates={"wd1.myworkdayjobs.com": (20.0, 10), "wd3.myworkdayjobs.com": (20.0, 10)},
        host_rates={},
    )
    net._client = ScriptedClient()
    hosts = [f"tenant{i}.{dc}.myworkdayjobs.com" for dc in ("wd1", "wd3") for i in range(10)]

    elapsed = await _elapsed(
        asyncio.gather(
            *(net._request("POST", f"https://{host}/wday/cxs/t/s/jobs", concurrency=4) for host in hosts)
        )
    )

    assert elapsed < 0.2


async def test_host_budget_binds_one_tenant_but_not_its_siblings():
    """6 starts to one tenant at 20 req/s with a burst of 2 need >= 0.2s; the
    same 6 spread over six tenants each fit their own burst."""
    net = Net(user_agent="test", vendor_rates={}, host_rates={"myworkdayjobs.com": (20.0, 2)})
    net._client = ScriptedClient()

    def burst(hosts):
        return asyncio.gather(
            *(net._request("POST", f"https://{host}/wday/cxs/t/s/jobs", concurrency=4) for host in hosts)
        )

    one = await _elapsed(burst(["acme.wd1.myworkdayjobs.com"] * 6))
    spread = await _elapsed(burst([f"t{i}.wd1.myworkdayjobs.com" for i in range(6)]))

    assert one >= 0.18
    assert spread < 0.1


async def test_rate_groups_report_starts_and_throttles():
    net = Net(
        user_agent="test",
        max_retries=0,
        vendor_rates={"wd1.myworkdayjobs.com": (8.0, 20), "myworkdayjobs.com": (3.0, 20)},
        host_rates={},
    )
    net._client = HostScriptedClient({"slow.wd1.myworkdayjobs.com": [(429, {})]})

    for host in ("a.wd1.myworkdayjobs.com", "b.wd1.myworkdayjobs.com", "c.wd12.myworkdayjobs.com"):
        await net._request("GET", f"https://{host}/x", concurrency=4)
    with pytest.raises(net_module.NetError):
        await net._request("GET", "https://slow.wd1.myworkdayjobs.com/x", concurrency=4)

    groups = net.rate_groups()
    assert groups["wd1.myworkdayjobs.com"]["budget"] == 8.0
    assert groups["wd1.myworkdayjobs.com"]["rate"] == round(3 / net_module._RATE_WINDOW_S, 2)
    assert groups["wd1.myworkdayjobs.com"]["throttled"] == 1
    assert groups["myworkdayjobs.com"]["rate"] == round(1 / net_module._RATE_WINDOW_S, 2)
    assert groups["myworkdayjobs.com"]["throttled"] == 0


def test_workday_rate_budgets_per_data_center():
    rates = net_module._VENDOR_RATES
    assert rates["wd1.myworkdayjobs.com"] == (8.0, 20)
    assert rates["wd5.myworkdayjobs.com"] == (8.0, 20)
    assert rates["wd3.myworkdayjobs.com"] == (6.0, 20)
    assert rates["myworkdayjobs.com"] == (3.0, 20)  # every unmeasured data center
    assert set(rates) <= set(_VENDOR_CAPS)  # every budget has a cap to size its pool
    assert net_module._HOST_RATES["myworkdayjobs.com"] == (2.0, 10)
