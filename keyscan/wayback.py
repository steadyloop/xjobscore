"""Token discovery from the Internet Archive's Wayback Machine CDX index.

An independent archive from Common Crawl, with its own crawler and its own
gaps: it still captures hosts that refuse CCBot (jobs.lever.co has answered
CCBot with a Cloudflare 403 since late 2025, yet the archive holds 200s for it
through September 2026), and it holds pages people asked it to save, which no
crawl would ever have reached.

Same vendor rules as the crawl -- each rule's domains choose what to ask for,
its URL patterns pull the token out -- but applied in Python to the URL lists
the CDX server returns, since there is no columnar index to push SQL into.

Paced on its own client rather than `Keyscan._request`: that path has no
per-host delay, and a 429 there is read as a *vendor* refusing us, which would
stop validation for the rest of the run. The archive is not a vendor.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import re
import time
from typing import Any, Callable

import httpx

from .rules import _SCHEME_HOST, Rule, valid_token

CDX = "https://web.archive.org/cdx/search/cdx"
# The CDX server answers a burst with 429 and names 15 requests a minute as
# its ceiling (no official figure is published), so one request every four
# seconds sits exactly on it -- each request returns up to `PAGE_LIMIT` URLs,
# so this is rarely the bottleneck.
GAP_SECONDS = 4.0
# 20,000 lever.co URLs came back in 13s (2.2MB) on 2026-10-05; a larger page
# risks the server's own timeout on the busiest domains.
PAGE_LIMIT = 20_000
# Generous: a cold domain query sat past 60s in testing before answering.
TIMEOUT_SECONDS = 180.0
# Waits after a refusal (429/503) or a timeout, used in order. Retry-After,
# when sent, wins. Running off the end gives up on the (vendor, year) pair --
# never on the run, which records the failure and moves on.
BACKOFF_SECONDS = (60.0, 120.0, 300.0, 600.0)
# Example URLs kept per token, matching the crawl.
URLS_PER_TOKEN = 5


async def pause(seconds: float) -> None:
    """Every wait goes through here -- pacing and backoff alike -- so a test
    replaces one function instead of the event loop's `asyncio.sleep`."""
    await asyncio.sleep(seconds)


class WaybackRefused(Exception):
    """The archive kept refusing (or timing out) past every backoff."""


def rule_token(rule: Rule, url: str) -> str | None:
    """`rule`'s token in `url`, or None.

    `rules.extract` stops at the first rule whose pattern matches *any* URL;
    here the rule is already known, so only its own patterns are tried -- in
    order, first match wins, exactly as `token_sql` orders them for DuckDB.
    """
    url = _SCHEME_HOST.sub(lambda match: match.group(1).lower(), url, count=1)
    for pattern in rule.patterns:
        match = re.match(pattern.rx, url)
        if match:
            token = "/".join(match.group(group) for group in pattern.groups)
            return token if valid_token(token, rule) else None
    return None


@dataclass
class Found:
    """One token's sightings within one (vendor, year)."""

    count: int = 0
    urls: list[str] = field(default_factory=list)


class WaybackClient:
    """CDX queries, one at a time, never closer than `gap` seconds apart."""

    def __init__(
        self, *, user_agent: str, transport: httpx.AsyncBaseTransport | None = None,
        gap: float = GAP_SECONDS, backoff: tuple[float, ...] = BACKOFF_SECONDS,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS, follow_redirects=True,
            headers={"User-Agent": user_agent}, transport=transport,
        )
        self._gap = gap
        self._backoff = backoff
        self._on_event = on_event or (lambda message, detail: None)
        self._last = float("-inf")
        self.requests = 0

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, params: dict[str, Any]) -> str:
        attempt = 0
        while True:
            wait = self._gap - (time.monotonic() - self._last)
            if wait > 0:
                await pause(wait)
            self._last = time.monotonic()
            self.requests += 1
            try:
                response = await self._client.get(CDX, params=params)
            except httpx.TimeoutException as exc:
                cause, retry_after = f"timeout: {type(exc).__name__}", None
            except httpx.TransportError as exc:
                cause, retry_after = f"transport: {type(exc).__name__}", None
            else:
                if response.status_code == 200:
                    return response.text
                if response.status_code not in (429, 500, 502, 503, 504):
                    raise WaybackRefused(f"HTTP {response.status_code}")
                cause = f"HTTP {response.status_code}"
                retry_after = _retry_after(response)
            if attempt >= len(self._backoff):
                raise WaybackRefused(f"{cause} after {attempt} backoffs")
            delay = retry_after if retry_after is not None else self._backoff[attempt]
            attempt += 1
            self._on_event("wayback backoff", {"cause": cause, "attempt": attempt, "pause_s": delay})
            await pause(delay)

    async def scan(self, rule: Rule, domain: str, year: int) -> dict[str, Found]:
        """Every token `rule` extracts from `domain`'s captures in `year`.

        Successful and redirect captures only (2xx/3xx): a board that once
        existed is worth probing even if the capture was a redirect, while
        4xx/5xx captures are the archive recording a miss. `collapse=urlkey`
        drops repeat captures of one URL server-side, which is most of the
        volume for a busy board.
        """
        found: dict[str, Found] = {}
        resume: str | None = None
        while True:
            params: dict[str, Any] = {
                "url": domain, "matchType": "domain",
                "from": str(year), "to": str(year),
                "fl": "original", "filter": "statuscode:[23]..",
                "collapse": "urlkey", "limit": str(PAGE_LIMIT), "showResumeKey": "true",
            }
            if resume:
                params["resumeKey"] = resume
            lines = (await self._get(params)).splitlines()
            # A resume key, when there is one, follows a blank line at the end.
            resume = None
            if len(lines) >= 2 and lines[-2] == "" and lines[-1]:
                resume = lines[-1]
                lines = lines[:-2]
            for url in lines:
                url = url.strip()
                if not url:
                    continue
                token = rule_token(rule, url)
                if token is None:
                    continue
                entry = found.setdefault(token, Found())
                entry.count += 1
                if len(entry.urls) < URLS_PER_TOKEN and url not in entry.urls:
                    entry.urls.append(url)
            if not resume:
                return found


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "").strip()
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:
        return None
