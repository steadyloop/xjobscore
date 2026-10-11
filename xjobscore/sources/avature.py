"""Avature public career-site adapter.

Avature has no uniform public JSON endpoint, but its search and detail pages
are fully server rendered.  Search pages provide stable detail/pager links;
the detail response itself is retained verbatim as the bronze item.
"""

from __future__ import annotations

import asyncio
import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from xjobscore import classify
from xjobscore.net import Net, NetError, etag
from xjobscore.records import Canonical, Feed, Item, Posting, Record


# Detail fan-out width for one board. A module constant rather than a literal
# at the call site so `sources.width` sizes this vendor's scheduler pool from
# the same number the requests use.
CONCURRENCY = 5

_BANNER_TITLE = re.compile(
    r"<h[1-6]\b[^>]*\bclass=[\"'][^\"']*\bbanner__text__title\b[^>]*>(.*?)</h[1-6]\s*>",
    re.I | re.S,
)

def _board(feed: Feed) -> tuple[str, str]:
    """``(company, board path)`` from a ``company`` or ``company/board`` key.

    One Avature subdomain hosts several boards at paths the customer names
    (``bupaanz/careersau``, ``astellasjapan/ja_JP/careers``); a bare key is
    the ``careers`` board, which is all this adapter used to read.
    """
    company, _, board = feed.key.partition("/")
    return company, board or "careers"


def _detail(feed: Feed) -> re.Pattern[str]:
    """Job-detail links of this feed's board only, not its sibling boards."""
    _, board = _board(feed)
    # The title slug is optional: some boards link `.../JobDetail/3869`.
    return re.compile(
        rf"/(?:[A-Za-z_]+/)?{re.escape(board)}/JobDetail/(?:[^?#]+/)?(?P<id>\d+)(?:[?#]|$)",
        re.I,
    )


class _Document(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.canonical: str | None = None
        self.og_title: str | None = None
        self.og_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value for name, value in attrs}
        if tag.lower() == "a" and values.get("href"):
            self.links.append(str(values["href"]))
        if (
            tag.lower() == "link"
            and (values.get("rel") or "").lower() == "canonical"
            and values.get("href")
        ):
            self.canonical = str(values["href"])
        if (
            tag.lower() == "meta"
            and (values.get("property") or "").lower() == "og:title"
            and values.get("content")
        ):
            self.og_title = " ".join(unescape(str(values["content"])).split())
        if (
            tag.lower() == "meta"
            and (values.get("property") or "").lower() == "og:url"
            and values.get("content")
        ):
            self.og_url = str(values["content"])


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _document(text: str) -> _Document:
    parser = _Document()
    parser.feed(text)
    parser.close()
    return parser


def _text(fragment: str) -> str:
    parser = _Text()
    parser.feed(fragment)
    parser.close()
    return " ".join(unescape("".join(parser.parts)).split())


def _div_blocks(text: str, class_token: str) -> list[tuple[str, str]]:
    """Return ``(opening tag, inner HTML)`` for balanced matching divs."""
    opening = re.compile(
        rf"<div\b(?=[^>]*\bclass=[\"'][^\"']*\b{re.escape(class_token)}\b[^\"']*[\"'])[^>]*>",
        re.I,
    )
    div = re.compile(r"<div\b[^>]*>|</div\s*>", re.I)
    result: list[tuple[str, str]] = []
    for match in opening.finditer(text):
        depth = 1
        end = None
        for token in div.finditer(text, match.end()):
            depth += -1 if token.group(0).lower().startswith("</") else 1
            if depth == 0:
                end = token.start()
                break
        if end is not None:
            result.append((match.group(0), text[match.end():end]))
    return result


def _fields(text: str) -> tuple[list[str], dict[str, str], str | None]:
    unlabeled: list[str] = []
    labeled: dict[str, str] = {}
    description: str | None = None
    for opening, body in _div_blocks(text, "article__content__view__field"):
        # Skip label/value descendants returned by the broad class-token match.
        classes = re.search(r"class=[\"']([^\"']*)", opening, re.I)
        tokens = set(classes.group(1).split()) if classes else set()
        if "article__content__view__field" not in tokens:
            continue
        labels = _div_blocks(body, "article__content__view__field__label")
        values = _div_blocks(body, "article__content__view__field__value")
        if not values:
            continue
        value_html = values[0][1].strip()
        value_text = _text(value_html)
        if (
            "field--rich-text" in tokens
            and value_html
            and (description is None or len(value_html) > len(description))
        ):
            description = value_html
        if labels:
            label = _text(labels[0][1]).rstrip(":").strip()
            if label and value_text:
                labeled[label] = value_text
        elif value_text:
            unlabeled.append(value_text)
    return unlabeled, labeled, description


# A portal path's leading locale segment: `/es_ES/careers/JobDetail/...`.
_LOCALE = re.compile(r"^/(?P<locale>[a-z]{2}_[A-Z]{2})(?P<rest>/.*)$")


def _english_url(url: str, links: list[str]) -> str | None:
    """The English version of `url` the page's own language menu links to.

    Avature keeps the visitor's language in a `portalLanguage` cookie, and a
    locale-less path redirects to whatever that cookie last held -- so one
    feed reading `/sv_SE/` turned every later page of that host Swedish
    (measured 2026-10-10; `Accept-Language` does not override the cookie).
    An explicit locale path does, so this swaps in the English one. None when
    `url` is already English or locale-less, or the portal offers no English.
    """
    parts = urlsplit(url)
    match = _LOCALE.match(parts.path)
    if not match or match.group("locale").startswith("en_"):
        return None
    found: dict[str, str] = {}
    for href in links:
        target = urlsplit(urljoin(url, href))
        other = _LOCALE.match(target.path)
        if (
            target.netloc == parts.netloc
            and other
            and other.group("locale").startswith("en_")
            and other.group("rest") == match.group("rest")
        ):
            found.setdefault(
                other.group("locale"),
                urlunsplit((target.scheme, target.netloc, target.path, target.query, "")),
            )
    if not found:
        return None
    return found.get("en_US") or found[min(found)]


def _search_url(feed: Feed) -> str:
    company, board = _board(feed)
    return str(
        feed.config.get(
            "url", f"https://{company}.avature.net/{board}/SearchJobs"
        )
    )


def _normalized_page(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    # Filters are retained; duplicate cosmetic query ordering is removed.
    normalized = urlencode(sorted((key, value) for key, values in query.items() for value in values))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, normalized, ""))


async def harvest(feed: Feed, net: Net) -> Record:
    first_url = _search_url(feed)
    first = await net.get(first_url, etag=feed.etag)
    if first.not_modified:
        return Record(http_status=304, etag=etag(first))
    # Read the board in English when it offers English: detail and pager
    # links then carry the English locale path, which outranks the cookie.
    if english := _english_url(first.url, _document(first.text).links):
        first = await net.get(english)

    detail = _detail(feed)
    search_path = f"/{_board(feed)[1]}/SearchJobs".lower()
    pending: list[tuple[str, object]] = [(first.url, first)]
    seen_pages: set[str] = set()
    details: dict[str, str] = {}
    partition_count = 0
    while pending:
        page_url, cached = pending.pop(0)
        normalized = _normalized_page(page_url)
        if normalized in seen_pages:
            continue
        seen_pages.add(normalized)
        response = cached if cached is not None else await net.get(page_url)
        partition_count += 1
        document = _document(response.text)
        for href in document.links:
            absolute = urljoin(response.url, href)
            if match := detail.search(absolute):
                details.setdefault(match.group("id"), absolute)
                continue
            parsed = urlsplit(absolute)
            if (
                parsed.netloc == urlsplit(first.url).netloc
                and search_path in parsed.path.lower()
                and "jobOffset=" in parsed.query
                and _normalized_page(absolute) not in seen_pages
            ):
                pending.append((absolute, None))

    links = list(details.items())
    responses = await asyncio.gather(
        *(net.get(url, concurrency=CONCURRENCY) for _, url in links),
        return_exceptions=True,
    )
    items: list[Item] = []
    truncated = False
    for (external_id, _url), response in zip(links, responses):
        if isinstance(response, NetError):
            truncated = True
            continue
        if isinstance(response, BaseException):
            raise response
        items.append(Item(external_id=external_id, payload=response.text, kind="html"))

    return Record(
        items=items,
        truncated=truncated,
        http_status=first.status,
        etag=etag(first),
        enumerated_item_count=len(links),
        detail_fetched_count=len(items),
        partition_count=partition_count,
    )


# Label/value markup the `article__content__view__field` divs do not cover:
# definition lists (TotalEnergies) and `data-map` spans (lululemon, NVA).
_DT_DD = re.compile(
    r"<dt\b[^>]*article__content__view__field__label[^>]*>(.*?)</dt\s*>\s*"
    r"<dd\b[^>]*article__content__view__field__value[^>]*>(.*?)</dd\s*>",
    re.I | re.S,
)
_DATA_MAP = re.compile(
    r"<span\b[^>]*data-map=[\"']item-title[\"'][^>]*>(.*?)</span\s*>\s*"
    r"<span\b[^>]*data-map=[\"']item-value[\"'][^>]*>(.*?)</span\s*>",
    re.I | re.S,
)
_HEADER_LOCATIONS = re.compile(
    r"<[a-z0-9]+\b[^>]*\bclass=[\"'][^\"']*\barticle__header--locations\b[^>]*>(.*?)</",
    re.I | re.S,
)
_JSON_LD = re.compile(r"<script\b[^>]*application/ld\+json[^>]*>(.*?)</script\s*>", re.I | re.S)
_FIELD_LABEL = re.compile(r"^[^:：]{1,40}[:：]\s*")

# Exact label names, from a census of every tenant's location-less postings
# (2026-10-10). Exact, not substring: "Location Type" is Onsite/Remote.
_CITY = {"city", "location city", "town", "ville", "ciudad", "cidade"}
_STATE = {
    "state", "location state", "state/province", "state/province/city",
    "states/provinces/cities", "province", "province/état", "region", "estado",
    "estado/cidade",
}
_COUNTRY = {"country", "country/region", "país", "pais", "pays"}
_PLACE = {"location", "locations", "job location", "work location", "workplace location", "address"}


def _location(html: str, fields: dict[str, str]) -> str | None:
    """A location for a page whose labeled fields name none.

    Tried in order: City/State/Country fields joined; a whole-place field;
    the `field--locations` block ("Locations: Kirkland, Washington, ...");
    the header's location line; the JobPosting JSON-LD address.
    """
    pairs = list(fields.items())
    pairs += [(_text(label), _text(value)) for label, value in _DT_DD.findall(html)]
    pairs += [(_text(label), _text(value)) for label, value in _DATA_MAP.findall(html)]
    named: dict[str, str] = {}
    for label, value in pairs:
        label = label.rstrip(":：").strip().casefold()
        if value:
            named.setdefault(label, value)

    def first(names: set[str]) -> str | None:
        return next((value for label, value in named.items() if label in names), None)

    city, state, country, place = first(_CITY), first(_STATE), first(_COUNTRY), first(_PLACE)
    # lululemon's "Location" is the country beside finer fields; anywhere it
    # already names the city it is the whole place, and adds nothing.
    if not country and place and not (city and city.casefold() in place.casefold()):
        country = place
    joined = ", ".join(dict.fromkeys(part for part in (city, state, country) if part))
    if joined:
        return joined
    for opening, body in _div_blocks(html, "field--locations"):
        text = _text(re.split(r"<ul\b", body, maxsplit=1, flags=re.I)[0])
        if text := _FIELD_LABEL.sub("", text).strip():
            return text
    if header := _HEADER_LOCATIONS.search(html):
        if text := _text(header.group(1)):
            return text
    for block in _JSON_LD.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        places = data.get("jobLocation") if isinstance(data, dict) else None
        for place in places if isinstance(places, list) else [places]:
            address = place.get("address") if isinstance(place, dict) else None
            if isinstance(address, dict):
                text = ", ".join(
                    str(address[key]).strip()
                    for key in ("addressLocality", "addressRegion", "addressCountry")
                    if isinstance(address.get(key), str) and address[key].strip()
                )
                if text:
                    return text
    return None


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    assert isinstance(item.payload, str)
    document = _document(item.payload)
    unlabeled, fields, description = _fields(item.payload)
    detail = _detail(feed)
    # The page's own address: canonical, else og:url, else the first job
    # link on the page -- which is often the language menu's first entry.
    own = document.canonical or (
        document.og_url if document.og_url and detail.search(document.og_url) else None
    ) or next(
        (
            urljoin(_search_url(feed), href)
            for href in document.links
            if detail.search(href)
        ),
        "",
    )
    url = (_english_url(own, document.links) if own else None) or own
    apply_url = next(
        (
            urljoin(url or _search_url(feed), href)
            for href in document.links
            if "Login?jobId=" in href or "Apply?jobId=" in href
        ),
        url or None,
    )
    location = next(
        (value for label, value in fields.items() if "location" in label.lower()),
        None,
    ) or _location(item.payload, fields)
    requisition = next(
        (
            value
            for label, value in fields.items()
            if label.lower() in {"ref #", "job id", "requisition id", "job number"}
        ),
        None,
    )
    # The page banner carries the posting title; the first unlabeled field is
    # only a fallback because some boards lead with the description there.
    banner = _BANNER_TITLE.search(item.payload)
    title = (
        document.og_title
        or (banner and _text(banner.group(1)))
        or (unlabeled[0] if unlabeled else "")
    )
    # Co-op before intern: a title can satisfy both ("Summer Intern Co-Op
    # Conversion"), and co-op is the more specific claim.
    job_type = (
        "coop" if classify.hit(title, "co-?ops?")
        else "intern" if classify.hit(title, "intern(s|ship|ships)?")
        else None
    )
    return [
        Posting(
            external_id=item.external_id,
            title=title,
            url_posting=url,
            apply_url=apply_url,
            company_raw=feed.company_name or feed.key,
            location_raw=location,
            description_html=description,
            requisition_id=requisition,
            job_type=job_type,
            source_extra=fields,
        )
    ]
