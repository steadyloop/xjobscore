"""Which vendors has Common Crawl been told not to fetch?

Diagnostic, not part of the pipeline. It exists because the alternative is
misreading the result: `jobs.lever.co` yields ~0 tokens not because the
extraction is wrong but because Lever serves `User-agent: CCBot / Disallow: /`,
and Common Crawl honours it. Without this command that looks exactly like a
broken regex, and the regex is fine.

Only meaningful for vendors on a shared board host. For `{tenant}.vendor.com`
vendors there is no host to check without picking a real tenant, so those are
reported as unknown rather than quietly passed.
"""

from __future__ import annotations

import asyncio
import re

import httpx

from cc_ats.ccindex import USER_AGENT
from cc_ats.vendors import VENDORS, Vendor

_HOST = re.compile(r"^https?://([^/]+)")
_PLACEHOLDER = {"token": "example", "t0": "example", "t1": "wd1", "t2": "Careers"}


def is_shared_host(vendor: Vendor) -> bool:
    """True when every tenant sits on one hostname, so robots.txt is one fact."""
    host = _HOST.match(vendor.board_url.format(**_PLACEHOLDER)).group(1)
    return "example" not in host


def board_host(vendor: Vendor) -> str:
    return _HOST.match(vendor.board_url.format(**_PLACEHOLDER)).group(1)


def blocks_ccbot(text: str) -> bool:
    """Read only the CCBot group.

    A blanket Disallow under some other agent says nothing about Common Crawl,
    so grouping matters -- a naive substring search for 'Disallow: /' reports
    almost every vendor as blocked.
    """
    current, blocked = None, False
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            current = value.lower()
        elif key == "disallow" and current == "ccbot" and value == "/":
            blocked = True
    return blocked


async def _check(client: httpx.AsyncClient, vendor: Vendor) -> tuple[str, str, str]:
    host = board_host(vendor)
    if not is_shared_host(vendor):
        return vendor.ats, "(per-tenant subdomain)", "unknown"
    try:
        response = await client.get(f"https://{host}/robots.txt")
    except httpx.HTTPError as exc:
        return vendor.ats, host, f"unreachable: {type(exc).__name__}"
    if response.status_code != 200:
        return vendor.ats, host, f"no robots.txt ({response.status_code})"
    return vendor.ats, host, "BLOCKS CCBot" if blocks_ccbot(response.text) else "allows CCBot"


async def run() -> None:
    async with httpx.AsyncClient(
        timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        results = await asyncio.gather(*(_check(client, v) for v in VENDORS))

    blocked = [r for r in results if r[2] == "BLOCKS CCBot"]
    for ats, host, verdict in sorted(results, key=lambda r: (r[2] != "BLOCKS CCBot", r[0])):
        print(f"  {ats:<17} {host:<34} {verdict}")
    if blocked:
        print(
            f"\n{len(blocked)} vendor(s) block CCBot. Common Crawl cannot see them at "
            f"all, so a near-zero token count for these is correct, not a bug. Their "
            f"tokens have to come from the bind stage (company careers pages) or from "
            f"a second source such as the Wayback CDX API."
        )
