"""Jobvite -- HTML board, JSON-LD per job page. No usable public JSON API.

    https://jobs.jobvite.com/{key}/jobs   (HTML)
    job links: /{key}/job/{job_id}

Payload sample: notebooks/ATS-payloads.md#jobvite.

- The board page is server-rendered HTML -- parse job links off it, one item
  per job page, not one item per array element.
- Each job page embeds a schema.org JobPosting in
  `<script type="application/ld+json">`; that's the record source, same
  extraction as the jsonld.py source (@graph wrapper possible there too --
  check whether Jobvite uses it).
- A real JSON/XML API exists (api.jobvite.com/api/v2/job with key+secret;
  app.jobvite.com/CompanyJobs/Xml.aspx?c={token}) but needs credentials --
  out of scope unless feed.credentialed and feed.config carries them.

Item granularity, a judgment call: the thing actually fetched for each job is
raw HTML, but the JSON-LD block embedded in it is a clean, already-structured
sub-document, and the surrounding HTML boilerplate carries no job data worth
keeping verbatim. SOURCES.md's harvest-granularity table names "one JSON-LD
JobPosting block on the page" as the unit for the sibling jsonld.py source
(careers-site scrape); this source demuxes the same way, so `Item.payload` is
the *parsed* JobPosting dict and `Item.kind = "json"`, not the raw HTML page.
The board page itself is never carried as an item -- it is transport-layer,
peeled off to find item boundaries (job_ids), same as Greenhouse's list
response is peeled to find `jobs[]`.

@graph handling, empirically: the one real capture available
(notebooks/ATS-payloads.md#jobvite, jobs.jobvite.com/egnyte/job/o16vAfwW) is a
*bare* JobPosting object, not `@graph`-wrapped. Nothing rules out Jobvite using
`@graph` on some other tenant's page, though (jsonld.py's docstring flags the
same open question for the generic scraper), so `_extract_job_posting` below
handles both shapes defensively rather than assuming the bare-object shape
holds everywhere.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from xjobscore.net import Net, NetError, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = "https://jobs.jobvite.com/{key}/jobs"
JOB = "https://jobs.jobvite.com/{key}/job/{job_id}"

# Deliberately simple: this is a scrape source, not a general-purpose HTML
# parser. No dependency beyond stdlib `re` -- pyproject.toml carries no HTML
# parsing library (no BeautifulSoup), and reaching for `html.parser` here
# would buy structure this task doesn't need. Two narrow, targeted regexes:
# job links off the board page (scoped to this feed's own key, so an
# unrelated "similar jobs at other companies" widget on the same page can't
# leak foreign job_ids into this feed's items), and the ld+json script block
# off a job page.
LDJSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)


def _job_link_re(key: str) -> re.Pattern[str]:
    return re.compile(r"/" + re.escape(key) + r"/job/([A-Za-z0-9]+)")


def _extract_job_posting(raw_ldjson: str) -> dict | None:
    """Parse one `<script type="application/ld+json">` block's text into a
    JobPosting dict, or None if it does not parse or carry one.

    Handles both JSON-LD shapes a page might use: a bare object asserting
    `@type: "JobPosting"` directly (confirmed live for Jobvite -- see module
    docstring), and an `@graph`-wrapped document (`{"@context":..,
    "@graph": [...]}`) where the JobPosting is one entry among others. Shared
    logic with jsonld.py's own extraction by necessity, not by import --
    sources may not import siblings (SOURCES.md).
    """
    try:
        parsed = json.loads(raw_ldjson)
    except json.JSONDecodeError:
        return None

    if isinstance(parsed, dict) and isinstance(parsed.get("@graph"), list):
        for entry in parsed["@graph"]:
            if isinstance(entry, dict) and entry.get("@type") == "JobPosting":
                return entry
        return None

    if isinstance(parsed, dict) and parsed.get("@type") == "JobPosting":
        return parsed

    return None


async def harvest(feed: Feed, net: Net) -> Record:
    list_url = feed.config.get("url", LIST).format(key=feed.key)
    r = await net.get(list_url, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))

    job_ids = list(dict.fromkeys(_job_link_re(feed.key).findall(r.text)))

    items: list[Item] = []
    for job_id in job_ids:
        job_url = JOB.format(key=feed.key, job_id=job_id)
        try:
            job_page = await net.get(job_url)
        except NetError:
            return Record(items=items, truncated=True, http_status=r.status, validators=validators(r))

        match = LDJSON_RE.search(job_page.text)
        if not match:
            # One malformed/missing job page is not a transport failure --
            # skip it and keep going, don't sink the whole harvest.
            continue
        job_posting = _extract_job_posting(match.group(1))
        if job_posting is None:
            continue

        items.append(Item(external_id=str(job_id), payload=job_posting, kind="json"))

    return Record(items=items, http_status=r.status, validators=validators(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Jobvite JSON-LD `JobPosting` -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    job = item.payload

    url_posting = JOB.format(key=feed.key, job_id=item.external_id)

    # datePosted is date-only ("2026-07-15"), no time component -- precision
    # is 'date', never 'exact', so detect_latency_s is never fabricated out of
    # a bare calendar date.
    published_at = None
    published_at_precision = "unknown"
    date_posted = job.get("datePosted")
    if date_posted:
        published_at = datetime.fromisoformat(date_posted)
        published_at_precision = "date"

    # hiringOrganization is schema.org-legal as either a plain string or a
    # {"name": ...} object -- the one real sample uses a bare string, but
    # nothing guarantees every tenant does.
    hiring_organization = job.get("hiringOrganization")
    if isinstance(hiring_organization, dict):
        company_raw = hiring_organization.get("name")
    else:
        company_raw = hiring_organization

    job_locations = job.get("jobLocation") or []

    def _location_str(loc: dict) -> str | None:
        address = loc.get("address") or {}
        parts = [address.get("addressLocality"), address.get("addressRegion")]
        parts = [p for p in parts if p]
        if parts:
            return ", ".join(parts)
        return address.get("addressCountry")

    location_raw = None
    if job_locations:
        location_raw = _location_str(job_locations[0])

    locations_extra = []
    for loc in job_locations[1:]:
        extra = _location_str(loc)
        if extra:
            locations_extra.append(extra)

    # identifier duplicates external_id (the job_id) on every sample seen so
    # far -- only surface it as requisition_id when it actually differs, to
    # avoid redundant duplication of the same value under two names.
    identifier = job.get("identifier")
    requisition_id = identifier if identifier and identifier != item.external_id else None

    source_extra = {
        "industry": job.get("industry"),
        "baseSalary": job.get("baseSalary"),
    }

    posting = Posting(
        external_id=item.external_id,
        title=job.get("title"),
        url_posting=url_posting,
        apply_url=url_posting,
        company_raw=company_raw,
        location_raw=location_raw,
        description_html=job.get("description"),
        requisition_id=requisition_id,
        employment_type=job.get("employmentType"),
        workplace_type_raw=None,
        published_at=published_at,
        published_at_precision=published_at_precision,
        source_updated_at=None,
        deadline_at=None,
        source_active=None,
        locations_extra=locations_extra,
        content_sections=[],
        source_extra=source_extra,
    )

    return [posting]
