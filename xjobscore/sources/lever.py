"""Lever -- one GET, full descriptions, response is a bare JSON array.

    https://api.lever.co/v0/postings/{key}?mode=json

Payload sample: notebooks/ATS-payloads.md#lever.

- Full descriptions included; paginate with &limit=N&skip=N if a board is large
  (not done here -- this source issues exactly one call and never sets
  `truncated`).
- The response root IS the array -- there is no wrapper key, so `envelope` is
  always `{}`: a bare list carries no response-root facts.
- `id` is a uuid string; `external_id` is `str(job["id"])`.
- The title field is `text`, not `title` -- a trap for the parsing layer
  downstream, not for this function, which keeps the element verbatim either
  way.
- `createdAt` is epoch MILLISECONDS. Keep it exactly as received (an int) --
  do not convert to seconds or to a datetime.
- hostedUrl is the hosted posting; applyUrl is the application page.
- EU tenants live at jobs.eu.lever.co/{key} -> api.eu.lever.co (different host,
  not just a param). This module does no region detection; an operator points
  at the EU host by setting `feed.config["url"]`, which this function honours
  by using it in place of `LIST`.
- Site name (`key`) is in the careers URL jobs.lever.co/{key}.
"""

from __future__ import annotations

from datetime import datetime, timezone

from xjobscore.net import Net, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = "https://api.lever.co/v0/postings/{key}?mode=json"


async def harvest(feed: Feed, net: Net) -> Record:
    url = feed.config.get("url", LIST).format(key=feed.key)
    r = await net.get(url, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))

    body = r.json()
    items = [Item(external_id=str(job["id"]), payload=job) for job in body]
    return Record(items=items, http_status=r.status, validators=validators(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Lever posting -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    payload = item.payload

    # Convert createdAt from milliseconds to datetime
    created_at_ms = payload.get("createdAt")
    published_at = None
    published_at_precision = "unknown"

    if created_at_ms:
        published_at = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc)
        published_at_precision = "exact"

    # Extract categories safely
    categories = payload.get("categories", {})

    # Build content_sections from lists -- avoid name collision with item parameter
    content_sections = []
    for section in payload.get("lists", []):
        content_sections.append({
            "heading": section.get("text"),
            "html": section.get("content")
        })

    # Build source_extra dict
    source_extra = {
        "country": payload.get("country"),
        "additional": payload.get("additional"),
        "additionalPlain": payload.get("additionalPlain"),
        "descriptionPlain": payload.get("descriptionPlain"),
        "descriptionBodyPlain": payload.get("descriptionBodyPlain"),
        "department": categories.get("department"),
        "team": categories.get("team")
    }

    # Get description_html -- prefer descriptionBody, fall back to description
    description_html = payload.get("descriptionBody") or payload.get("description")

    posting = Posting(
        external_id=item.external_id,
        title=payload["text"],
        url_posting=payload["hostedUrl"],
        fidelity="origin",
        state="open",
        closed_evidence=None,
        company_raw=feed.key,
        apply_url=payload.get("applyUrl"),
        location_raw=categories.get("location"),
        description_html=description_html,
        requisition_id=None,
        employment_type=categories.get("commitment"),
        workplace_type_raw=payload.get("workplaceType"),
        published_at=published_at,
        published_at_precision=published_at_precision,
        source_updated_at=None,
        deadline_at=None,
        source_active=None,
        locations_extra=categories.get("allLocations", []),
        content_sections=content_sections,
        source_extra=source_extra,
        origin_url=None
    )

    return [posting]
