"""schema.org JSON-LD scraper -- generic careers-page adapter, origin fidelity.

    GET {careers_url}  (from feed.key)
    -> parse <script type="application/ld+json">
    -> keep blocks where @type == "JobPosting"

Payload sample: notebooks/nATS-payloads.md#2-schemaorg-json-ld-scraper.

- Verify before trusting: most careers pages carry JSON-LD that is *not* a
  JobPosting (site nav, WebPage/Yoast SEO boilerplate). A page with zero
  JobPosting blocks is a legitimate empty result, not an error.
- Handle @graph wrappers: a block may be {"@context":.., "@graph":[...]}
  rather than a bare object -- walk the graph, don't assume `d["@type"]`
  exists at the top level.
- Fields when present: title, description, datePosted, validThrough,
  employmentType, hiringOrganization, jobLocation, baseSalary.
- One page = the whole board for this feed; if the operator has confirmed
  that and flagged the feed complete+closes, a JobPosting missing from a
  later fetch is reconcile's business, not this function's.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from xjobscore.net import Net, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

# Pragmatic regex extraction, same reasoning as jobvite.py: this is one block
# scan over a page we do not control the markup of, not a general HTML parser
# problem. DOTALL so a block's JSON can span multiple lines (it always does
# once pretty-printed).
SCRIPT_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)


def _job_postings(blob: dict) -> list[dict]:
    """Pull JobPosting-typed dicts out of one parsed ld+json block.

    A block is either a bare object (`@type` at the top level) or an
    `@graph`-wrapped list of objects (Yoast and friends). Either shape can
    hold zero, one, or -- rarely -- multiple JobPosting entries.
    """
    graph = blob.get("@graph")
    if isinstance(graph, list):
        return [entry for entry in graph if isinstance(entry, dict) and entry.get("@type") == "JobPosting"]
    if blob.get("@type") == "JobPosting":
        return [blob]
    return []


def _external_id(job: dict) -> str:
    """Resolve a stable-enough id for a JobPosting block.

    JSON-LD carries no guaranteed id, so this is a judgment call, in order of
    preference:
      1. `identifier` -- schema.org allows either a bare string or a
         PropertyValue dict ({"@type": "PropertyValue", "value": ...});
         handle both.
      2. a hash of `url`, when the block names its own canonical URL.
      3. a content hash of the whole block, as a last resort so external_id
         is never empty (two structurally-identical postings on one page,
         with no identifier and no url, would collide here -- that is a
         real limitation of the fallback, not a bug).
    """
    identifier = job.get("identifier")
    if isinstance(identifier, dict):
        value = identifier.get("value")
        if value:
            return str(value)
    elif identifier:
        return str(identifier)

    url = job.get("url")
    if url:
        return hashlib.sha256(str(url).encode()).hexdigest()[:16]

    return hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()[:16]


async def harvest(feed: Feed, net: Net) -> Record:
    r = await net.get(feed.key, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))

    jobs: list[dict] = []
    for raw_block in SCRIPT_RE.findall(r.text):
        try:
            blob = json.loads(raw_block)
        except json.JSONDecodeError:
            # Malformed JSON-LD on someone else's page is not our failure --
            # skip this one block, keep going.
            continue
        if not isinstance(blob, dict):
            continue
        jobs.extend(_job_postings(blob))

    items = [Item(external_id=_external_id(job), payload=job, kind="json") for job in jobs]
    return Record(items=items, http_status=r.status, validators=validators(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """schema.org JobPosting -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    job = item.payload

    def _parse_dt(value: object) -> tuple[datetime | None, str]:
        """Try datetime.fromisoformat; precision is 'date' with no 'T', else 'exact'."""
        if not value or not isinstance(value, str):
            return None, "unknown"
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None, "unknown"
        precision = "date" if "T" not in value else "exact"
        return dt, precision

    published_at, published_at_precision = _parse_dt(job.get("datePosted"))
    deadline_at, _ = _parse_dt(job.get("validThrough"))

    # hiringOrganization is a plain string on some pages, an Organization dict
    # ({"name": ...}) on others -- same reasoning as jobvite.py.
    hiring_org = job.get("hiringOrganization")
    if isinstance(hiring_org, dict):
        company_raw = hiring_org.get("name")
    else:
        company_raw = hiring_org

    # jobLocation[0].address -> a readable string built from whichever
    # PostalAddress fields are present (schema.org does not require all of
    # addressLocality/addressRegion/addressCountry).
    location_raw = None
    locations_extra: list = []
    job_location = job.get("jobLocation")
    if isinstance(job_location, list) and job_location:
        def _place_str(place: object) -> str | None:
            if not isinstance(place, dict):
                return None
            address = place.get("address")
            if not isinstance(address, dict):
                return None
            parts = [
                address.get(k)
                for k in ("addressLocality", "addressRegion", "addressCountry")
                if address.get(k)
            ]
            return ", ".join(parts) if parts else None

        location_raw = _place_str(job_location[0])
        locations_extra = [s for s in (_place_str(p) for p in job_location[1:]) if s]

    # identifier is consumed by harvest's external_id fallback chain only
    # when no url is present and it wasn't the source of external_id itself;
    # keep it in source_extra verbatim regardless, it's cheap and harmless.
    source_extra = {
        "baseSalary": job.get("baseSalary"),
        "identifier": job.get("identifier"),
        "industry": job.get("industry"),
        "validThrough_raw": job.get("validThrough"),
    }

    posting = Posting(
        external_id=item.external_id,
        title=job.get("title"),
        # Some minimal JobPosting blocks don't carry their own canonical URL;
        # fall back to the careers page itself (feed.key) rather than None.
        url_posting=job.get("url") or feed.key,
        company_raw=company_raw,
        location_raw=location_raw,
        description_html=job.get("description"),
        employment_type=job.get("employmentType"),
        published_at=published_at,
        published_at_precision=published_at_precision,
        deadline_at=deadline_at,
        locations_extra=locations_extra,
        source_extra=source_extra,
    )

    return [posting]
