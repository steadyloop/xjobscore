"""SmartRecruiters -- paginated list of summaries, detail call for the body.

    https://api.smartrecruiters.com/v1/companies/{key}/postings

Payload sample: notebooks/ATS-payloads.md#smartrecruiters.

- Response is {offset, limit, totalFound, content[]}; content items have no
  description. Paginate with ?limit=100&offset=N.
- Detail: /v1/companies/{key}/postings/{posting_id} -- adds jobAd.sections
  (HTML blocks: companyDescription, jobDescription, qualifications,
  additionalInformation) plus postingUrl, applyUrl, referralUrl.
- Company identifier (`key`) is in the careers URL
  careers.smartrecruiters.com/{key} or jobs.smartrecruiters.com/{key}/{posting_id}.

One item is one posting's *detail* response (not the summary): the list call
only exists here to discover ids, so `Item.payload` is what
`.../postings/{id}` returned -- verbatim, per SOURCES.md -- and `parse` never
sees a bare summary.
"""

from __future__ import annotations

from datetime import datetime

from xjobscore.net import Net, NetError, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = "https://api.smartrecruiters.com/v1/companies/{key}/postings"
DETAIL = "https://api.smartrecruiters.com/v1/companies/{key}/postings/{posting_id}"

PAGE_SIZE = 100

# Section keys in the order SmartRecruiters presents them on the wire --
# used both for content_sections ordering and as the set of names we look for.
SECTION_NAMES = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")


async def harvest(feed: Feed, net: Net) -> Record:
    list_url = feed.config.get("url", LIST).format(key=feed.key)

    r = await net.get(list_url, params={"limit": PAGE_SIZE, "offset": 0}, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))

    # First response's status/validators ride along on every return path,
    # per SOURCES.md -- a later page's headers are not what feed.state wants.
    first_status = r.status
    first_validators = validators(r)

    body = r.json()
    total_found = body.get("totalFound", 0)
    summaries = list(body.get("content", []))

    items: list[Item] = []
    try:
        # Page through the rest of the list until we have totalFound summaries
        # or a page comes back short/empty.
        content = body.get("content", [])
        offset = len(summaries)
        while len(summaries) < total_found and content:
            page = await net.get(list_url, params={"limit": PAGE_SIZE, "offset": offset})
            page_body = page.json()
            content = page_body.get("content", [])
            if not content:
                break
            summaries.extend(content)
            offset += len(content)

        # One detail GET per summary -- this is where the real payload (and
        # jobAd.sections) comes from.
        for summary in summaries:
            posting_id = str(summary["id"])
            detail_url = DETAIL.format(key=feed.key, posting_id=posting_id)
            detail_r = await net.get(detail_url)
            items.append(Item(external_id=posting_id, payload=detail_r.json()))
    except NetError:
        return Record(items=items, truncated=True, http_status=first_status, validators=first_validators)

    return Record(items=items, http_status=first_status, validators=first_validators)


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """SmartRecruiters posting detail -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    job = item.payload

    company = job.get("company") or {}
    location = job.get("location") or {}
    industry = job.get("industry") or {}
    function = job.get("function") or {}
    department = job.get("department") or {}
    experience_level = job.get("experienceLevel") or {}
    type_of_employment = job.get("typeOfEmployment") or {}
    language = job.get("language") or {}

    released_date = job.get("releasedDate")
    published_at = None
    if released_date:
        # 3.12 stdlib fromisoformat accepts the trailing "Z" directly.
        published_at = datetime.fromisoformat(released_date)
    published_at_precision = "exact" if published_at else "unknown"

    # remote/hybrid are independent booleans on the wire; treat remote as
    # taking precedence when (implausibly) both are set. No vendor sample
    # observed with both true -- documented judgment call, not an
    # observed contract.
    workplace_type_raw = None
    if location.get("remote"):
        workplace_type_raw = "remote"
    elif location.get("hybrid"):
        workplace_type_raw = "hybrid"

    # jobAd.sections is the only body SmartRecruiters gives us; each entry is
    # {title, text} and several are routinely empty strings (see the real
    # sample in notebooks/ATS-payloads.md, posting 744000133907678, where
    # companyDescription/jobDescription/qualifications are all "").
    sections = ((job.get("jobAd") or {}).get("sections")) or {}
    content_sections = []
    for name in SECTION_NAMES:
        section = sections.get(name)
        if not section:
            continue
        text = section.get("text")
        if not text:
            continue
        content_sections.append({"heading": section.get("title"), "html": text})

    # No single "description" field exists on this vendor; concatenate the
    # section bodies (in job-description-first reading order) into
    # description_html AS WELL as keeping the structured content_sections,
    # since most consumers expect *a* body string. Order mirrors what a human
    # reads as "the posting": job description, then qualifications, then the
    # company blurb, then boilerplate.
    body_order = ("jobDescription", "qualifications", "companyDescription", "additionalInformation")
    body_parts = [sections[n]["text"] for n in body_order if sections.get(n) and sections[n].get("text")]
    description_html = "\n".join(body_parts) if body_parts else None

    source_extra = {
        "industry": industry or None,
        "function": function or None,
        "department": department or None,
        "experienceLevel": experience_level or None,
        "customField": job.get("customField"),
        "visibility": job.get("visibility"),
        "ref": job.get("ref"),
        "language": language or None,
        "referralUrl": job.get("referralUrl"),
    }

    posting = Posting(
        external_id=item.external_id,
        title=job["name"],
        url_posting=job.get("postingUrl"),
        fidelity="origin",
        state="open",
        closed_evidence=None,
        company_raw=company.get("name"),
        apply_url=job.get("applyUrl"),
        location_raw=location.get("fullLocation"),
        description_html=description_html,
        requisition_id=job.get("refNumber"),
        employment_type=type_of_employment.get("label"),
        workplace_type_raw=workplace_type_raw,
        published_at=published_at,
        published_at_precision=published_at_precision,
        source_updated_at=None,
        deadline_at=None,
        source_active=job.get("active"),
        locations_extra=[],
        content_sections=content_sections,
        source_extra=source_extra,
        origin_url=None,
    )

    return [posting]


def datetime_fromisoformat(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
