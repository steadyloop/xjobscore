"""iCIMS -- one call per page gets everything, no separate detail call.

    https://careers.{key}.com/api/jobs?page=1

Payload sample: notebooks/ATS-payloads.md#icims.

- Each jobs[].data is the full posting: HTML description, salary, location.
  Paginate with ?page=N, 10 items per page.
- Job URL: jobs[].data.meta_data.canonical_url. Apply page:
  jobs[].data.apply_url.
- Legacy careers-{tenant}.icims.com portals are HTML-only and usually redirect
  to this CXP API -- if a probe 404s, check for the redirect target first.
- Root list is `body["jobs"]`; `external_id` is `str(job["req_id"])` (the
  employer-assigned requisition id -- equal to `slug` on every fixture job
  seen so far, but `req_id` is the field this vendor calls out as the stable
  identifier, so it's preferred over `slug`).
- `body["totalCount"]`, when present, becomes `envelope = {"totalCount": N}`;
  when absent, `envelope = {}`. It is not carried on any single job.
- Pagination ends the first time a page returns fewer than 10 items in
  `jobs[]` (including an empty page) -- that is a normal short final page,
  not truncation. `truncated` is reserved for a `NetError` that interrupts
  the loop after page one; the first page's failure is left to propagate
  (nothing to salvage). Conditional GET (`state=feed.state`) is only sent on
  page one -- a 304 there means nothing changed and short-circuits the whole
  poll; later pages don't repeat it.
"""

from __future__ import annotations

from datetime import datetime

from xjobscore.net import Net, NetError, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = "https://careers.{key}.com/api/jobs?page={page}"
PAGE_SIZE = 10


async def harvest(feed: Feed, net: Net) -> Record:
    url_template = feed.config.get("url", LIST)

    first_url = url_template.format(key=feed.key, page=1)
    r = await net.get(first_url, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))

    body = r.json()
    jobs = body.get("jobs", [])
    items = [Item(external_id=str(j["data"]["req_id"]), payload=j["data"]) for j in jobs]
    envelope = {"totalCount": body["totalCount"]} if "totalCount" in body else {}

    page = 2
    while len(jobs) >= PAGE_SIZE:
        page_url = url_template.format(key=feed.key, page=page)
        try:
            page_body = await net.json(page_url)
        except NetError:
            return Record(
                items=items,
                envelope=envelope,
                truncated=True,
                http_status=r.status,
                validators=validators(r),
            )

        jobs = page_body.get("jobs", [])
        items.extend(Item(external_id=str(j["data"]["req_id"]), payload=j["data"]) for j in jobs)
        page += 1

    return Record(items=items, envelope=envelope, http_status=r.status, validators=validators(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """iCIMS `jobs[].data` -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    job = item.payload

    published_at = None
    if job.get("posted_date"):
        published_at = datetime.fromisoformat(job["posted_date"])
    published_at_precision = "exact" if published_at else "unknown"

    source_updated_at = None
    if job.get("update_date"):
        source_updated_at = datetime.fromisoformat(job["update_date"])

    meta_data = job.get("meta_data") or {}
    url_posting = meta_data.get("canonical_url") or job.get("apply_url")

    location_raw = job.get("short_location")
    if not location_raw:
        city = job.get("city")
        state = job.get("state")
        if city and state:
            location_raw = f"{city}, {state}"
        else:
            location_raw = city or state

    locations_extra = []
    for loc in job.get("additional_locations") or []:
        city = loc.get("city")
        state = loc.get("state")
        if city and state:
            locations_extra.append(f"{city}, {state}")
        elif city or state:
            locations_extra.append(city or state)
        elif loc.get("location_name"):
            locations_extra.append(loc["location_name"])

    company_raw = job.get("hiring_organization") or feed.key

    source_extra = {
        "slug": job.get("slug"),
        "language": job.get("language"),
        "languages": job.get("languages"),
        "client_code": job.get("client_code"),
        "location_name": job.get("location_name"),
        "street_address": job.get("street_address"),
        "country": job.get("country"),
        "country_code": job.get("country_code"),
        "postal_code": job.get("postal_code"),
        "location_type": job.get("location_type"),
        "latitude": job.get("latitude"),
        "longitude": job.get("longitude"),
        "categories": job.get("categories"),
        "category": job.get("category"),
        "tags1": job.get("tags1"),
        "tags2": job.get("tags2"),
        "tags3": job.get("tags3"),
        "tags4": job.get("tags4"),
        "department": job.get("department"),
        "salary_value": job.get("salary_value"),
        "salary_min_value": job.get("salary_min_value"),
        "salary_max_value": job.get("salary_max_value"),
        "qualifications": job.get("qualifications"),
        "responsibilities": job.get("responsibilities"),
        "internal": job.get("internal"),
        "external": job.get("external"),
        "searchable": job.get("searchable"),
        "applyable": job.get("applyable"),
        "li_easy_applyable": job.get("li_easy_applyable"),
        "ats_code": job.get("ats_code"),
        "hiring_flow_name": job.get("hiring_flow_name"),
        "meta_data": meta_data,
        "create_date": job.get("create_date"),
        "full_location": job.get("full_location"),
        "short_location": job.get("short_location"),
        "multipleLocations": job.get("multipleLocations"),
    }

    posting = Posting(
        external_id=item.external_id,
        title=job["title"],
        url_posting=url_posting,
        apply_url=job.get("apply_url"),
        company_raw=company_raw,
        location_raw=location_raw,
        description_html=job.get("description"),
        requisition_id=job.get("req_id"),
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
    )

    return [posting]
