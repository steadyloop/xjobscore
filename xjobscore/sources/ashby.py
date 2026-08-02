"""Ashby -- one GET, full descriptions, no separate detail endpoint.

    https://api.ashbyhq.com/posting-api/job-board/{key}?includeCompensation=true

Payload sample: notebooks/ATS-payloads.md#ashby.

- jobUrl is the hosted posting; applyUrl is the application page.
- includeCompensation=true adds a `compensation` object (tiers + summary
  components) -- decide whether that belongs in source_extra.
- Board name (`key`) is in the careers URL jobs.ashbyhq.com/{key}.
"""

from __future__ import annotations

from datetime import datetime

from xjobscore.net import Net, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = "https://api.ashbyhq.com/posting-api/job-board/{key}?includeCompensation=true"


async def harvest(feed: Feed, net: Net) -> Record:
    url = feed.config.get("url", LIST).format(key=feed.key)
    r = await net.get(url, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))
    body = r.json()
    items = [Item(external_id=str(job["id"]), payload=job) for job in body["jobs"]]
    envelope = {"apiVersion": body["apiVersion"]} if "apiVersion" in body else {}
    return Record(items=items, envelope=envelope, http_status=r.status, validators=validators(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Ashby job -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    job = item.payload

    # Parse published_at if present
    published_at_str = job.get("publishedAt")
    published_at = None
    if published_at_str:
        published_at = datetime.fromisoformat(published_at_str)

    published_at_precision = "exact" if published_at else "unknown"

    # Extract secondary locations
    locations_extra = [
        loc.get("location")
        for loc in job.get("secondaryLocations", [])
        if loc.get("location")
    ]

    # Build source_extra with optional fields
    source_extra = {
        "department": job.get("department"),
        "team": job.get("team"),
        "isRemote": job.get("isRemote"),
        "compensation": job.get("compensation"),
        "descriptionPlain": job.get("descriptionPlain"),
        "address": job.get("address"),
    }

    posting = Posting(
        external_id=item.external_id,
        title=job["title"],
        url_posting=job["jobUrl"],
        fidelity="origin",
        state="open",
        closed_evidence=None,
        company_raw=feed.key,
        apply_url=job["applyUrl"],
        location_raw=job["location"],
        description_html=job["descriptionHtml"],
        requisition_id=None,
        employment_type=job["employmentType"],
        workplace_type_raw=job["workplaceType"],
        published_at=published_at,
        published_at_precision=published_at_precision,
        source_updated_at=None,
        deadline_at=None,
        source_active=job.get("isListed"),
        locations_extra=locations_extra,
        content_sections=[],
        source_extra=source_extra,
        origin_url=None,
    )

    return [posting]
