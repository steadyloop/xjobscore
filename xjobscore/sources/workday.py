"""Workday -- POST, paginated, list items are summaries needing a detail call.

    POST https://{tenant}.{wd_host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

Payload sample: notebooks/ATS-payloads.md#workday.

- Response is {total, jobPostings[]}; paginate with `offset`, limit caps at 20.
  A page that comes back short of `total` and stops early is a partial fetch,
  not evidence the rest closed.
- Detail: GET /wday/cxs/{tenant}/{site}{externalPath} -- externalPath comes
  from each list item; full description lives only in the detail response.
- Hosted posting page = https://{tenant}.{wd_host}.myworkdayjobs.com/{site}{externalPath}
  (also returned as `externalUrl` in the detail).
- tenant, wd_host (wd1/wd3/wd5/wd12/...) and site are in the careers URL
  {tenant}.{wd_host}.myworkdayjobs.com/{site} -- feed.key needs to carry all
  three, so this module encodes `key` as "tenant/wd_host/site"
  (e.g. "nvidia/wd5/NVIDIAExternalCareerSite"), split on "/" in `harvest`.

Note on conditional GET: the list call is a POST, and `Net.post` takes no
`state` param -- there is no conditional-POST support in this codebase's
transport, and Workday's list endpoint gives no ETag/Last-Modified worth
reading anyway. So every poll re-fetches the full list from offset 0; the
per-item detail GET is the natural rate limiter here, not a 304 short
circuit. `Record.validators` is left as `{}` -- there is nothing to write
back into `feed.state` for this source.
"""

from __future__ import annotations

from datetime import datetime

from xjobscore.net import Net, NetError
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIMIT = 20


async def harvest(feed: Feed, net: Net) -> Record:
    tenant, wd_host, site = feed.key.split("/", 2)
    base = f"https://{tenant}.{wd_host}.myworkdayjobs.com"
    list_url = f"{base}/wday/cxs/{tenant}/{site}/jobs"

    postings: list[dict] = []
    offset = 0
    try:
        while True:
            body = {"appliedFacets": {}, "limit": LIMIT, "offset": offset, "searchText": ""}
            r = await net.post(list_url, json=body)
            page = r.json()
            job_postings = page.get("jobPostings", [])
            postings.extend(job_postings)
            total = page.get("total", 0)
            if len(job_postings) < LIMIT or len(postings) >= total:
                break
            offset += LIMIT
    except NetError:
        return Record(truncated=True)

    items: list[Item] = []
    try:
        for job in postings:
            external_path = job.get("externalPath", "")
            detail_url = f"{base}/wday/cxs/{tenant}/{site}{external_path}"
            dr = await net.get(detail_url)
            detail = dr.json()
            info = detail.get("jobPostingInfo", {}) or {}
            external_id = str(info.get("jobReqId") or info.get("jobPostingId") or "")
            items.append(Item(external_id=external_id, payload=detail))
    except NetError:
        return Record(items=items, truncated=True, http_status=r.status)

    return Record(items=items, http_status=r.status)


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Workday detail response -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    body = item.payload
    info = body.get("jobPostingInfo", {}) or {}
    org = body.get("hiringOrganization", {}) or {}

    # `postedOn` ("Posted Today" / "Posted Yesterday") is relative text, not a
    # real timestamp -- never fed into published_at. `startDate` is the only
    # date-shaped field Workday gives us here (e.g. "2026-07-30"), so it is
    # used instead, at "date" precision (no time-of-day component).
    published_at = None
    published_at_precision = "unknown"
    start_date = info.get("startDate")
    if start_date:
        try:
            published_at = datetime.fromisoformat(start_date)
            published_at_precision = "date"
        except (ValueError, TypeError):
            published_at = None
            published_at_precision = "unknown"

    source_extra = {
        "country": info.get("country"),
        "jobRequisitionLocation": info.get("jobRequisitionLocation"),
        "canApply": info.get("canApply"),
        "posted": info.get("posted"),
        "questionnaireId": info.get("questionnaireId"),
        "similarJobs": body.get("similarJobs"),
        "jobPostingSiteId": info.get("jobPostingSiteId"),
        "includeResumeParsing": info.get("includeResumeParsing"),
        "id": info.get("id"),
        "postedOn": info.get("postedOn"),
        "hiringOrganizationUrl": org.get("url"),
        "userAuthenticated": body.get("userAuthenticated"),
    }

    posting = Posting(
        external_id=item.external_id,
        title=info["title"],
        url_posting=info["externalUrl"],
        apply_url=info.get("externalUrl"),
        company_raw=org.get("name"),
        location_raw=info.get("location"),
        description_html=info.get("jobDescription"),
        requisition_id=info.get("jobReqId"),
        employment_type=info.get("timeType"),
        workplace_type_raw=None,
        published_at=published_at,
        published_at_precision=published_at_precision,
        source_updated_at=None,
        deadline_at=None,
        source_active=None,
        locations_extra=[],
        content_sections=[],
        source_extra=source_extra,
    )

    return [posting]
