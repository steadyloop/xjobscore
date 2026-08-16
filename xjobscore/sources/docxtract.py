"""Config-driven adapter for public job documents and feeds.

The adapter has one small pipeline: fetch a board, extract jobs directly or
discover detail links, then parse each raw job item in a replayable second
step.  It intentionally does not handle rendered, authenticated, or JSON API
boards.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import partial
from html import unescape
from html.parser import HTMLParser
from importlib import import_module
from typing import Any
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

from xjobscore.net import Net, NetError, etag
from xjobscore.records import Canonical, Feed, Item, Posting, Record
CONFIGS = import_module(".docxtract-configs", __package__).CONFIGS

SPECS = CONFIGS


class _HTML(HTMLParser):
    """Collect links and JSON-LD script contents without interpreting markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.links: list[str] = []
        self.scripts: list[str] = []
        self._script: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs}
        if tag.lower() == "a" and values.get("href"):
            self.links.append(values["href"])
        if tag.lower() == "script" and (values.get("type") or "").lower() == "application/ld+json":
            self._script = []

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None


def _html(text: str) -> _HTML:
    parser = _HTML()
    parser.feed(text)
    parser.close()
    return parser


def _is_job_posting(value: object) -> bool:
    kind = value.get("@type") if isinstance(value, dict) else None
    return kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind)


def _job_postings(value: object) -> list[dict]:
    if isinstance(value, list):
        return [entry for entry in value if _is_job_posting(entry)]
    if not isinstance(value, dict):
        return []
    graph = value.get("@graph")
    if isinstance(graph, list):
        return [entry for entry in graph if _is_job_posting(entry)]
    return [value] if _is_job_posting(value) else []


def _jsonld(text: str) -> list[dict]:
    jobs: list[dict] = []
    for raw in _html(text).scripts:
        try:
            jobs.extend(_job_postings(json.loads(raw)))
        except json.JSONDecodeError:
            continue
    return jobs


def _extract_job_posting(raw_ldjson: str) -> dict | None:
    """Compatibility helper for callers with one JSON-LD script block."""
    try:
        jobs = _job_postings(json.loads(raw_ldjson))
    except json.JSONDecodeError:
        return None
    return jobs[0] if jobs else None


def _external_id(job: dict) -> str:
    identifier = job.get("identifier")
    if isinstance(identifier, dict) and identifier.get("value"):
        return str(identifier["value"])
    if identifier:
        return str(identifier)
    if job.get("url"):
        return hashlib.sha256(str(job["url"]).encode()).hexdigest()[:16]
    return hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()[:16]


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _format(template: str, feed: Feed, **values: str) -> str:
    return template.format(key=feed.key, **values)


def _list_url(config: dict, feed: Feed) -> str:
    settings = feed.config or {}
    return _format(settings.get("url", config["list"]["url"]), feed)


def _xml_items(text: str, spec: dict) -> list[Item]:
    root = ET.fromstring(text)
    items: list[Item] = []
    for element in root.findall(spec["path"]):
        external_id = next((_text(element.find(path)) for path in spec["id"] if _text(element.find(path))), None)
        if external_id:
            items.append(Item(external_id=external_id, payload=ET.tostring(element, encoding="unicode"), kind="xml"))
    return items


def _dig(value: Any, path: str) -> Any:
    if not path:
        return value
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _json_items(body: Any, spec: dict) -> list[Item]:
    rows = _dig(body, spec["items"])
    if not isinstance(rows, list):
        return []
    return [
        Item(external_id=str(external_id), payload=row)
        for row in rows
        if isinstance(row, dict) and (external_id := _dig(row, spec["id"])) is not None
    ]


def _links(text: str, base_url: str, spec: dict, feed: Feed) -> list[tuple[str, str]]:
    import re

    pattern = re.compile(spec["pattern"].format(key=re.escape(feed.key)))
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href in _html(text).links:
        match = pattern.search(href)
        if not match:
            continue
        url = urljoin(base_url, href)
        if url not in seen:
            seen.add(url)
            found.append((match.group("id"), url))
    return found


async def harvest(name: str, feed: Feed, net: Net) -> Record:
    config = CONFIGS[name]
    list_spec = config["list"]
    response = await net.get(_list_url(config, feed), etag=feed.etag)
    if response.not_modified:
        return Record(http_status=304, etag=etag(response))

    if list_spec["extract"] == "jsonld":
        items = [Item(external_id=_external_id(job), payload=job) for job in _jsonld(response.text)]
        return Record(items=items, http_status=response.status, etag=etag(response))
    if list_spec["extract"] == "xml_items":
        return Record(items=_xml_items(response.text, list_spec), http_status=response.status, etag=etag(response))
    if list_spec["extract"] == "json_items":
        return Record(items=_json_items(response.json(), list_spec), http_status=response.status, etag=etag(response))
    if list_spec["extract"] != "html_links":
        raise ValueError(f"unsupported web-board extractor: {list_spec['extract']}")

    links = _links(response.text, response.url, list_spec, feed)
    # `detail_max` is the ceiling on what one poll spends here, the counterpart
    # of json-api's `page.max`/`detail.max`. This shape has no pagination to
    # bound it, so without a cap the only limit is how many links the board
    # printed. Sets `truncated`, so a capped poll never reads as a complete one.
    detail_max = config.get("detail_max")
    links_capped = detail_max is not None and len(links) > detail_max
    if links_capped:
        links = links[:detail_max]
    # A board that only links to detail pages pays one request per item; the
    # rest of Net's sources get everything in the one list request. Most
    # tenants are small enough that 1-request-per-second is fine, but a board
    # this shape can raise `detail_concurrency` in its config (see jobvite) to
    # fetch several detail pages at once instead of paying it sequentially.
    # `concurrency` is only passed through when raised above the default so a
    # fake Net exposing just `get(url, *, params, headers, etag)` -- the
    # documented contract -- still works unmodified.
    concurrency = config.get("detail_concurrency", 1)
    get = (lambda url: net.get(url, concurrency=concurrency)) if concurrency > 1 else net.get
    results = await asyncio.gather(
        *(get(url) for _, url in links),
        return_exceptions=True,
    )
    items: list[Item] = []
    truncated = links_capped
    for (external_id, _url), result in zip(links, results):
        if isinstance(result, NetError):
            truncated = True
            continue
        if isinstance(result, BaseException):
            raise result
        for job in _jsonld(result.text):
            item_id = external_id if config["detail"].get("id") == "discovered" else _external_id(job)
            items.append(Item(external_id=item_id, payload=job))
    return Record(items=items, truncated=truncated, http_status=response.status, etag=etag(response))


def _parse_date(value: object) -> tuple[datetime | None, str]:
    if not isinstance(value, str) or not value:
        return None, "unknown"
    try:
        return datetime.fromisoformat(value), "exact" if "T" in value else "date"
    except ValueError:
        return None, "unknown"


def _schemaorg(config: dict, feed: Feed, item: Item) -> Posting:
    job = item.payload
    assert isinstance(job, dict)
    organization = job.get("hiringOrganization")
    company_raw = organization.get("name") if isinstance(organization, dict) else organization
    locations = job.get("jobLocation") or []
    if isinstance(locations, dict):
        locations = [locations]
    location_fields = config.get("location_fields", ("addressLocality", "addressRegion", "addressCountry"))

    def location(place: object) -> str | None:
        address = place.get("address") if isinstance(place, dict) else None
        if not isinstance(address, dict):
            return None
        parts = [address.get(field) for field in location_fields if address.get(field)]
        return ", ".join(parts) if parts else None

    location_values = [value for value in (location(place) for place in locations) if value]
    published_at, precision = _parse_date(job.get("datePosted"))
    deadline_at, _ = _parse_date(job.get("validThrough"))
    url_posting = (
        _format(config["posting_url"], feed, id=item.external_id)
        if config.get("posting_url")
        else job.get("url") or feed.key
    )
    identifier = job.get("identifier")
    requisition_id = identifier if config.get("requisition_id") and identifier and identifier != item.external_id else None
    extras = config.get("source_extra", ("baseSalary", "identifier", "industry", "validThrough_raw"))
    source_extra = {
        key: job.get("validThrough") if key == "validThrough_raw" else job.get(key)
        for key in extras
    }
    return Posting(
        external_id=item.external_id,
        title=job.get("title") or "",
        url_posting=url_posting,
        apply_url=url_posting if config.get("apply_url") else None,
        company_raw=company_raw,
        location_raw=location_values[0] if location_values else None,
        description_html=job.get("description"),
        requisition_id=requisition_id,
        employment_type=job.get("employmentType"),
        published_at=published_at,
        published_at_precision=precision,
        deadline_at=None if config.get("posting_url") else deadline_at,
        locations_extra=location_values[1:],
        source_extra=source_extra,
    )


def _rss(config: dict, feed: Feed, item: Item) -> Posting:
    assert isinstance(item.payload, str)
    element = ET.fromstring(item.payload)
    rss = config["rss"]
    namespace = rss["namespace"]
    qualified = lambda path: "/".join(f"{{{namespace}}}{part}" for part in path.split("/"))
    # A feed either nests the place name a level down (Teamtailor's
    # <location><name>) or states it as the element's own text
    # (SuccessFactors' <g:location>). Fall back rather than branch on a flag:
    # the nested lookup simply finds nothing in the flat shape.
    located = element.findall(qualified(rss["location_path"]))
    locations = [_text(el.find(f"{{{namespace}}}name")) or _text(el) for el in located]
    locations = [value for value in locations if value]
    published_at = None
    if (pub_date := _text(element.find("pubDate"))):
        published_at = parsedate_to_datetime(pub_date)
    extras = {key: _text(element.find(qualified(path))) for key, path in rss["extra_paths"].items()}
    extras["guid"] = _text(element.find("guid"))
    # Most RSS boards are per-company, so the feed key names the employer. A
    # multi-tenant feed keyed by host carries the real name in an element
    # instead, and `company_path` points at it.
    company_raw = feed.key
    if (path := rss.get("company_path")):
        company_raw = _text(element.find(qualified(path))) or feed.key
    # A feed may CDATA-wrap markup that is itself entity-escaped, so the text
    # node reads '&lt;p&gt;' rather than '<p>'. Exactly one unescape restores
    # the markup; a second would decode entities that belong inside attribute
    # values. Opt-in, because a feed sending plain HTML must not have a
    # literal '&lt;' a job author typed turned into a tag.
    description_html = _text(element.find("description"))
    if description_html and rss.get("unescape_description"):
        description_html = unescape(description_html)
    return Posting(
        external_id=item.external_id,
        title=_text(element.find("title")) or "",
        url_posting=_text(element.find("link")) or "",
        company_raw=company_raw,
        location_raw=locations[0] if locations else None,
        description_html=description_html,
        employment_type=(
            _text(element.find(qualified(path)))
            if (path := rss.get("employment_type_path"))
            else None
        ),
        workplace_type_raw=_text(element.find("remoteStatus")),
        published_at=published_at,
        published_at_precision="exact" if published_at else "unknown",
        locations_extra=locations[1:],
        source_extra=extras,
    )


ATOM_NS = "http://www.w3.org/2005/Atom"


def _atom(config: dict, feed: Feed, item: Item) -> Posting:
    """Atom is RSS's opposite case: every base field, not just extensions,
    lives in a namespace, and the URL is an attribute (link/@href) rather
    than element text. _rss's unprefixed element.find("title") reads nothing
    here, which is what makes this a separate function rather than a flag.
    """
    assert isinstance(item.payload, str)
    element = ET.fromstring(item.payload)
    atom = config["atom"]
    qualified = lambda path: "/".join(f"{{{ATOM_NS}}}{part}" for part in path.split("/"))
    link = next(
        (el for el in element.findall(qualified("link")) if el.get("rel") in (None, "alternate")),
        None,
    )
    url_posting = (link.get("href") if link is not None else None) or _text(element.find(qualified("id"))) or ""
    published_at = None
    if (published := _text(element.find(qualified("published")))):
        published_at = datetime.fromisoformat(published)
    extras = {key: _text(element.find(qualified(path))) for key, path in atom.get("extra_paths", {}).items()}
    # Most Atom boards this engine sees are per-tenant, so the feed key names
    # the employer, matching _rss's default; company_path is there for a
    # multi-tenant feed that states the real name on an element instead.
    company_raw = feed.key
    if (path := atom.get("company_path")):
        company_raw = _text(element.find(qualified(path))) or feed.key
    description_path = atom.get("description_path", "content")
    locations = []
    if (location_path := atom.get("location_path")):
        locations = [
            value
            for location_element in element.findall(qualified(location_path))
            if (value := _text(location_element))
        ]
    employment_type = None
    if (employment_type_path := atom.get("employment_type_path")):
        employment_type = _text(element.find(qualified(employment_type_path)))
    return Posting(
        external_id=item.external_id,
        title=_text(element.find(qualified("title"))) or "",
        url_posting=url_posting,
        company_raw=company_raw,
        location_raw=locations[0] if locations else None,
        description_html=_text(element.find(qualified(description_path))),
        employment_type=employment_type,
        published_at=published_at,
        published_at_precision="exact" if published_at else "unknown",
        locations_extra=locations[1:],
        source_extra=extras,
    )


def _xml_map(config: dict, feed: Feed, item: Item) -> Posting:
    """A flat, unqualified XML schema that is neither RSS nor Atom -- a plain
    field-name-to-element-path map, for a vendor with its own bespoke tag set
    (Personio's `<workzag-jobs><position>`). No namespace handling: a vendor
    that needs one belongs in `_rss` or `_atom`, not here.
    """
    assert isinstance(item.payload, str)
    element = ET.fromstring(item.payload)
    xml_map = config["xml_map"]
    fields = xml_map["fields"]
    field = lambda name: _text(element.find(fields[name])) if fields.get(name) else None
    locations = [value for path in xml_map.get("location_paths", ()) if (value := _text(element.find(path)))]
    published_at, precision = _parse_date(field("published"))
    url_posting = field("url")
    if not url_posting and config.get("posting_url"):
        url_posting = _format(config["posting_url"], feed, id=item.external_id)
    company_raw = feed.key
    if (path := xml_map.get("company_path")):
        company_raw = _text(element.find(path)) or feed.key
    extras = {key: _text(element.find(path)) for key, path in xml_map.get("extra_paths", {}).items()}
    return Posting(
        external_id=item.external_id,
        title=field("title") or "",
        url_posting=url_posting or "",
        company_raw=company_raw,
        location_raw=locations[0] if locations else None,
        description_html=field("description"),
        employment_type=field("employment_type"),
        published_at=published_at,
        published_at_precision=precision,
        locations_extra=locations[1:],
        source_extra=extras,
    )


def _simplify(config: dict, item: Item) -> Posting:
    payload = item.payload
    assert isinstance(payload, dict)
    locations = payload.get("locations") or []
    active = payload.get("active")
    closed = active is False
    updated = payload.get("date_updated")
    source_updated_at = datetime.fromtimestamp(updated, tz=timezone.utc) if updated is not None else None
    return Posting(
        external_id=item.external_id,
        title=payload["title"],
        url_posting=payload["url"],
        fidelity="secondhand",
        state="closed" if closed else "open",
        closed_evidence="simplify: active=false" if closed else None,
        company_raw=payload.get("company_name"),
        location_raw=locations[0] if locations else None,
        source_updated_at=source_updated_at,
        source_active=active,
        locations_extra=locations[1:],
        source_extra={key: payload.get(key) for key in config["source_extra"]},
        origin_url=payload["url"],
    )


def parse(name: str, feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    config = CONFIGS[name]
    if config["parser"] == "schemaorg":
        return [_schemaorg(config, feed, item)]
    if config["parser"] == "rss":
        return [_rss(config, feed, item)]
    if config["parser"] == "atom":
        return [_atom(config, feed, item)]
    if config["parser"] == "xml_map":
        return [_xml_map(config, feed, item)]
    if config["parser"] == "simplify":
        return [_simplify(config, item)]
    raise ValueError(f"unsupported web-board parser: {config['parser']}")


def harvester(name: str):
    return partial(harvest, name)


def parser(name: str):
    return partial(parse, name)
