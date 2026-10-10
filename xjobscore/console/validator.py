"""Token validation for the console's Tokens page.

This is a thin adapter over :mod:`keyscan.probe` -- the *same* engine the crawl
uses -- so per-vendor liveness rules (fallbacks, the Comeet handshake, the
zero-count and generic-HTML nuances) stay in one place. We only translate its
three-valued result into the two-and-a-half-valued ``token.status`` column and
record the check timestamp.

We ask a narrower question than the crawl does, though: this page stores
``status``, ``checked_at``, and a ``token_log`` row, so ``liveness_only=True`` lets
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

from keyscan.probe import HTMLControlCache, RequestFn, default_request, probe_rule
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


async def probe_token(
    ats: str, token: str, *, request: RequestFn,
    controls: dict[str, str | None] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Probe one token without persisting: ``(status, details)``.

    ``status`` is already in ``token.status`` vocabulary. Split out of
    `validate_token` so a caller that must not record every outcome -- the
    sweep requeues a rate-limited probe rather than writing it as checked --
    can decide before anything reaches the database.
    """
    rule = BY_SOURCE.get(ats)
    if rule is None:
        return "unknown", {"reason": f"unknown_ats_{ats}"}
    try:
        probe_status, count, details = await probe_rule(
            rule, token, request=request, controls=controls, liveness_only=True
        )
    except Exception as exc:  # never let one board's failure abort a batch
        return "unknown", {"error": str(exc)}
    if count is not None:
        details = {**details, "count": count}
    if ats == "zohorecruit" and probe_status == "live":
        # Keep the region that answered; harvesting otherwise defaults to .com.
        endpoint = details["responses"][-1][0]
        details = {**details, "config": {"url": endpoint}}
    if ats == "lever" and probe_status == "live":
        # Keep the working region without the probe's one-job limit.
        endpoint = details["responses"][-1][0].split("&limit=", 1)[0]
        details = {**details, "config": {"url": endpoint}}
    # Comeet's probe resolves the public `slug/board-id` locator into the
    # board id and API token its jobs API needs. The crawl renames its key to
    # `board-id/api-token`; here the key is the feed's identity, so the
    # resolution goes to `token.config` instead, which the adapter applies
    # over the split key. Without it, the adapter reads the slug as the board
    # and the board id as the token, and every poll is an HTTP 400.
    adapter_token = details.pop("_comeet_adapter_token", None)
    if adapter_token:
        board_id, api_token = adapter_token.split("/", 1)
        details = {**details, "config": {"company": board_id, "token": api_token}}
    return _STATUS.get(probe_status, "unknown"), details


async def persist_result(
    pool: asyncpg.Pool, ats: str, token: str, status: str, checked_at: datetime,
    *, log: bool = True, config: dict[str, Any] | None = None,
) -> bool:
    """Write one probe outcome to ``token``; with `log`, to ``token_log`` too.

    `config` (a probe's ``details["config"]``) is merged over ``token.config``.
    Returns False when the write failed, so a caller can surface it without
    losing the freshly-probed result.
    """
    try:
        if log:
            # One statement, so the row and its log entry move together: no
            # crash can record a check that the log misses, or the reverse.
            # The INSERT is fed by the UPDATE's RETURNING, so a token that is
            # not in the inventory logs nothing.
            await pool.execute(
                """
                WITH checked AS (
                    UPDATE token
                       SET status = $1, checked_at = $2, config = config || $5
                     WHERE ats = $3 AND token = $4
                 RETURNING ats, token
                )
                INSERT INTO token_log (checked_at, ats, token, status)
                SELECT $2, ats, token, $1 FROM checked
                """,
                status,
                checked_at,  # timestamptz column -- pass a datetime, not a string
                ats,
                token,
                config or {},
            )
        else:
            await pool.execute(
                "UPDATE token SET status = $1, checked_at = $2, config = config || $5"
                " WHERE ats = $3 AND token = $4",
                status, checked_at, ats, token, config or {},
            )
    except (OSError, asyncpg.PostgresError):
        return False
    return True


async def persist_moved(
    pool: asyncpg.Pool, ats: str, token: str, moved_to: str, checked_at: datetime,
) -> bool:
    """Record the token a probe found in place of `token` as live.

    The old row is left for `persist_result` to mark dead, so its history
    stays; the new one inherits its company and is logged like any live check.
    """
    try:
        await pool.execute(
            """
            WITH moved AS (
                INSERT INTO token (ats, token, company_id, status, checked_at, source, discovered_at)
                SELECT ats, $3, company_id, 'live', $4, 'validator', $4
                  FROM token WHERE ats = $1 AND token = $2
                ON CONFLICT (ats, token) DO UPDATE
                   SET status = 'live', checked_at = EXCLUDED.checked_at
             RETURNING ats, token
            )
            INSERT INTO token_log (checked_at, ats, token, status)
            SELECT $4, ats, token, 'live' FROM moved
            """,
            ats, token, moved_to, checked_at,
        )
    except (OSError, asyncpg.PostgresError):
        return False
    return True


async def validate_token(
    ats: str,
    token: str,
    *,
    pool: asyncpg.Pool | None = None,
    timeout: float = 10.0,
    client: httpx.AsyncClient | None = None,
    controls: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Probe a single token and return its validation result.

    Returns a dict with ``ats``, ``token``, ``status`` ("live"/"dead"/
    "unknown"), ``checked_at`` (ISO string) and ``details`` (debug info).
    When ``client`` is given it is reused; otherwise a short-lived one is
    opened for this call.
    """
    checked_at = datetime.now(timezone.utc)
    if ats not in BY_SOURCE:
        status, details = await probe_token(ats, token, request=None)  # type: ignore[arg-type]
    else:
        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient(
                timeout=timeout, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
            )
        try:
            status, details = await probe_token(
                ats, token, request=partial(default_request, client), controls=controls,
            )
        finally:
            if owns_client:
                await client.aclose()

    if pool is not None and not await persist_result(
        pool, ats, token, status, checked_at, config=details.get("config")
    ):
        # A failed persist must not hide the freshly-probed result from the
        # caller, but it should not pass silently either.
        details = {**details, "persist_error": True}
    moved_to = details.get("moved_to")
    if pool is not None and moved_to and not await persist_moved(pool, ats, token, moved_to, checked_at):
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

    controls = HTMLControlCache()

    async def one(ats: str, token: str) -> dict[str, Any]:
        async with semaphore:
            return await validate_token(
                ats, token, pool=pool, timeout=timeout, client=client, controls=controls,
            )

    try:
        return await asyncio.gather(*(one(ats, token) for ats, token in tokens))
    finally:
        if owns_client:
            await client.aclose()
