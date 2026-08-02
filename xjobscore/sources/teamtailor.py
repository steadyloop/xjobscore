"""Teamtailor -- RSS/XML, not JSON. One GET, full descriptions.

    https://{key}.teamtailor.com/jobs.rss

Payload sample: notebooks/ATS-payloads.md#teamtailor.

- Response is RSS/XML, so this parses <item> elements, not a JSON list --
  use net.get(...).text and an XML parser, not .json().
- Per <item>: <link> is the job URL, <description> is the full HTML detail,
  `tt:` namespaced tags (tt:locations, tt:department, ...) add structured
  fields the plain RSS fields don't carry.
- The JSON API (api.teamtailor.com) requires an API key -- not used here.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from xjobscore.net import Net, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record

LIST = "https://{key}.teamtailor.com/jobs.rss"

TT_NS = "https://teamtailor.com/locations"

# Without this, ET.tostring re-serializes the tt: namespace as an
# auto-generated ns0: prefix -- cosmetically different from what the source
# sent, even though the payload is still faithful byte-for-byte in meaning.
# Registering the prefix keeps the round-tripped XML string visually
# identical to the wire format.
ET.register_namespace("tt", TT_NS)


def _text(el: ET.Element | None) -> str | None:
    """Element text, treating missing/empty/whitespace-only as None."""
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


async def harvest(feed: Feed, net: Net) -> Record:
    url = feed.config.get("url", LIST).format(key=feed.key)
    r = await net.get(url, state=feed.state)
    if r.not_modified:
        return Record(http_status=304, validators=validators(r))

    root = ET.fromstring(r.text)
    channel = root.find("channel")
    elements = channel.findall("item") if channel is not None else []

    items = []
    for el in elements:
        guid = _text(el.find("guid"))
        external_id = guid if guid is not None else _text(el.find("link")) or ""
        items.append(Item(external_id=external_id, payload=ET.tostring(el, encoding="unicode"), kind="xml"))

    return Record(items=items, http_status=r.status, validators=validators(r))


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Teamtailor RSS `<item>` -> Posting. See ARCHITECTURE.md's parsing-layer section."""
    el = ET.fromstring(item.payload)

    title = _text(el.find("title")) or ""
    url_posting = _text(el.find("link")) or ""
    description_html = _text(el.find("description"))
    workplace_type_raw = _text(el.find("remoteStatus"))

    published_at = None
    pub_date = _text(el.find("pubDate"))
    if pub_date:
        published_at = parsedate_to_datetime(pub_date)
        if not isinstance(published_at, datetime):
            published_at = None
    published_at_precision = "exact" if published_at else "unknown"

    locations = el.findall(f"{{{TT_NS}}}locations/{{{TT_NS}}}location")
    location_names = [_text(loc.find(f"{{{TT_NS}}}name")) for loc in locations]
    location_names = [name for name in location_names if name]
    location_raw = location_names[0] if location_names else None
    locations_extra = location_names[1:]

    guid = _text(el.find("guid"))

    source_extra = {
        "guid": guid,
        "department": _text(el.find(f"{{{TT_NS}}}department")),
        "role": _text(el.find(f"{{{TT_NS}}}role")),
        "division": _text(el.find(f"{{{TT_NS}}}division")),
    }

    posting = Posting(
        external_id=item.external_id,
        title=title,
        url_posting=url_posting,
        company_raw=feed.key,
        location_raw=location_raw,
        description_html=description_html,
        employment_type=None,
        workplace_type_raw=workplace_type_raw,
        published_at=published_at,
        published_at_precision=published_at_precision,
        locations_extra=locations_extra,
        source_extra=source_extra,
    )

    return [posting]
