"""ATS URL extraction and board-validation definitions for keyscan.

This module is intentionally data-first.  The same regular expressions are
compiled by Python for page/Wayback discovery and rendered into DuckDB SQL for
Common Crawl, keeping the two discovery paths in lockstep.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


STOP = frozenset(
    """
    www www2 api apis app apps admin help support docs doc status static cdn
    assets img images media mail smtp ftp dev staging stage test demo portal my
    go get info news resources partners developers community learn academy
    security trust legal privacy terms careers career jobs job account accounts
    auth login signin signup id email links link track click files file public
    shared share embed widget widgets preview sandbox error 404 favicon.ico
    robots.txt sitemap.xml index.html about contact blog press events pricing
    """.split()
)
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")
_SCHEME_HOST = re.compile(r"^(https?://[^/?#]+)")


@dataclass(frozen=True, slots=True)
class Pattern:
    rx: str
    groups: tuple[int, ...] = (1,)


@dataclass(frozen=True, slots=True)
class Rule:
    source: str
    domains: tuple[str, ...]
    where_sql: str
    patterns: tuple[Pattern, ...]
    probe_kind: str
    probe_url: str
    board_url: str
    fallbacks: tuple[str, ...] = ()
    name_url: str = ""
    name_fallbacks: tuple[str, ...] = ()
    method: str = "GET"
    body: dict | None = None
    headers: dict[str, str] | None = None
    count_path: tuple[str | int, ...] = ()
    absent_markers: tuple[str, ...] = ()
    xml_required_markers: tuple[str, ...] = ()
    html_required_markers: tuple[str, ...] = ()
    html_any_markers: tuple[str, ...] = ()
    catalog_requires: tuple[str, ...] = ()

    def urls(self, token: str) -> dict[str, str]:
        fields = {"token": token}
        fields.update({f"t{i}": value for i, value in enumerate(token.split("/"))})
        return fields


def _r(
    source: str, domains: tuple[str, ...], where_sql: str, patterns: tuple[Pattern, ...],
    probe_kind: str, probe_url: str, board_url: str, **kwargs: object,
) -> Rule:
    return Rule(source, domains, where_sql, patterns, probe_kind, probe_url, board_url, **kwargs)


RULES: tuple[Rule, ...] = (
    _r("greenhouse", ("greenhouse.io",), "url_host_name LIKE '%greenhouse.io'", (
        Pattern(r"^https?://(?:job-)?boards(?:\.eu)?\.greenhouse\.io/embed/job_(?:board|app)\?(?:[^&]*&)*for=([A-Za-z0-9_.-]+)"),
        Pattern(r"^https?://(?:job-)?boards(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_.-]+)"),
    ), "json", "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=false", "https://job-boards.greenhouse.io/{token}",
       fallbacks=("https://boards-api.eu.greenhouse.io/v1/boards/{token}/jobs?content=false",),
       name_url="https://boards-api.greenhouse.io/v1/boards/{token}",
       name_fallbacks=("https://boards-api.eu.greenhouse.io/v1/boards/{token}",)),
    _r("lever", ("lever.co",), "url_host_name IN ('jobs.lever.co', 'jobs.eu.lever.co')", (Pattern(r"^https?://jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)"),), "json", "https://api.lever.co/v0/postings/{token}?mode=json", "https://jobs.lever.co/{token}", fallbacks=("https://api.eu.lever.co/v0/postings/{token}?mode=json",)),
    _r("ashby", ("ashbyhq.com",), "url_host_name = 'jobs.ashbyhq.com'", (Pattern(r"^https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)"),), "json", "https://api.ashbyhq.com/posting-api/job-board/{token}", "https://jobs.ashbyhq.com/{token}"),
    _r("workday", ("myworkdayjobs.com",), "url_host_name LIKE '%.myworkdayjobs.com'", (
        Pattern(r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/wday/cxs/[^/]+/([^/?#]+)", (1, 2, 3)),
        Pattern(r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/[a-z]{2}(?:-[A-Za-z]{2})?/([^/?#]+)", (1, 2, 3)),
        Pattern(r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/([^/?#]+)", (1, 2, 3)),
    ), "json", "https://{t0}.{t1}.myworkdayjobs.com/wday/cxs/{t0}/{t2}/jobs", "https://{t0}.{t1}.myworkdayjobs.com/en-US/{t2}", method="POST", body={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}),
    _r("smartrecruiters", ("smartrecruiters.com",), "url_host_name = 'jobs.smartrecruiters.com'", (Pattern(r"^https?://jobs\.smartrecruiters\.com/([A-Za-z0-9_.-]+)"),), "json", "https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=1", "https://jobs.smartrecruiters.com/{token}"),
    _r("workable", ("workable.com",), "url_host_name LIKE '%workable.com'", (
        Pattern(r"^https?://apply\.workable\.com/([A-Za-z0-9_-]+)"), Pattern(r"^https?://([a-z0-9-]+)\.workable\.com/"),
    ), "json", "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true", "https://apply.workable.com/{token}/"),
    _r("recruitee", ("recruitee.com",), "url_host_name LIKE '%.recruitee.com'", (Pattern(r"^https?://([a-z0-9-]+)\.recruitee\.com"),), "json", "https://{token}.recruitee.com/api/offers/", "https://{token}.recruitee.com/"),
    _r("bamboohr", ("bamboohr.com",), "url_host_name LIKE '%.bamboohr.com' AND (url_path LIKE '/careers%' OR url_path LIKE '/jobs%')", (Pattern(r"^https?://([a-z0-9-]+)\.bamboohr\.com/(?:careers|jobs)"),), "json", "https://{token}.bamboohr.com/careers/list", "https://{token}.bamboohr.com/careers"),
    _r("breezy", ("breezy.hr",), "url_host_name LIKE '%.breezy.hr'", (Pattern(r"^https?://([a-z0-9-]+)\.breezy\.hr"),), "json", "https://{token}.breezy.hr/json", "https://{token}.breezy.hr/"),
    _r("jazzhr", ("applytojob.com",), "url_host_name LIKE '%.applytojob.com'", (Pattern(r"^https?://([a-z0-9-]+)\.applytojob\.com"),), "html", "https://{token}.applytojob.com/apply/jobs/", "https://{token}.applytojob.com/apply/jobs/", html_required_markers=("jazzhr",), html_any_markers=("form_search_jobs", "job_title_link", "/apply/jobs/details/")),
    _r("personio", ("personio.de", "personio.com"), "url_host_name LIKE '%.jobs.personio.de' OR url_host_name LIKE '%.jobs.personio.com'", (Pattern(r"^https?://([a-z0-9-]+)\.jobs\.personio\.(?:de|com)"),), "xml", "https://{token}.jobs.personio.de/xml", "https://{token}.jobs.personio.de/", fallbacks=("https://{token}.jobs.personio.com/xml",)),
    _r("rippling", ("rippling.com",), "url_host_name = 'ats.rippling.com'", (
        Pattern(r"^https?://ats\.rippling\.com/[a-z]{2}(?:-[A-Za-z]{2})?/([A-Za-z0-9_-]+)(?:/|$)"),
        Pattern(r"^https?://ats\.rippling\.com/([A-Za-z0-9_-]+)"),
    ), "json", "https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs", "https://ats.rippling.com/{token}/jobs"),
    _r("pinpoint", ("pinpointhq.com",), "url_host_name LIKE '%.pinpointhq.com'", (Pattern(r"^https?://([a-z0-9-]+)\.pinpointhq\.com"),), "json", "https://{token}.pinpointhq.com/postings.json", "https://{token}.pinpointhq.com/"),
    _r("comeet", ("comeet.co", "comeet.com"), "url_host_name IN ('comeet.co', 'www.comeet.co', 'comeet.com', 'www.comeet.com') AND url_path LIKE '/jobs/%'", (
        Pattern(r"^https?://(?:www\.)?comeet\.(?:co|com)/jobs/([A-Za-z0-9_-]+)/([A-Za-z0-9._-]+)", (1, 2)),
    ), "comeet", "https://www.comeet.co/careers-api/2.0/company/{t1}/positions", "https://www.comeet.com/jobs/{t0}/{t1}"),
    _r("jobvite", ("jobvite.com",), "url_host_name = 'jobs.jobvite.com'", (Pattern(r"^https?://jobs\.jobvite\.com/([A-Za-z0-9_-]+)"),), "html", "https://jobs.jobvite.com/{token}", "https://jobs.jobvite.com/{token}", absent_markers=("page not found", "no longer available")),
    _r("teamtailor", ("teamtailor.com",), "url_host_name LIKE '%.teamtailor.com'", (Pattern(r"^https?://([a-z0-9-]+)\.teamtailor\.com"),), "html", "https://{token}.teamtailor.com/jobs", "https://{token}.teamtailor.com/jobs", absent_markers=("page not found",)),
    # The branded iCIMS API token is the label after ``careers.``, not the
    # unrelated tenant slug in ``*.icims.com`` portal URLs.
    # It has no bounded vendor domain for a practical Common Crawl scan.
    _r("icims", (), "FALSE", (Pattern(r"^https?://careers\.([a-z0-9-]+)\.com(?:/|$)"),), "json", "https://careers.{token}.com/api/jobs?page=1", "https://careers.{token}.com/api/jobs?page=1"),
    _r("taleo", ("taleo.net",), "url_host_name LIKE '%.taleo.net'", (Pattern(r"^https?://([a-z0-9-]+)\.taleo\.net"),), "html", "https://{token}.taleo.net/careersection/", "https://{token}.taleo.net/careersection/"),
    _r("successfactors", ("successfactors.com", "successfactors.eu"), "url_host_name LIKE 'career%.successfactors.%' AND url_query LIKE '%company=%'", (Pattern(r"^https?://(career\d*\.successfactors\.(?:com|eu))/career\?(?:[^&]*&)*company=([A-Za-z0-9_-]+)", (1, 2)),), "html", "https://{t0}/career?company={t1}", "https://{t0}/career?company={t1}"),
    # PCS-X needs both the serving host and the board's domain query value.
    # Matching only URLs that expose both avoids inventing config for vanity
    # hosts, while still covering standard *.eightfold.ai boards and API URLs.
    _r("eightfold", ("eightfold.ai",), "url_host_name LIKE '%.eightfold.ai' AND url_query LIKE '%domain=%'", (
        Pattern(r"^https?://([A-Za-z0-9.-]+)/(?:api/pcsx/search|careers)(?:[^?#]*)\?(?:[^&]*&)*domain=([A-Za-z0-9.-]+)", (1, 2)),
    ), "json", "https://{t0}/api/pcsx/search?domain={t1}&query=&location=&start=0", "https://{t0}/careers?domain={t1}",
       count_path=("data", "count"), catalog_requires=("host", "domain", "company_raw", "query")),
    _r("oracle", ("oraclecloud.com",), "url_host_name LIKE '%.oraclecloud.com' AND url_path LIKE '/hcmUI/CandidateExperience/%'", (
        Pattern(r"^https?://([a-z0-9.-]+)\.oraclecloud\.com/hcmUI/CandidateExperience/[^/]+/sites/([A-Za-z0-9_-]+)", (1, 2)),
    ), "json", (
        "https://{t0}.oraclecloud.com/hcmRestApi/resources/latest/"
        "recruitingCEJobRequisitions?onlyData=true&finder=findReqs;"
        "siteNumber={t1},limit=1&expand=requisitionList"
    ), "https://{t0}.oraclecloud.com/hcmUI/CandidateExperience/en/sites/{t1}",
       count_path=("items", 0, "TotalJobsCount"), catalog_requires=("company_raw",)),
    _r("ukg", ("ultipro.com",), "regexp_matches(url_host_name, '^recruiting[0-9]*\\.ultipro\\.com$') AND url_path LIKE '%/JobBoard/%'", (
        Pattern(r"^https?://(recruiting[0-9]*\.ultipro\.com)/([A-Za-z0-9_]+)/JobBoard/([0-9a-fA-F-]{36})(?:/|$)", (1, 2, 3)),
    ), "json", "https://{t0}/{t1}/JobBoard/{t2}/JobBoardView/LoadSearchResults", "https://{t0}/{t1}/JobBoard/{t2}",
       method="POST", body={
           "opportunitySearch": {
               "Top": 1, "Skip": 0, "QueryString": "", "Filters": [],
               "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False}],
           },
           "matchCriteria": {
               "PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [],
               "Skills": [], "hasNoLicenses": False, "SkippedSkills": [],
           },
       }, count_path=("totalCount",), catalog_requires=("company_raw",)),
    _r("paylocity", ("paylocity.com",), "url_host_name = 'recruiting.paylocity.com'", (Pattern(r"^https?://recruiting\.paylocity\.com/recruiting/jobs/All/([0-9a-fA-F-]{36})"),), "html", "https://recruiting.paylocity.com/recruiting/jobs/All/{token}", "https://recruiting.paylocity.com/recruiting/jobs/All/{token}"),
    _r("dayforce", ("dayforcehcm.com",), "url_host_name = 'jobs.dayforcehcm.com' AND regexp_matches(url_path, '^/[a-z]{2}(-[A-Za-z]{2})?/[A-Za-z0-9_-]+/CANDIDATEPORTAL(/|$)')", (
        Pattern(r"^https?://jobs\.dayforcehcm\.com/[a-z]{2}(?:-[A-Za-z]{2})?/([A-Za-z0-9_-]+)/CANDIDATEPORTAL(?:/|[?#]|$)"),
    ), "html", "https://jobs.dayforcehcm.com/en-US/{token}/CANDIDATEPORTAL", "https://jobs.dayforcehcm.com/en-US/{token}/CANDIDATEPORTAL", html_required_markers=("dayforce", "clientnamespace"), html_any_markers=("job board", "search jobs", "job postings")),
    _r("avature", ("avature.net",), "url_host_name LIKE '%.avature.net'", (Pattern(r"^https?://([a-z0-9-]+)\.avature\.net"),), "html", "https://{token}.avature.net/careers", "https://{token}.avature.net/careers", html_required_markers=("avature.portal.",), html_any_markers=("search jobs", "find jobs", "/jobdetail/", "requesttype&quot;:&quot;job&quot;")),
    _r("zohorecruit", ("zohorecruit.com", "zohorecruit.eu"), "url_host_name LIKE '%.zohorecruit.com' OR url_host_name LIKE '%.zohorecruit.eu'", (Pattern(r"^https?://([a-z0-9-]+)\.zohorecruit\.(?:com|eu)"),), "html", "https://{token}.zohorecruit.com/jobs/Careers", "https://{token}.zohorecruit.com/jobs/Careers", fallbacks=("https://{token}.zohorecruit.eu/jobs/Careers",)),
    _r("homerun", ("homerun.co",), "url_host_name LIKE '%.homerun.co'", (Pattern(r"^https?://([a-z0-9-]+)\.homerun\.co"),), "html", "https://{token}.homerun.co/", "https://{token}.homerun.co/"),
    _r("join", ("join.com",), "url_host_name LIKE '%join.com' AND url_path LIKE '/companies/%'", (Pattern(r"^https?://(?:www\.)?join\.com/companies/([A-Za-z0-9_-]+)"),), "html", "https://join.com/companies/{token}", "https://join.com/companies/{token}"),
    _r("trakstar", ("trakstar.com",), "url_host_name LIKE '%.hire.trakstar.com'", (Pattern(r"^https?://([a-z0-9-]+)\.hire\.trakstar\.com(?:/|$)"),), "html", "https://{token}.hire.trakstar.com/jobs", "https://{token}.hire.trakstar.com/jobs"),
    _r("gem", ("gem.com",), "url_host_name = 'jobs.gem.com'", (
        Pattern(r"^https?://jobs\.gem\.com/([A-Za-z0-9_-]+)(?:/|$)"),
    ), "json", "https://api.gem.com/job_board/v0/{token}/job_posts/", "https://jobs.gem.com/{token}",
       catalog_requires=("company_raw",)),
    _r("manatal", ("careers-page.com",), "url_host_name = 'careers-page.com' OR url_host_name = 'www.careers-page.com' OR url_host_name LIKE '%.careers-page.com'", (
        Pattern(r"^https?://(?:www\.)?careers-page\.com/api/v1\.0/c/([A-Za-z0-9_-]+)/jobs/"),
        Pattern(r"^https?://(?:www\.)?careers-page\.com/([A-Za-z0-9_-]+)(?:/|$)"),
        Pattern(r"^https?://([a-z0-9-]+)\.careers-page\.com(?:/|$)"),
    ), "json", "https://www.careers-page.com/api/v1.0/c/{token}/jobs/", "https://www.careers-page.com/{token}"),
    _r("peopleadmin", ("peopleadmin.com",), "url_host_name LIKE '%.peopleadmin.com' AND url_path LIKE '/postings%'", (
        Pattern(r"^https?://([a-z0-9-]+)\.peopleadmin\.com/postings(?:/|$)"),
    ), "xml", "https://{token}.peopleadmin.com/postings/search.atom", "https://{token}.peopleadmin.com/postings/search"),
    _r("oleeo", ("tal.net",), "url_host_name LIKE '%.tal.net' AND url_path LIKE '/candidate/jobboard/vacancy/%'", (
        Pattern(r"^https?://([a-z0-9-]+)\.tal\.net/candidate/jobboard/vacancy/([0-9]+)(?:/|$)", (1, 2)),
    ), "xml", "https://{t0}.tal.net/candidate/jobboard/vacancy/{t1}/feed", "https://{t0}.tal.net/candidate/jobboard/vacancy/{t1}",
       xml_required_markers=('content type="text"',)),
    _r("vincere", ("vincere.io",), "url_host_name LIKE '%.vincere.io' AND url_path LIKE '/careers%'", (
        Pattern(r"^https?://([a-z0-9-]+)\.vincere\.io/careers(?:/|$)"),
    ), "html", "https://{token}.vincere.io/careers/", "https://{token}.vincere.io/careers/",
       html_any_markers=("/careers/job/",)),
    _r("recruitcrm", ("recruitcrm.io",), "url_host_name IN ('careers.recruitcrm.io', 'app.recruitcrm.io', 'albatross.recruitcrm.io')", (
        Pattern(r"^https?://careers\.recruitcrm\.io/([A-Za-z0-9_-]+)(?:/|$)"),
        Pattern(r"^https?://app\.recruitcrm\.io/jobs/([A-Za-z0-9_-]+)(?:/|$)"),
        Pattern(r"^https?://albatross\.recruitcrm\.io/v1/external-pages/jobs-by-account/get\?(?:[^&]*&)*account=([A-Za-z0-9_-]+)"),
    ), "json", "https://albatross.recruitcrm.io/v1/external-pages/jobs-by-account/get?account={token}", "https://careers.recruitcrm.io/{token}",
       method="POST", headers={"Origin": "https://careers.recruitcrm.io", "Referer": "https://careers.recruitcrm.io/"},
       count_path=("data", "jobs")),
    _r("adp", ("adp.com",), "url_host_name = 'workforcenow.adp.com' AND url_query LIKE '%cid=%' AND url_query LIKE '%ccId=%'", (
        Pattern(r"^https?://workforcenow\.adp\.com/[^?#]*\?(?:[^&]*&)*cid=([0-9a-fA-F-]{36})&(?:[^&]*&)*ccId=([A-Za-z0-9_-]+)", (1, 2)),
        Pattern(r"^https?://workforcenow\.adp\.com/[^?#]*\?(?:[^&]*&)*ccId=([A-Za-z0-9_-]+)&(?:[^&]*&)*cid=([0-9a-fA-F-]{36})", (2, 1)),
    ), "json", (
        "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/"
        "staffing/v1/job-requisitions?cid={t0}&ccId={t1}&$top=1&$skip=1"
    ), (
        "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
        "recruitment.html?cid={t0}&ccId={t1}&lang=en_US"
    ), count_path=("meta", "totalNumber"), catalog_requires=("company_raw",)),
    _r("bullhorn", ("bullhornstaffing.com",), "regexp_matches(url_host_name, '^public-rest[0-9]+\\.bullhornstaffing\\.com$') AND url_path LIKE '/rest-services/%/query/JobBoardPost'", (
        Pattern(r"^https?://public-rest([0-9]+)\.bullhornstaffing\.com/rest-services/([A-Za-z0-9_-]+)/query/JobBoardPost", (1, 2)),
    ), "json", (
        "https://public-rest{t0}.bullhornstaffing.com/rest-services/{t1}/query/JobBoardPost"
        "?fields=id,title&where=isOpen=true&start=0&count=1&showTotalMatched=true"
    ), "https://public-rest{t0}.bullhornstaffing.com/rest-services/{t1}/query/JobBoardPost",
       count_path=("total",), catalog_requires=("company_raw", "board_url")),
)

BY_SOURCE = {rule.source: rule for rule in RULES}


def valid_token(token: str) -> bool:
    return bool(TOKEN_RE.match(token)) and token.split("/", 1)[0].lower() not in STOP


def extract(url: str) -> tuple[str, str] | None:
    """Return a normalized ``(source, key)`` from a public board URL."""
    url = _SCHEME_HOST.sub(lambda match: match.group(1).lower(), url, count=1)
    for rule in RULES:
        for pattern in rule.patterns:
            match = re.match(pattern.rx, url)
            if not match:
                continue
            token = "/".join(match.group(group) for group in pattern.groups)
            return (rule.source, token) if valid_token(token) else None
    return None


def surt_range(domain: str) -> tuple[str, str]:
    prefix = ",".join(reversed(domain.split(".")))
    return prefix + ")", prefix + "-"


def token_sql(rule: Rule) -> str:
    """DuckDB expression that preserves the Python pattern ordering."""
    branches = []
    for pattern in rule.patterns:
        rx = "'" + pattern.rx.replace("'", "''") + "'"
        groups = " || '/' || ".join(
            f"regexp_extract(url, {rx}, {group})" for group in pattern.groups
        )
        branches.append(f"CASE WHEN regexp_matches(url, {rx}) THEN {groups} END")
    return "coalesce(" + ", ".join(branches) + ", '')"


SAMPLES: tuple[tuple[str, tuple[str, str] | None], ...] = (
    ("https://boards.greenhouse.io/stripe", ("greenhouse", "stripe")),
    ("https://jobs.lever.co/palantir", ("lever", "palantir")),
    ("https://jobs.ashbyhq.com/openai/123", ("ashby", "openai")),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite", ("workday", "nvidia/wd5/NVIDIAExternalCareerSite")),
    ("https://jobs.smartrecruiters.com/Palantir", ("smartrecruiters", "Palantir")),
    ("https://apply.workable.com/deliveroo/", ("workable", "deliveroo")),
    ("https://acme.recruitee.com/o/backend", ("recruitee", "acme")),
    ("https://acme.bamboohr.com/careers/1", ("bamboohr", "acme")),
    ("https://acme.breezy.hr/p/1", ("breezy", "acme")),
    ("https://acme.applytojob.com/apply/1", ("jazzhr", "acme")),
    ("https://acme.jobs.personio.de/job/1", ("personio", "acme")),
    ("https://ats.rippling.com/en-GB/acme/jobs/123", ("rippling", "acme")),
    ("https://ats.rippling.com/acme/jobs", ("rippling", "acme")),
    ("https://acme.pinpointhq.com/postings/1", ("pinpoint", "acme")),
    ("https://www.comeet.co/jobs/acme/A5.004", ("comeet", "acme/A5.004")),
    ("https://www.comeet.com/jobs/acme/A5.004", ("comeet", "acme/A5.004")),
    ("https://jobs.jobvite.com/acme/job/1", ("jobvite", "acme")),
    ("https://acme.teamtailor.com/jobs/1", ("teamtailor", "acme")),
    ("https://careers.acme.com/careers/jobs", ("icims", "acme")),
    ("https://careers.acme.com/api/jobs?page=1", ("icims", "acme")),
    ("https://acme.icims.com/jobs/1", None),
    ("https://acme.taleo.net/careersection/1", ("taleo", "acme")),
    ("https://career4.successfactors.com/career?company=acme", ("successfactors", "career4.successfactors.com/acme")),
    ("https://app.eightfold.ai/careers?domain=acme.com", ("eightfold", "app.eightfold.ai/acme.com")),
    ("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1", ("oracle", "eeho.fa.us2/CX_1")),
    ("https://recruiting2.ultipro.com/ACME/JobBoard/3391213d-67ca-497b-be36-e41affd954f7", ("ukg", "recruiting2.ultipro.com/ACME/3391213d-67ca-497b-be36-e41affd954f7")),
    ("https://recruiting.paylocity.com/recruiting/jobs/All/1e0f2a3b-4c5d-6e7f-8a9b-0c1d2e3f4a5b", ("paylocity", "1e0f2a3b-4c5d-6e7f-8a9b-0c1d2e3f4a5b")),
    ("https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL", ("dayforce", "acme")),
    ("https://jobs.dayforcehcm.com/en-CA/acme/CANDIDATEPORTAL/jobs/123", ("dayforce", "acme")),
    ("https://jobs.dayforcehcm.com/en-US/CANDIDATEPORTAL", None),
    ("https://acme.avature.net/careers", ("avature", "acme")),
    ("https://acme.zohorecruit.eu/jobs/Careers", ("zohorecruit", "acme")),
    ("https://acme.homerun.co/", ("homerun", "acme")),
    ("https://join.com/companies/acme", ("join", "acme")),
    ("https://acme.hire.trakstar.com/jobs", ("trakstar", "acme")),
    ("https://jobs.gem.com/acme/123", ("gem", "acme")),
    ("https://www.careers-page.com/acme/job/ABC", ("manatal", "acme")),
    ("https://acme.peopleadmin.com/postings/123", ("peopleadmin", "acme")),
    ("https://acme.tal.net/candidate/jobboard/vacancy/14/feed", ("oleeo", "acme/14")),
    ("https://acme.vincere.io/careers/job/123/role", ("vincere", "acme")),
    ("https://careers.recruitcrm.io/Acme_jobs", ("recruitcrm", "Acme_jobs")),
    ("https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=d16ba13d-474e-4326-b628-74c87f0b289a&ccId=19000101_000001", ("adp", "d16ba13d-474e-4326-b628-74c87f0b289a/19000101_000001")),
    ("https://public-rest44.bullhornstaffing.com/rest-services/CJAT1S/query/JobBoardPost", ("bullhorn", "44/CJAT1S")),
    ("https://acme.recruiterbox.com/", None),
    ("https://www.recruitee.com/pricing", None),
    ("https://api.lever.co/v0/postings/acme", None),
)
