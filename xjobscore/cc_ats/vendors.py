"""Where each ATS puts its tenant in a URL, and how to ask whether it is real.

One regex per pattern, used in two places: compiled by Python for the bind
stage, and emitted as a DuckDB expression for the Common Crawl scan. They are
the same string, so the two stages cannot drift apart -- which they would
immediately if the scan and the binder each carried their own copy of the
rule. Both RE2 (DuckDB) and `re` (Python) accept the subset used here, so
nothing needs translating. `selftest` checks the compiled side against
SAMPLE_URLS; verification step 2 checks the SQL side.

Tokens match feed.key: a bare slug where one part identifies the tenant, and
slash-joined parts where the vendor needs more than one -- Workday's
`nvidia/wd5/NVIDIAExternalCareerSite` is the example already in schema.sql.
Probe URLs interpolate `{token}` for the whole thing and `{t0}`, `{t1}`, `{t2}`
for its slash-separated parts, which is what keeps multi-part vendors from
each needing a special case in probe.py.

Probe quality is not uniform and pretending otherwise would inflate the live
rate. `json`/`xml` vendors return a real job count and sometimes a display
name. `html` vendors only tell us the board URL returned 200, so they yield no
company signal and are likelier to false-positive on a vendor's generic "no
such board" page -- hence absent_markers, which is a mitigation, not a fix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Subdomain labels and first path segments that are never a tenant. Kept as one
# list rather than per-vendor because the failure mode is identical everywhere:
# without it, `www.recruitee.com` becomes a tenant called "www" that probes 200.
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


@dataclass(frozen=True)
class Pat:
    """A rule for reading a token out of a full URL.

    `groups` is the capture groups to slash-join, in order. Patterns are tried
    in declaration order and the first match wins, so more specific forms
    (Greenhouse's ?for= embed, Workday's /wday/cxs/) must come first --
    otherwise the general path rule matches them and yields "embed" or a
    locale code as the tenant.
    """

    rx: str
    groups: tuple[int, ...] = (1,)


@dataclass(frozen=True)
class Vendor:
    ats: str
    domains: tuple[str, ...]  # registrable domains -> surtkey ranges
    where_sql: str  # cheap prefilter, runs before the regex
    pats: tuple[Pat, ...]
    probe_kind: str  # 'json' | 'xml' | 'html'
    probe_url: str
    board_url: str  # canonical board, for html probes and bind matching
    # Regional API hosts, tried in order on 404. Several vendors run separate
    # data-residency endpoints and a tenant exists on exactly one of them:
    # Lever EU tenants 404 on api.lever.co and 200 on api.eu.lever.co. Without
    # this the probe records real boards as 'absent', which is silent data loss
    # -- the row is written and never revisited.
    probe_fallbacks: tuple[str, ...] = ()
    name_url: str = ""  # second call, only for live rows, only if it adds a name
    name_fallbacks: tuple[str, ...] = ()
    probe_method: str = "GET"
    probe_json: dict | None = None
    absent_markers: tuple[str, ...] = ()
    html_company_xpath: str = ""  # XPath/regex to extract company from HTML (for html probe_kind)
    note: str = ""
    _compiled: list = field(default_factory=list, repr=False, compare=False)

    def compiled(self) -> list[tuple[re.Pattern, tuple[int, ...]]]:
        # Deliberately no re.IGNORECASE: DuckDB's regexp_extract is
        # case-sensitive, and a flag set on only one of the two stages is
        # precisely the drift this shared-regex design exists to prevent.
        # extract() lowercases the host instead, which is the only part of a
        # URL where case is genuinely insignificant -- Workday site names and
        # SmartRecruiters ids are mixed case and must survive verbatim.
        if not self._compiled:
            self._compiled.extend((re.compile(p.rx), p.groups) for p in self.pats)
        return self._compiled

    def valid(self, token: str) -> bool:
        if not token or not TOKEN_RE.match(token):
            return False
        return token.split("/")[0].lower() not in STOP

    def urls(self, token: str) -> dict[str, str]:
        parts = token.split("/")
        fields = {"token": token}
        fields.update({f"t{i}": p for i, p in enumerate(parts)})
        return fields


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

VENDORS: tuple[Vendor, ...] = (
    Vendor(
        ats="greenhouse",
        domains=("greenhouse.io",),
        where_sql="url_host_name LIKE '%greenhouse.io'",
        pats=(
            # Embed form first: its path segment is "embed", not the tenant.
            # job_board is the whole-board embed, job_app a single posting;
            # both carry the tenant in ?for=, and the per-posting form is the
            # one that actually appears on company careers pages.
            Pat(r"^https?://(?:job-)?boards(?:\.eu)?\.greenhouse\.io/embed/job_(?:board|app)\?(?:[^&]*&)*for=([A-Za-z0-9_.-]+)"),
            Pat(r"^https?://(?:job-)?boards(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_.-]+)"),
        ),
        probe_kind="json",
        probe_url="https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=false",
        probe_fallbacks=("https://boards-api.eu.greenhouse.io/v1/boards/{token}/jobs?content=false",),
        name_url="https://boards-api.greenhouse.io/v1/boards/{token}",
        name_fallbacks=("https://boards-api.eu.greenhouse.io/v1/boards/{token}",),
        board_url="https://job-boards.greenhouse.io/{token}",
    ),
    Vendor(
        ats="lever",
        domains=("lever.co",),
        where_sql="url_host_name IN ('jobs.lever.co', 'jobs.eu.lever.co')",
        pats=(Pat(r"^https?://jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)"),),
        probe_kind="json",
        probe_url="https://api.lever.co/v0/postings/{token}?mode=json",
        probe_fallbacks=("https://api.eu.lever.co/v0/postings/{token}?mode=json",),
        board_url="https://jobs.lever.co/{token}",
        note=(
            "jobs.lever.co serves `User-agent: CCBot / Disallow: /`, so Common Crawl "
            "has no rows for it and the scan returns almost nothing -- measured 75 "
            "tokens on CC-MAIN-2026-30, essentially all from jobs.eu.lever.co. The "
            "patterns are correct; the data is not there. Lever tenants have to come "
            "from the bind stage or a second source. Run `python -m cc_ats robots`. "
            "Postings also carry no company name, so unbound rows park under the token."
        ),
    ),
    Vendor(
        ats="ashby",
        domains=("ashbyhq.com",),
        where_sql="url_host_name = 'jobs.ashbyhq.com'",
        pats=(Pat(r"^https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)"),),
        probe_kind="json",
        probe_url="https://api.ashbyhq.com/posting-api/job-board/{token}",
        board_url="https://jobs.ashbyhq.com/{token}",
    ),
    Vendor(
        ats="workday",
        domains=("myworkdayjobs.com",),
        where_sql="url_host_name LIKE '%.myworkdayjobs.com'",
        pats=(
            Pat(r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/wday/cxs/[^/]+/([^/?#]+)", (1, 2, 3)),
            # Locale segment before the site; matched before the bare form so
            # the site does not come back as "en-US".
            Pat(r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/[a-z]{2}(?:-[A-Za-z]{2})?/([^/?#]+)", (1, 2, 3)),
            Pat(r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/([^/?#]+)", (1, 2, 3)),
        ),
        probe_kind="json",
        probe_method="POST",
        probe_json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
        probe_url="https://{t0}.{t1}.myworkdayjobs.com/wday/cxs/{t0}/{t2}/jobs",
        board_url="https://{t0}.{t1}.myworkdayjobs.com/en-US/{t2}",
    ),
    Vendor(
        ats="smartrecruiters",
        domains=("smartrecruiters.com",),
        where_sql="url_host_name = 'jobs.smartrecruiters.com'",
        pats=(Pat(r"^https?://jobs\.smartrecruiters\.com/([A-Za-z0-9_.-]+)"),),
        probe_kind="json",
        probe_url="https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=1",
        board_url="https://jobs.smartrecruiters.com/{token}",
    ),
    Vendor(
        ats="workable",
        domains=("workable.com",),
        where_sql="url_host_name LIKE '%workable.com'",
        pats=(
            Pat(r"^https?://apply\.workable\.com/([A-Za-z0-9_-]+)"),
            Pat(r"^https?://([a-z0-9-]+)\.workable\.com/"),
        ),
        probe_kind="json",
        probe_url="https://apply.workable.com/api/v1/widget/accounts/{token}?details=true",
        board_url="https://apply.workable.com/{token}/",
    ),
    Vendor(
        ats="recruitee",
        domains=("recruitee.com",),
        where_sql="url_host_name LIKE '%.recruitee.com'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.recruitee\.com"),),
        probe_kind="json",
        probe_url="https://{token}.recruitee.com/api/offers/",
        board_url="https://{token}.recruitee.com/",
    ),
    Vendor(
        ats="bamboohr",
        domains=("bamboohr.com",),
        where_sql="url_host_name LIKE '%.bamboohr.com' AND (url_path LIKE '/careers%' OR url_path LIKE '/jobs%')",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.bamboohr\.com/(?:careers|jobs)"),),
        probe_kind="json",
        probe_url="https://{token}.bamboohr.com/careers/list",
        board_url="https://{token}.bamboohr.com/careers",
    ),
    Vendor(
        ats="breezy",
        domains=("breezy.hr",),
        where_sql="url_host_name LIKE '%.breezy.hr'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.breezy\.hr"),),
        probe_kind="json",
        probe_url="https://{token}.breezy.hr/json",
        board_url="https://{token}.breezy.hr/",
    ),
    Vendor(
        ats="jazzhr",
        domains=("applytojob.com",),
        where_sql="url_host_name LIKE '%.applytojob.com'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.applytojob\.com"),),
        probe_kind="xml",
        probe_url="https://{token}.applytojob.com/apply/jobs/feed",
        board_url="https://{token}.applytojob.com/apply/",
    ),
    Vendor(
        ats="personio",
        domains=("personio.de", "personio.com"),
        where_sql="url_host_name LIKE '%.jobs.personio.de' OR url_host_name LIKE '%.jobs.personio.com'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.jobs\.personio\.(?:de|com)"),),
        probe_kind="xml",
        probe_url="https://{token}.jobs.personio.de/xml",
        probe_fallbacks=("https://{token}.jobs.personio.com/xml",),
        board_url="https://{token}.jobs.personio.de/",
    ),
    Vendor(
        ats="rippling",
        domains=("rippling.com",),
        where_sql="url_host_name = 'ats.rippling.com'",
        pats=(Pat(r"^https?://ats\.rippling\.com/([A-Za-z0-9_-]+)"),),
        probe_kind="json",
        probe_url="https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs",
        board_url="https://ats.rippling.com/{token}/jobs",
    ),
    Vendor(
        ats="pinpoint",
        domains=("pinpointhq.com",),
        where_sql="url_host_name LIKE '%.pinpointhq.com'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.pinpointhq\.com"),),
        probe_kind="json",
        probe_url="https://{token}.pinpointhq.com/postings.json",
        board_url="https://{token}.pinpointhq.com/",
    ),
    Vendor(
        ats="comeet",
        domains=("comeet.co",),
        where_sql="url_host_name LIKE '%comeet.co' AND url_path LIKE '/jobs/%'",
        pats=(Pat(r"^https?://(?:www\.)?comeet\.co/jobs/([A-Za-z0-9_-]+)/([A-Za-z0-9._-]+)", (1, 2)),),
        probe_kind="json",
        probe_url="https://www.comeet.co/careers-api/2.0/company/{t1}/positions",
        board_url="https://www.comeet.co/jobs/{t0}/{t1}",
    ),
    Vendor(
        ats="jobvite",
        domains=("jobvite.com",),
        where_sql="url_host_name = 'jobs.jobvite.com'",
        pats=(Pat(r"^https?://jobs\.jobvite\.com/([A-Za-z0-9_-]+)"),),
        probe_kind="html",
        probe_url="https://jobs.jobvite.com/{token}",
        board_url="https://jobs.jobvite.com/{token}",
        absent_markers=("page not found", "no longer available"),
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
    ),
    Vendor(
        ats="teamtailor",
        domains=("teamtailor.com",),
        where_sql="url_host_name LIKE '%.teamtailor.com'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.teamtailor\.com"),),
        probe_kind="html",
        probe_url="https://{token}.teamtailor.com/jobs",
        board_url="https://{token}.teamtailor.com/jobs",
        absent_markers=("page not found",),
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
    ),
    Vendor(
        ats="icims",
        domains=("icims.com",),
        where_sql="url_host_name LIKE '%.icims.com'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.icims\.com"),),
        probe_kind="html",
        probe_url="https://{token}.icims.com/jobs/search",
        board_url="https://{token}.icims.com/jobs/search",
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
    ),
    Vendor(
        ats="taleo",
        domains=("taleo.net",),
        where_sql="url_host_name LIKE '%.taleo.net'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.taleo\.net"),),
        probe_kind="html",
        probe_url="https://{token}.taleo.net/careersection/",
        board_url="https://{token}.taleo.net/careersection/",
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
    ),
    Vendor(
        ats="successfactors",
        domains=("successfactors.com", "successfactors.eu"),
        where_sql="url_host_name LIKE 'career%.successfactors.%' AND url_query LIKE '%company=%'",
        pats=(Pat(r"^https?://(career\d*\.successfactors\.(?:com|eu))/career\?(?:[^&]*&)*company=([A-Za-z0-9_-]+)", (1, 2)),),
        probe_kind="html",
        probe_url="https://{t0}/career?company={t1}",
        board_url="https://{t0}/career?company={t1}",
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
        note="token is host/companyId because the careerN host varies per customer and is not derivable",
    ),
    Vendor(
        ats="eightfold",
        domains=("eightfold.ai",),
        where_sql="url_host_name LIKE '%.eightfold.ai'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.eightfold\.ai"),),
        probe_kind="html",
        probe_url="https://{token}.eightfold.ai/careers",
        board_url="https://{token}.eightfold.ai/careers",
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
    ),
    Vendor(
        ats="oraclecloud",
        domains=("oraclecloud.com",),
        where_sql="url_host_name LIKE '%.oraclecloud.com' AND url_path LIKE '/hcmUI/CandidateExperience/%'",
        pats=(Pat(r"^https?://([a-z0-9.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[^/]+/sites/([A-Za-z0-9_-]+)", (1, 2)),),
        probe_kind="html",
        probe_url="https://{t0}/hcmUI/CandidateExperience/en/sites/{t1}",
        board_url="https://{t0}/hcmUI/CandidateExperience/en/sites/{t1}",
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
    ),
    Vendor(
        ats="ultipro",
        domains=("ultipro.com",),
        where_sql="url_host_name = 'recruiting.ultipro.com'",
        pats=(Pat(r"^https?://recruiting\.ultipro\.com/([A-Za-z0-9_]+)"),),
        probe_kind="html",
        probe_url="https://recruiting.ultipro.com/{token}",
        board_url="https://recruiting.ultipro.com/{token}",
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
    ),
    Vendor(
        ats="paylocity",
        domains=("paylocity.com",),
        where_sql="url_host_name = 'recruiting.paylocity.com'",
        pats=(Pat(r"^https?://recruiting\.paylocity\.com/recruiting/jobs/All/([0-9a-fA-F-]{36})"),),
        probe_kind="html",
        probe_url="https://recruiting.paylocity.com/recruiting/jobs/All/{token}",
        board_url="https://recruiting.paylocity.com/recruiting/jobs/All/{token}",
        note="token is an opaque uuid; unguessable, so Common Crawl is the only way to get it",
    ),
    Vendor(
        ats="dayforce",
        domains=("dayforcehcm.com",),
        where_sql="url_host_name LIKE '%.dayforcehcm.com'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.dayforcehcm\.com"),),
        probe_kind="html",
        probe_url="https://{token}.dayforcehcm.com/CandidatePortal/",
        board_url="https://{token}.dayforcehcm.com/CandidatePortal/",
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
    ),
    Vendor(
        ats="avature",
        domains=("avature.net",),
        where_sql="url_host_name LIKE '%.avature.net'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.avature\.net"),),
        probe_kind="html",
        probe_url="https://{token}.avature.net/careers",
        board_url="https://{token}.avature.net/careers",
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
    ),
    Vendor(
        ats="zohorecruit",
        domains=("zohorecruit.com", "zohorecruit.eu"),
        where_sql="url_host_name LIKE '%.zohorecruit.com' OR url_host_name LIKE '%.zohorecruit.eu'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.zohorecruit\.(?:com|eu)"),),
        probe_kind="html",
        probe_url="https://{token}.zohorecruit.com/jobs/Careers",
        probe_fallbacks=("https://{token}.zohorecruit.eu/jobs/Careers",),
        board_url="https://{token}.zohorecruit.com/jobs/Careers",
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
    ),
    Vendor(
        ats="homerun",
        domains=("homerun.co",),
        where_sql="url_host_name LIKE '%.homerun.co'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.homerun\.co"),),
        probe_kind="html",
        probe_url="https://{token}.homerun.co/",
        board_url="https://{token}.homerun.co/",
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
    ),
    Vendor(
        ats="join",
        domains=("join.com",),
        where_sql="url_host_name LIKE '%join.com' AND url_path LIKE '/companies/%'",
        pats=(Pat(r"^https?://(?:www\.)?join\.com/companies/([A-Za-z0-9_-]+)"),),
        probe_kind="html",
        probe_url="https://join.com/companies/{token}",
        board_url="https://join.com/companies/{token}",
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
    ),
    Vendor(
        ats="recruiterbox",
        domains=("recruiterbox.com",),
        where_sql="url_host_name LIKE '%.recruiterbox.com'",
        pats=(Pat(r"^https?://([a-z0-9-]+)\.recruiterbox\.com"),),
        probe_kind="html",
        probe_url="https://{token}.recruiterbox.com/",
        board_url="https://{token}.recruiterbox.com/",
        html_company_xpath="title|//h1|//meta[@property='og:site_name']/@content",
    ),
)

BY_ATS = {v.ats: v for v in VENDORS}


# --------------------------------------------------------------------------
# SURT ranges
# --------------------------------------------------------------------------


def surt_range(domain: str) -> tuple[str, str]:
    """Byte range covering a registrable domain and every subdomain of it.

    The columnar index is sorted by url_surtkey, which reverses the host
    labels: boards.greenhouse.io -> `io,greenhouse,boards)/path`. A range
    predicate on that column is what lets parquet footer statistics skip whole
    row groups, turning a nominal ~300 GB scan into tens of megabytes.

    The bounds work because of byte order: ')' (0x29) < ',' (0x2C) < '-'
    (0x2D). The low bound `R)` catches the apex, the high bound `R-` stops
    just past every `R,subdomain)` -- and excludes neighbours like
    `greenhouse2.io`, whose next byte is well above '-'.
    """
    reversed_labels = ",".join(reversed(domain.split(".")))
    return reversed_labels + ")", reversed_labels + "-"


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def token_sql(vendor: Vendor) -> str:
    """The vendor's patterns as one DuckDB expression over the `url` column.

    Running extraction inside the scan is the difference between DuckDB
    handing back a few thousand grouped tokens and a few million posting URLs.
    CASE/coalesce rather than a chained regex so that pattern order -- which
    Greenhouse and Workday both depend on -- survives into SQL unchanged.
    """
    branches = []
    for pat in vendor.pats:
        rx = _sql_str(pat.rx)
        extract = " || '/' || ".join(f"regexp_extract(url, {rx}, {g})" for g in pat.groups)
        branches.append(f"CASE WHEN regexp_matches(url, {rx}) THEN {extract} END")
    return "coalesce(" + ", ".join(branches) + ", '')"


# --------------------------------------------------------------------------
# Python-side extraction (bind stage)
# --------------------------------------------------------------------------


_SCHEME_HOST = re.compile(r"^(https?://[^/?#]+)")


def extract(url: str) -> tuple[str, str] | None:
    """(ats, token) for a board URL, or None. Same rules as the scan.

    The scheme and host are lowercased first. Common Crawl already stores them
    that way, so this only matters for the bind stage, which reads hrefs off
    live careers pages where `https://Boards.Greenhouse.io/acme` is legal and
    does occur.
    """
    url = _SCHEME_HOST.sub(lambda m: m.group(1).lower(), url, count=1)
    for vendor in VENDORS:
        for rx, groups in vendor.compiled():
            match = rx.match(url)
            if not match:
                continue
            token = "/".join(match.group(g) for g in groups)
            if vendor.valid(token):
                return vendor.ats, token
            break
    return None


# Fixtures for `python -m cc_ats selftest`. Each entry is the URL and what
# extract() must return -- None where the URL looks like a board but is not one.
SAMPLE_URLS: tuple[tuple[str, tuple[str, str] | None], ...] = (
    ("https://boards.greenhouse.io/stripe", ("greenhouse", "stripe")),
    ("https://job-boards.greenhouse.io/anthropic/jobs/4020128008", ("greenhouse", "anthropic")),
    ("https://boards.greenhouse.io/embed/job_board?for=airbnb", ("greenhouse", "airbnb")),
    ("https://boards.eu.greenhouse.io/deepl", ("greenhouse", "deepl")),
    ("https://boards.greenhouse.io/embed/job_app?for=figma&token=1234", ("greenhouse", "figma")),
    ("https://jobs.lever.co/palantir", ("lever", "palantir")),
    ("https://jobs.eu.lever.co/imc/abc-123", ("lever", "imc")),
    ("https://jobs.ashbyhq.com/openai/1234-5678", ("ashby", "openai")),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite", ("workday", "nvidia/wd5/NVIDIAExternalCareerSite")),
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/x", ("workday", "nvidia/wd5/NVIDIAExternalCareerSite")),
    ("https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs", ("workday", "nvidia/wd5/NVIDIAExternalCareerSite")),
    ("https://jobs.smartrecruiters.com/Palantir", ("smartrecruiters", "Palantir")),
    ("https://apply.workable.com/deliveroo/", ("workable", "deliveroo")),
    ("https://acme.recruitee.com/o/backend-engineer", ("recruitee", "acme")),
    ("https://acme.bamboohr.com/careers/42", ("bamboohr", "acme")),
    ("https://acme.breezy.hr/p/xyz", ("breezy", "acme")),
    ("https://acme.applytojob.com/apply/xyz", ("jazzhr", "acme")),
    ("https://acme.jobs.personio.de/job/12345", ("personio", "acme")),
    ("https://ats.rippling.com/acme/jobs", ("rippling", "acme")),
    ("https://acme.pinpointhq.com/postings/abc", ("pinpoint", "acme")),
    ("https://www.comeet.co/jobs/acme/A5.004", ("comeet", "acme/A5.004")),
    ("https://recruiting.paylocity.com/recruiting/jobs/All/1e0f2a3b-4c5d-6e7f-8a9b-0c1d2e3f4a5b/acme",
     ("paylocity", "1e0f2a3b-4c5d-6e7f-8a9b-0c1d2e3f4a5b")),
    ("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions",
     ("oraclecloud", "eeho.fa.us2.oraclecloud.com/CX_1001")),
    ("https://career4.successfactors.com/career?company=acmecorp&career_job_req_id=1",
     ("successfactors", "career4.successfactors.com/acmecorp")),
    # Non-tenants that must be rejected by STOP rather than probed.
    ("https://www.recruitee.com/pricing", None),
    ("https://boards.greenhouse.io/embed/job_board.js", None),
    ("https://support.bamboohr.com/careers", None),
    ("https://api.lever.co/v0/postings/foo", None),
)
