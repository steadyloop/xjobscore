"""Comeet -- one GET, full descriptions, needs a page token as well as an id.

    https://www.comeet.co/careers-api/2.0/company/{company_uid}/positions?token={token}&details=true

Payload sample: notebooks/ATS-payloads.md#comeet.

- company_uid is in the careers URL comeet.com/jobs/{name}/{company_uid};
  `token` is embedded in that page's source, not the API -- both need to live
  in feed.key/feed.config, since neither alone identifies the board.
- Response is an array of positions; details[] holds the description sections.
- Job URL: url_comeet_hosted_page (position_url is the API's own detail link,
  not meant for humans -- do not use it as url_posting).
"""

from __future__ import annotations

from datetime import datetime

from xjobscore.net import Net, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = "https://www.comeet.co/careers-api/2.0/company/{key}/positions?token={token}&details=true"


async def harvest(feed: Feed, net: Net) -> Record:
    url = feed.config.get("url", LIST).format(key=feed.key, token=feed.config["token"])
    r = await net.get(url, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))

    body = r.json()
    items = [Item(external_id=str(position["uid"]), payload=position) for position in body]
    return Record(items=items, http_status=r.status, validators=validators(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Comeet position -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    position = item.payload

    source_updated_at = None
    if position.get("time_updated"):
        source_updated_at = datetime.fromisoformat(position["time_updated"].replace("Z", "+00:00"))

    # location_raw: location.name is often a generic label ("Remote") while
    # city/state/country carry the human-readable detail. Prefer combining
    # city + state/country when name looks generic; fall back to name, then
    # city/country alone, then None -- never raise on a missing location.
    location = position.get("location") or {}
    location_raw = None
    name = location.get("name")
    city = location.get("city")
    state = location.get("state")
    country = location.get("country")
    if name and name.lower() not in ("remote", ""):
        location_raw = name
    elif city:
        parts = [p for p in (city, state, country) if p]
        location_raw = ", ".join(parts)
    elif name:
        location_raw = name

    # details[] is a sectioned description: keep it as content_sections, and
    # also concatenate the HTML blocks (in order) into a single
    # description_html so there is one readable body too.
    details = sorted(position.get("details") or [], key=lambda d: d.get("order") or 0)
    content_sections = [{"heading": d.get("name"), "html": d.get("value")} for d in details]
    description_html = "".join(d.get("value") or "" for d in details) or None

    source_extra = {
        "department": position.get("department"),
        "email": position.get("email"),
        "experience_level": position.get("experience_level"),
        "position_url": position.get("position_url"),
        "categories": position.get("categories"),
        "location_postal_code": location.get("postal_code"),
        "location_street_name": location.get("street_name"),
        "location_timezone": location.get("timezone"),
        "location_uid": location.get("location_uid"),
        "location_is_remote": location.get("is_remote"),
    }

    posting = Posting(
        external_id=item.external_id,
        title=position["name"],
        url_posting=position["url_comeet_hosted_page"],
        company_raw=position.get("company_name"),
        location_raw=location_raw,
        description_html=description_html,
        employment_type=position.get("employment_type"),
        workplace_type_raw=position.get("workplace_type"),
        published_at=None,
        published_at_precision="unknown",
        source_updated_at=source_updated_at,
        content_sections=content_sections,
        source_extra=source_extra,
    )

    return [posting]
