"""Dayforce public candidate-portal adapter.

The current board is a Next.js application backed by two public endpoints.
Mutating requests require the same NextAuth CSRF handshake used by the browser;
without it ``jobposting/search`` returns 403 even though the board is public.
"""

from __future__ import annotations

from datetime import datetime

from xjobscore.net import Net
from xjobscore.records import Canonical, Feed, Item, Posting, Record
from .json_api import shared_job_type


PAGE_SIZE = 25


def _board(feed: Feed) -> str:
    return str(feed.config.get("board", "CANDIDATEPORTAL"))


def _root(feed: Feed) -> str:
    return str(feed.config.get("root", "https://jobs.dayforcehcm.com"))


def _posting_url(feed: Feed, external_id: str) -> str:
    locale = str(feed.config.get("locale", "en-US"))
    return f"{_root(feed)}/{locale}/{feed.key}/{_board(feed)}/jobs/{external_id}"


async def harvest(feed: Feed, net: Net) -> Record:
    root = _root(feed)
    locale = str(feed.config.get("locale", "en-US"))
    board = _board(feed)

    site = await net.get(
        f"{root}/api/geo/{feed.key}/sitecontext/{feed.key}/{board}/{locale}"
    )
    site_info = site.json()
    board_code = str(site_info.get("jobBoardCode") or board)

    csrf_response = await net.get(f"{root}/api/auth/csrf")
    csrf_token = csrf_response.json().get("csrfToken")
    if not isinstance(csrf_token, str) or not csrf_token:
        raise ValueError("Dayforce CSRF response did not contain csrfToken")

    items: list[Item] = []
    start = 0
    last_status = site.status
    while True:
        response = await net.post(
            f"{root}/api/geo/{feed.key}/jobposting/search",
            headers={"X-CSRF-TOKEN": csrf_token},
            json={
                "clientNamespace": feed.key,
                "jobBoardCode": board_code,
                "cultureCode": locale,
                "paginationStart": start,
            },
        )
        last_status = response.status
        payload = response.json()
        rows = payload.get("jobPostings") or []
        if not isinstance(rows, list):
            raise ValueError("Dayforce jobposting/search returned no jobPostings list")
        for row in rows:
            if isinstance(row, dict) and row.get("jobPostingId") is not None:
                items.append(Item(external_id=str(row["jobPostingId"]), payload=row))

        maximum = payload.get("maxCount")
        start += len(rows)
        if not rows or (isinstance(maximum, int) and start >= maximum):
            break
        if len(rows) < PAGE_SIZE and not isinstance(maximum, int):
            break

    return Record(
        items=items,
        envelope={"site": site_info},
        http_status=last_status,
        partition_count=max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE),
    )


def _date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    row = item.payload
    assert isinstance(row, dict)
    locations = [
        value
        for location in row.get("postingLocations") or []
        if isinstance(location, dict)
        and (value := location.get("formattedAddress"))
        and isinstance(value, str)
    ]
    url = _posting_url(feed, item.external_id)
    published = _date(row.get("postingStartTimestampUTC"))
    title = str(row.get("jobTitle") or "")
    return [
        Posting(
            external_id=item.external_id,
            title=title,
            job_type=shared_job_type(title),
            url_posting=url,
            apply_url=url,
            company_raw=feed.company_name or feed.key,
            location_raw=locations[0] if locations else None,
            locations_extra=locations[1:],
            description_html=row.get("jobDescription"),
            requisition_id=(
                str(row["jobReqId"]) if row.get("jobReqId") is not None else None
            ),
            employment_type=row.get("employmentType"),
            published_at=published,
            published_at_precision="exact" if published else "unknown",
            deadline_at=_date(row.get("postingExpiryTimestampUTC")),
            workplace_type_raw="remote" if row.get("hasVirtualLocation") else None,
            source_extra={
                "client_namespace": row.get("clientNamespace"),
                "job_board_id": row.get("jobBoardId"),
                "is_evergreen": row.get("isEvergreen"),
                "posting_locations": row.get("postingLocations"),
            },
        )
    ]
