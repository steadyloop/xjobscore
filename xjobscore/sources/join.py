"""JOIN public company-board adapter.

Company pages server-render their list state in ``__NEXT_DATA__``. Detail
pages expose schema.org JobPosting JSON-LD, which is the stable, complete
posting representation used here.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from html.parser import HTMLParser

from xjobscore.net import Net, NetError
from xjobscore.records import Canonical, Feed, Item, Posting, Record


class _Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.next_data: list[str] = []
        self.jsonld: list[str] = []
        self._target: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value for name, value in attrs}
        if tag.lower() != "script":
            return
        if values.get("id") == "__NEXT_DATA__":
            self._target = self.next_data
        elif (values.get("type") or "").lower() == "application/ld+json":
            self._target = self.jsonld

    def handle_data(self, data: str) -> None:
        if self._target is not None:
            self._target.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script":
            self._target = None


def _scripts(text: str) -> _Scripts:
    parser = _Scripts()
    parser.feed(text)
    parser.close()
    return parser


def _state(text: str) -> dict:
    scripts = _scripts(text)
    if not scripts.next_data:
        raise ValueError("JOIN page did not contain __NEXT_DATA__")
    data = json.loads("".join(scripts.next_data))
    return data["props"]["pageProps"]["initialState"]


def _job_posting(text: str) -> dict | None:
    for raw in _scripts(text).jsonld:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if isinstance(candidate, dict) and isinstance(candidate.get("@graph"), list):
                candidates.extend(candidate["@graph"])
            if not isinstance(candidate, dict):
                continue
            kind = candidate.get("@type")
            if kind == "JobPosting" or (
                isinstance(kind, list) and "JobPosting" in kind
            ):
                return candidate
    return None


async def harvest(feed: Feed, net: Net) -> Record:
    root = f"https://join.com/companies/{feed.key}"
    first = await net.get(root, etag=feed.etag)
    if first.not_modified:
        return Record(http_status=304, etag=first.headers.get("etag"))

    state = _state(first.text)
    jobs = state.get("jobs") or {}
    rows = list(jobs.get("items") or [])
    pagination = jobs.get("pagination") or {}
    page_count = int(pagination.get("pageCount") or 1)
    for page in range(2, page_count + 1):
        response = await net.get(root, params={"page": page})
        page_jobs = (_state(response.text).get("jobs") or {}).get("items") or []
        rows.extend(page_jobs)

    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        slug = row.get("idParam")
        external_id = row.get("id")
        if not slug or external_id is None or str(external_id) in seen:
            continue
        seen.add(str(external_id))
        links.append((str(external_id), f"{root}/{slug}"))

    responses = await asyncio.gather(
        *(net.get(url, concurrency=4) for _, url in links),
        return_exceptions=True,
    )
    items: list[Item] = []
    truncated = False
    for (external_id, url), response in zip(links, responses):
        if isinstance(response, NetError):
            truncated = True
            continue
        if isinstance(response, BaseException):
            raise response
        posting = _job_posting(response.text)
        if posting is None:
            truncated = True
            continue
        items.append(Item(external_id=external_id, payload=posting))

    return Record(
        items=items,
        envelope={"company": state.get("company"), "pagination": pagination},
        truncated=truncated,
        http_status=first.status,
        etag=first.headers.get("etag"),
        enumerated_item_count=len(links),
        detail_fetched_count=len(items),
        partition_count=page_count,
    )


def _date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _locations(job: dict) -> list[str]:
    raw = job.get("jobLocation") or []
    if isinstance(raw, dict):
        raw = [raw]
    values: list[str] = []
    for location in raw:
        address = location.get("address") if isinstance(location, dict) else None
        if not isinstance(address, dict):
            continue
        parts = [
            address.get(name)
            for name in ("addressLocality", "addressRegion", "addressCountry")
            if address.get(name)
        ]
        if parts:
            values.append(", ".join(str(part) for part in parts))
    return values


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    job = item.payload
    assert isinstance(job, dict)
    locations = _locations(job)
    organization = job.get("hiringOrganization")
    company = organization.get("name") if isinstance(organization, dict) else None
    identifier = job.get("identifier")
    requisition_id = (
        identifier.get("value") if isinstance(identifier, dict) else identifier
    )
    published = _date(job.get("datePosted"))
    deadline = _date(job.get("validThrough"))
    url = str(job.get("url") or "")
    return [
        Posting(
            external_id=item.external_id,
            title=str(job.get("title") or ""),
            url_posting=url,
            apply_url=url,
            company_raw=company or feed.company_name or feed.key,
            location_raw=locations[0] if locations else None,
            locations_extra=locations[1:],
            description_html=job.get("description"),
            requisition_id=str(requisition_id) if requisition_id else None,
            employment_type=job.get("employmentType"),
            published_at=published,
            published_at_precision="exact" if published else "unknown",
            deadline_at=deadline,
            source_extra={
                "industry": job.get("industry"),
                "base_salary": job.get("baseSalary"),
            },
        )
    ]
