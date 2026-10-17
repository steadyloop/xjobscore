"""Try the Companies page's domain guess once, without changing resolution."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING
import unicodedata

import asyncpg
import httpx

from xjobscore import store
from xjobscore.net import DEFAULT_USER_AGENT

if TYPE_CHECKING:
    from asyncpg import Pool
    from xjobscore.records import Feed

log = logging.getLogger(__name__)
EXCLUDED_ATS = frozenset({"greenhouse", "gem", "recruitee", "rippling"})
TIMEOUT_S = 5.0


def guess_domain(name: str | None) -> str:
    """Match console/static/companies/model.mjs's guessDomain, including ASCII boundaries."""
    if not name:
        return ""
    slug = unicodedata.normalize("NFKC", name).lower()
    slug = re.sub(
        r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|group|plc)\b\.?",
        "", slug, flags=re.ASCII,
    )
    slug = re.sub(r"[^a-z0-9]", "", slug)
    return f"{slug}.com" if slug else ""


async def _reachable(candidate: str) -> bool:
    """One GET, bounded across redirects; only response headers are needed."""
    try:
        async with asyncio.timeout(TIMEOUT_S), httpx.AsyncClient(
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=TIMEOUT_S, follow_redirects=True, max_redirects=5,
        ) as client:
            async with client.stream("GET", f"https://{candidate}/") as response:
                return (
                    200 <= response.status_code < 300
                    and store.normalize_domain(str(response.url)) is not None
                )
    except (httpx.HTTPError, TimeoutError) as error:
        log.debug("domain guess failed candidate=%s: %s", candidate, error)
        return False


async def try_for_feed(pool: Pool, feed: Feed) -> bool:
    """Claim before HTTP, then save only if the company still has no answer.

    The committed marker survives failures and interruption. Reading the token
    again also finds a company minted by this poll, absent from the claimed Feed.
    """
    if feed.type != "ATS" or feed.source in EXCLUDED_ATS:
        return False
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """
            SELECT c.id, c.name FROM company c
            JOIN token t ON t.company_id = c.id
            WHERE t.ats = $1 AND t.token = $2
              AND c.domain IS NULL AND c.reviewed = 'pending'
              AND NOT c.tags ? 'domain_guess'
            FOR UPDATE OF c
            """,
            feed.source, feed.key,
        )
        if row is None:
            return False
        company_id, name = row["id"], row["name"]
        candidate = store.normalize_domain(guess_domain(name))
        attempt = await conn.fetchval(
            """
            UPDATE company SET tags = tags || jsonb_build_object(
                'domain_guess', jsonb_build_object(
                    'candidate', $2::text, 'checked_at', clock_timestamp(), 'added', false
                )
            ) WHERE id = $1 RETURNING tags->'domain_guess'
            """,
            company_id, candidate,
        )

    if candidate is None or not await _reachable(candidate):
        return False

    try:
        async with pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                """
                SELECT id FROM company WHERE id = $1 AND name = $2
                  AND domain IS NULL AND reviewed = 'pending'
                  AND tags->'domain_guess' = $3::jsonb
                FOR UPDATE
                """,
                company_id, name, attempt,
            )
            if current is None or await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM company WHERE lower(domain) = $1)", candidate
            ):
                return False
            old_key = await store.company_identity_key(conn, company_id)
            await conn.execute(
                """
                UPDATE company SET domain = $2,
                    tags = jsonb_set(tags, '{domain_guess,added}', 'true'::jsonb)
                        || CASE WHEN tags ? 'domain_machine' THEN '{}'::jsonb
                           ELSE jsonb_build_object('domain_machine', $2::text) END
                WHERE id = $1
                """,
                company_id, candidate,
            )
            await store.rekey_company_identity(conn, company_id, old_key)
    except asyncpg.UniqueViolationError:
        # A competing company won the domain; retain the earlier attempt marker.
        return False
    log.info("company domain automatically added company_id=%s domain=%s", company_id, candidate)
    return True
