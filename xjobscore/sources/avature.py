"""Avature public career-site adapter.

Avature has no uniform public JSON endpoint, but its search and detail pages
are fully server rendered.  Search pages provide stable detail/pager links;
the detail response itself is retained verbatim as the bronze item.
"""

from __future__ import annotations

import asyncio
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


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    assert isinstance(item.payload, str)
    document = _document(item.payload)
    unlabeled, fields, description = _fields(item.payload)
    detail = _detail(feed)
    url = document.canonical or next(
        (
            urljoin(_search_url(feed), href)
            for href in document.links
            if detail.search(href)
        ),
        "",
    )
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
    )
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
