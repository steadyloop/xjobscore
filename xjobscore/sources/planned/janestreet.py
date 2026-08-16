"""Jane Street -- bespoke JSON from the employer's current-role feed.

    https://www.janestreet.com/jobs/main.json

Payload sample: notebooks/nATS-payloads.md#3-bespoke-quant-career-sites.

- main.json is the source of truth: id, city, availability, overview (HTML).
  Jane Street's open-roles page treats its separate internships.json payload
  as a closed-listing archive: matching rows are discarded in favor of the
  open main.json role. It must not be joined onto main.json as status data.
- Titles can carry Unicode homoglyphs (Lisu U+A4DF/U+A4E1/U+A4E3 standing in
  for M/L/R, e.g. "ꓟachine ꓡearning ꓣesearcher"). Store `title` verbatim --
  TR39 confusables folding is title_normalized's job downstream, not this
  function's.
- `min_salary`/`max_salary` come back as the *string* "None", not null or a
  number. Never coerce blindly.
- `id` values look Greenhouse-shaped, but the board token isn't public --
  this JSON is the only supported path, don't try to guess a Greenhouse token.
"""

from __future__ import annotations

from xjobscore.net import Net, etag
from xjobscore.records import Canonical, Feed, Item, Posting, Record

MAIN = "https://www.janestreet.com/jobs/main.json"


async def harvest(feed: Feed, net: Net) -> Record:
    main_url = feed.config.get("main_url", MAIN)
    r = await net.get(main_url, etag=feed.etag)
    if r.not_modified:
        return Record(http_status=304, etag=etag(r))

    main_jobs = r.json()
    items = [Item(external_id=str(job["id"]), payload=job) for job in main_jobs]

    return Record(items=items, http_status=r.status, etag=etag(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Jane Street main.json entry -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    job = item.payload

    title = job["position"]  # verbatim -- homoglyphs are title_normalized's job, not ours

    url_posting = job.get("url") or f"https://www.janestreet.com/join-jane-street/position/{job['id']}/"

    location_raw = job.get("city") or job.get("location")

    # `availability` reads like "Summer Internship" / "Full-Time: Experienced"
    # -- not a clean employment_type taxonomy value, and the Posting field is
    # documented as "raw string, unmapped" anyway, so pass it through as-is.
    employment_type = job.get("availability")

    # min_salary / max_salary sometimes arrive as the literal string "None"
    # (not JSON null, not a number). Store exactly what was received --
    # source_extra is a verbatim long tail, coercion is downstream's job.
    source_extra = {
        "category": job.get("category"),
        "team": job.get("team"),
        "duration": job.get("duration"),
        "min_salary": job.get("min_salary"),
        "max_salary": job.get("max_salary"),
    }

    posting = Posting(
        external_id=item.external_id,
        title=title,
        url_posting=url_posting,
        fidelity="origin",
        state="open",
        company_raw="Jane Street",
        location_raw=location_raw,
        description_html=job.get("overview"),
        requisition_id=None,
        employment_type=employment_type,
        workplace_type_raw=None,
        published_at=None,
        published_at_precision="unknown",
        source_updated_at=None,
        deadline_at=None,
        source_active=None,
        locations_extra=[],
        content_sections=[],
        source_extra=source_extra,
    )

    return [posting]
