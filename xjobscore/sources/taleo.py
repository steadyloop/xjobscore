"""Oracle Taleo Enterprise classic-career-section adapter.

Classic Taleo renders list/detail records through ``api.fillList`` calls.  The
pager is an AJAX form tied to the initial cookie session; Net's shared client
keeps that session while this adapter enumerates every list page.  Each detail
HTML response is retained verbatim for replay.
"""

from __future__ import annotations

import ast
import asyncio
import math
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit, urlunsplit

from xjobscore.net import Net, NetError, etag
from xjobscore.records import Canonical, Feed, Item, Posting, Record


class _Inputs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        values = dict(attrs)
        name = values.get("name")
        if name:
            self.values[str(name)] = str(values.get("value") or "")


def _inputs(text: str) -> dict[str, str]:
    parser = _Inputs()
    parser.feed(text)
    parser.close()
    return parser.values


def _array(raw: str) -> list[str]:
    value = ast.literal_eval(raw)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("Taleo fillList payload was not a string array")
    return value


def _definition(text: str, list_name: str) -> tuple[list[str], list[str], int]:
    match = re.search(
        rf"\b{re.escape(list_name)}\s*:\s*\{{\s*_size:\s*(\d+),\s*"
        rf"_hles:\s*(\[.*?\]),\s*_hlid:\s*(\[.*?\])",
        text,
        re.S,
    )
    if not match:
        raise ValueError(f"Taleo page did not define {list_name}")
    return _array(match.group(2)), _array(match.group(3)), int(match.group(1))


def _fill(text: str, list_name: str) -> list[str]:
    match = re.search(
        rf"api\.fillList\('[^']+',\s*'{re.escape(list_name)}',\s*(\[.*?\])\);",
        text,
        re.S,
    )
    if not match:
        raise ValueError(f"Taleo page did not fill {list_name}")
    return _array(match.group(1))


def _rows(values: list[str], fields: list[str], labels: list[str]) -> list[dict[str, list[str]]]:
    if not fields or len(fields) != len(labels) or len(values) % len(fields):
        raise ValueError("Taleo list field/value cardinality mismatch")
    result: list[dict[str, list[str]]] = []
    for start in range(0, len(values), len(fields)):
        row: dict[str, list[str]] = {}
        for label, value in zip(labels, values[start:start + len(fields)]):
            row.setdefault(label.removeprefix("reqlistitem."), []).append(value)
        result.append(row)
    return result


def _decoded(value: str) -> str:
    return unquote(value.removeprefix("!*!"))


def _value(row: dict[str, list[str]], name: str, *, longest: bool = False) -> str | None:
    values = [_decoded(value) for value in row.get(name, []) if value]
    if not values:
        return None
    return max(values, key=len) if longest else values[0]


def _list_rows(text: str, values: list[str] | None = None) -> list[dict[str, list[str]]]:
    fields, labels, _ = _definition(text, "listRequisition")
    return _rows(values if values is not None else _fill(text, "listRequisition"), fields, labels)


def _search_url(feed: Feed) -> str:
    url = feed.config.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("Taleo feed requires config.url for its career section")
    return url


def _detail_url(search_url: str, external_id: str) -> str:
    parts = urlsplit(search_url)
    directory = parts.path.rsplit("/", 1)[0]
    return urlunsplit((parts.scheme, parts.netloc, f"{directory}/jobdetail.ftl", f"job={external_id}&lang=en", ""))


async def harvest(feed: Feed, net: Net) -> Record:
    search_url = _search_url(feed)
    first = await net.get(search_url, etag=feed.etag)
    if first.not_modified:
        return Record(http_status=304, etag=etag(first))

    form = _inputs(first.text)
    initial_rows = _list_rows(first.text)
    total = int(form.get("listRequisition.nbElements") or len(initial_rows))
    page_size = int(form.get("listRequisition.size") or len(initial_rows) or 1)
    page_count = max(1, math.ceil(total / page_size))
    rows = list(initial_rows)
    ajax_url = urlsplit(first.url)
    ajax_path = re.sub(r"\.ftl$", ".ajax", ajax_url.path)
    ajax_url_text = urlunsplit((ajax_url.scheme, ajax_url.netloc, ajax_path, "", ""))
    for page in range(2, page_count + 1):
        data = {
            **form,
            "ftlinterfaceid": "requisitionListInterface",
            "ftlcompid": "rlPager",
            "jsfCmdId": "rlPager",
            "ftlcompclass": "PagerComponent",
            "ftlcallback": "ftlPager_processResponse",
            "ftlajaxid": "ftlx1",
            "rlPager.currentPage": str(page),
        }
        response = await net.post(ajax_url_text, data=data)
        chunks = response.text.split("!$!")
        width = len(_definition(first.text, "listRequisition")[0])
        candidates = [
            chunk.split("!|!")
            for chunk in chunks
            if chunk and len(chunk.split("!|!")) % width == 0
        ]
        if not candidates:
            raise ValueError("Taleo pager response contained no requisition rows")
        values = max(candidates, key=len)
        rows.extend(_list_rows(first.text, values))

    identities: list[str] = []
    seen: set[str] = set()
    for row in rows:
        candidates = row.get("no", [])
        external_id = next((value for value in candidates if value.isdigit()), None)
        if external_id and external_id not in seen:
            seen.add(external_id)
            identities.append(external_id)

    responses = await asyncio.gather(
        *(net.get(_detail_url(first.url, external_id), concurrency=4) for external_id in identities),
        return_exceptions=True,
    )
    items: list[Item] = []
    truncated = False
    for external_id, response in zip(identities, responses):
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
        enumerated_item_count=len(identities),
        detail_fetched_count=len(items),
        partition_count=page_count,
    )


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    for pattern in ("%m/%d/%Y, %I:%M:%S %p", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            pass
    return None


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    assert isinstance(item.payload, str)
    fields, labels, _ = _definition(item.payload, "descRequisition")
    rows = _rows(_fill(item.payload, "descRequisition"), fields, labels)
    if len(rows) != 1:
        raise ValueError("Taleo detail page did not contain exactly one requisition")
    row = rows[0]
    description_parts = []
    for name in ("description", "qualification"):
        if value := _value(row, name, longest=True):
            if value not in description_parts:
                description_parts.append(value)
    location = _value(row, "primarylocation", longest=True)
    other = _value(row, "otherlocations", longest=True)
    locations_extra = [other] if other and other != location else []
    published = _date(_value(row, "postingdate"))
    url = _detail_url(_search_url(feed), item.external_id)
    extra_names = (
        "jobfield", "jobschedule", "joblevel", "jobtype", "jobshift",
        "willtravel", "organization",
    )
    return [
        Posting(
            external_id=item.external_id,
            title=_value(row, "title", longest=True) or "",
            url_posting=url,
            apply_url=url,
            company_raw=feed.company_name or feed.key,
            location_raw=location,
            locations_extra=locations_extra,
            description_html="\n".join(description_parts) or None,
            requisition_id=_value(row, "contestnumber"),
            employment_type=_value(row, "jobschedule") or _value(row, "jobtype"),
            published_at=published,
            published_at_precision="date" if published else "unknown",
            source_extra={name: _value(row, name, longest=True) for name in extra_names},
        )
    ]
