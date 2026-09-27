"""Dictionary enumeration: guess a vendor's tokens from a wordlist and probe.

The crawl finds tokens where the vendor has a bounded domain to scan. Some
do not -- iCIMS serves each board on the customer's own ``careers.{x}.com``
-- so the only discovery left is to guess ``x``. Every existing iCIMS token
in xjobscore was the company's domain label (``amd.com`` -> ``amd``), so a
company list is a good dictionary, and the rule's own ``probe_url`` is the
oracle.

Standalone like ``name_boards``: a plain client, no run row, and only a
confirmed ``live`` board is written to ``tenants`` (``method = 'enum'``).
Misses are counted, not stored -- a word that resolved to nothing is not a
tenant, and reprobing it next time costs one DNS lookup.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import httpx

from .core import USER_AGENT, KeyscanError, _canonical_token, _json, utc_now
from .probe import probe_rule
from .rules import BY_SOURCE, TOKEN_RE


def words_to_tokens(words: Iterable[str]) -> tuple[list[str], int]:
    """Normalise a wordlist into unique candidate tokens.

    A domain contributes its first label (``spglobal.com`` -> ``spglobal``),
    so a column of ``company.domain`` works unedited. Returns the tokens in
    first-seen order and how many lines were dropped as unusable.
    """
    tokens: dict[str, None] = {}
    dropped = 0
    for raw in words:
        word = raw.strip().lower()
        if not word or word.startswith("#"):
            continue
        if word.startswith("www."):
            word = word[4:]
        token = word.split(".", 1)[0]
        if not token or not TOKEN_RE.fullmatch(token):
            dropped += 1
            continue
        tokens.setdefault(token, None)
    return list(tokens), dropped


async def enumerate_tokens(
    db_path: str,
    vendor: str,
    words: Iterable[str],
    *,
    concurrency: int = 16,
    dry_run: bool = False,
    on_row: Callable[[int, int, str, str, int | None], None] | None = None,
) -> dict[str, Any]:
    rule = BY_SOURCE.get(vendor)
    if rule is None:
        raise KeyscanError(f"unknown vendor: {vendor}")
    if rule.probe_kind == "comeet":
        raise KeyscanError("comeet boards are located by URL, not by slug; enum cannot guess them")

    tokens, dropped = words_to_tokens(words)
    tokens = [_canonical_token(vendor, token) for token in tokens]
    con = sqlite3.connect(db_path)
    try:
        known = {
            row[0] for row in con.execute("SELECT key FROM tenants WHERE ats = ?", (vendor,))
        }
    finally:
        con.close()
    pending = [token for token in tokens if token not in known]

    sem = asyncio.Semaphore(concurrency)
    unreachable: set[str] = set()
    controls: dict[str, str | None] = {}
    results: list[tuple[str, str, int | None, dict[str, Any]]] = []
    lock = asyncio.Lock()

    async with httpx.AsyncClient(
        timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        async def request(
            method: str, url: str, *, json_body: dict | None = None,
            headers: dict[str, str] | None = None,
        ) -> httpx.Response | None:
            try:
                return await client.request(method, url, json=json_body, headers=headers)
            except httpx.ConnectError:
                # NXDOMAIN or refused. For a vanity-host vendor this *is* the
                # negative answer; `one` decides whether it applies.
                unreachable.add((urlsplit(url).hostname or "").lower())
                return None
            except httpx.HTTPError:
                return None

        async def one(token: str) -> None:
            async with sem:
                status, count, details = await probe_rule(rule, token, request=request, controls=controls)
            if status == "inconclusive" and details.get("reason") == "transport":
                host = (urlsplit(rule.probe_url.format(**rule.urls(token))).hostname or "").lower()
                if host in unreachable and token in host:
                    status, details = "absent", {**details, "reason": "unreachable_host"}
            async with lock:
                results.append((token, status, count, details))
                if on_row:
                    on_row(len(results), len(pending), token, status, count)

        await asyncio.gather(*(one(token) for token in pending))

    live = [(token, count, details) for token, status, count, details in results if status == "live"]
    if live and not dry_run:
        now = utc_now()
        rows = []
        for token, count, details in live:
            canonical = rule.board_url.format(**rule.urls(token))
            rows.append((
                vendor, token, details.get("company_name"), canonical, _json([canonical]),
                _json({**details, "verdict": "live", "job_count": count}), now, now,
            ))
        con = sqlite3.connect(db_path)
        try:
            con.executemany(
                """INSERT OR IGNORE INTO tenants(
                       ats, key, name, status, canonical_url, urls, method, details,
                       discovered_at, checked_at)
                   VALUES (?, ?, ?, 'live', ?, ?, 'enum', ?, ?, ?)""",
                rows,
            )
            con.commit()
        finally:
            con.close()

    return {
        "vendor": vendor,
        "tokens": len(tokens),
        "dropped": dropped,
        "known": len(tokens) - len(pending),
        "probed": len(results),
        "absent": sum(1 for _, status, _, _ in results if status == "absent"),
        "written": 0 if dry_run else len(live),
        "live": [
            {"key": token, "name": details.get("company_name"), "job_count": count}
            for token, count, details in live
        ],
        # Worth a second look by hand: no answer either way, so not stored.
        "inconclusive": [
            {"key": token, "reason": details.get("reason")}
            for token, status, _, details in results if status == "inconclusive"
        ],
    }
