"""Inspector-only HTTP telemetry layered on top of the shared Net policy."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from xjobscore.net import RETRYABLE_STATUS, Net, NetError, Response


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
    }


def telemetry(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {"summary": summarize_events(events), "requests": events}


class InspectorNet(Net):
    """The production Net policy with request observations attached."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.events: list[dict[str, Any]] = []
        self._request_number = 0

    async def _request(self, method: str, url: str, **kwargs: Any) -> Response:
        if self._client is None:
            raise RuntimeError("Net must be used as an async context manager")

        self._request_number += 1
        request_id = self._request_number
        params = kwargs.get("params")
        headers = kwargs.get("headers") or {}
        host = urlsplit(url).netloc
        attempt = 0
        while True:
            await self._throttle(host)
            started = time.monotonic()
            try:
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
            self.events.append(event)

            if response.status_code == 304 or response.status_code < 400:
                return Response(
                    url=str(response.url),
                    status=response.status_code,
                    headers={k.lower(): v for k, v in response.headers.items()},
                    body=response.content,
                )

            if response.status_code not in RETRYABLE_STATUS or attempt >= self._max_retries:
                raise NetError(
                    f"HTTP {response.status_code}",
                    url=sanitize_url(str(response.url)),
                    status=response.status_code,
                )

            await self._sleep_before_retry(attempt, response.headers.get("retry-after"))
            attempt += 1
