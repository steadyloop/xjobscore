"""Per-vendor end-to-end verification of a keyscan ``Rule``.

Where the crawl exhaustively harvests every token it can find, ``verify``
proves the *rule itself* is trustworthy: that its Common Crawl pattern still
finds tokens, that its probe endpoint still resolves, and that its liveness
classifier can actually tell a real board apart from a dead one. It samples
just enough real tokens to see one ``live`` and one ``absent`` result, adds a
synthetic known-bad control, and stops -- a handful of probes per vendor
instead of a full crawl.

It writes nothing to the token database: this is a diagnostic, safe to
re-run at any time, not a discovery method.
"""

from __future__ import annotations

import asyncio
import gzip
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .core import CC_CDN, CC_PATHS, COLLINFO, USER_AGENT, KeyscanError, cc_scan
from .probe import default_request, probe_rule
from .rules import BY_SOURCE, Rule, expand_sources

_SENTINEL = "keyscan-verify-does-not-exist-9f4d2a"
_WAF_MARKERS = ("cloudflare", "cf-ray", "access denied", "attention required", "request blocked", "akamai")
_RETRYABLE_5XX = frozenset({500, 502, 503, 504})


def synthetic_token(token: str) -> str:
    """A deliberately-bogus token, derived from a real one.

    Composite locators (workday's ``site/farm/board``, oracle's
    ``host/site``, ...) carry the serving host in every component but the
    last; blanking the whole token would point at a host that does not
    resolve, turning a negative control into a meaningless transport error.
    Replacing only the final ``/``-separated component keeps the host intact
    and invalidates just the part that actually names a company -- the same
    substitution :func:`keyscan.probe._html_control` uses for its generic-page
    fingerprint.
    """
    parts = token.split("/")
    parts[-1] = _SENTINEL
    return "/".join(parts)


@dataclass
class _BanState:
    classification: str | None = None
    detail: str = ""
    consecutive_errors: int = 0
    consecutive_blocked: int = 0
    saw_success: bool = False
    blocked_hosts: dict[str, int] = field(default_factory=dict)
    blocked_with_marker: bool = False


async def _recording_request(
    client: httpx.AsyncClient, method: str, url: str, *,
    json_body: dict | None = None, headers: dict[str, str] | None = None,
    attempts: list[dict[str, Any]], ban: _BanState, host_last: dict[str, float], delay: float,
) -> httpx.Response | None:
    """A single-shot :data:`RequestFn` that records every attempt and never
    retries a rate limit -- retrying would only deepen it. Once ``ban`` is
    set, every further call is a no-op so a blocked vendor stops immediately
    instead of burning through its remaining probes.
    """
    if ban.classification:
        return None
    host = (urlsplit(url).hostname or "").lower()
    wait = delay - (time.monotonic() - host_last.get(host, 0.0))
    if wait > 0:
        await asyncio.sleep(wait)
    host_last[host] = time.monotonic()
    try:
        response = await client.request(method, url, json=json_body, headers=headers)
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        attempts.append({"url": url, "status": None, "error": type(exc).__name__})
        ban.consecutive_errors += 1
        if ban.consecutive_errors >= 3:
            ban.classification = "degraded"
            ban.detail = f"{ban.consecutive_errors} consecutive transport errors/timeouts"
        return None
    attempts.append({"url": url, "status": response.status_code})
    if response.status_code == 429 or response.headers.get("retry-after"):
        ban.classification = "rate_limited"
        ban.detail = f"HTTP 429 on {url}"
        return response
    if response.status_code == 403:
        body = response.text[:4000].lower()
        marker = next((m for m in _WAF_MARKERS if m in body), None)
        if marker:
            ban.blocked_with_marker = True
        ban.blocked_hosts[host] = ban.blocked_hosts.get(host, 0) + 1
        ban.consecutive_blocked += 1
        # Two arms, because the vendors differ in shape:
        #   - one shared host for every tenant (Ashby, SmartRecruiters): that
        #     host refusing us twice is the vendor itself blocking us.
        #   - one host per tenant (Workday, Oracle, Eightfold): plenty of
        #     tenants run their own WAF -- measured 2026-08-20, 5 of 14
        #     Eightfold boards 403 while 6 served us normally -- so at that
        #     rate even a run of three proves nothing.
        # `saw_success` is what separates them: a block aimed at us refuses
        # everything, so one clean response anywhere in the vendor's run is
        # proof we are not the problem, and the remaining 403s are tenant
        # configuration we should probe straight past.
        # Five, not three: at a 36% tenant-block rate three in a row happens
        # ~5% of the time, and the trade is lopsided -- over-probing a blocked
        # vendor wastes at most `max_probes` requests, while calling a working
        # vendor blocked writes it off entirely.
        if ban.blocked_hosts[host] >= 2 or (ban.consecutive_blocked >= 5 and not ban.saw_success):
            ban.classification = "blocked" if ban.blocked_with_marker else "blocked_suspected"
            hosts = ", ".join(sorted(ban.blocked_hosts))
            ban.detail = f"HTTP 403 from {hosts}" + (f" (marker: {marker})" if marker else "")
        return response
    if response.status_code in _RETRYABLE_5XX:
        ban.consecutive_errors += 1
        if ban.consecutive_errors >= 3:
            ban.classification = "degraded"
            ban.detail = f"{ban.consecutive_errors} consecutive 5xx responses"
    else:
        # Any completed answer -- a 200 or an honest 404 -- proves the vendor
        # is reachable from here.
        ban.saw_success = True
        ban.consecutive_errors = 0
        ban.consecutive_blocked = 0
    return response


def _response_status(details: dict[str, Any]) -> int | None:
    responses = details.get("responses") or []
    return responses[-1][1] if responses else None


def _discrimination(dead: dict[str, Any] | None, synthetic: dict[str, Any]) -> str:
    if synthetic["status"] == "live":
        return "fail_synthetic_live"
    if synthetic["status"] == "inconclusive":
        return "warn_inconclusive_control"
    # synthetic == "absent"
    if dead is None:
        return "pass_synthetic_only"
    if _response_status(dead["details"]) == _response_status(synthetic["details"]):
        return "pass_consistent"
    return "pass_divergent"


async def verify_source(
    rule: Rule, *, crawls: int = 1, max_probes: int = 40,
    client: httpx.AsyncClient, workdir: Path, request_delay: float = 0.3,
) -> dict[str, Any]:
    """Verify one vendor's rule against live data. Never writes to the token DB."""
    if not rule.domains or rule.where_sql == "FALSE":
        return {"source": rule.source, "outcome": "no_cc_path"}

    manifest = await client.get(COLLINFO)
    if manifest.status_code != 200:
        return {"source": rule.source, "outcome": "cc_unavailable", "detail": f"collinfo http_{manifest.status_code}"}
    crawl_ids = [row["id"] for row in manifest.json()[:max(1, crawls)]]

    tokens: list[dict[str, Any]] = []
    scanned_crawls: list[str] = []
    unavailable: list[str] = []
    for crawl in crawl_ids:
        cc_manifest = await default_request(client, "GET", CC_PATHS.format(crawl=crawl))
        if cc_manifest is None or cc_manifest.status_code != 200:
            # A manifest we could not fetch means this crawl was never read.
            # Recording that separately matters: a sweep that scans several
            # vendors back to back can be throttled here, and reporting the
            # skip as "no tokens" would read as a verdict on the rule.
            unavailable.append(f"{crawl} " + ("transport" if cc_manifest is None else f"http_{cc_manifest.status_code}"))
            continue
        paths = gzip.decompress(cc_manifest.content).decode().splitlines()
        urls = [CC_CDN + path for path in paths if path.endswith(".parquet") and ("/subset=warc/" in path or "/subset=crawldiagnostics/" in path)]
        target = workdir / "verify" / crawl / f"{rule.source}.parquet"
        records = await asyncio.to_thread(cc_scan, crawl, rule, urls, target)
        tokens.extend(records)
        scanned_crawls.append(crawl)

    if not scanned_crawls:
        return {
            "source": rule.source, "outcome": "cc_unavailable",
            "detail": "; ".join(unavailable), "crawls_scanned": [],
        }
    if not tokens:
        return {"source": rule.source, "outcome": "no_tokens_found", "crawls_scanned": scanned_crawls}

    attempts: list[dict[str, Any]] = []
    host_last: dict[str, float] = {}
    ban = _BanState()

    async def request(method: str, url: str, *, json_body: dict | None = None, headers: dict[str, str] | None = None) -> httpx.Response | None:
        return await _recording_request(client, method, url, json_body=json_body, headers=headers, attempts=attempts, ban=ban, host_last=host_last, delay=request_delay)

    live: dict[str, Any] | None = None
    dead: dict[str, Any] | None = None
    inconclusive_count = 0
    probed = 0
    for record in tokens:
        if probed >= max_probes or ban.classification:
            break
        status, count, details = await probe_rule(rule, record["token"], request=request)
        probed += 1
        if status == "live" and live is None:
            live = {"token": record["token"], "count": count, "details": details}
        elif status == "absent" and dead is None:
            dead = {"token": record["token"], "details": details}
        elif status == "inconclusive":
            inconclusive_count += 1
        if live is not None and dead is not None:
            break

    result: dict[str, Any] = {
        "source": rule.source, "tokens_discovered": len(tokens), "crawls_scanned": scanned_crawls,
        "probes": probed, "live": live, "dead": dead, "attempts": attempts,
    }
    if ban.classification:
        result["outcome"] = ban.classification
        result["detail"] = ban.detail
        return result

    base_token = live["token"] if live else dead["token"] if dead else tokens[0]["token"]
    synth = synthetic_token(base_token)
    synth_status, _synth_count, synth_details = await probe_rule(rule, synth, request=request)
    probed += 1
    result["probes"] = probed
    result["synthetic"] = {"token": synth, "status": synth_status, "details": synth_details}
    if ban.classification:
        result["outcome"] = ban.classification
        result["detail"] = ban.detail
        return result

    if live is None and dead is None and inconclusive_count >= 3:
        result["outcome"] = "all_inconclusive"
        result["discrimination"] = _discrimination(dead, result["synthetic"])
        return result

    result["outcome"] = "verified"
    result["discrimination"] = _discrimination(dead, result["synthetic"])
    if dead is None:
        result["note"] = "no_dead_found"
    if live is None:
        result["note"] = "no_live_found"
    return result


async def verify_sources(
    names: tuple[str, ...], *, crawls: int = 1, max_probes: int = 40, workdir: Path,
) -> list[dict[str, Any]]:
    """Verify each named vendor (or group, e.g. ``tier1``) one at a time."""
    selected = expand_sources(names)
    unknown = set(selected) - BY_SOURCE.keys()
    if unknown:
        raise KeyscanError(f"unknown source(s): {', '.join(sorted(unknown))}")

    results = []
    async with httpx.AsyncClient(
        timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT},
    ) as client:
        for source in selected:
            results.append(await verify_source(BY_SOURCE[source], crawls=crawls, max_probes=max_probes, client=client, workdir=workdir))
    return results
