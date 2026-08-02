"""Oracle Recruiting Cloud (ORC) -- classic Taleo is retired, treat Oracle = ORC.

    https://{host}.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions
        ?onlyData=true&finder=findReqs;siteNumber={site},
         facetsList=LOCATIONS;WORK_LOCATIONS;WORKPLACE_TYPES;TITLES;CATEGORIES;
                    ORGANIZATIONS;POSTING_DATES;FLEX_FIELDS,
         limit=25,sortBy=POSTING_DATES_DESC

Payload sample: notebooks/ATS-payloads.md#oracletaleo.

- The `facetsList` param is required -- omit it and requisitionList comes back
  empty, which looks like a legitimate zero-postings response but isn't.
- List items are summaries: items[0].requisitionList[], total count in
  items[0].TotalJobsCount.
- Detail: .../recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;siteNumber={site},Id="{Id}"
  -- adds ExternalDescriptionStr, ExternalQualificationsStr,
  ExternalResponsibilitiesStr, locations, flex fields.
- host and site number (`key`) are in the careers URL
  {host}.oraclecloud.com/hcmUI/CandidateExperience/en/sites/{site}
  (e.g. careers.oracle.com -> eeho.fa.us2.oraclecloud.com, CX_45001).
- {company}.taleo.net/careersection/... is dead; do not build a fallback for it.

Two-step source: the list call returns summaries only, so each requisition
needs a follow-up detail fetch for its description. `key` encodes both `host`
and `site` as a single `/`-separated token, e.g. "eeho.fa.us2/CX_45001" --
chosen because `key`'s whole job is "locate the endpoint" and a feed is one
token to copy around, not two coupled fields that could drift apart. An
operator may instead set `config["host"]` / `config["site"]` explicitly,
which wins when present.
"""

from __future__ import annotations

from datetime import datetime

from xjobscore.net import Net, NetError, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = (
    "https://{host}.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    "?onlyData=true&finder=findReqs;siteNumber={site},"
    "facetsList=LOCATIONS;WORK_LOCATIONS;WORKPLACE_TYPES;TITLES;CATEGORIES;ORGANIZATIONS;POSTING_DATES;FLEX_FIELDS,"
    "limit=25,sortBy=POSTING_DATES_DESC"
)

DETAIL = (
    "https://{host}.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
    '?expand=all&onlyData=true&finder=ById;siteNumber={site},Id="{id}"'
)

CAREERS = "https://{host}.oraclecloud.com/hcmUI/CandidateExperience/en/sites/{site}/job/{id}/"


def _host_site(feed: Feed) -> tuple[str, str]:
    """(host, site), from `feed.config` if the operator supplied them, else
    parsed out of `feed.key`."""
    host = feed.config.get("host")
    site = feed.config.get("site")
    if host and site:
        return host, site
    host, _, site = feed.key.partition("/")
    return host, site


async def harvest(feed: Feed, net: Net) -> Record:
    host, site = _host_site(feed)
    url = LIST.format(host=host, site=site)
    r = await net.get(url, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))

    body = r.json()
    root_items = body.get("items") or []
    root = root_items[0] if root_items else {}
    summaries = root.get("requisitionList") or []
    envelope = {"TotalJobsCount": root["TotalJobsCount"]} if "TotalJobsCount" in root else {}

    # Empty requisitionList is a legitimate empty board (not an error) -- the
    # loop below simply does not run and `items` stays empty.
    items: list[Item] = []
    try:
        for summary in summaries:
            req_id = summary["Id"]
            detail_url = DETAIL.format(host=host, site=site, id=req_id)
            dr = await net.get(detail_url)
            detail_body = dr.json()
            detail_items = detail_body.get("items") or []
            detail = detail_items[0] if detail_items else {}
            # Nothing from either call is dropped: the summary carries facet
            # context the detail lacks, the detail carries the descriptions.
            payload = {**summary, **detail}
            items.append(Item(external_id=str(req_id), payload=payload))
    except NetError:
        return Record(
            items=items,
            envelope=envelope,
            truncated=True,
            http_status=r.status,
            validators=validators(r),
        )

    return Record(items=items, envelope=envelope, http_status=r.status, validators=validators(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Oracle ORC merged summary+detail requisition -> Posting."""
    job = item.payload
    host, site = _host_site(feed)

    req_id = job.get("Id", item.external_id)
    url_posting = CAREERS.format(host=host, site=site, id=req_id)

    # PostedDate ("2026-07-30") is a bare date -> precision "date". The
    # detail call's ExternalPostedStartDate carries a time component and is
    # used as a fallback when PostedDate is absent, at "exact" precision.
    published_at = None
    published_at_precision = "unknown"
    posted_date = job.get("PostedDate")
    if posted_date:
        try:
            published_at = datetime.fromisoformat(posted_date)
            published_at_precision = "exact" if "T" in posted_date else "date"
        except ValueError:
            published_at = None
    if published_at is None and job.get("ExternalPostedStartDate"):
        try:
            published_at = datetime.fromisoformat(job["ExternalPostedStartDate"])
            published_at_precision = "exact"
        except ValueError:
            published_at = None

    # Oracle has no brand/company field on the requisition itself.
    # `LegalEmployer` looks like a fit but is a legal-entity name, not the
    # employer brand (documented as a "false friend" in
    # notebooks/ATS-payloads.md's alignment notes) -- using it would mislabel
    # postings under e.g. a shell-company name instead of "Oracle". Absent an
    # operator-supplied brand, leave company_raw unset rather than guess wrong.
    company_raw = feed.config.get("company_raw")

    locations_extra = [loc for loc in (job.get("secondaryLocations") or []) if loc]

    source_extra = {
        "HotJobFlag": job.get("HotJobFlag"),
        "TrendingFlag": job.get("TrendingFlag"),
        "BeFirstToApplyFlag": job.get("BeFirstToApplyFlag"),
        "BusinessUnit": job.get("BusinessUnit"),
        "BusinessUnitId": job.get("BusinessUnitId"),
        "LegalEmployer": job.get("LegalEmployer"),
        "LegalEmployerId": job.get("LegalEmployerId"),
        "Organization": job.get("Organization"),
        "OrganizationId": job.get("OrganizationId"),
        "Category": job.get("Category"),
        "RequisitionType": job.get("RequisitionType"),
        "RequisitionId": job.get("RequisitionId"),
        "JobFamily": job.get("JobFamily"),
        "JobFunction": job.get("JobFunction"),
        "ManagerLevel": job.get("ManagerLevel"),
        "NumberOfOpenings": job.get("NumberOfOpenings"),
        "MediaThumbURL": job.get("MediaThumbURL"),
        "ShortDescriptionStr": job.get("ShortDescriptionStr"),
        "CorporateDescriptionStr": job.get("CorporateDescriptionStr"),
        "InternalDescriptionStr": job.get("InternalDescriptionStr"),
        "ExternalQualificationsStr": job.get("ExternalQualificationsStr"),
        "InternalQualificationsStr": job.get("InternalQualificationsStr"),
        "InternalResponsibilitiesStr": job.get("InternalResponsibilitiesStr"),
        "requisitionFlexFields": job.get("requisitionFlexFields"),
        "skills": job.get("skills"),
    }

    posting = Posting(
        external_id=item.external_id,
        title=job["Title"],
        url_posting=url_posting,
        company_raw=company_raw,
        location_raw=job.get("PrimaryLocation"),
        description_html=job.get("ExternalDescriptionStr"),
        requisition_id=str(req_id) if req_id is not None else None,
        employment_type=job.get("JobType") or job.get("WorkerType"),
        workplace_type_raw=job.get("WorkplaceType") or None,
        published_at=published_at,
        published_at_precision=published_at_precision,
        locations_extra=locations_extra,
        source_extra=source_extra,
    )

    return [posting]
