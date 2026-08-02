"""Breezy -- one GET, full descriptions if you ask for them.

    https://{key}.breezy.hr/json?verbose=true

Payload sample: notebooks/ATS-payloads.md#breezy.

- Response is an array of positions with full HTML description --
  `verbose=true` is required, or descriptions are missing entirely.
- Job URL: `url` field (= https://{key}.breezy.hr/p/{friendly_id}).
- `key` is in the HOSTNAME (`{key}.breezy.hr`), not the path -- the one
  structural difference from the other sources in this batch.
- The response root IS a bare JSON array; there is no wrapper key, so
  `envelope` is always `{}` -- there is no response-root to carry facts a
  single element doesn't already have. Each element nests its own `company`
  object, but that stays in the payload since every item carries it.
- `external_id` is `str(job["id"])`.
"""

from __future__ import annotations

from datetime import datetime

from xjobscore.net import Net, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = "https://{key}.breezy.hr/json?verbose=true"


async def harvest(feed: Feed, net: Net) -> Record:
    url = feed.config.get("url", LIST).format(key=feed.key)
    r = await net.get(url, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))
    body = r.json()
    items = [Item(external_id=str(job["id"]), payload=job) for job in body]
    return Record(items=items, http_status=r.status, validators=validators(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Breezy position -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    payload = item.payload

    # Parse published_at from ISO8601 UTC string
    published_date_str = payload.get("published_date")
    published_at = None
    if published_date_str:
        published_at = datetime.fromisoformat(published_date_str)

    # published_at_precision must match whether published_at is set
    published_at_precision = "exact" if published_at else "unknown"

    # Extract location info
    location = payload.get("location") or {}
    location_raw = location.get("name")

    # Extract type (employment_type) info
    type_info = payload.get("type") or {}
    employment_type = type_info.get("name")

    # Extract company info
    company = payload.get("company") or {}
    company_raw = company.get("name")
    company_friendly_id = company.get("friendly_id")

    # Extract locations_extra list
    locations_extra = [
        loc.get("name")
        for loc in payload.get("locations", [])
        if loc.get("name")
    ]

    # Build source_extra dict with the long tail
    source_extra = {
        "department": payload.get("department"),
        "salary": payload.get("salary"),
        "is_remote": location.get("is_remote"),
        "friendly_id": payload.get("friendly_id"),
        "company_friendly_id": company_friendly_id,
    }

    posting = Posting(
        external_id=item.external_id,
        title=payload.get("name", ""),
        url_posting=payload.get("url", ""),
        fidelity="origin",
        state="open",
        closed_evidence=None,
        company_raw=company_raw,
        apply_url=None,
        location_raw=location_raw,
        description_html=payload.get("description"),
        requisition_id=None,
        employment_type=employment_type,
        workplace_type_raw=None,
        published_at=published_at,
        published_at_precision=published_at_precision,
        source_updated_at=None,
        deadline_at=None,
        source_active=None,
        locations_extra=locations_extra,
        content_sections=[],
        source_extra=source_extra,
        origin_url=None,
    )

    return [posting]
