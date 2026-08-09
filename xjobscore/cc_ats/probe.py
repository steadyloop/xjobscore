"""Stage 2 -- ask each vendor whether a discovered token is a real board.

Results append to probe.jsonl as they arrive, and a resumed run skips tokens
already in the file. With a few hundred thousand rate-limited requests ahead of
it, a run that has to start over from zero after a network blip is not usable.

Transport errors are deliberately *not* recorded. A timeout is not evidence
that a board is absent, and writing it as one would burn the token permanently
on the first flaky night; leaving it out means the next run retries it.

No company domain is collected here. Binding is company-side only (see
bind.py), so a domain scraped off a board would be a field nothing reads --
and one that quietly invites the exact parent/subsidiary mis-binding the
approved design rules out.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime
from pathlib import Path

import httpx

from cc_ats.ccindex import USER_AGENT
from cc_ats.vendors import BY_ATS, Vendor



# Keys vendors use for the job array and for a total. Handling the variation in
# one place beats twenty near-identical per-vendor parsers.
LIST_KEYS = ("jobs", "offers", "data", "content", "jobPostings", "results", "result", "positions", "items")
TOTAL_KEYS = ("total", "totalFound", "totalCount", "count")
# Deliberately not "title": several vendors put the *job* title there, and a
# job title landing in company_new as a company string is worse than no name.
NAME_KEYS = ("name", "companyName", "organizationName")

BATCH = 2000


def _job_count(payload) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return None
    for key in TOTAL_KEYS:
        if isinstance(payload.get(key), int):
            return payload[key]
    for key in LIST_KEYS:
        if isinstance(payload.get(key), list):
            return len(payload[key])
    meta = payload.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("total"), int):
        return meta["total"]
    return None


def _company_name(payload) -> str | None:
    if isinstance(payload, dict):
        for key in NAME_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        company = payload.get("company")
        if isinstance(company, dict):
            return _company_name(company)
        for key in LIST_KEYS:
            first = payload.get(key)
            if isinstance(first, list) and first and isinstance(first[0], dict):
                nested = first[0].get("company")
                if isinstance(nested, dict):
                    return _company_name(nested)
    return None


def _extract_company_from_html(html_text: str) -> str | None:
    """Extract company name from HTML page content.

    Tries common sources: <title>, <h1>, meta og:site_name, in order.
    Returns first non-empty match.
    """
    if not html_text:
        return None

    import html as html_module

    # Try <title> tag
    import re as regex_module
    title_match = regex_module.search(r'<title[^>]*>([^<]+)</title>', html_text, regex_module.I)
    if title_match:
        text = html_module.unescape(title_match.group(1)).strip()
        # Clean up common patterns like "Company Name | Careers"
        text = regex_module.sub(r'\s*[\|\-]\s*(careers?|jobs?|apply|positions).*', '', text, flags=regex_module.I)
        text = text.strip()
        if text and len(text) > 2:
            return text

    # Try <h1> tag
    h1_match = regex_module.search(r'<h1[^>]*>([^<]+)</h1>', html_text, regex_module.I)
    if h1_match:
        text = html_module.unescape(h1_match.group(1)).strip()
        if text and len(text) > 2:
            return text

    # Try og:site_name meta tag
    og_match = regex_module.search(r'<meta\s+property=["\']og:site_name["\'][^>]*content=["\']([^"\']+)["\']', html_text, regex_module.I)
    if not og_match:
        og_match = regex_module.search(r'<meta\s+content=["\']([^"\']+)["\'][^>]*property=["\']og:site_name["\']', html_text, regex_module.I)
    if og_match:
        text = html_module.unescape(og_match.group(1)).strip()
        if text and len(text) > 2:
            return text

    return None


async def _request(client: httpx.AsyncClient, method: str, url: str, body: dict | None, log_file = None) -> httpx.Response:
    """One request with backoff. 429 and 5xx are the vendor asking us to slow
    down, so they are retried; 404 is an answer and is returned immediately."""
    delay = 1.0
    for attempt in range(4):
        start_time = time.time()
        response = await client.request(method, url, json=body)
        elapsed_ms = int((time.time() - start_time) * 1000)
        timestamp = datetime.now().isoformat(timespec='milliseconds')

        # Log every request
        log_line = f"{timestamp} {method:4} {response.status_code} {elapsed_ms:4}ms {url[:70]}"
        print(log_line)
        if log_file:
            log_file.write(log_line + "\n")
            log_file.flush()

        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 3:
                return response
            wait = response.headers.get("Retry-After")
            sleep_time = float(wait) if wait and wait.isdigit() else delay
            status_label = "rate limited" if response.status_code == 429 else f"{response.status_code}"
            backoff_line = f"  └─ {status_label}, attempt {attempt + 1}/4, backing off {sleep_time:.1f}s"
            print(backoff_line)
            if log_file:
                log_file.write(backoff_line + "\n")
                log_file.flush()
            await asyncio.sleep(sleep_time)
            delay *= 2
            continue
        return response
    return response


async def probe_one(
    client: httpx.AsyncClient,
    vendor: Vendor,
    token: str,
    vendor_sem: asyncio.Semaphore,
    global_sem: asyncio.Semaphore,
    log_file = None,
) -> dict | None:
    fields = vendor.urls(token)
    primary = vendor.probe_url if vendor.probe_kind != "html" else vendor.board_url
    urls = [u.format(**fields) for u in (primary, *vendor.probe_fallbacks)]
    result = {"ats": vendor.ats, "token": token, "job_count": None, "company_name": None}

    # Random jitter before acquiring semaphores (mimics human-like delays)
    await asyncio.sleep(random.uniform(0.01, 0.15))

    async with global_sem, vendor_sem:
        # 'absent' is only concluded once every regional host has 404'd. A
        # tenant exists on exactly one of them, so stopping at the first 404
        # would record real boards as absent -- the failure that made Lever
        # look 2% live when it is not.
        response = None
        for url in urls:
            try:
                response = await _request(client, vendor.probe_method, url, vendor.probe_json, log_file)
            except httpx.HTTPError:
                # Covers connect, read, timeout and TLS failures -- all of
                # which httpx wraps. None means "no answer", never "absent".
                return None
            if response.status_code not in (404, 410):
                break
        else:
            return {**result, "status": "absent"}

        if response.status_code != 200:
            return None  # 403/503/etc: inconclusive, retry on the next run

        if vendor.probe_kind == "json":
            try:
                payload = response.json()
            except ValueError:
                return {**result, "status": "absent"}
            result["job_count"] = _job_count(payload)
            result["company_name"] = _company_name(payload)
        elif vendor.probe_kind == "xml":
            text = response.text
            if "<" not in text:
                return {**result, "status": "absent"}
            result["job_count"] = max(text.count("<item"), text.count("<position"), text.count("<job"))
        else:
            body = response.text[:20000].lower()
            if any(marker in body for marker in vendor.absent_markers):
                return {**result, "status": "absent"}

        result["status"] = "live"

        # Try to extract company name from multiple sources
        if not result["company_name"]:
            # Source 1: dedicated name_url endpoint (Greenhouse, etc.)
            if vendor.name_url:
                for name_url in (vendor.name_url, *vendor.name_fallbacks):
                    try:
                        extra = await _request(client, "GET", name_url.format(**fields), None, log_file)
                        if extra.status_code == 200:
                            result["company_name"] = _company_name(extra.json())
                            break
                    except (httpx.HTTPError, ValueError):
                        break

        if not result["company_name"]:
            # Source 2: extract from HTML response (HTML vendors only)
            if vendor.probe_kind == "html" and vendor.html_company_xpath:
                result["company_name"] = _extract_company_from_html(response.text)

        return result


async def run(
    workdir: Path,
    candidates: list[dict],
    per_vendor: int = 6,
    concurrency: int = 40,
    limit: int | None = None,
) -> Path:
    out = workdir / "probe.jsonl"
    done: set[tuple[str, str]] = set()
    if out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                done.add((row["ats"], row["token"]))

    todo = [c for c in candidates if (c["ats"], c["token"]) not in done]
    if limit:
        todo = todo[:limit]
    print(f"probing {len(todo):,} tokens ({len(done):,} already done)")

    # Static concurrency: 3 global, 2 per vendor
    global_sem = asyncio.Semaphore(3)
    vendor_sems = {ats: asyncio.Semaphore(2) for ats in BY_ATS}
    counts = {"live": 0, "absent": 0, "error": 0}

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    # Randomize timeout to avoid bot-like predictability (25-35s instead of always 30s)
    timeout = random.uniform(25, 35)

    # Open request log file
    log_path = workdir / "probe_requests.log"

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        limits=limits,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        with log_path.open("a") as log_file, out.open("a") as handle:
            # Batched rather than one coroutine per candidate up front: at a few
            # hundred thousand tokens, materialising every task before any of
            # them runs costs more memory than the scan did. The semaphores
            # still set the real concurrency; the batch only bounds what is
            # allocated at once.
            done_count = 0
            for start in range(0, len(todo), BATCH):
                batch = todo[start : start + BATCH]
                tasks = [
                    probe_one(client, BY_ATS[c["ats"]], c["token"], vendor_sems[c["ats"]], global_sem, log_file)
                    for c in batch
                ]
                for coro in asyncio.as_completed(tasks):
                    result = await coro
                    if result is None:
                        counts["error"] += 1
                    else:
                        counts[result["status"]] += 1
                        handle.write(json.dumps(result) + "\n")
                done_count += len(batch)
                handle.flush()
                print(f"  {done_count:,}/{len(todo):,}  {counts}")

    print(f"done: {counts}")
    return out


def load_results(workdir: Path) -> list[dict]:
    out = workdir / "probe.jsonl"
    if not out.exists():
        raise SystemExit(f"no probe output at {out}; run `probe` first")
    return [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
