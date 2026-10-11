"""Simplify-only posting projection and insert-only ATS discovery.

The shared parser still marks aggregator records as secondhand. This explicit
opt-in retains that provenance while allowing Simplify's current internships
into the serving pipeline, without admitting other secondhand sources.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from keyscan.rules import extract
from xjobscore import store
from xjobscore.feed_identity import canonical_feed_identity
from xjobscore.records import Posting
from xjobscore.source_policy import source_disabled


def project(posting: Posting, payload: dict[str, Any]) -> Posting:
    """Map fields absent from the generic parser; keep secondhand provenance."""
    return replace(
        posting,
        apply_url=payload["url"],
        published_at=datetime.fromtimestamp(payload["date_posted"], tz=timezone.utc),
        published_at_precision="exact",
        # Only current Summer 2027 rows from the curated internship feed reach
        # this path. The season alone is never used to classify other sources.
        job_type="intern",
        remote="yes" if any(
            "remote" in value.casefold() for value in payload.get("locations", [])
            if isinstance(value, str)
        ) else "unknown",
        location_normalization_extra=list(posting.locations_extra),
    )


def metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Preserve every upstream field, including exact locations and attribution."""
    return {"simplify": dict(payload), "provenance": {"source": "simplify", "fidelity": "secondhand"}}


async def company(
    conn, posting: Posting, *, dry_run: bool, aliases: dict[str, int] | None,
) -> tuple[int | None, bool]:
    """Resolve the listing's employer, never the aggregator feed's company."""
    name = (posting.company_raw or "").strip()
    if not name:
        return None, False
    resolved = await store.resolve_company(conn, name, aliases=aliases)
    if resolved is not None or dry_run or aliases is not None:
        return resolved, False
    # Never changes an existing token's company or attaches employers to
    # Simplify's token: only the listing's own employer is resolved or minted.
    return await store.resolve_or_mint(conn, name, discovered_from="simplify")


def token_candidate(url: str, company_id: int) -> tuple[str, str, int] | None:
    found = extract(url)
    if found is None or source_disabled(found[0]):
        return None
    ats, key = canonical_feed_identity(*found)
    return ats, key, company_id


async def discover(conn, candidates: list[tuple[str, str, int]], seen_at: datetime) -> int:
    """Run after posting writes; leave all existing token rows untouched."""
    identities = {(ats, key): company_id for ats, key, company_id in candidates}
    if not identities:
        return 0
    result = await conn.execute(
        """
        INSERT INTO token(ats, token, company_id, status, source, discovered_at)
        SELECT ats, token, company_id, 'unknown', 'simplify', $4
        FROM unnest($1::text[], $2::text[], $3::bigint[]) AS t(ats, token, company_id)
        ON CONFLICT (ats, token) DO NOTHING
        """,
        [ats for ats, _ in identities],
        [key for _, key in identities],
        list(identities.values()),
        seen_at,
    )
    return int(result.rsplit(" ", 1)[-1])
