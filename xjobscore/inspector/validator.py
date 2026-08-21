"""Token validation for the inspector's Tokens page.

This is a thin adapter over :mod:`keyscan.probe` -- the *same* engine the crawl
uses -- so per-vendor liveness rules (fallbacks, the Comeet handshake, the
zero-count and generic-HTML nuances) stay in one place. We only translate its
three-valued result into the two-and-a-half-valued ``token.status`` column and
record the check timestamp.

We ask a narrower question than the crawl does, though: this page stores
``status`` and ``checked_at`` and nothing else, so ``liveness_only=True`` lets
each rule swap in its cheap variant -- a lever board costs 12 KB instead of
5.9 MB, an ashby one a bodiless HEAD instead of 1.29 MB. The crawl keeps the
full path because it persists ``job_count``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import partial
from typing import Any

import asyncpg
import httpx

from keyscan.probe import default_request, probe_rule
from keyscan.rules import BY_SOURCE
from xjobscore.net import DEFAULT_USER_AGENT

# keyscan.probe returns live/absent/inconclusive; schema.sql constrains
# token.status to exactly these three (CHECK status IN ('unknown','live','dead')).
_STATUS = {"live": "live", "absent": "dead", "inconclusive": "unknown"}

# Public job boards, no auth: the crawl's user-agent is the default here too --
# imported rather than repeated, since this module and keyscan hit the same
# vendor hosts and a second copy is how the two drift apart.
_USER_AGENT = DEFAULT_USER_AGENT

# One HTTP request must stay short enough to outlive nothing: probes run three
# at a time with a per-board timeout, so a few hundred tokens is already
# minutes. Callers that want more (the Tokens page's per-vendor button) split
# the work into batches and keep their own progress.
MAX_BATCH = 200


async def validate_token(
    ats: str,
    token: str,
    *,
    pool: asyncpg.Pool | None = None,
    timeout: float = 10.0,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Probe a single token and return its validation result.

    Returns a dict with ``ats``, ``token``, ``status`` ("live"/"dead"/
    "unknown"), ``checked_at`` (ISO string) and ``details`` (debug info).
    When ``client`` is given it is reused; otherwise a short-lived one is
    opened for this call.
    """
    checked_at = datetime.now(timezone.utc)
    status = "unknown"
    details: dict[str, Any] = {}

    rule = BY_SOURCE.get(ats)
    if rule is None:
        details["reason"] = f"unknown_ats_{ats}"
    else:
        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient(
                timeout=timeout, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
            )
        try:
            probe_status, count, details = await probe_rule(
                rule, token, request=partial(default_request, client), liveness_only=True
            )
            status = _STATUS.get(probe_status, "unknown")
            if count is not None:
                details = {**details, "count": count}
        except Exception as exc:  # never let one board's failure abort a batch
            status = "unknown"
            details = {"error": str(exc)}
        finally:
            if owns_client:
                await client.aclose()

    if pool is not None:
        try:
            await pool.execute(
                "UPDATE token SET status = $1, checked_at = $2 WHERE ats = $3 AND token = $4",
                status,
                checked_at,  # timestamptz column -- pass a datetime, not a string
                ats,
                token,
            )
        except (OSError, asyncpg.PostgresError):
            # A failed persist must not hide the freshly-probed result from the
            # caller, but it should not pass silently either.
            details = {**details, "persist_error": True}

    return {
        "ats": ats,
        "token": token,
        "status": status,
        "checked_at": checked_at.isoformat(timespec="seconds"),
        "details": details,
    }


async def validate_tokens(
    tokens: list[tuple[str, str]],
    *,
    pool: asyncpg.Pool | None = None,
    timeout: float = 10.0,
    max_concurrent: int = 3,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Probe many ``(ats, token)`` pairs, at most ``max_concurrent`` at a time.

    Results are returned in the same order as ``tokens``. One client is shared
    across the whole batch so connections are reused; keyscan's engine handles
    per-request retries. Pass ``client`` to reuse an existing one (it is left
    open); otherwise a short-lived one is opened for the batch.
    """
    if not tokens:
        return []
    semaphore = asyncio.Semaphore(max(1, max_concurrent))
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        )

    async def one(ats: str, token: str) -> dict[str, Any]:
        async with semaphore:
            return await validate_token(ats, token, pool=pool, timeout=timeout, client=client)

    try:
        return await asyncio.gather(*(one(ats, token) for ats, token in tokens))
    finally:
        if owns_client:
            await client.aclose()
