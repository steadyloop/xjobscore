from __future__ import annotations

import html
import json

from xjobscore.net import Response
from xjobscore.records import Feed
from xjobscore.sources import PARSERS, SOURCES, avature, dayforce, docxtract, join, taleo, zohorecruit


def response(url: str, body: str | dict) -> Response:
    raw = json.dumps(body) if isinstance(body, dict) else body
    return Response(url=url, status=200, headers={}, body=raw.encode())


class RouteNet:
    def __init__(self, routes: dict[tuple[str, str], Response]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        if params:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(params)}"
        self.calls.append(("GET", url))
        return self.routes[("GET", url)]

    async def post(self, url, *, json=None, data=None, headers=None, concurrency=1):
        self.calls.append(("POST", url))
        return self.routes[("POST", url)]


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
    detail = "https://demo.taleo.net/careersection/ex/jobdetail.ftl?job=42&lang=en"
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
    assert posting.published_at is not None


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
