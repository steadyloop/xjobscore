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

__all__ = ["Net", "NetError", "Response", "conditional", "validators"]

# 429 and 5xx are transient; everything else 4xx is a final answer. Timeouts
# and other transport failures are retried regardless of status (there is none).
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


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
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    when = email.utils.parsedate_to_datetime(retry_after)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    delay = max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError):
                    delay = 0.0
        else:
            delay = (2**attempt) + random.uniform(0, 1)
        if delay:
            await asyncio.sleep(delay)

    async def _request(self, method: str, url: str, **kwargs: Any) -> Response:
        if self._client is None:
            raise RuntimeError("Net must be used as an async context manager")
        host = urlsplit(url).netloc
        attempt = 0
        while True:
            await self._throttle(host)
            try:
                r = await self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise NetError(str(exc), url=url) from exc
                await self._sleep_before_retry(attempt, None)
                attempt += 1
                continue

            if r.status_code == 304 or r.status_code < 400:
                return _to_response(r)

            if r.status_code not in RETRYABLE_STATUS or attempt >= self._max_retries:
                raise NetError(f"HTTP {r.status_code}", url=str(r.url), status=r.status_code)

            await self._sleep_before_retry(attempt, r.headers.get("retry-after"))
            attempt += 1

    async def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        state: dict | None = None,
    ) -> Response:
        """GET, conditionally if `state` carries validators.

        `state` is `feed.state` and is READ, never mutated: this method has no
        business deciding what the feed's next state is. Pass the returned
        Response to `validators()` and let `work.next_state` fold it in.

        Returns a Response with `not_modified` True on 304 rather than raising,
        because "nothing changed" is a successful poll.
        """
        merged = dict(headers or {})
        merged.update(conditional(state))
        return await self._request("GET", url, params=params, headers=merged)

    async def post(
        self,
        url: str,
        *,
        json: Any = None,
        data: Any = None,
        headers: dict | None = None,
    ) -> Response:
        """POST. Exists for the paged and authed boards that demand it."""
        return await self._request("POST", url, json=json, data=data, headers=headers)

    async def json(self, url: str, **kw: Any) -> Any:
        """`get` + `.json()`, which is what most sources actually want.

        Raises NetError on 304 -- a caller that cares about conditional GET has
        to use `get` and check `not_modified` itself.
        """
        response = await self.get(url, **kw)
        if response.not_modified:
            raise NetError("304 Not Modified", url=url, status=304)
        return response.json()


def conditional(state: dict | None) -> dict[str, str]:
    """Build If-None-Match / If-Modified-Since headers from `feed.state`.

    Empty dict when the feed has no validators yet, so the caller never needs
    a branch.
    """
    if not state:
        return {}
    headers: dict[str, str] = {}
    etag = state.get("etag")
    if etag:
        headers["If-None-Match"] = etag
    last_modified = state.get("last_modified")
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return headers


def validators(response: Response) -> dict:
    """Extract `{etag, last_modified}` from a response, for `feed.state`.

    Keys are omitted rather than set to None when the server sent neither, so
    merging this into an existing state cannot erase validators that a 304
    response simply did not repeat.
    """
    out: dict[str, str] = {}
    etag = response.headers.get("etag")
    if etag:
        out["etag"] = etag
    last_modified = response.headers.get("last-modified")
    if last_modified:
        out["last_modified"] = last_modified
    return out
