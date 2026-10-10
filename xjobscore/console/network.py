"""Console-only HTTP telemetry layered on top of the shared Net policy."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from xjobscore import workable_policy

from xjobscore.net import (
    RETRYABLE_STATUS,
    Net,
    NetError,
    Response,
    _HostPace,
    _retry_after_seconds,
)


_VALIDATOR_HEADERS = {"etag", "last-modified"}
_SAFE_HEADERS = {
    "cache-control",
    "content-encoding",
    "content-length",
    "content-type",
    "expires",
    "retry-after",
    "vary",
}


def sanitize_url(value: str, *, has_query: bool = False) -> str:
    """Keep origin/path while removing credentials, query values, and fragments."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = parsed.port
        except ValueError:
            port = None
        netloc = f"{host}:{port}" if port else host
        path = parsed.path or "/"
        query_marker = "?…" if (parsed.query or has_query) else ""
        return f"{parsed.scheme}://{netloc}{path}{query_marker}"
    except (TypeError, ValueError):
        return "<invalid URL>"


def safe_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return the small allowlist useful for debugging without secrets."""
    output: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = raw_name.lower()
        if name in _VALIDATOR_HEADERS:
            # Validator values are deliberately not persisted; presence is enough.
            output[name] = "<present>"
        elif name in _SAFE_HEADERS or name.startswith("x-ratelimit-") or name.startswith("ratelimit-"):
            output[name] = str(raw_value)
    return output


def _conditional_headers(headers: Mapping[str, str]) -> list[str]:
    names = {name.lower() for name in headers}
    found: list[str] = []
    if "if-none-match" in names:
        found.append("If-None-Match")
    if "if-modified-since" in names:
        found.append("If-Modified-Since")
    return found


def _response_event(
    *,
    request_id: int,
    method: str,
    requested_url: str,
    params: Any,
    headers: Mapping[str, str],
    response: httpx.Response,
    elapsed_ms: float,
    attempt: int,
) -> dict[str, Any]:
    history = [
        {"status": previous.status_code, "url": sanitize_url(str(previous.url))}
        for previous in response.history
    ]
    return {
        "request_id": request_id,
        "attempt": attempt,
        "method": method.upper(),
        "requested_url": sanitize_url(requested_url, has_query=bool(params)),
        "final_url": sanitize_url(str(response.url)),
        "status": response.status_code,
        "elapsed_ms": round(elapsed_ms, 1),
        "response_bytes": len(response.content),
        "redirects": history,
        "conditional_headers": _conditional_headers(headers),
        "response_headers": safe_response_headers(response.headers),
    }


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    request_ids = {event["request_id"] for event in events}
    attempts = len(events)
    redirects = sum(len(event.get("redirects", [])) for event in events)
    response_bytes = sum(int(event.get("response_bytes", 0) or 0) for event in events)
    statuses = [event.get("status") for event in events if event.get("status") is not None]
    errors = [event for event in events if event.get("error")]
    return {
        "request_count": len(request_ids),
        "attempt_count": attempts,
        "retry_count": max(0, attempts - len(request_ids)),
        "redirect_count": redirects,
        "response_bytes": response_bytes,
        "final_status": statuses[-1] if statuses else None,
        "error_count": len(errors),
        # Time actually spent waiting on HTTP. Subtracting this from the run's
        # elapsed_ms is what separates "the board was slow" from "we were slow".
        "elapsed_ms": round(sum(float(event.get("elapsed_ms", 0) or 0) for event in events), 1),
    }


def telemetry(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {"summary": summarize_events(events), "requests": events}


class StageTimer:
    """Wall-clock breakdown of one fetch, recorded in order.

    The stages partition the same window ``RunSnapshot.elapsed_ms`` measures,
    so they sum to it. Persistence is deliberately outside that window and so
    is not a stage.
    """

    def __init__(self) -> None:
        self._stages: list[dict[str, Any]] = []
        self._mark = time.monotonic()

    def record(self, name: str) -> None:
        now = time.monotonic()
        self._stages.append({"name": name, "elapsed_ms": round((now - self._mark) * 1000, 1)})
        self._mark = now

    def stages(self) -> list[dict[str, Any]]:
        return list(self._stages)


class ConsoleNet(Net):
    """The production Net policy with request observations attached."""

    def __init__(
        self,
        *,
        shared_host_semaphores: dict[str, asyncio.Semaphore] | None = None,
        shared_host_paces: dict[str, _HostPace] | None = None,
        shared_vendor_semaphores: dict[str, asyncio.Semaphore] | None = None,
        shared_vendor_budgets: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.events: list[dict[str, Any]] = []
        self._request_number = 0
        # ConsoleService creates a fresh HTTP client per feed. This registry
        # is process-scoped so concurrent manual/scheduled runs still obey a
        # vendor-wide cap rather than each receiving the full allowance.
        self._shared_host_semaphores = (
            shared_host_semaphores if shared_host_semaphores is not None else {}
        )
        # Same reasoning for pacing: a 429 cooldown learned during one run
        # should still be respected by the next run against the same host.
        if shared_host_paces is not None:
            self._host_paces = shared_host_paces
        # Same reasoning again for Net._vendor_semaphores: ConsoleService
        # builds a fresh ConsoleNet per feed, so without a shared dict here
        # a vendor cap (e.g. myworkdayjobs.com) would reset to empty on every
        # fetch and never actually bind across concurrent feeds of the same
        # vendor -- see ConsoleService.__init__.
        if shared_vendor_semaphores is not None:
            self._vendor_semaphores = shared_vendor_semaphores
        # And for Net._vendor_budgets: a per-feed budget would hand every
        # concurrent console fetch the vendor's whole rate.
        if shared_vendor_budgets is not None:
            self._vendor_budgets = shared_vendor_budgets

    async def _request(self, method: str, url: str, *, concurrency: int = 1, **kwargs: Any) -> Response:
        if self._client is None:
            raise RuntimeError("Net must be used as an async context manager")
        host = urlsplit(url).netloc
        # Host limits only -- the exact-host shared cap, then the local one.
        # The vendor-wide slot and rate budget are taken per attempt, around
        # the wire call alone (Net._admitted), same as Net._request.
        shared = self._shared_host_semaphores.get(host)
        if shared is not None:
            async with shared:
                return await self._request_with_local_limit(
                    method, url, host, concurrency=concurrency, **kwargs
                )
        return await self._request_with_local_limit(
            method, url, host, concurrency=concurrency, **kwargs
        )

    async def _request_with_local_limit(
        self, method: str, url: str, host: str, *, concurrency: int, **kwargs: Any
    ) -> Response:
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
        self._request_number += 1
        request_id = self._request_number
        params = kwargs.get("params")
        headers = kwargs.get("headers") or {}
        attempt = 0
        while True:
            # A concurrency-capped host is paced by its semaphore, held for
            # this whole call including retries -- see Net._request_one.
            if throttled:
                await self._throttle(host)
            paced_s = 0.0
            started = time.monotonic()
            try:
                async with self._admitted(host, pace, validation=workable_policy.verification(url, kwargs.get("params"))) as paced_s:
                    started = time.monotonic()
                    response = await self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                elapsed_ms = (time.monotonic() - started) * 1000
                self.events.append(
                    {
                        "request_id": request_id,
                        "attempt": attempt + 1,
                        "method": method.upper(),
                        "requested_url": sanitize_url(url, has_query=bool(params)),
                        "final_url": None,
                        "status": None,
                        "elapsed_ms": round(elapsed_ms, 1),
                        "response_bytes": 0,
                        "redirects": [],
                        "conditional_headers": _conditional_headers(headers),
                        "response_headers": {},
                        "error": {"class": type(exc).__name__},
                        **({"paced_ms": round(paced_s * 1000, 1)} if paced_s else {}),
                    }
                )
                if attempt >= self._max_retries:
                    raise NetError(type(exc).__name__, url=sanitize_url(url), status=None) from exc
                await self._sleep_before_retry(attempt, None)
                attempt += 1
                continue

            elapsed_ms = (time.monotonic() - started) * 1000
            event = _response_event(
                request_id=request_id,
                method=method,
                requested_url=url,
                params=params,
                headers=headers,
                response=response,
                elapsed_ms=elapsed_ms,
                attempt=attempt + 1,
            )
            if paced_s:
                event["paced_ms"] = round(paced_s * 1000, 1)
            self.events.append(event)

            if response.status_code == 304 or response.status_code < 400:
                pace.succeeded()
                return Response(
                    url=str(response.url),
                    status=response.status_code,
                    headers={k.lower(): v for k, v in response.headers.items()},
                    body=response.content,
                )

            if response.status_code == 429:
                if await workable_policy.note_response(response):
                    raise NetError("HTTP 429", url=sanitize_url(str(response.url)), status=429)
                # Host-wide: the next reserve() waits the cooldown out, and so
                # does every sibling slot, instead of each sleeping alone.
                pace.throttled(_retry_after_seconds(response.headers.get("retry-after")))

            if response.status_code not in RETRYABLE_STATUS or attempt >= self._max_retries:
                raise NetError(
                    f"HTTP {response.status_code}",
                    url=sanitize_url(str(response.url)),
                    status=response.status_code,
                )

            if response.status_code != 429:
                await self._sleep_before_retry(attempt, response.headers.get("retry-after"))
            attempt += 1
