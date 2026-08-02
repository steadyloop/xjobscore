"""GitHub internship lists (Simplify) -- one GET, one big JSON array, secondhand.

    https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json

Payload sample: notebooks/nATS-payloads.md#1-github-internship-lists.
Mirrors with the same schema: vanshb03/Summer2027-Internships,
cvrve/Summer2027-Internships -- same harvest, different repo path in feed.key.

- Plain unauthenticated GET, no pagination, no rate limit worth worrying about.
- Every item here is a copy, not the origin ATS listing -- `url` points at the
  actual Workday/Lever/iCIMS/etc. posting. Keep it in the raw payload as-is;
  fidelity and whether it's worth a re-fetch at origin are the parsing
  layer's call, not this function's.
- `active: false` is a real closure signal from a non-owning source. It lives
  in the raw payload -- don't drop it while demuxing, and never infer closure
  from an item's absence yourself (that stays reconcile's job, and only on
  feeds flagged complete+closes).
- `date_posted`/`date_updated` are Unix epoch seconds, and they're the
  aggregator's own sighting, not the employer's publish time. Keep them
  verbatim; what precision that implies is decided downstream.
- `terms[]` and `sponsorship` have no ATS equivalent -- keep them in the raw
  payload rather than treating them as noise to strip. `sponsorship` is ~99%
  the uninformative value "Other"; that's a downstream judgment call too.
- `company_name` is the raw, unresolved company string -- pass it through.
"""

from __future__ import annotations

from datetime import datetime, timezone

from xjobscore.net import Net, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = "https://raw.githubusercontent.com/{key}/dev/.github/scripts/listings.json"


async def harvest(feed: Feed, net: Net) -> Record:
    url = feed.config.get("url", LIST).format(key=feed.key)
    r = await net.get(url, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))
    body = r.json()
    items = [Item(external_id=str(row["id"]), payload=row) for row in body]
    return Record(items=items, http_status=r.status, validators=validators(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Simplify listing -> secondhand Posting. See ARCHITECTURE.md's parsing-layer section."""
    payload = item.payload

    # Extract locations: first one is location_raw, rest go to locations_extra
    locations = payload.get("locations", [])
    location_raw = locations[0] if locations else None
    locations_extra = locations[1:] if len(locations) > 1 else []

    # Determine state and closed_evidence based on active flag
    active = payload.get("active")
    if active is False:
        state = "closed"
        closed_evidence = "simplify: active=false"
    else:
        state = "open"
        closed_evidence = None

    # Convert date_updated (epoch seconds int) to datetime
    date_updated = payload.get("date_updated")
    source_updated_at = None
    if date_updated is not None:
        source_updated_at = datetime.fromtimestamp(date_updated, tz=timezone.utc)

    # Build source_extra with raw values, preserving epoch ints
    source_extra = {
        "source": payload.get("source"),
        "category": payload.get("category"),
        "terms": payload.get("terms"),
        "sponsorship": payload.get("sponsorship"),
        "degrees": payload.get("degrees"),
        "is_visible": payload.get("is_visible"),
        "company_url": payload.get("company_url"),
        "date_posted": payload.get("date_posted"),
        "date_updated": payload.get("date_updated"),
    }

    # Construct the Posting: secondhand aggregator, no description, pointer row
    posting = Posting(
        external_id=item.external_id,
        title=payload["title"],
        url_posting=payload["url"],
        fidelity="secondhand",
        state=state,
        closed_evidence=closed_evidence,
        company_raw=payload.get("company_name"),
        apply_url=None,
        location_raw=location_raw,
        description_html=None,
        requisition_id=None,
        employment_type=None,
        workplace_type_raw=None,
        published_at=None,
        published_at_precision="unknown",
        source_updated_at=source_updated_at,
        deadline_at=None,
        source_active=payload.get("active"),
        locations_extra=locations_extra,
        content_sections=[],
        source_extra=source_extra,
        origin_url=payload["url"],
    )

    return [posting]
