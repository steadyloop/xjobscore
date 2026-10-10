from __future__ import annotations

import html
import json
from typing import NamedTuple

import pytest

from xjobscore.net import NetError, Response
from xjobscore.records import Feed, Item
from xjobscore.sources import PARSERS, SOURCES, avature, dayforce, docxtract, join, taleo, zohorecruit


def response(url: str, body: str | dict) -> Response:
    raw = json.dumps(body) if isinstance(body, dict) else body
    return Response(url=url, status=200, headers={}, body=raw.encode())


class Call(NamedTuple):
    method: str
    url: str
    data: dict | None = None
    concurrency: int = 1


class RouteNet:
    """A routing fake. A route may be a `Response` or a `(url, data)` callable.

    The callable form exists because a Taleo pager POST distinguishes its pages
    only by request body -- keying on the URL alone would hand back the same
    page forever, which is exactly the bug the size-selector work uncovered
    against the live board.
    """

    def __init__(self, routes: dict[tuple[str, str], Response]) -> None:
        self.routes = routes
        self.calls: list[Call] = []

    def _route(self, method, url, data=None):
        route = self.routes[(method, url)]
        return route(url, data) if callable(route) else route

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        if params:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(params)}"
        self.calls.append(Call("GET", url, None, concurrency))
        return self._route("GET", url)

    async def post(self, url, *, json=None, data=None, headers=None, concurrency=1):
        self.calls.append(Call("POST", url, dict(data or {}), concurrency))
        return self._route("POST", url, data)


def avature_detail(external_id: str, title: str) -> str:
    url = f"https://demo.avature.net/careers/JobDetail/{title}/{external_id}"
    return f"""
    <link rel="canonical" href="{url}">
    <div class="article__content__view__field article__content__view__field__value--font">
      <div class="article__content__view__field__value">{title}</div>
    </div>
    <div class="article__content__view__field">
      <div class="article__content__view__field__label">Location:</div>
      <div class="article__content__view__field__value">Seattle, WA</div>
    </div>
    <div class="article__content__view__field">
      <div class="article__content__view__field__label">Ref #</div>
      <div class="article__content__view__field__value">R-{external_id}</div>
    </div>
    <div class="article__content__view__field field--rich-text">
      <div class="article__content__view__field__value"><p>Build <b>things</b>.</p></div>
    </div>
    <a href="/careers/Login?jobId={external_id}">Apply</a>
    """


async def test_avature_enumerates_pages_and_keeps_raw_detail_html():
    root = "https://demo.avature.net/careers/SearchJobs"
    page2 = f"{root}?jobOffset=12"
    detail1 = "https://demo.avature.net/careers/JobDetail/Engineer/1"
    detail2 = "https://demo.avature.net/careers/JobDetail/Scientist/2"
    net = RouteNet({
        ("GET", root): response(root, f'<a href="{detail1}">one</a><a href="{page2}">2</a>'),
        ("GET", page2): response(page2, f'<a href="{detail2}">two</a>'),
        ("GET", detail1): response(detail1, avature_detail("1", "Engineer")),
        ("GET", detail2): response(detail2, avature_detail("2", "Scientist")),
    })
    feed = Feed(1, "avature", "demo", company_name="Demo")

    record = await avature.harvest(feed, net)
    posting = avature.parse(feed, record.items[0], record.envelope)[0]

    assert [item.external_id for item in record.items] == ["1", "2"]
    assert all(item.kind == "html" for item in record.items)
    assert record.partition_count == 2
    assert posting.title == "Engineer"
    assert posting.location_raw == "Seattle, WA"
    assert posting.requisition_id == "R-1"
    assert posting.description_html == "<p>Build <b>things</b>.</p>"


async def test_avature_reads_the_board_named_in_its_key():
    """`company/board` reads that board only: one subdomain hosts several
    (bupaanz serves /careersau and /careersnz), and a sibling board's links on
    the same page are not this feed's jobs. Slug-less links count too."""
    root = "https://bupaanz.avature.net/careersau/SearchJobs"
    page2 = f"{root}?jobOffset=12"
    detail1 = "https://bupaanz.avature.net/careersau/JobDetail/Carer/1"
    detail2 = "https://bupaanz.avature.net/CareersAU/JobDetail/2"
    sibling = "https://bupaanz.avature.net/careersnz/JobDetail/Nurse/9"
    net = RouteNet({
        ("GET", root): response(
            root, f'<a href="{detail1}">1</a><a href="{sibling}">nz</a><a href="{page2}">2</a>'
        ),
        ("GET", page2): response(page2, f'<a href="{detail2}">2</a>'),
        ("GET", detail1): response(detail1, avature_detail("1", "Carer")),
        ("GET", detail2): response(detail2, avature_detail("2", "Nurse")),
    })
    feed = Feed(1, "avature", "bupaanz/careersau", company_name="Bupa")

    record = await avature.harvest(feed, net)

    assert [item.external_id for item in record.items] == ["1", "2"]
    assert record.partition_count == 2


def test_avature_bare_key_and_locale_boards_build_their_search_url():
    assert avature._search_url(Feed(1, "avature", "deloitteus")) == (
        "https://deloitteus.avature.net/careers/SearchJobs"
    )
    assert avature._search_url(Feed(1, "avature", "astellasjapan/ja_JP/careers")) == (
        "https://astellasjapan.avature.net/ja_JP/careers/SearchJobs"
    )
    # A bare key keeps accepting a locale in front of `careers`, as before.
    assert avature._detail(Feed(1, "avature", "deloitteus")).search(
        "https://apply.deloitte.com/en_US/careers/JobDetail/Analyst/123"
    )


def test_avature_title_comes_from_banner_not_leading_unlabeled_field():
    # Boards like broadinstitute lead with an unlabeled description field.
    page = """
    <meta property="og:title" content="Data Scientist &amp; Engineer" />
    <h2 class="banner__text__title title title--banner">Data Scientist &amp; Engineer</h2>
    <div class="article__content__view__field">
      <div class="article__content__view__field__value">The Institute seeks a scientist to ...</div>
    </div>
    """
    feed = Feed(1, "avature", "demo")
    item = Item(external_id="1", payload=page, kind="html")
    assert avature.parse(feed, item, {})[0].title == "Data Scientist & Engineer"

    banner_only = page.replace('<meta property="og:title" content="Data Scientist &amp; Engineer" />', "")
    item = Item(external_id="1", payload=banner_only, kind="html")
    assert avature.parse(feed, item, {})[0].title == "Data Scientist & Engineer"


async def test_dayforce_uses_csrf_search_and_parses_complete_row():
    root = "https://jobs.dayforcehcm.com"
    site_url = f"{root}/api/geo/demo/sitecontext/demo/CANDIDATEPORTAL/en-US"
    csrf_url = f"{root}/api/auth/csrf"
    search_url = f"{root}/api/geo/demo/jobposting/search"
    row = {
        "jobPostingId": 7,
        "jobTitle": "Data Engineer Intern",
        "jobDescription": "<p>Data</p>",
        "jobReqId": "REQ-7",
        "postingLocations": [{"formattedAddress": "Chicago, IL"}],
        "postingStartTimestampUTC": "2026-08-01T12:00:00+00:00",
    }
    net = RouteNet({
        ("GET", site_url): response(site_url, {"jobBoardCode": "CANDIDATEPORTAL"}),
        ("GET", csrf_url): response(csrf_url, {"csrfToken": "token"}),
        ("POST", search_url): response(search_url, {"jobPostings": [row], "maxCount": 1}),
    })
    feed = Feed(1, "dayforce", "demo", company_name="Demo")

    record = await dayforce.harvest(feed, net)
    posting = dayforce.parse(feed, record.items[0], record.envelope)[0]

    assert posting.title == "Data Engineer Intern"
    assert posting.location_raw == "Chicago, IL"
    assert posting.requisition_id == "REQ-7"
    assert posting.description_html == "<p>Data</p>"


async def test_join_reads_next_state_then_retains_exact_jobposting_jsonld():
    root = "https://join.com/companies/demo"
    detail = f"{root}/engineer-abc"
    state = {
        "props": {"pageProps": {"initialState": {
            "company": {"name": "Demo"},
            "jobs": {"items": [{"id": 8, "idParam": "engineer-abc"}], "pagination": {"pageCount": 1}},
        }}}
    }
    job = {
        "@type": "JobPosting", "title": "Engineer", "url": detail,
        "description": "<p>Code</p>", "identifier": {"value": "REQ-8"},
        "hiringOrganization": {"name": "Demo"},
        "jobLocation": {"address": {"addressLocality": "Berlin", "addressCountry": "DE"}},
    }
    net = RouteNet({
        ("GET", root): response(root, f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(state)}</script>'),
        ("GET", detail): response(detail, f'<script type="application/ld+json">{json.dumps(job)}</script>'),
    })
    feed = Feed(1, "join", "demo")

    record = await join.harvest(feed, net)
    posting = join.parse(feed, record.items[0], record.envelope)[0]

    assert record.items[0].payload == job
    assert posting.url_posting == detail
    assert posting.location_raw == "Berlin, DE"
    assert posting.requisition_id == "REQ-8"


def taleo_definition(name: str, labels: list[str], values: list[str]) -> str:
    fields = [f"ID{index}" for index in range(len(labels))]
    return (
        f"{name}: {{ _size: 1, _hles: {fields!r}, _hlid: {labels!r} }},"
        f" api.fillList('interface', '{name}', {values!r});"
    )


async def test_taleo_keeps_raw_detail_and_parses_filllist_fields():
    root = "https://demo.taleo.net/careersection/ex/jobsearch.ftl?lang=en"
    detail = "https://demo.taleo.net/careersection/ex/jobdetail.ftl?job=42&lang=en&tz=GMT%2B00%3A00&tzname=UTC"
    list_html = (
        '<input name="listRequisition.nbElements" value="1">'
        '<input name="listRequisition.size" value="1">'
        + taleo_definition(
            "listRequisition", ["reqlistitem.no", "reqlistitem.title"], ["42", "Engineer"]
        )
    )
    labels = [
        "reqlistitem.title", "reqlistitem.contestnumber", "reqlistitem.description",
        "reqlistitem.qualification", "reqlistitem.primarylocation",
        "reqlistitem.jobschedule", "reqlistitem.postingdate",
    ]
    values = [
        "Engineer", "REQ-42", "!*!%3Cp%3EBuild%3C/p%3E", "!*!%3Cp%3ELearn%3C/p%3E",
        "US-California", "Full-time", "08/13/2026, 3:26:14 PM",
    ]
    detail_html = taleo_definition("descRequisition", labels, values)
    net = RouteNet({("GET", root): response(root, list_html), ("GET", detail): response(detail, detail_html)})
    feed = Feed(1, "taleo", "demo", config={"url": root}, company_name="Demo")

    record = await taleo.harvest(feed, net)
    posting = taleo.parse(feed, record.items[0], record.envelope)[0]

    assert record.items[0].payload == detail_html
    assert record.items[0].kind == "html"
    assert posting.title == "Engineer"
    assert posting.requisition_id == "REQ-42"
    assert posting.description_html == "<p>Build</p>\n<p>Learn</p>"
    assert posting.employment_type == "Full-time"
    assert posting.published_at.isoformat() == "2026-08-13T15:26:14"
    assert posting.url_posting == detail.split("&tz=", 1)[0]
    assert net.calls[1].url == detail


async def test_zohorecruit_reads_hidden_jobs_and_meta_payloads():
    url = "https://demo.zohorecruit.com/jobs/Careers"
    jobs = [{"id": "9", "Posting_Title": "Bioengineer", "City": "Boston", "Publish": True}]
    meta = {"org_info": {"company_name": "Demo Labs"}}
    body = (
        f'<input id="jobs" value="{html.escape(json.dumps(jobs), quote=True)}">'
        f'<input id="meta" value="{html.escape(json.dumps(meta), quote=True)}">'
    )
    net = RouteNet({("GET", url): response(url, body)})
    feed = Feed(1, "zohorecruit", "demo")

    record = await zohorecruit.harvest(feed, net)
    posting = zohorecruit.parse(feed, record.items[0], record.envelope)[0]

    assert record.items[0].payload == jobs[0]
    assert posting.title == "Bioengineer"
    assert posting.location_raw == "Boston"
    assert posting.company_raw == "Demo Labs"


@pytest.mark.parametrize("body", [
    "<html><body>Sign in</body></html>",
    '<html><input id="meta" value="{}"><h1>Page unavailable</h1></html>',
])
async def test_zohorecruit_rejects_pages_without_jobs_payload(body):
    url = "https://demo.zohorecruit.com/jobs/Careers"
    net = RouteNet({("GET", url): response(url, body)})

    with pytest.raises(ValueError, match="did not expose jobs payload"):
        await zohorecruit.harvest(Feed(1, "zohorecruit", "demo"), net)


async def test_zohorecruit_accepts_explicitly_empty_jobs():
    url = "https://demo.zohorecruit.com/jobs/Careers"
    net = RouteNet({("GET", url): response(url, '<input id="jobs" value="[]">')})

    record = await zohorecruit.harvest(Feed(1, "zohorecruit", "demo"), net)

    assert record.items == []
    assert record.http_status == 200
    assert not record.truncated


@pytest.mark.parametrize("config, url", [
    ({"url": "https://{key}.zohorecruit.eu/jobs/Careers"},
     "https://demo.zohorecruit.eu/jobs/Careers"),
    ({"path": "/jobs/Recruiting/"}, "https://demo.zohorecruit.com/jobs/Recruiting"),
])
async def test_zohorecruit_uses_configured_url_for_harvest_and_postings(config, url):
    jobs = [{"id": "9", "Posting_Title": "Bioengineer"}]
    body = f'<input id="jobs" value="{html.escape(json.dumps(jobs), quote=True)}">'
    net = RouteNet({("GET", url): response(url, body)})
    feed = Feed(1, "zohorecruit", "demo", config=config)

    record = await zohorecruit.harvest(feed, net)
    posting = zohorecruit.parse(feed, record.items[0], record.envelope)[0]

    assert net.calls[0].url == url
    assert posting.url_posting == f"{url}/9"
    assert posting.apply_url == posting.url_posting


async def test_homerun_strips_illegal_xml_control_character():
    url = "https://feed.homerun.co/demo"
    body = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>h1</id><title>Engineer</title><link href="https://example/h1"/>
      <description><![CDATA[<p>Use your real\x02world experience.</p>\n\t<p>R&amp;D</p>]]></description>
    </entry></feed>'''
    feed = Feed(1, "homerun", "demo")
    record = await docxtract.harvest(
        "homerun", feed, RouteNet({("GET", url): response(url, body)}),
    )
    posting = docxtract.parse("homerun", feed, record.items[0], {})[0]

    assert len(record.items) == 1
    assert not record.truncated
    assert "\x02" not in record.items[0].payload
    assert posting.description_html == "<p>Use your realworld experience.</p>\n\t<p>R&amp;D</p>"


async def test_homerun_and_trakstar_document_configs_parse_vendor_extensions():
    homerun_xml = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>h1</id><title>ML Intern</title><link href="https://example/h1"/>
      <author><name>Demo</name></author><description>&lt;p&gt;ML&lt;/p&gt;</description>
      <location>Amsterdam</location><type>Internship</type><department>Engineering</department>
    </entry></feed>'''
    trakstar_xml = b'''<rss xmlns:j="https://recruiterbox.com/rss/job/"><channel><item>
      <guid>t1</guid><title>Research Intern</title><link>https://example/t1</link>
      <description>&lt;p&gt;Research&lt;/p&gt;</description><j:locationCity>Dallas</j:locationCity>
      <j:positionType>Internship</j:positionType><j:team>R&amp;D</j:team>
    </item></channel></rss>'''
    homerun_url = "https://feed.homerun.co/demo"
    trakstar_url = "https://demo.hire.trakstar.com/jobfeeds/demo"
    homerun_record = await docxtract.harvest(
        "homerun", Feed(1, "homerun", "demo"),
        RouteNet({("GET", homerun_url): response(homerun_url, homerun_xml.decode())}),
    )
    trakstar_record = await docxtract.harvest(
        "trakstar", Feed(2, "trakstar", "demo"),
        RouteNet({("GET", trakstar_url): response(trakstar_url, trakstar_xml.decode())}),
    )

    homerun_posting = docxtract.parse("homerun", Feed(1, "homerun", "demo"), homerun_record.items[0], {})[0]
    trakstar_posting = docxtract.parse("trakstar", Feed(2, "trakstar", "demo"), trakstar_record.items[0], {})[0]
    assert (homerun_posting.company_raw, homerun_posting.location_raw, homerun_posting.employment_type) == ("Demo", "Amsterdam", "Internship")
    assert (trakstar_posting.location_raw, trakstar_posting.employment_type, trakstar_posting.source_extra["team"]) == ("Dallas", "Internship", "R&D")


def test_all_seven_cataloged_vendors_are_registered_end_to_end():
    names = {"avature", "dayforce", "homerun", "join", "taleo", "trakstar", "zohorecruit"}
    assert names <= SOURCES.keys()
    assert names <= PARSERS.keys()


# --- taleo pagination ------------------------------------------------------
#
# The pre-existing taleo test above uses nbElements=1, so page_count is 1 and
# the pager never runs. Everything below covers the leg that was 76-81% of a
# live run's wall clock and had no coverage at all.

TALEO_ROOT = "https://demo.taleo.net/careersection/ex/jobsearch.ftl?lang=en"
TALEO_AJAX = "https://demo.taleo.net/careersection/ex/jobsearch.ajax"
TALEO_LIST_LABELS = ["reqlistitem.no", "reqlistitem.title"]


def taleo_rows(start: int, count: int) -> list[tuple[str, str]]:
    return [(str(1000 + i), f"Engineer {i}") for i in range(start, start + count)]


def taleo_list_page(*, total: int, size: int, rows, sizes=()) -> str:
    """A search page: the form inputs, the optional size `select`, the fillList."""
    select = ""
    if sizes:
        options = "".join(f'<option value="{s}">{s}</option>' for s in sizes)
        select = f'<select id="x" name="dropListSize">{options}</select>'
    return (
        f'<input name="listRequisition.nbElements" value="{total}">'
        f'<input name="listRequisition.size" value="{size}">'
        '<input name="csrftoken" value="tok">' + select
        + taleo_definition(
            "listRequisition", TALEO_LIST_LABELS, [v for row in rows for v in row]
        )
    )


def taleo_pager_body(rows) -> str:
    """The `!$!`/`!|!` AJAX payload a pager POST answers with."""
    return "header!$!" + "!|!".join(v for row in rows for v in row)


def taleo_pager(pages: dict):
    """Route pager POSTs by their requested page. A value may be an exception."""
    def handler(url, data):
        page = pages[int(data["rlPager.currentPage"])]
        if isinstance(page, Exception):
            raise page
        return response(url, taleo_pager_body(page))
    return handler


def taleo_details(rows) -> dict:
    routes = {}
    for external_id, title in rows:
        url = f"https://demo.taleo.net/careersection/ex/jobdetail.ftl?job={external_id}&lang=en&tz=GMT%2B00%3A00&tzname=UTC"
        routes[("GET", url)] = response(
            url, taleo_definition("descRequisition", ["reqlistitem.title"], [title])
        )
    return routes


def taleo_feed() -> Feed:
    return Feed(1, "taleo", "demo", config={"url": TALEO_ROOT}, company_name="Demo")


async def test_taleo_walks_every_pager_page_when_no_size_selector():
    rows = taleo_rows(0, 25)
    net = RouteNet({
        ("GET", TALEO_ROOT): response(
            TALEO_ROOT, taleo_list_page(total=25, size=10, rows=rows[:10])
        ),
        ("POST", TALEO_AJAX): taleo_pager({2: rows[10:20], 3: rows[20:]}),
        **taleo_details(rows),
    })

    record = await taleo.harvest(taleo_feed(), net)

    posts = [call for call in net.calls if call.method == "POST"]
    assert [call.data["rlPager.currentPage"] for call in posts] == ["2", "3"]
    assert all(call.url == TALEO_AJAX for call in posts)
    assert [item.external_id for item in record.items] == [row[0] for row in rows]
    assert record.partition_count == 3
    assert record.truncated is False


async def test_taleo_asks_for_the_largest_offered_page_size():
    rows = taleo_rows(0, 250)
    net = RouteNet({
        ("GET", TALEO_ROOT): response(
            TALEO_ROOT,
            taleo_list_page(total=250, size=10, rows=rows[:10], sizes=(5, 10, 25, 50, 100)),
        ),
        ("POST", TALEO_ROOT): response(
            TALEO_ROOT,
            taleo_list_page(total=250, size=100, rows=rows[:100], sizes=(5, 10, 25, 50, 100)),
        ),
        ("POST", TALEO_AJAX): taleo_pager({2: rows[100:200], 3: rows[200:]}),
        **taleo_details(rows),
    })

    record = await taleo.harvest(taleo_feed(), net)

    resize = [call for call in net.calls if call.method == "POST" and call.url == TALEO_ROOT]
    assert len(resize) == 1
    assert resize[0].data["dropListSize"] == "100"
    assert resize[0].data["ftlcompid"] == "actNbElementByPage"
    assert resize[0].data["ftlcompclass"] == "PageComponent"
    # 250 postings in 3 pages instead of 25, and every id still collected.
    assert len([c for c in net.calls if c.method == "POST" and c.url == TALEO_AJAX]) == 2
    assert record.partition_count == 3
    assert [item.external_id for item in record.items] == [row[0] for row in rows]


async def test_taleo_carries_the_page_size_into_every_pager_post():
    """The `<select>` value is absent from the inputs, so it must be added back.

    Live, a pager POST that omits `dropListSize` silently reverts to ten rows a
    page and returns page 1 of that listing -- 200 OK, well-formed, and wrong.
    """
    rows = taleo_rows(0, 250)
    net = RouteNet({
        ("GET", TALEO_ROOT): response(
            TALEO_ROOT,
            taleo_list_page(total=250, size=10, rows=rows[:10], sizes=(10, 100)),
        ),
        ("POST", TALEO_ROOT): response(
            TALEO_ROOT, taleo_list_page(total=250, size=100, rows=rows[:100], sizes=(10, 100))
        ),
        ("POST", TALEO_AJAX): taleo_pager({2: rows[100:200], 3: rows[200:]}),
        **taleo_details(rows),
    })

    await taleo.harvest(taleo_feed(), net)

    pager = [c for c in net.calls if c.method == "POST" and c.url == TALEO_AJAX]
    assert pager and all(call.data["dropListSize"] == "100" for call in pager)


async def test_taleo_falls_back_when_the_tenant_ignores_the_size_request():
    rows = taleo_rows(0, 250)
    pages = {page: rows[(page - 1) * 10:page * 10] for page in range(2, 26)}
    net = RouteNet({
        ("GET", TALEO_ROOT): response(
            TALEO_ROOT, taleo_list_page(total=250, size=10, rows=rows[:10], sizes=(10, 100))
        ),
        # Echoes the old size: the board declined. Not an error.
        ("POST", TALEO_ROOT): response(
            TALEO_ROOT, taleo_list_page(total=250, size=10, rows=rows[:10], sizes=(10, 100))
        ),
        ("POST", TALEO_AJAX): taleo_pager(pages),
        **taleo_details(rows),
    })

    record = await taleo.harvest(taleo_feed(), net)

    assert record.partition_count == 25
    assert record.truncated is False
    assert [item.external_id for item in record.items] == [row[0] for row in rows]


async def test_taleo_skips_the_size_request_when_it_would_not_pay():
    rows = taleo_rows(0, 15)
    net = RouteNet({
        ("GET", TALEO_ROOT): response(
            TALEO_ROOT, taleo_list_page(total=15, size=10, rows=rows[:10], sizes=(10, 100))
        ),
        ("POST", TALEO_AJAX): taleo_pager({2: rows[10:]}),
        **taleo_details(rows),
    })

    record = await taleo.harvest(taleo_feed(), net)

    # Two pages either way, so the ~150 kB form POST buys nothing.
    assert [c for c in net.calls if c.method == "POST" and c.url == TALEO_ROOT] == []
    assert len(record.items) == 15


async def test_taleo_keeps_the_pages_it_read_when_a_pager_page_fails():
    rows = taleo_rows(0, 50)
    net = RouteNet({
        ("GET", TALEO_ROOT): response(
            TALEO_ROOT, taleo_list_page(total=50, size=10, rows=rows[:10])
        ),
        ("POST", TALEO_AJAX): taleo_pager({
            2: rows[10:20],
            3: NetError("HTTP 503", url=TALEO_AJAX, status=503),
            4: rows[30:40],
            5: rows[40:],
        }),
        **taleo_details(rows),
    })

    record = await taleo.harvest(taleo_feed(), net)

    # A contiguous prefix, not a hole: pages 4-5 are dropped even though they
    # would have answered, because rows after a gap cannot be placed.
    assert record.truncated is True
    assert [item.external_id for item in record.items] == [row[0] for row in rows[:20]]
    assert record.partition_count == 2


async def test_taleo_keeps_the_pages_it_read_when_a_page_has_no_rows():
    rows = taleo_rows(0, 30)
    net = RouteNet({
        ("GET", TALEO_ROOT): response(
            TALEO_ROOT, taleo_list_page(total=30, size=10, rows=rows[:10])
        ),
        ("POST", TALEO_AJAX): taleo_pager({2: rows[10:20], 3: []}),
        **taleo_details(rows),
    })

    record = await taleo.harvest(taleo_feed(), net)

    assert record.truncated is True
    assert len(record.items) == 20


async def test_taleo_raises_when_the_first_page_cannot_be_parsed():
    net = RouteNet({("GET", TALEO_ROOT): response(TALEO_ROOT, "<html>nothing here</html>")})

    with pytest.raises(ValueError):
        await taleo.harvest(taleo_feed(), net)


async def test_taleo_uses_one_width_for_the_pager_and_detail_legs():
    """`Net._host_semaphores` is keyed by (host, width), so two widths would
    take two semaphores and the real in-flight count would be their sum."""
    rows = taleo_rows(0, 25)
    net = RouteNet({
        ("GET", TALEO_ROOT): response(
            TALEO_ROOT, taleo_list_page(total=25, size=10, rows=rows[:10])
        ),
        ("POST", TALEO_AJAX): taleo_pager({2: rows[10:20], 3: rows[20:]}),
        **taleo_details(rows),
    })

    await taleo.harvest(taleo_feed(), net)

    widths = {call.concurrency for call in net.calls if call.url != TALEO_ROOT}
    assert widths == {taleo._CONCURRENCY}


async def test_taleo_reports_partial_when_a_detail_fetch_fails():
    rows = taleo_rows(0, 3)
    routes = taleo_details(rows)
    dead = f"https://demo.taleo.net/careersection/ex/jobdetail.ftl?job={rows[1][0]}&lang=en&tz=GMT%2B00%3A00&tzname=UTC"

    def fail(url, data=None):
        raise NetError("HTTP 500", url=dead, status=500)

    routes[("GET", dead)] = fail
    net = RouteNet({
        ("GET", TALEO_ROOT): response(
            TALEO_ROOT, taleo_list_page(total=3, size=10, rows=rows)
        ),
        **routes,
    })

    record = await taleo.harvest(taleo_feed(), net)

    assert record.truncated is True
    assert record.enumerated_item_count == 3
    assert record.detail_fetched_count == 2
