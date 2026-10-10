"""The only code in the tree that touches the internet.

Conditional GET, retry and rate limit live here once, so a source that does one
GET writes one line and still gets 304s, backoff and politeness for free. A
source receives a `Net` instance and may call it; it may not import httpx, open
a socket, or sleep.

`Net` is a plain object passed into `harvest`, not a module-level singleton, so
a test injects a fake with the same three methods and no network runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import email.utils
import json as _json
import random
import time
from collections import deque
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Self
from urllib.parse import urlsplit

import httpx

from . import workable_policy

__all__ = [
    "DEFAULT_USER_AGENT", "Net", "NetError", "Response", "conditional", "etag", "rate_group",
    "vendor_cap",
]

# Declared once here rather than per entry point: the string is long enough
# that a second copy is how production and the console drift into presenting
# themselves differently, which would make any measured per-vendor ceiling
# untransferable between them.
#
# A browser string, not the `xjobscore/0.1` bot token it replaces. The public
# board endpoints this client calls are the same ones a careers page hits on
# every page load, and a self-identifying UA is a single WAF rule away from
# taking out every board under a vendor at once. Note what this does NOT fix:
# the Eightfold 405 (see json_api_configs.py) hit our httpx client while curl
# from the same host and the same UA still got 200, so that edge was matching
# on TLS/ALPN, below the HTTP layer. Changing the UA cannot reach that.
#
# Keep the Chrome version roughly current when touching this -- a long-stale
# build number is itself a signal. Chrome 154 was stable on 2026-10-05.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)

# 429 and 5xx are transient; everything else 4xx is a final answer. Timeouts
# and other transport failures are retried regardless of status (there is none).
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Vendor-wide concurrency ceilings, keyed by hostname suffix. `_host_semaphores`
# below caps one exact host, which fragments for a multi-tenant vendor that
# hands every customer its own hostname (Workday: 87 feeds, 77 distinct
# hostnames, each capped at 32 -- nothing bounds the vendor total). These
# entries are provisional ceilings pending measurement, not tuned limits; a
# vendor absent from this table is uncapped, i.e. today's behaviour.
#
# Deliberately an explicit opt-in table, not automatic eTLD+1 derivation:
# deriving the registrable domain needs the public suffix list (breaks on
# multi-label suffixes like `.co.uk`) and would silently impose an unmeasured
# cap on every vendor, not just the ones actually measured here.
_VENDOR_CAPS: dict[str, int] = {
    # Workday's limiter counts per data center (`wdN`), so each measured one
    # gets its own cap; `myworkdayjobs.com` is every other data center sharing
    # one. Each cap is the in-flight headroom its rate budget below needs at
    # p95 reply time (~1.3s), so the budget, not the cap, binds.
    "wd1.myworkdayjobs.com": 24,
    "wd5.myworkdayjobs.com": 24,
    "wd3.myworkdayjobs.com": 16,
    "myworkdayjobs.com": 24,
    "eightfold.ai": 16,
    # Measured 2026-10-05: every tenant host resolves to a few dozen Akamai
    # edge IPs (1,042 hosts -> 77 IPs), so a vendor-wide cap is the right
    # shape -- an edge limit would bind across tenants. Zero errors at 96
    # concurrent light list requests and at 72 concurrent full polls (list
    # pages + 200-id detail batches, 27 boards); 48 is two-thirds of that.
    "oraclecloud.com": 48,
    "bamboohr.com": 8,
    # Provisional, and the one entry here whose hostnames actively mislead: all
    # 288 catalogued Taleo tenants have distinct hostnames, so the per-host
    # semaphore fragments 288 ways, but DNS collapses them onto a handful of
    # shared zone load balancers -- baesystems, aa010 and who all resolve to
    # lhrc1a-lbaas-otacextwb-001 at one IP, and a 60-host sample lands on 8 LBs
    # with 16 on the busiest.  That is the Workday shape exactly.  12 mirrors
    # oraclecloud.com (same operator); sizing it from measurement needs a
    # cross-tenant sweep against boards we do not poll, which has not been run.
    "taleo.net": 12,
}


def _vendor_key(host: str) -> str | None:
    """Resolve a netloc to its `_VENDOR_CAPS` key, or None if uncapped.

    Matches on a dot boundary so `notbamboohr.com` does not match
    `bamboohr.com`. Any port or IPv6 brackets are stripped before matching.
    The longest matching suffix wins, so `wd1.myworkdayjobs.com` binds a wd1
    tenant and `myworkdayjobs.com` only the data centers without their own.
    """
    return _suffix_key(host, _VENDOR_CAPS)


def rate_group(host: str) -> str | None:
    """The `_VENDOR_RATES` key whose budget `host` spends, or None.

    Public for the same reason as `vendor_cap`: the console labels each
    scheduler lane with the budget `Net` actually charges it.
    """
    return _suffix_key(host, _VENDOR_RATES)


def _suffix_key(host: str, table: Mapping[str, Any]) -> str | None:
    """The longest `table` key that `host` equals or ends with on a dot."""
    hostname = urlsplit(f"//{host}").hostname or host
    matches = [
        suffix
        for suffix in table
        if hostname == suffix or hostname.endswith("." + suffix)
    ]
    return max(matches, key=len) if matches else None


def vendor_cap(host: str) -> int | None:
    """The vendor-wide ceiling binding `host`, or None if it is uncapped.

    Public because the scheduler sizes a vendor's worker pool against the same
    table `_request` enforces (`sources.width`). Reading it rather than
    restating it is the point: a pool wider than the cap is workers holding
    feed leases while queued on `_vendor_semaphores`.
    """
    key = _vendor_key(host)
    return None if key is None else _VENDOR_CAPS[key]


# Vendor-wide request-RATE budgets, keyed like `_VENDOR_CAPS`: (sustained
# requests per second, burst). A concurrency cap bounds nothing about rate
# when replies are fast -- 24 slots at Workday's ~0.6s replies is ~40 req/s --
# and `_HostPace` only slows down after a 429 has already happened. A budget
# spends below the limiter instead, so the steady state is no 429 at all.
#
# Workday's CXS endpoints sit behind Cloudflare with a rate-limiting rule. It
# counts per client IP per data center (`wdN`), shared by every tenant there:
# measured 2026-10-05 with load spread at <= 0.2 req/s per tenant, wd1 ran
# clean at 10 req/s for 6 min and tripped at 20; wd3 clean at 8, tripped at
# ~11; wd5 clean at 10; wd1 + wd5 at 10 each through the same LAX edge were
# clean, so the edge is not the counter, and 60 req/s across data centers
# tripped only wd3's share, so neither is the IP alone. A trip is a managed
# challenge (HTML 429, `cf-mitigated: challenge`, no Retry-After) that clears
# within ~2 min. Budgets sit at ~80% of the clean rate; data centers not
# measured share the old 3 req/s. See docs/ats-poll-rate.md.
#
# One budget per `Net` instance, like `_vendor_semaphores`: the scheduler's
# single Net carries all production polling, and the console shares one
# dict across its per-feed instances (`ConsoleNet`).
_VENDOR_RATES: dict[str, tuple[float, int]] = {
    "wd1.myworkdayjobs.com": (8.0, 20),
    "wd5.myworkdayjobs.com": (8.0, 20),
    "wd3.myworkdayjobs.com": (6.0, 20),
    "myworkdayjobs.com": (3.0, 20),
}

# Per-host budgets, keyed by the same suffixes but spent per exact hostname.
# Single-tenant Workday sweeps drew 429s from ~3.5-5 req/s after a ~400-request
# burst, a limit the per-data-center budgets above would let one large tenant
# reach on its own.
_HOST_RATES: dict[str, tuple[float, int]] = {
    "myworkdayjobs.com": (2.0, 10),
}

# How far back `Net.rate_groups` looks for the throttles it reports.
_THROTTLE_WINDOW_S = 3600.0
# How far back it averages starts into an achieved rate.
_RATE_WINDOW_S = 60.0


class _RateBudget:
    """A token bucket that queues callers instead of refusing them.

    Each `take` claims the next token in arrival order, letting the balance go
    negative, and sleeps until that token has accrued. Callers are therefore
    served first-come first-served with no lock (every state change happens
    between awaits), and the starts in any window of T seconds never exceed
    `burst + rate * T`. A caller cancelled mid-wait forfeits its token, which
    only ever errs slower.
    """

    __slots__ = ("rate", "burst", "tokens", "stamp")

    def __init__(self, rate: float, burst: int) -> None:
        self.rate = rate
        self.burst = float(burst)
        self.tokens = float(burst)
        self.stamp = time.monotonic()

    async def take(self) -> float:
        """Wait for one token; returns seconds waited."""
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now
        self.tokens -= 1.0
        if self.tokens >= 0:
            return 0.0
        wait = -self.tokens / self.rate
        await asyncio.sleep(wait)
        return wait


# Adaptive pacing bounds. A host is unpaced until it answers 429; spacing then
# starts at the floor, doubles on each fresh 429 window up to the ceiling, and
# decays back toward unpaced, so the client settles just under the host's
# sustained rate without hardcoding what that rate is.
_PACE_FLOOR_S = 0.125
_PACE_CEIL_S = 2.0
_PACE_COOLDOWN_CAP_S = 30.0
# Recovery halves `interval` per this much elapsed time, NOT per success.
# Decaying per success made recovery proportional to throughput, which is
# itself inversely proportional to `interval`: at 0.125s spacing (8 req/s) a
# 2% per-success decay halves in ~9s, but at the 2.0s ceiling (0.5 req/s) the
# same rule needs ~138s. The pacer therefore crawled back slowest exactly when
# it was throttled hardest -- positive feedback that left a large Workday run
# oscillating around ~3.5 req/s for 80 minutes. Elapsed time is the honest
# clock: a limiter's bucket refills whether or not we are asking.
_PACE_HALFLIFE_S = 30.0
# Pace state older than this describes a limiter whose burst allowance has
# long since refilled; start the host fresh instead of dribbling.
_PACE_RESET_S = 600.0


class _HostPace:
    """Adaptive politeness for one host, shared by every request to it.

    A 429 opens a host-wide cooldown: every in-flight slot waits for the same
    window instead of independently sleeping and retrying into the same wall,
    which is what turned 34-41% of a large Workday run's attempts into waste.
    Only the first 429 of a window extends it -- the rest were already in
    flight when the wall appeared and teach us nothing new.

    All state transitions are synchronous between awaits, so no lock is
    needed; ``reserve`` re-checks the cooldown after sleeping because another
    slot's 429 may have extended it meanwhile.
    """

    __slots__ = (
        "next_start", "interval", "cooldown_until", "strikes", "last_used", "last_decay",
    )

    def __init__(self) -> None:
        self.next_start = 0.0
        self.interval = 0.0
        self.cooldown_until = 0.0
        self.strikes = 0
        self.last_used = 0.0
        # When recovery was last credited; see `succeeded`.
        self.last_decay = 0.0

    async def reserve(self) -> float:
        """Wait for this host's next allowed start; returns seconds waited."""
        now = time.monotonic()
        if self.last_used and now - self.last_used > _PACE_RESET_S:
            self.next_start = 0.0
            self.interval = 0.0
            self.cooldown_until = 0.0
            self.strikes = 0
            self.last_decay = 0.0
        self.last_used = now
        start = max(now, self.next_start, self.cooldown_until)
        self.next_start = start + self.interval
        waited = 0.0
        while True:
            now = time.monotonic()
            target = max(start, self.cooldown_until)
            if target <= now:
                return waited
            waited += target - now
            await asyncio.sleep(target - now)

    def throttled(self, retry_after: float | None) -> None:
        """Record a 429: begin (or keep) a host-wide cooldown, slow spacing.

        Growth happens once per window, not once per 429: sixteen in-flight
        slots hitting the same wall is one signal, not sixteen.
        """
        now = time.monotonic()
        if now < self.cooldown_until:
            return
        self.strikes += 1
        self.interval = min(max(self.interval * 2, _PACE_FLOOR_S), _PACE_CEIL_S)
        delay = (
            retry_after
            if retry_after is not None
            else 2.0 ** (self.strikes - 1) + random.uniform(0, 0.5)
        )
        self.cooldown_until = now + min(delay, _PACE_COOLDOWN_CAP_S)
        # Recovery is measured from the most recent 429, not from the last
        # success, so a burst of successes cannot buy back time we never spent.
        self.last_decay = now

    def cooling(self) -> bool:
        """True while a 429 cooldown is open for this host."""
        return time.monotonic() < self.cooldown_until

    def succeeded(self) -> None:
        """Credit elapsed time against `interval`, halving per `_PACE_HALFLIFE_S`.

        Deliberately not per-call: the exponent is elapsed wall time, so the
        decay a caller sees is identical whether it is managing 8 requests a
        second or one every two seconds. Successive calls telescope --
        0.5**(a/H) * 0.5**(b/H) == 0.5**((a+b)/H) -- so frequency cannot
        accelerate recovery.
        """
        self.strikes = 0
        if not self.interval:
            return
        now = time.monotonic()
        if not self.last_decay:
            self.last_decay = now
            return
        elapsed = now - self.last_decay
        if elapsed <= 0:
            return
        self.last_decay = now
        interval = self.interval * (0.5 ** (elapsed / _PACE_HALFLIFE_S))
        self.interval = interval if interval > 0.02 else 0.0


class NetError(Exception):
    """A request that failed and will not be retried further.

    Covers a non-2xx, non-304 response and a transport failure that survived
    `max_retries`. 304 is never an error: an unchanged board is the single most
    common successful outcome of a poll.
    """

    def __init__(
        self, message: str, *, url: str, status: int | None = None,
        response: Response | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status = status
        # The final non-2xx response, when there was one: some refusals are
        # answers that only the body can tell apart (Workday's disabled-site
        # 403 versus a WAF's).
        self.response = response


@dataclass(frozen=True, slots=True)
class Response:
    """One HTTP response, detached from the client that produced it.

    Frozen and body-in-memory on purpose: a source may read it repeatedly, and
    the bronze layer needs the same bytes the parser saw.
    """

    url: str  # after redirects -- redirect convergence is a dedup signal
    status: int  # 304 is normal and expected
    headers: dict[str, str]  # lowercased keys
    body: bytes

    def json(self) -> Any:
        """Parse the body as JSON. Raises NetError if it is not JSON."""
        try:
            return _json.loads(self.body)
        except _json.JSONDecodeError as exc:
            raise NetError(f"response body is not JSON: {exc}", url=self.url, status=self.status) from exc

    @property
    def text(self) -> str:
        """Body decoded per the response charset, falling back to utf-8."""
        charset = "utf-8"
        content_type = self.headers.get("content-type", "")
        for part in content_type.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                charset = value.strip('"')
        try:
            return self.body.decode(charset)
        except (LookupError, UnicodeDecodeError):
            return self.body.decode("utf-8", errors="replace")

    @property
    def not_modified(self) -> bool:
        """True on 304: the board is unchanged and `body` is empty."""
        return self.status == 304


def _to_response(r: httpx.Response) -> Response:
    return Response(
        url=str(r.url),
        status=r.status_code,
        headers={k.lower(): v for k, v in r.headers.items()},
        body=r.content,
    )


class Net:
    """An HTTP client with the engine's policy already applied.

    Policy the bodies must honor:

    - one connection pool for the process, one token bucket *per host*, so a
      hundred Greenhouse feeds cannot become a hundred concurrent requests to
      Greenhouse
    - retry only on 429, 5xx and timeout, with jittered exponential backoff;
      `Retry-After` wins over the computed delay when present
    - 4xx other than 429 is final -- a 404 board is an answer, not a hiccup
    - never follow a redirect chain silently past `max_redirects`
    """

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_s: float = 30.0,
        min_interval_s: float = 1.0,
        max_retries: int = 3,
        max_connections: int = 256,
        max_keepalive_connections: int = 64,
        vendor_rates: Mapping[str, tuple[float, int]] | None = None,
        host_rates: Mapping[str, tuple[float, int]] | None = None,
        connect_timeout_s: float | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._timeout_s = timeout_s
        # A tighter bound on establishing a connection alone (DNS, TCP, TLS);
        # None keeps `timeout_s` for every phase. A host that never accepts
        # otherwise costs the full timeout on every retry.
        self._connect_timeout_s = connect_timeout_s
        self._min_interval_s = min_interval_s
        self._max_retries = max_retries
        self._max_connections = max_connections
        self._max_keepalive_connections = max_keepalive_connections
        self._max_redirects = 10
        self._client: httpx.AsyncClient | None = None
        # one lock + last-request timestamp per host: the token bucket
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        # Opt-in per-host concurrency caps for sources that pay one request per
        # item (docxtract's html_links boards). Semaphores are keyed by both
        # host and width: paginated+detail sources may deliberately use
        # different limits for their sequential phases (Workday: 32 then 16).
        self._host_semaphores: dict[tuple[str, int], asyncio.Semaphore] = {}
        # Adaptive rate-limit response, keyed by host alone: a 429 means the
        # *host* is throttling, so page and detail legs must share the answer.
        self._host_paces: dict[str, _HostPace] = {}
        # Vendor-wide cap, keyed by `_VENDOR_CAPS` suffix rather than exact
        # host: unlike `_host_semaphores`, one entry here is shared by every
        # tenant hostname under that suffix. Held per attempt, around the wire
        # call alone -- see `_admitted`.
        self._vendor_semaphores: dict[str, asyncio.Semaphore] = {}
        # Vendor-wide rate budgets, keyed the same way (`_VENDOR_RATES`).
        self._vendor_rates = _VENDOR_RATES if vendor_rates is None else dict(vendor_rates)
        self._vendor_budgets: dict[str, _RateBudget] = {}
        # Per-hostname budgets (`_HOST_RATES`), keyed by exact host.
        self._host_rates = _HOST_RATES if host_rates is None else dict(host_rates)
        self._host_budgets: dict[str, _RateBudget] = {}
        # Monotonic times of wire starts and 429s per rate-budget key, for
        # `rate_groups`. Starts are trimmed to `_RATE_WINDOW_S`, 429s to
        # `_THROTTLE_WINDOW_S`, as they are recorded.
        self._group_starts: dict[str, deque[float]] = {}
        self._group_throttles: dict[str, deque[float]] = {}

    async def __aenter__(self) -> Self:
        """Open the underlying client. Use `async with Net(...) as net`."""
        # Stated, not inherited: httpx defaults to 100 connections, and the
        # scheduler's per-vendor pools plus each poll's own fan-out exceed that
        # (ashby 44 + greenhouse 30 + workday 2x10 + eightfold 2x8 + ...). Over
        # the default the surplus queues inside httpx with no error and no 429,
        # which reads as the origin being slow. Headroom, not a measured limit:
        # the real ceilings are `_VENDOR_CAPS` and the per-host semaphores.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_s, connect=self._connect_timeout_s)
            if self._connect_timeout_s is not None else self._timeout_s,
            headers={"User-Agent": self._user_agent},
            follow_redirects=True,
            max_redirects=self._max_redirects,
            limits=httpx.Limits(
                max_connections=self._max_connections,
                max_keepalive_connections=self._max_keepalive_connections,
            ),
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _throttle(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._host_last.get(host)
            if last is not None:
                wait = self._min_interval_s - (time.monotonic() - last)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()

    async def _sleep_before_retry(self, attempt: int, retry_after: str | None) -> None:
        delay = _retry_after_seconds(retry_after)
        if delay is None:
            delay = (2**attempt) + random.uniform(0, 1)
        if delay:
            await asyncio.sleep(delay)

    def _vendor_semaphore(self, host: str) -> asyncio.Semaphore | None:
        """The vendor-wide semaphore for `host`'s `_VENDOR_CAPS` suffix, if any."""
        key = _vendor_key(host)
        if key is None:
            return None
        return self._vendor_semaphores.setdefault(key, asyncio.Semaphore(_VENDOR_CAPS[key]))

    def _rate_group(self, host: str) -> str | None:
        """The `_VENDOR_RATES` key whose budget `host` spends, if any."""
        return _suffix_key(host, self._vendor_rates)

    def _vendor_budget(self, host: str) -> _RateBudget | None:
        """The vendor-wide rate budget for `host`'s suffix, if it has one."""
        key = self._rate_group(host)
        if key is None:
            return None
        budget = self._vendor_budgets.get(key)
        if budget is None:
            budget = self._vendor_budgets[key] = _RateBudget(*self._vendor_rates[key])
        return budget

    def _host_budget(self, host: str) -> _RateBudget | None:
        """This exact host's own rate budget, if its suffix has one."""
        key = _suffix_key(host, self._host_rates)
        if key is None:
            return None
        budget = self._host_budgets.get(host)
        if budget is None:
            budget = self._host_budgets[host] = _RateBudget(*self._host_rates[key])
        return budget

    def _record(self, events: dict[str, deque[float]], host: str, window_s: float) -> None:
        key = self._rate_group(host)
        if key is None:
            return
        now = time.monotonic()
        times = events.setdefault(key, deque())
        times.append(now)
        while times and times[0] < now - window_s:
            times.popleft()

    def rate_groups(self) -> dict[str, dict[str, Any]]:
        """Each rate budget's configured and achieved rate, and recent 429s.

        Achieved rate averages wire starts over `_RATE_WINDOW_S`, so it reads
        low for that long after a start; 429s count over `_THROTTLE_WINDOW_S`.
        """
        now = time.monotonic()
        groups = {}
        for key, (rate, burst) in self._vendor_rates.items():
            starts = self._group_starts.get(key, ())
            throttles = self._group_throttles.get(key, ())
            groups[key] = {
                "budget": rate,
                "burst": burst,
                "rate": round(
                    sum(1 for t in starts if t >= now - _RATE_WINDOW_S) / _RATE_WINDOW_S, 2
                ),
                "throttled": sum(1 for t in throttles if t >= now - _THROTTLE_WINDOW_S),
            }
        return groups

    @contextlib.asynccontextmanager
    async def _admitted(self, host: str, pace: _HostPace, *, validation: bool = False) -> AsyncIterator[float]:
        """Hold one attempt's vendor-wide admission; yields seconds waited for it.

        Nothing here sleeps while holding a vendor slot. The host's own pacing
        and any 429 cooldown are waited out first, then the host's and the
        vendor's rate budgets, and only then is a `_VENDOR_CAPS` slot taken -- for the wire
        call alone, released before any retry backoff. `_request` used to take
        the slot first and hold it across all of that, so one large tenant in
        cooldown parked every slot of its vendor: on 2026-10-05 a 2-request
        Workday board waited 267s behind Target's partitioned poll, and
        Salesforce 125s behind RTX's. The order is still fixed -- host
        semaphore, then vendor slot -- so it cannot deadlock.
        """
        vendor = self._vendor_semaphore(host)
        host_budget = self._host_budget(host)
        budget = self._vendor_budget(host)
        waited = 0.0
        while True:
            waited += await pace.reserve()
            if host_budget is not None:
                waited += await host_budget.take()
            if budget is not None:
                waited += await budget.take()
            if vendor is None:
                break
            await vendor.acquire()
            if not pace.cooling():
                break
            # A sibling's 429 opened a cooldown while this attempt queued for
            # the slot; sending now would walk into the wall it just found.
            vendor.release()
        try:
            try:
                waited += await workable_policy.before_request(host, validation=validation)
            except workable_policy.CoolingDown as exc:
                raise NetError(str(exc), url="https://" + host, status=429) from exc
            self._record(self._group_starts, host, _RATE_WINDOW_S)
            yield waited
        finally:
            if vendor is not None:
                vendor.release()

    async def _request(self, method: str, url: str, *, concurrency: int = 1, **kwargs: Any) -> Response:
        if self._client is None:
            raise RuntimeError("Net must be used as an async context manager")
        host = urlsplit(url).netloc
        # Host limits only. The vendor-wide slot and rate budget are taken per
        # attempt, inside `_request_one` -- see `_admitted`.
        if concurrency > 1:
            semaphore = self._host_semaphores.setdefault(
                (host, concurrency), asyncio.Semaphore(concurrency)
            )
            async with semaphore:
                return await self._request_one(method, url, host, throttled=False, **kwargs)
        return await self._request_one(method, url, host, throttled=True, **kwargs)

    async def _request_one(
        self, method: str, url: str, host: str, *, throttled: bool, **kwargs: Any
    ) -> Response:
        pace = self._host_paces.setdefault(host, _HostPace())
        attempt = 0
        while True:
            # A concurrency-capped host is paced by its semaphore, held for
            # this whole call including retries -- the min-interval lock
            # would just serialize it again.
            if throttled:
                await self._throttle(host)
            try:
                async with self._admitted(host, pace, validation=workable_policy.verification(url, kwargs.get("params"))):
                    r = await self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise NetError(str(exc), url=url) from exc
                await self._sleep_before_retry(attempt, None)
                attempt += 1
                continue

            if r.status_code == 304 or r.status_code < 400:
                pace.succeeded()
                return _to_response(r)

            if r.status_code == 429:
                if await workable_policy.note_response(r):
                    # Persist the full vendor cooldown and return the refusal
                    # immediately. Other clients fail locally without wire I/O.
                    raise NetError("HTTP 429", url=str(r.url), status=429, response=_to_response(r))
                # Host-wide: the next reserve() waits the cooldown out, and so
                # does every sibling slot, instead of each sleeping alone.
                pace.throttled(_retry_after_seconds(r.headers.get("retry-after")))
                self._record(self._group_throttles, host, _THROTTLE_WINDOW_S)

            if r.status_code not in RETRYABLE_STATUS or attempt >= self._max_retries:
                raise NetError(
                    f"HTTP {r.status_code}", url=str(r.url), status=r.status_code,
                    response=_to_response(r),
                )

            if r.status_code != 429:
                await self._sleep_before_retry(attempt, r.headers.get("retry-after"))
            attempt += 1

    async def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        etag: str | None = None,
        concurrency: int = 1,
    ) -> Response:
        """GET, conditionally when schema.sql has persisted an ETag.

        Returns a Response with `not_modified` True on 304 rather than raising,
        because "nothing changed" is a successful poll.

        `concurrency` opts a host out of the default 1-request-per-second
        pacing in favor of up to that many requests to it in flight at once.
        Only worth raising for a source that pays one request per item
        (docxtract's html_links boards); every other call leaves it at 1 and
        gets today's serialized behavior unchanged.
        """
        merged = dict(headers or {})
        merged.update(conditional(etag))
        return await self._request("GET", url, params=params, headers=merged, concurrency=concurrency)

    async def head(self, url: str, *, headers: dict | None = None, concurrency: int = 1) -> Response:
        """HEAD, for a vendor that publishes a validator header but not an
        ETag (Trakstar's Last-Modified). Shares `get`'s retry/pacing policy
        via `_request`; body is always empty. Not conditional -- a source
        that wants that compares the header itself, since a server may honor
        HEAD while ignoring If-Modified-Since entirely.
        """
        return await self._request("HEAD", url, headers=headers, concurrency=concurrency)

    async def post(
        self,
        url: str,
        *,
        json: Any = None,
        data: Any = None,
        headers: dict | None = None,
        concurrency: int = 1,
    ) -> Response:
        """POST. Exists for the paged and authed boards that demand it.

        `concurrency` means the same as on `get`: >1 opts the host out of the
        1-request-per-second pacing in favor of up to that many requests in
        flight at once.
        """
        return await self._request("POST", url, json=json, data=data, headers=headers, concurrency=concurrency)

    async def json(self, url: str, **kw: Any) -> Any:
        """`get` + `.json()`, which is what most sources actually want.

        Raises NetError on 304 -- a caller that cares about conditional GET has
        to use `get` and check `not_modified` itself.
        """
        response = await self.get(url, **kw)
        if response.not_modified:
            raise NetError("304 Not Modified", url=url, status=304)
        return response.json()


def _retry_after_seconds(retry_after: str | None) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date).

    None when absent or when it asks for no wait at all: Workable's 429 carries
    `Retry-After: 0`, and honoring that literally retried at once, spent every
    retry in a burst, and handed the sweep a 429 it then cooled a minute on.
    No usable hint means the computed backoff applies instead.
    """
    if not retry_after:
        return None
    try:
        seconds = float(retry_after)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(retry_after)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = (when - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            return None
    return seconds if seconds > 0 else None


def conditional(value: str | None) -> dict[str, str]:
    """Build the conditional request headers represented by a ``feed``."""
    return {"If-None-Match": value} if value else {}


def etag(response: Response) -> str | None:
    """Extract the response ETag persisted by ``feed.etag``."""
    return response.headers.get("etag") or None


def last_modified(response: Response) -> str | None:
    """Extract the raw Last-Modified header persisted by ``feed.last_modified``.

    Kept as the literal header string, never parsed as a date: it is only
    ever compared byte-for-byte against a previous value, the same contract
    ``etag`` already has.
    """
    return response.headers.get("last-modified") or None
