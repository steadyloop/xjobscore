"""BambooHR -- list of summaries, one detail call per posting.

    https://{key}.bamboohr.com/careers/list

Payload sample: notebooks/ATS-payloads.md#bamboohr.

- List: result[] summaries, no description. Detail:
  /careers/{id}/detail -> result.jobOpening, which has the HTML description.
- Job URL: result.jobOpening.jobOpeningShareUrl
  (= https://{key}.bamboohr.com/careers/{id}).
- Only the LIST url honors `feed.config.get("url", LIST)`; the per-posting
  detail call is always built from `feed.key` and the list item's `id` -- an
  operator override only ever points at the board, never at one job.
- `external_id` is the list item's `id`; the detail response's
  `jobOpening` carries no `id` field of its own, only `jobOpeningShareUrl`
  and friends, so the id has to travel from the list call into the Item.
- Conditional GET (`state`) applies to the list call only. The detail calls
  are unconditional -- there is no cheap way to know a single posting's
  detail changed without fetching it.
- If a detail call fails after the list call already succeeded, that is
  mid-fetch, not page-one: catch `NetError`, keep the items already built,
  and return `truncated=True` with the list response's status/validators
  (SOURCES.md "two rules that are easy to get wrong").
- No company name field appears anywhere in either response; `parse` uses
  `feed.key` as `company_raw`, the same fallback Lever and Breezy use for
  boards with no company element.
"""

from __future__ import annotations

from datetime import datetime, timezone

from xjobscore.net import Net, NetError, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = "https://{key}.bamboohr.com/careers/list"
DETAIL = "https://{key}.bamboohr.com/careers/{id}/detail"


async def harvest(feed: Feed, net: Net) -> Record:
    url = feed.config.get("url", LIST).format(key=feed.key)
    r = await net.get(url, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))

    body = r.json()
    summaries = body.get("result", [])

    items: list[Item] = []
    try:
        for summary in summaries:
            job_id = summary["id"]
            detail_url = DETAIL.format(key=feed.key, id=job_id)
            detail_r = await net.get(detail_url)
            job = detail_r.json()["result"]["jobOpening"]
            items.append(Item(external_id=str(job_id), payload=job))
    except NetError:
        return Record(items=items, truncated=True, http_status=r.status, validators=validators(r))

    return Record(items=items, http_status=r.status, validators=validators(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """BambooHR `jobOpening` -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    job = item.payload

    # datePosted is date-only ("2026-06-10"), never a full timestamp.
    published_at = None
    published_at_precision = "unknown"
    date_posted = job.get("datePosted")
    if date_posted:
        published_at = datetime.fromisoformat(date_posted).replace(tzinfo=timezone.utc)
        published_at_precision = "date"

    # Build location_raw from city, state -- BambooHR never carries a single
    # pre-joined location string.
    location = job.get("location") or {}
    location_parts = [p for p in (location.get("city"), location.get("state")) if p]
    location_raw = ", ".join(location_parts) if location_parts else None

    # jobOpeningStatus is a source-stated fact ("Open"/"Closed"/...), not an
    # inference -- safe to map straight onto source_active.
    source_active = None
    job_opening_status = job.get("jobOpeningStatus")
    if job_opening_status is not None:
        source_active = job_opening_status == "Open"

    source_extra = {
        "jobOpeningStatus": job_opening_status,
        "jobCategoryId": job.get("jobCategoryId"),
        "departmentId": job.get("departmentId"),
        "departmentLabel": job.get("departmentLabel"),
        "atsLocation": job.get("atsLocation"),
        "compensation": job.get("compensation"),
        "minimumExperience": job.get("minimumExperience"),
        "locationType": job.get("locationType"),
        "seekPromoted": job.get("seekPromoted"),
        "isRemote": job.get("isRemote"),
    }

    posting = Posting(
        external_id=item.external_id,
        title=job.get("jobOpeningName", ""),
        url_posting=job.get("jobOpeningShareUrl", ""),
        fidelity="origin",
        state="open",
        closed_evidence=None,
        company_raw=feed.key,
        apply_url=None,
        location_raw=location_raw,
        description_html=job.get("description"),
        requisition_id=None,
        employment_type=job.get("employmentStatusLabel"),
        workplace_type_raw=None,
        published_at=published_at,
        published_at_precision=published_at_precision,
        source_updated_at=None,
        deadline_at=None,
        source_active=source_active,
        locations_extra=[],
        content_sections=[],
        source_extra=source_extra,
        origin_url=None,
    )

    return [posting]
