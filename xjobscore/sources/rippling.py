"""Rippling -- list is a bare array of summaries; detail call for the body.

    https://api.rippling.com/platform/api/ats/v1/board/{key}/jobs

Payload sample: notebooks/ATS-payloads.md#rippling.

- List has no description. Detail:
  /platform/api/ats/v1/board/{key}/jobs/{uuid} -- adds HTML `description`,
  workLocations[], employmentType, payRangeDetails, embedded jsonLd, etc.
- Board name (`key`) is in the careers URL ats.rippling.com/{key}.
- Schema has changed across captures: older responses have `id`,
  `locations[]`, `language`; current ones have `uuid`, `department{id,label}`,
  `workLocation`. Handle both shapes rather than assuming one.
- The list is a real bare array *with duplicate entries*: the same job
  (same `uuid`) is listed once per work location. `harvest` dedupes on the
  identifier before issuing detail calls, so one job -> one detail fetch ->
  one `Item`, no matter how many locations it was listed under.
- `envelope` is always `{}`: the list's response root is the array itself,
  no wrapper key, so there is nothing to carry that isn't already on an item
  (matches lever.py's reasoning).
"""

from __future__ import annotations

from datetime import datetime

from xjobscore.net import Net, NetError, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = "https://api.rippling.com/platform/api/ats/v1/board/{key}/jobs"
DETAIL = "https://api.rippling.com/platform/api/ats/v1/board/{key}/jobs/{id}"


def _summary_id(entry: dict) -> str | None:
    """A list-summary's identifier -- `uuid` (current shape) or `id` (old)."""
    identifier = entry.get("uuid") or entry.get("id")
    return str(identifier) if identifier is not None else None


async def harvest(feed: Feed, net: Net) -> Record:
    url = feed.config.get("url", LIST).format(key=feed.key)
    r = await net.get(url, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))

    entries = r.json()

    items: list[Item] = []
    seen: set[str] = set()
    for entry in entries:
        identifier = _summary_id(entry)
        if identifier is None or identifier in seen:
            # No usable identifier, or a duplicate listing of a job already
            # queued for a detail fetch (Rippling repeats a uuid once per
            # work location) -- either way, nothing new to fetch.
            continue
        seen.add(identifier)

        detail_url = DETAIL.format(key=feed.key, id=identifier)
        try:
            dr = await net.get(detail_url)
        except NetError:
            # Mid-loop failure: report what we already fetched rather than
            # discarding it. The list call itself is not wrapped -- failing
            # on page one has nothing to salvage.
            return Record(items=items, truncated=True, http_status=r.status, validators=validators(r))

        items.append(Item(external_id=identifier, payload=dr.json()))

    return Record(items=items, http_status=r.status, validators=validators(r))


def _location_fields(payload: dict) -> tuple[str | None, list]:
    """(location_raw, locations_extra) across both detail schema shapes.

    Current shape: `workLocations` is a flat list of strings. Older captures
    carry `locations` instead (same shape: a flat list). If neither list is
    present, fall back to the single `workLocation.label` (current) or
    `department`'s location-shaped fields are not location data, so the last
    resort is `None` -- there simply isn't a location on that payload.
    """
    work_locations = payload.get("workLocations")
    if isinstance(work_locations, list) and work_locations:
        return work_locations[0], list(work_locations[1:])

    locations = payload.get("locations")
    if isinstance(locations, list) and locations:
        return locations[0], list(locations[1:])

    work_location = payload.get("workLocation")
    if isinstance(work_location, dict) and work_location.get("label"):
        return work_location["label"], []

    return None, []


def _description_html(description) -> str | None:
    """Combine Rippling's sectioned description into one HTML body.

    The detail payload's `description` is a dict with (at least) `role` and
    `company` sub-keys, each already HTML -- not a single string. `role` is
    the job-specific content and is put first; `company` is the standing
    boilerplate ("About Rippling...") and is appended after it, which reads
    the way the hosted posting page itself orders the two sections. Older
    captures may carry a plain string instead of a dict; that string is used
    as-is. The raw dict is also kept verbatim in `source_extra` under
    `description_parts` so the role/company split is not lost to the
    concatenation.
    """
    if isinstance(description, dict):
        role = description.get("role") or ""
        company = description.get("company") or ""
        combined = role + company
        return combined or None
    if isinstance(description, str) and description:
        return description
    return None


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Rippling detail payload -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    payload = item.payload

    published_at = None
    published_at_precision = "unknown"
    if payload.get("createdOn"):
        published_at = datetime.fromisoformat(payload["createdOn"])
        published_at_precision = "exact"

    location_raw, locations_extra = _location_fields(payload)

    employment_type_field = payload.get("employmentType")
    employment_type = None
    if isinstance(employment_type_field, dict):
        employment_type = employment_type_field.get("label")

    board = payload.get("board")
    company_raw = payload.get("companyName")
    if not company_raw and isinstance(board, dict):
        company_raw = board.get("title")
    if not company_raw:
        company_raw = feed.key

    description = payload.get("description")
    description_html = _description_html(description)

    source_extra = {
        "description_parts": description,
        "department": payload.get("department"),
        "employmentType": employment_type_field,
        "activeJobApplication": payload.get("activeJobApplication"),
        "payRangeDetails": payload.get("payRangeDetails"),
        "jsonLd": payload.get("jsonLd"),
        "board": board,
        "eeocQuestionnaireEnabled": payload.get("eeocQuestionnaireEnabled"),
        "eeocQuestionnaireEnabledForJobPost": payload.get("eeocQuestionnaireEnabledForJobPost"),
        "applicationConfirmationTemplate": payload.get("applicationConfirmationTemplate"),
        "hasAIEvaluationsEnabled": payload.get("hasAIEvaluationsEnabled"),
        "unlistedFromSearch": payload.get("unlistedFromSearch"),
        "language": payload.get("language"),
    }

    identifier = payload.get("uuid") or payload.get("id")
    external_id = str(identifier) if identifier is not None else item.external_id

    posting = Posting(
        external_id=external_id,
        title=payload.get("name"),
        url_posting=payload.get("url"),
        fidelity="origin",
        state="open",
        closed_evidence=None,
        company_raw=company_raw,
        apply_url=None,
        location_raw=location_raw,
        description_html=description_html,
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
