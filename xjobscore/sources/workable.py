"""Workable -- one GET, full descriptions if you ask for them.

    https://apply.workable.com/api/v1/widget/accounts/{key}?details=true

Payload sample: notebooks/ATS-payloads.md#workable.

- Response is {name, description, jobs[]} -- every published job in one call.
  `details=true` is required or jobs come back as summaries only.
- url/shortlink is the hosted posting; application_url is the application page.
- Account name (`key`) is in the careers URL apply.workable.com/{key}
  (also seen as {key}.workable.com).
- Jobs here carry no `id` field -- `external_id` is `job["shortcode"]`.
- `name` and `description` exist only at the response root, on no job
  element, which is exactly why `Record.envelope` exists (see SOURCES.md).
  They land in `envelope`, never on an item's payload.
"""

from __future__ import annotations

from datetime import datetime, timezone

from xjobscore.net import Net, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = "https://apply.workable.com/api/v1/widget/accounts/{key}?details=true"


async def harvest(feed: Feed, net: Net) -> Record:
    url = feed.config.get("url", LIST).format(key=feed.key)
    r = await net.get(url, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))
    body = r.json()
    items = [Item(external_id=str(job["shortcode"]), payload=job) for job in body["jobs"]]
    envelope = {}
    if "name" in body:
        envelope["name"] = body["name"]
    if "description" in body:
        envelope["description"] = body["description"]
    return Record(items=items, envelope=envelope, http_status=r.status, validators=validators(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Workable job -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    job = item.payload

    # Determine company_raw: from envelope first, fall back to feed.key
    company_raw = envelope.get("name") if envelope else None
    if not company_raw:
        company_raw = feed.key

    # Parse published_on (date-only format)
    published_on = job.get("published_on")
    published_at = None
    published_at_precision = "unknown"
    if published_on:
        published_at = datetime.fromisoformat(published_on).replace(tzinfo=timezone.utc)
        published_at_precision = "date"

    # Parse created_at (date-only format)
    created_at = job.get("created_at")
    source_updated_at = None
    if created_at:
        source_updated_at = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc)

    # Build location_raw from city, state (region), country
    location_parts = []
    if job.get("city"):
        location_parts.append(job["city"])
    if job.get("state"):
        location_parts.append(job["state"])
    if job.get("country"):
        location_parts.append(job["country"])
    location_raw = ", ".join(location_parts) if location_parts else None

    # Build locations_extra from locations array
    locations_extra = []
    for loc in job.get("locations", []):
        city_or_country = loc.get("city") or loc.get("country")
        if city_or_country:
            locations_extra.append(city_or_country)

    # Handle requisition_id: treat empty string as None
    requisition_id = job.get("code")
    if not requisition_id:  # Covers None and empty string
        requisition_id = None

    # Build source_extra dict
    source_extra = {
        "telecommuting": job.get("telecommuting"),
        "department": job.get("department"),
        "education": job.get("education"),
        "experience": job.get("experience"),
        "function": job.get("function"),
        "industry": job.get("industry"),
    }

    posting = Posting(
        external_id=item.external_id,
        title=job["title"],
        url_posting=job["url"],
        fidelity="origin",
        state="open",
        closed_evidence=None,
        company_raw=company_raw,
        apply_url=job.get("application_url"),
        location_raw=location_raw,
        description_html=job.get("description"),
        requisition_id=requisition_id,
        employment_type=job.get("employment_type"),
        workplace_type_raw=None,
        published_at=published_at,
        published_at_precision=published_at_precision,
        source_updated_at=source_updated_at,
        deadline_at=None,
        source_active=None,
        locations_extra=locations_extra,
        content_sections=[],
        source_extra=source_extra,
        origin_url=None,
    )

    return [posting]
