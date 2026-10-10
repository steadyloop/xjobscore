"""Hacker News monthly ``Who is hiring?`` thread.

This is deliberately one bespoke adapter.  Algolia already provides clean JSON
and integer pagination; the bespoke part is the boundary between a top-level HN
comment and a conservative :class:`Posting` parsed from its free-form header.

``feed.key`` may be a numeric HN story id to pin a particular month.  Any
other key (the standalone console uses ``hn``) discovers the newest canonical
thread posted by the ``whoishiring`` account.  ``feed.config.story_id`` is the
explicit equivalent of a numeric key and is useful for backfills.
"""

from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
import re

from xjobscore.net import Net, NetError, etag
from xjobscore.records import Canonical, Feed, Item, Posting, Record

API = "https://hn.algolia.com/api/v1/search_by_date"
THREAD_QUERY = "Ask HN: Who is hiring?"
THREAD_TITLE = re.compile(r"^Ask HN: Who is hiring\? \([^)]+\)$", re.IGNORECASE)
# `search_by_date` returns whoishiring's threads newest-first, so the current
# month is hit 0 and the runners-up are the preceding months. Five is a wide
# margin for an off-schedule or mistitled thread; twenty cost 161 KB to read
# three fields.
DISCOVERY_HITS = 5

_ROLE = re.compile(
    r"\b(?:architect|ceo|chief|cto|designer|developer|engineer|engineering|"
    r"founder|intern(?:ship)?|lead|manager|product|researcher|scientist|"
    r"software|staff|director|analyst|administrator|consultant|roles?|"
    r"positions?)\b",
    re.IGNORECASE,
)
_WORKPLACE = re.compile(r"\b(?:remote|on[ -]?site|hybrid)\b", re.IGNORECASE)
_EMPLOYMENT = re.compile(
    r"\b(?:full[ -]?time|part[ -]?time|contract(?:or)?|intern(?:ship)?)\b",
    re.IGNORECASE,
)
_COMPENSATION = re.compile(
    r"(?:[$€£]\s?\d|\b\d[\d,.]*\s?(?:k|usd|eur|gbp)\b|\bequity\b)",
    re.IGNORECASE,
)
_LOCATION = re.compile(
    r"(?:,|\b(?:worldwide|global|usa?|u\.s\.|uk|europe|eu|canada|germany|"
    r"france|spain|italy|netherlands|australia|india|japan|singapore)\b)",
    re.IGNORECASE,
)
_URL = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_PARAGRAPH = re.compile(r"(?i)<p(?:\s[^>]*)?>|[\r\n]+")


class _HTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.links: list[str] = []

    def handle_data(self, data: str) -> None:
        self.text.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href and href.startswith(("http://", "https://")):
            self.links.append(href)


def _html(value: str) -> _HTML:
    parsed = _HTML()
    parsed.feed(value)
    return parsed


def _hit(raw: dict) -> dict:
    """Drop Algolia's search decoration from a record we are about to store.

    ``_highlightResult`` is a verbatim copy of author/comment_text/story_title
    wrapped in match metadata. These queries carry no search term, so every
    ``matchLevel`` is ``"none"`` and every ``matchedWords`` is empty -- it is
    ~45% of a comment record and says nothing the record does not already say.
    Algolia returns it whatever ``attributesToRetrieve`` asks for, so it has to
    come off here rather than on the wire.
    """
    return {key: value for key, value in raw.items() if key != "_highlightResult"}


def _story_id(feed: Feed) -> str | None:
    configured = feed.config.get("story_id")
    candidate = configured if configured is not None else feed.key
    text = str(candidate)
    return text if text.isdigit() else None


async def _latest_story(net: Net, url: str) -> dict:
    response = await net.get(
        url,
        params={
            "query": THREAD_QUERY,
            "tags": "story,author_whoishiring",
            "hitsPerPage": DISCOVERY_HITS,
        },
    )
    for hit in response.json().get("hits", []):
        if (
            hit.get("author") == "whoishiring"
            and THREAD_TITLE.fullmatch(hit.get("title") or "")
            and str(hit.get("objectID", "")).isdigit()
        ):
            return _hit(hit)
    raise ValueError("HN Algolia search returned no canonical 'Who is hiring?' thread")


async def harvest(feed: Feed, net: Net) -> Record:
    """Discover one monthly thread and return only its top-level comments."""
    url = str(feed.config.get("api_url", API))
    story_id = _story_id(feed)
    story: dict = {}
    if story_id is None:
        story = await _latest_story(net, url)
        story_id = str(story["objectID"])

    hits_per_page = int(feed.config.get("hits_per_page", 1000))
    max_pages = int(feed.config.get("max_pages", 20))
    if hits_per_page < 1 or max_pages < 1:
        raise ValueError("HN hits_per_page and max_pages must be positive")

    items: list[Item] = []
    seen: set[str] = set()
    page = 0
    nb_pages = 1
    first_response = None
    envelope = {
        "story_id": story_id,
        "story_title": story.get("title"),
        "story": story,
    }
    while page < nb_pages and page < max_pages:
        try:
            response = await net.get(
                url,
                params={
                    "tags": f"comment,story_{story_id}",
                    "hitsPerPage": hits_per_page,
                    "page": page,
                },
                etag=feed.etag if page == 0 else None,
            )
        except NetError:
            if page == 0:
                raise
            return Record(
                items=items,
                envelope=envelope,
                truncated=True,
                http_status=first_response.status,
                etag=etag(first_response),
            )
        if page == 0:
            first_response = response
            if response.not_modified:
                return Record(http_status=304, etag=etag(response))

        body = response.json()
        nb_pages = max(1, int(body.get("nbPages", 1)))
        for hit in body.get("hits", []):
            external_id = str(hit.get("objectID", ""))
            if (
                external_id
                and external_id not in seen
                and str(hit.get("parent_id", "")) == story_id
                and hit.get("comment_text")
            ):
                seen.add(external_id)
                items.append(Item(external_id=external_id, payload=_hit(hit)))
        page += 1

    return Record(
        items=items,
        envelope=envelope,
        truncated=page < nb_pages,
        http_status=first_response.status if first_response else None,
        etag=etag(first_response) if first_response else None,
    )


def _header(comment_html: str) -> tuple[list[str], list[str]]:
    first = _PARAGRAPH.split(comment_html, maxsplit=1)[0]
    parsed = _html(first)
    text = " ".join("".join(parsed.text).split())
    segments = [part.strip(" -–—•\t") for part in text.split("|")]
    return [part for part in segments if part], parsed.links


def _is_metadata(segment: str) -> bool:
    return bool(
        _WORKPLACE.search(segment)
        or _EMPLOYMENT.search(segment)
        or _COMPENSATION.search(segment)
        or _URL.search(segment)
    )


def _fields(
    comment_html: str,
) -> tuple[str | None, str, str | None, str | None, str | None, str | None]:
    segments, header_links = _header(comment_html)
    if not segments:
        return None, "HN hiring post", None, None, None, None

    role_indices = {index for index, value in enumerate(segments) if _ROLE.search(value)}
    plain_indices = [
        index
        for index, value in enumerate(segments)
        if index not in role_indices and not _is_metadata(value)
    ]

    company_index = None
    for index in plain_indices:
        if _LOCATION.search(segments[index]) and len(plain_indices) > 1:
            continue
        company_index = index
        break

    company = segments[company_index] if company_index is not None else None
    if company and (len(company) > 100 or (len(segments) == 1 and len(company.split()) > 10)):
        company = None

    roles = [segments[index] for index in sorted(role_indices)]
    title = " / ".join(roles)[:300] if roles else f"{company} — hiring" if company else "HN hiring post"

    location_parts = []
    for index, value in enumerate(segments):
        if index == company_index or index in role_indices:
            continue
        if _WORKPLACE.search(value) or (
            not _is_metadata(value) and (_LOCATION.search(value) or index in plain_indices)
        ):
            location_parts.append(value)
    location = " | ".join(location_parts) or None

    workplace = next((value for value in segments if _WORKPLACE.search(value)), None)
    employment = next((value for value in segments if _EMPLOYMENT.search(value)), None)

    all_links = header_links or _html(comment_html).links
    preferred = next(
        (
            link
            for link in all_links
            if re.search(
                r"/(?:jobs?|careers?|apply|positions?|openings?|hiring)(?:/|\?|$)",
                link,
                re.IGNORECASE,
            )
        ),
        None,
    )
    apply_url = preferred or (all_links[0] if all_links else None)
    return company, title, location, workplace, employment, apply_url


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Turn one top-level employer comment into one conservative posting."""
    hit = item.payload
    if not isinstance(hit, dict):
        return []
    comment_html = hit.get("comment_text")
    if not isinstance(comment_html, str) or not comment_html.strip():
        return []

    company, title, location, workplace, employment, apply_url = _fields(comment_html)
    if company is None:
        return []

    created = hit.get("created_at")
    try:
        published_at = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None
    except (TypeError, ValueError):
        published_at = None
    updated = hit.get("updated_at")
    try:
        source_updated_at = datetime.fromisoformat(updated.replace("Z", "+00:00")) if updated else None
    except (TypeError, ValueError):
        source_updated_at = None

    story_id = str(hit.get("story_id") or envelope.get("story_id") or "")
    comment_url = f"https://news.ycombinator.com/item?id={item.external_id}"
    return [
        Posting(
            external_id=item.external_id,
            title=title,
            url_posting=comment_url,
            fidelity="origin",
            state="open",
            company_raw=company,
            apply_url=apply_url,
            location_raw=location,
            description_html=comment_html,
            employment_type=employment,
            workplace_type_raw=workplace,
            published_at=published_at,
            published_at_precision="exact" if published_at else "unknown",
            source_updated_at=source_updated_at,
            source_extra={
                "author": hit.get("author"),
                "story_id": story_id,
                "story_title": hit.get("story_title") or envelope.get("story_title"),
                "parent_id": hit.get("parent_id"),
            },
        )
    ]
