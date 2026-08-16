"""Zoho Recruit public career-site adapter.

The server-rendered page carries the complete open-job array and organization
metadata in entity-escaped hidden inputs. No private Zoho API or browser
execution is required.
"""

from __future__ import annotations

import json
from datetime import datetime
from html.parser import HTMLParser

from xjobscore.net import Net
from xjobscore.records import Canonical, Feed, Item, Posting, Record


class _Inputs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        values = dict(attrs)
        if values.get("id") and values.get("value") is not None:
            self.values[str(values["id"])] = str(values["value"])


def _payload(text: str) -> tuple[list[dict], dict]:
    parser = _Inputs()
    parser.feed(text)
    parser.close()
    jobs = json.loads(parser.values.get("jobs", "[]"))
    meta = json.loads(parser.values.get("meta", "{}"))
    if not isinstance(jobs, list) or not isinstance(meta, dict):
        raise ValueError("Zoho Recruit page did not expose jobs/meta payloads")
    return [row for row in jobs if isinstance(row, dict)], meta


async def harvest(feed: Feed, net: Net) -> Record:
    path = str(feed.config.get("path", "jobs/Careers"))
    url = f"https://{feed.key}.zohorecruit.com/{path.lstrip('/')}"
    response = await net.get(url, etag=feed.etag)
    if response.not_modified:
        return Record(http_status=304, etag=response.headers.get("etag"))
    jobs, meta = _payload(response.text)
    items = [
        Item(external_id=str(row["id"]), payload=row)
        for row in jobs
        if row.get("id") is not None and row.get("Publish") is not False
    ]
    return Record(
        items=items,
        envelope={"meta": meta},
        http_status=response.status,
        etag=response.headers.get("etag"),
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
    meta = envelope.get("meta") if isinstance(envelope, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    organization = meta.get("org_info")
    organization = organization if isinstance(organization, dict) else {}
    path = str(feed.config.get("path", "jobs/Careers")).strip("/")
    url = f"https://{feed.key}.zohorecruit.com/{path}/{item.external_id}"
    locations = [
        str(row[name])
        for name in ("City", "State", "Country")
        if row.get(name)
    ]
    published = _date(row.get("Date_Opened"))
    known = {
        "id", "Posting_Title", "Job_Opening_Name", "Job_Description",
        "City", "State", "Country", "Job_Type", "Date_Opened", "Remote_Job",
    }
    return [
        Posting(
            external_id=item.external_id,
            title=str(
                row.get("Posting_Title") or row.get("Job_Opening_Name") or ""
            ),
            url_posting=url,
            apply_url=url,
            company_raw=(
                str(organization.get("company_name"))
                if organization.get("company_name")
                else feed.company_name or feed.key
            ),
            location_raw=", ".join(locations) or (
                "Remote" if row.get("Remote_Job") else None
            ),
            description_html=row.get("Job_Description"),
            employment_type=row.get("Job_Type"),
            workplace_type_raw="remote" if row.get("Remote_Job") else None,
            published_at=published,
            published_at_precision="date" if published else "unknown",
            source_extra={key: value for key, value in row.items() if key not in known},
        )
    ]
