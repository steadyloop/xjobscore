"""Oracle Taleo Enterprise classic-career-section adapter.

Classic Taleo renders list/detail records through ``api.fillList`` calls.  The
list defaults to ten rows a page, so a large career section costs dozens of
pager POSTs before a single posting is fetched.  Where the search page offers a
"results per page" selector this adapter first asks for the largest size it
lists, which is what keeps the pager short -- see ``_page_size_body``.  Each
detail HTML response is retained verbatim for replay.

Two vendor facts worth not rediscovering.  Taleo sends neither ``ETag`` nor
``Last-Modified``, so ``feed.etag`` stays NULL and the conditional-GET below
never short-circuits a poll; every poll pays the full cost.  And the pager is
not session-bound the way an AJAX form usually is: the initial GET sets only a
``locale`` cookie, and every pager POST carries the whole form -- ``csrftoken``
and target page included -- in its body.
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
from xjobscore.records import Canonical, Feed, Item, Posting, Record, Unparseable

# One width for both legs on purpose.  `Net._host_semaphores` is keyed by
# (host, concurrency), so two widths take two semaphores and the true in-flight
# count against a host is their sum.  Taleo's legs are phase-separated -- the
# pager finishes before the detail gather starts -- so unlike Workday one feed
# could not carry both at once today; the reason to share a width anyway is
# that a tenant running two career sections is two feeds on one host, and
# those phases do overlap.  See doc/networking/rate-limits.md.
#
# 12 per that doc: sweeps of kp (681 details) and textron (473) were clean at
# every width to 24, so no ceiling was found and this is a chosen value, not a
# measured one.  It is the widest width that inflated no latency on *either*
# tenant -- textron starts queueing at 24 (p50 99 -> 158ms, p95 178 -> 303ms
# for 30% more throughput) while kp does not, and 12 stays under both.  Not the
# widest clean width, deliberately: at width 24 kp's whole detail leg finishes
# in 2.1s, far inside any burst allowance, so the sweep says nothing about a
# sustained limiter -- the same trap the retired Workday config fell into when
# it read a small clean tenant as licence for width 32.
_CONCURRENCY = 12

# Largest page size worth asking for.  Taleo's own selector stops at 100.
_PAGE_SIZE_CAP = 100


class _Form(HTMLParser):
    """The search form's `input` values and `select` options, in one pass.

    The options matter as much as the inputs: the page-size control is a
    `<select>`, so a parser that only saw `<input>` could not find it.  Reading
    real tags rather than regex-matching the name is deliberate -- `dropListSize`
    also appears inside the `!|!`-delimited fillList payload, where it is data
    about the rendered page rather than a control we may drive.
    """

    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, str] = {}
        self.options: dict[str, list[str]] = {}
        self._select: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = dict(attrs)
        name = values.get("name")
        if tag == "input":
            if name:
                self.values[str(name)] = str(values.get("value") or "")
        elif tag == "select":
            self._select = str(name) if name else None
            if self._select:
                self.options.setdefault(self._select, [])
        elif tag == "option" and self._select:
            value = values.get("value")
            if value is not None:
                self.options[self._select].append(str(value))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "select":
            self._select = None


def _form(text: str) -> _Form:
    parser = _Form()
    parser.feed(text)
    parser.close()
    return parser


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
        raise Unparseable(f"Taleo page did not define {list_name}")
    return _array(match.group(2)), _array(match.group(3)), int(match.group(1))


def _fill(text: str, list_name: str) -> list[str]:
    match = re.search(
        rf"api\.fillList\('[^']+',\s*'{re.escape(list_name)}',\s*(\[.*?\])\);",
        text,
        re.S,
    )
    if not match:
        raise Unparseable(f"Taleo page did not fill {list_name}")
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


def _page_size(options: dict[str, list[str]], current: int) -> int | None:
    """The largest offered page size, or None if it would not beat `current`."""
    sizes = [int(value) for value in options.get("dropListSize", []) if value.isdigit()]
    best = max((size for size in sizes if size <= _PAGE_SIZE_CAP), default=0)
    return best if best > current else None


def _page_size_body(form: dict[str, str], size: int) -> dict[str, str]:
    """The form POST Taleo's own `dropListSize` onchange handler builds.

    `ftlPage_doRefresh` resets `currentPage`, names `actNbElementByPage` as the
    firing component, then submits the whole search form -- so this goes to the
    `.ftl`, not the `.ajax` path the pager loop uses.
    """
    return {
        **form,
        "dropListSize": str(size),
        "ftlinterfaceid": "requisitionListInterface",
        "ftlcompid": "actNbElementByPage",
        "ftlcompclass": "PageComponent",
        "jsfCmdId": "actNbElementByPage",
        "actNbElementByPage.target": "",
        "rlPager.currentPage": "1",
    }


async def _resize(
    net: Net,
    url: str,
    form: dict[str, str],
    size: int,
    shape: tuple[list[str], list[str]],
    total: int,
) -> tuple[dict[str, str], list[dict[str, list[str]]]] | None:
    """Ask for `size` rows a page; return the new form and rows, or None.

    Verified rather than trusted, and on any doubt the caller keeps the page it
    already has.  A tenant that ignores `dropListSize` answers 200 with the old
    page, and one that honours it must still describe its rows the same way.
    Never raises: 288 catalogued tenants render this form, only two are polled
    today, and a career section that declines a bigger page is not a failure.
    """
    try:
        response = await net.post(url, data=_page_size_body(form, size), concurrency=_CONCURRENCY)
    except NetError:
        return None
    parsed = _form(response.text)
    if int(parsed.values.get("listRequisition.size") or 0) != size:
        return None
    try:
        fields, labels, _ = _definition(response.text, "listRequisition")
        rows = _rows(_fill(response.text, "listRequisition"), fields, labels)
    except (ValueError, SyntaxError):
        return None
    if (fields, labels) != shape or len(rows) != min(size, total):
        return None
    # `dropListSize` is a `<select>`, so it is absent from the parsed inputs and
    # every later pager POST would omit it -- and the `.ajax` pager silently
    # falls back to ten rows a page when it does, handing back page 1 of a
    # ten-row listing while we believe we are reading page 2 of a hundred-row
    # one.  Carry the size we asked for in the form itself.
    return {**parsed.values, "dropListSize": str(size)}, rows


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

    parsed = _form(first.text)
    form = parsed.values
    # Derived once and threaded through the pager: `_definition` is a re.S scan
    # of a ~145 kB document, and the loop used to run two of them per page.
    fields, labels, _ = _definition(first.text, "listRequisition")
    rows = _rows(_fill(first.text, "listRequisition"), fields, labels)
    total = int(form.get("listRequisition.nbElements") or len(rows))
    page_size = int(form.get("listRequisition.size") or len(rows) or 1)

    # Ask for a bigger page before walking any.  Taleo defaults to ten rows,
    # and one POST that turns 69 pages into 7 beats any amount of pager
    # concurrency -- without needing to know whether Taleo tolerates
    # out-of-order page requests.  Guarded so a small board never pays a
    # ~150 kB POST to save nothing.
    wider = _page_size(parsed.options, page_size)
    if wider is not None and math.ceil(total / wider) + 1 < math.ceil(total / page_size):
        resized = await _resize(net, first.url, form, wider, (fields, labels), total)
        if resized is not None:
            form, rows = resized
            page_size = wider

    page_count = max(1, math.ceil(total / page_size))
    ajax_url = urlsplit(first.url)
    ajax_path = re.sub(r"\.ftl$", ".ajax", ajax_url.path)
    ajax_url_text = urlunsplit((ajax_url.scheme, ajax_url.netloc, ajax_path, "", ""))
    # Built once -- the form is ~200 keys and only the page number moves.  Each
    # POST is serialized before the next mutation, so reusing the dict is safe.
    pager = {
        **form,
        "ftlinterfaceid": "requisitionListInterface",
        "ftlcompid": "rlPager",
        "jsfCmdId": "rlPager",
        "ftlcompclass": "PagerComponent",
        "ftlcallback": "ftlPager_processResponse",
        "ftlajaxid": "ftlx1",
    }
    width = len(fields)
    truncated = False
    pages = 1
    for page in range(2, page_count + 1):
        pager["rlPager.currentPage"] = str(page)
        # `concurrency` on a strictly sequential await: one request in flight,
        # in page order, but opted out of Net's 1 req/s per-host floor -- which
        # was ~90% of this leg's wall clock, idle rather than in transit.  It
        # says nothing about issuing pages concurrently, which is a separate
        # question about `rlPager`'s server-side state.
        try:
            response = await net.post(ajax_url_text, data=pager, concurrency=_CONCURRENCY)
        except NetError:
            truncated = True
            break
        candidates = [
            chunk.split("!|!")
            for chunk in response.text.split("!$!")
            if chunk and len(chunk.split("!|!")) % width == 0
        ]
        if not candidates:
            # Keep the contiguous prefix rather than losing the whole run.  A
            # partial still stores its rows, and `reconcile` refuses to close
            # postings absent from an incomplete fetch, so a short listing
            # cannot manufacture a closure.
            truncated = True
            break
        rows.extend(_rows(max(candidates, key=len), fields, labels))
        pages = page

    identities: list[str] = []
    seen: set[str] = set()
    for row in rows:
        candidates = row.get("no", [])
        external_id = next((value for value in candidates if value.isdigit()), None)
        if external_id and external_id not in seen:
            seen.add(external_id)
            identities.append(external_id)

    # Pin the rendering timezone so hour/day shifts do not create revisions.
    responses = await asyncio.gather(
        *(
            net.get(_detail_url(first.url, external_id) + "&tz=GMT%2B00%3A00&tzname=UTC",
                    concurrency=_CONCURRENCY)
            for external_id in identities
        ),
        return_exceptions=True,
    )
    items: list[Item] = []
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
        # Pages actually walked, not the count the first page declared: a run
        # that broke off early must not report the coverage it planned.
        partition_count=pages,
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
    # No descRequisition block at all means this response was never a detail
    # page: Taleo answers 200 with the career section's landing page for a
    # requisition pulled between the listing walk and the detail fetch, and for
    # a JSESSIONID that expired in between. Both are the origin's answer about
    # this one posting, so `_definition` and `_fill` raise `Unparseable` and
    # silver skips the item instead of losing every other posting in the batch.
    # A block that *is* present but malformed still raises, and still fails the
    # batch -- that is the shape changing under us.
    fields, labels, _ = _definition(item.payload, "descRequisition")
    rows = _rows(_fill(item.payload, "descRequisition"), fields, labels)
    if not rows:
        raise Unparseable("Taleo detail page listed no requisition")
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
