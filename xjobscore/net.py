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
import email.utils
import json as _json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Self
from urllib.parse import urlsplit

import httpx

__all__ = ["DEFAULT_USER_AGENT", "Net", "NetError", "Response", "conditional", "etag"]

# Declared once here rather than per entry point: the string is long enough
# that a second copy is how production and the inspector drift into presenting
# themselves differently, which would make any measured per-vendor ceiling
# untransferable between them.
#
# A browser string, not the `xjobscore/0.1` bot token it replaces. The public
# board endpoints this client calls are the same ones a careers page hits on
# every page load, and a self-identifying UA is a single WAF rule away from
# taking out every board under a vendor at once. Note what this does NOT fix:
# the Eightfold 405 (see json-api-configs.py) hit our httpx client while curl
# from the same host and the same UA still got 200, so that edge was matching
# on TLS/ALPN, below the HTTP layer. Changing the UA cannot reach that.
#
# Keep the Chrome version roughly current when touching this -- a long-stale
# build number is itself a signal.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
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
    "myworkdayjobs.com": 24,
    "eightfold.ai": 16,
    "oraclecloud.com": 12,
    "bamboohr.com": 8,
}


def _vendor_key(host: str) -> str | None:
    """Resolve a netloc to its `_VENDOR_CAPS` key, or None if uncapped.

    Matches on a dot boundary so `notbamboohr.com` does not match
    `bamboohr.com`. Any port or IPv6 brackets are stripped before matching.
    """
    hostname = urlsplit(f"//{host}").hostname or host
    for suffix in _VENDOR_CAPS:
        if hostname == suffix or hostname.endswith("." + suffix):
            return suffix
    return None


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

    def __init__(self, message: str, *, url: str, status: int | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.status = status


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
    ) -> None:
        self._user_agent = user_agent
        self._timeout_s = timeout_s
        self._min_interval_s = min_interval_s
        self._max_retries = max_retries
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
        # tenant hostname under that suffix. Acquired outside the host
        # semaphore/throttle -- see `_request`.
        self._vendor_semaphores: dict[str, asyncio.Semaphore] = {}

    async def __aenter__(self) -> Self:
        """Open the underlying client. Use `async with Net(...) as net`."""
        self._client = httpx.AsyncClient(
            timeout=self._timeout_s,
            headers={"User-Agent": self._user_agent},
            follow_redirects=True,
            max_redirects=self._max_redirects,
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

    async def _request(self, method: str, url: str, *, concurrency: int = 1, **kwargs: Any) -> Response:
        if self._client is None:
            raise RuntimeError("Net must be used as an async context manager")
        host = urlsplit(url).netloc
        vendor = self._vendor_semaphore(host)
        # Acquisition order is always vendor -> host, never the reverse, so
        # nesting can never deadlock. The vendor semaphore is held for the
        # whole request including retries, exactly like the host semaphore
        # and throttle below -- see `_request_one`.
        if concurrency > 1:
            semaphore = self._host_semaphores.setdefault(
                (host, concurrency), asyncio.Semaphore(concurrency)
            )
            if vendor is None:
                async with semaphore:
                    return await self._request_one(method, url, host, throttled=False, **kwargs)
            async with vendor, semaphore:
                return await self._request_one(method, url, host, throttled=False, **kwargs)
        if vendor is None:
            return await self._request_one(method, url, host, throttled=True, **kwargs)
        async with vendor:
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
            await pace.reserve()
            try:
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
                # Host-wide: the next reserve() waits the cooldown out, and so
                # does every sibling slot, instead of each sleeping alone.
                pace.throttled(_retry_after_seconds(r.headers.get("retry-after")))

            if r.status_code not in RETRYABLE_STATUS or attempt >= self._max_retries:
                raise NetError(f"HTTP {r.status_code}", url=str(r.url), status=r.status_code)

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
    """Parse a Retry-After header (delta-seconds or HTTP-date); None if absent."""
    if not retry_after:
        return None
    try:
        return float(retry_after)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(retry_after)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return 0.0


def conditional(value: str | None) -> dict[str, str]:
    """Build the conditional request headers represented by a ``feed``."""
    return {"If-None-Match": value} if value else {}


def etag(response: Response) -> str | None:
    """Extract the response ETag persisted by ``feed.etag``."""
    return response.headers.get("etag") or None
