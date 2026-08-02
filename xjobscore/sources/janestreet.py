"""Jane Street -- bespoke JSON, two endpoints that disagree in shape.

    https://www.janestreet.com/jobs/main.json           (full roles)
    https://www.janestreet.com/jobs/internships.json     (summary + status only)

Payload sample: notebooks/nATS-payloads.md#3-bespoke-quant-career-sites.

- main.json is the source of truth: id, city, availability, overview (HTML).
  internships.json has no `id` -- use it only to read `status`
  ("closed"/etc.), matched against main.json by position/team, and never as a
  primary listing.
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

from xjobscore.net import Net, NetError, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

MAIN = "https://www.janestreet.com/jobs/main.json"
INTERNSHIPS = "https://www.janestreet.com/jobs/internships.json"


async def harvest(feed: Feed, net: Net) -> Record:
    main_url = feed.config.get("main_url", MAIN)
    r = await net.get(main_url, state=feed.state)
    if r.not_modified:
        # Nothing changed on main.json -- skip internships.json too, since its
        # only job is enriching entries that didn't change either.
        return Record(http_status=304, validators=validators(r))

    main_jobs = r.json()
    items = [Item(external_id=str(job["id"]), payload=job) for job in main_jobs]

    # internships.json is a secondary, best-effort enrichment source (status
    # only, no `id`) -- its failure must not sink an otherwise-good main.json
    # poll. It carries no conditional-GET state of its own here since the
    # engine only tracks one validators dict per feed, keyed off main.json.
    internships_url = feed.config.get("internships_url", INTERNSHIPS)
    internships: list = []
    try:
        internships_r = await net.get(internships_url)
        if not internships_r.not_modified:
            internships = internships_r.json()
    except NetError:
        internships = []

    envelope = {"internships": internships}
    return Record(items=items, envelope=envelope, http_status=r.status, validators=validators(r))


def _internship_key(entry: dict) -> tuple:
    """Best available correlation key between main.json and internships.json.

    internships.json has no `id`, so entries must be matched by content.
    Inspecting the real fixtures: both files share `position` and `team`, but
    `position` alone collides -- the sample internships.json has two
    "Hardware Engineer (FPGA/ASIC)" rows under the same `team` that differ
    only by city (main.json's `city` vs internships.json's `location`, same
    value domain e.g. "NYC"/"LDN"). So the key is the triple
    (position, team, city-or-location) -- the narrowest one available that
    still uses only fields actually present in both payloads.
    """
    return (
        entry.get("position"),
        entry.get("team"),
        entry.get("city") or entry.get("location"),
    )


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

    # internships.json only ever backfills `status` for entries that actually
    # have an internship-track counterpart there. Absence must never imply
    # closure, so source_active stays None unless a match with a "closed"
    # status was found.
    source_active = None
    internships = envelope.get("internships") or []
    lookup = {_internship_key(entry): entry for entry in internships}
    matched = lookup.get(_internship_key(job))
    if matched is not None and matched.get("status") == "closed":
        source_active = False

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
        source_active=source_active,
        locations_extra=[],
        content_sections=[],
        source_extra=source_extra,
    )

    return [posting]
