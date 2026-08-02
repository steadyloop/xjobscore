"""Greenhouse -- one GET, full descriptions, no separate detail endpoint.

    https://boards-api.greenhouse.io/v1/boards/{key}/jobs?content=true

Payload sample: notebooks/ATS-payloads.md#greenhouse.

- content=true is what adds `content` (the HTML body) plus `departments`,
  `offices` and the `ai_*` fields; without it the job is a bare summary and
  there is no second endpoint that fills it in cheaply. Note
  notebooks/ATS-payloads.md#greenhouse writes the URL as
  `.../jobs/content=true` -- that's a typo in the notebook, it is a query
  param (`?content=true`), confirmed live against a real board.
- `content` is *double*-escaped HTML (`&lt;h2&gt;`) -- store it exactly as
  received; unescaping exactly once is the parsing layer's job, not this
  function's.
- absolute_url is the hosted posting and the application page both.
- Board token (`key`) is in the careers URL boards.greenhouse.io/{token} or
  job-boards.greenhouse.io/{token}.
- Root list is `body["jobs"]`; `external_id` is `str(job["id"])`.
- `body["meta"]` (e.g. `{"total": N}`), when present, becomes
  `envelope = {"meta": body["meta"]}`; when absent, `envelope = {}`. It is not
  carried on any single job.
- Single call, one page: `truncated` is never set here.
"""

from __future__ import annotations

import html
from datetime import datetime

from xjobscore.net import Net, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = "https://boards-api.greenhouse.io/v1/boards/{key}/jobs?content=true"


async def harvest(feed: Feed, net: Net) -> Record:
    url = feed.config.get("url", LIST).format(key=feed.key)
    r = await net.get(url, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))

    body = r.json()
    items = [Item(external_id=str(job["id"]), payload=job) for job in body["jobs"]]
    envelope = {"meta": body["meta"]} if "meta" in body else {}
    return Record(items=items, envelope=envelope, http_status=r.status, validators=validators(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Greenhouse `job` -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    job = item.payload

    # Parse datetime fields
    published_at = None
    if job.get("first_published"):
        published_at = datetime.fromisoformat(job["first_published"])

    published_at_precision = "exact" if published_at else "unknown"

    source_updated_at = None
    if job.get("updated_at"):
        source_updated_at = datetime.fromisoformat(job["updated_at"])

    deadline_at = None
    if job.get("application_deadline"):
        deadline_at = datetime.fromisoformat(job["application_deadline"])

    # Handle description HTML (unescape exactly once)
    description_html = None
    if job.get("content"):
        description_html = html.unescape(job["content"])

    # Handle requisition_id - filter out junk sentinel value
    requisition_id = job.get("requisition_id")
    if requisition_id and " ".join(requisition_id.split()).lower() == "see opening id":
        requisition_id = None

    # Handle location
    location_raw = None
    location = job.get("location")
    if location and isinstance(location, dict):
        location_raw = location.get("name")

    # Handle offices (locations_extra)
    locations_extra = [o["name"] for o in job.get("offices", []) if o.get("name")]

    # Build source_extra dict with long-tail fields
    source_extra = {
        "internal_job_id": job.get("internal_job_id"),
        "metadata": job.get("metadata"),
        "language": job.get("language"),
        "departments": job.get("departments"),
        "data_compliance": job.get("data_compliance"),
        "ai_disclaimer": job.get("ai_disclaimer"),
        "include_ai_disclaimer": job.get("include_ai_disclaimer"),
        "ai_opt_out_request_url": job.get("ai_opt_out_request_url"),
    }

    posting = Posting(
        external_id=item.external_id,
        title=job["title"],
        url_posting=job["absolute_url"],
        apply_url=job["absolute_url"],
        company_raw=job.get("company_name"),
        location_raw=location_raw,
        description_html=description_html,
        requisition_id=requisition_id,
        employment_type=None,
        workplace_type_raw=None,
        published_at=published_at,
        published_at_precision=published_at_precision,
        source_updated_at=source_updated_at,
        deadline_at=deadline_at,
        source_active=None,
        locations_extra=locations_extra,
        content_sections=[],
        source_extra=source_extra,
    )

    return [posting]
