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
    method: str = "GET"
    body: dict | None = None
    headers: dict[str, str] | None = None
    count_path: tuple[str | int, ...] = ()
    # Some vendors answer their job endpoint at tenant scope, ignoring the part
    # of the token that names the board: a nonexistent board returns the same
    # 200 and the same job count as a real one, so the probe alone cannot tell
    # them apart. `exists_url` is an optional first request against an endpoint
    # that *is* board-scoped; a zero count there means the board is not real
    # and the probe is skipped entirely.
    exists_url: str = ""
    exists_count_path: tuple[str | int, ...] = ()
    # Further board-scoped lookups, tried in order only while every earlier one
    # answered a definite zero -- for a board that can be named more than one
    # way. The board is absent only when all of them say so.
    exists_fallbacks: tuple[str, ...] = ()
    # Liveness-only overrides, taken only when the caller asks "does this board
    # still exist" and not "how many jobs does it have" -- the inspector's
    # Tokens page writes `token.status` and nothing else, while the crawl keeps
    # the full path because it persists `job_count`. Measured 2026-08-20: these
    # turn a 5.9 MB lever board into 12 KB and a 1.29 MB ashby board into zero
    # body bytes. Only set them where a bogus token is known to 404 --
    # smartrecruiters answers 200 with count 0 for one, so its verdict lives in
    # the body and it must keep the full path. See docs/token-validation.md.
    light_url: str = ""  # "" -> reuse probe_url
    light_fallbacks: tuple[str, ...] = ()  # "" -> reuse fallbacks
    light_method: str = ""  # "" -> reuse method; "HEAD" -> status alone decides
    absent_markers: tuple[str, ...] = ()
    # JSON `errorCode` values that, on a non-200 answer, mean the board is gone
    # rather than that we were refused. Workday answers a disabled site with a
    # 403 `{"errorCode": "S22", "message": "permission denied"}` (its page
    # redirects to community.workday.com/maintenance-page); read as a bare 403
    # it looked like a block, and the sweep cooled down on it until it quit.
    absent_error_codes: tuple[str, ...] = ()
    # Values the token's second part (`t1`) may take. A probe answered 422 --
    # Workday's reply for a tenant not on this host -- tries the others in
    # order before calling the tenant gone; see `probe._probe_shards`.
    shards: tuple[str, ...] = ()
    # Whether a 200 with a zero count proves the board exists. Off by default
    # because smartrecruiters answers a bogus id that way; on only where every
    # bogus token is known to be refused instead. Workday names a missing site
    # in a 404 (S21), a missing tenant in a 422 and a disabled site in a 403
    # (S22), so its 200 is a real site that is just empty (2026-10-05: ~10% of
    # the unchecked backlog, previously `inconclusive` / `zero_count`).
    zero_is_live: bool = False
    # A company-scoped endpoint that settles the `zero_count` ambiguity where
    # `zero_is_live` cannot be set. Asked only after a 200 with a zero count
    # and no name: its 404/410 is `absent`, its 200 `live` (a real company with
    # no open jobs), and anything else stays `inconclusive`.
    zero_check_url: str = ""
    # Token checks beyond the shared `TOKEN_RE`, applied identically by
    # `valid_token` (Python) and `token_filter_sql` (DuckDB). `stop_words`
    # rejects a token whose first part is in `STOP`; off only where the host
    # shape already proves the first part is a tenant (Workday's
    # `{tenant}.wdN.myworkdayjobs.com` -- `academy` is a real tenant).
    # `token_rx` is an extra full-token match; RE2 syntax, since DuckDB runs it.
    stop_words: bool = True
    token_rx: str = ""
    # Replaces the shared `TOKEN_RE` for a vendor whose tokens need other
    # characters (Ashby board names hold `%20`). Same RE2 caveat as `token_rx`.
    token_re: str = ""
    # `{tail}` in a template is every token part after the first, so one
    # template serves tokens of different lengths (Avature's `co/board` and
    # `co/xx_YY/board`). A bare one-part token reads as `tail_default`.
    tail_default: str = ""
    xml_required_markers: tuple[str, ...] = ()
    html_required_markers: tuple[str, ...] = ()
    html_any_markers: tuple[str, ...] = ()
    html_control_token: str = "keyscan-token-does-not-exist-9f4d2a"

    def urls(self, token: str) -> dict[str, str]:
        fields = {"token": token}
        parts = token.split("/")
        fields.update({f"t{i}": value for i, value in enumerate(parts)})
        fields["tail"] = "/".join(parts[1:]) or self.tail_default
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
       # No EU fallback: boards-api.eu.greenhouse.io does not resolve in DNS
       # (verified 2026-08-19). Probing it wastes 3 retries with backoff and
       # turns a genuine 404 into a false "inconclusive/transport".
       name_url="https://boards-api.greenhouse.io/v1/boards/{token}",
       light_method="HEAD"),
    # jobs.lever.co (the US host, most boards) answers CCBot with a Cloudflare
    # 403, so it is absent from every snapshot from CC-MAIN-2025-47 on; through
    # CC-MAIN-2025-43 it holds ~1,000-1,900 boards per snapshot. Only the EU
    # host survives in newer crawls. Board names are case-sensitive and may
    # carry dots (`Academy` is live where `academy` 404s; `glow.co` is real).
    # A few index URLs keep an explicit `:443` (jobs.lever.co:443/bisnow).
    _r("lever", ("lever.co",), "url_host_name IN ('jobs.lever.co', 'jobs.eu.lever.co')", (Pattern(r"^https?://jobs(?:\.eu)?\.lever\.co(?::443)?/([A-Za-z0-9_.-]+)"),), "json", "https://api.lever.co/v0/postings/{token}?mode=json", "https://jobs.lever.co/{token}", fallbacks=("https://api.eu.lever.co/v0/postings/{token}?mode=json",),
       # `limit` is honoured (verified: palantir 308 postings, 5.9 MB -> 12 KB)
       # and a nonexistent board still 404s, so one posting is enough to prove
       # the board. GET rather than HEAD because the EU fallback has to be able
       # to distinguish 404 from a body, and a single posting is already cheap.
       light_url="https://api.lever.co/v0/postings/{token}?mode=json&limit=1",
       light_fallbacks=("https://api.eu.lever.co/v0/postings/{token}?mode=json&limit=1",)),
    # Board names may hold spaces (`Citizen%20Health`, 147 seen 2026-10-06,
    # `%20` the only escape). The pattern used to stop at the `%`, turning the
    # board into `citizen` -- a different company's live board. Kept encoded
    # here so Python and DuckDB agree; `_canonical_token` stores it as a space.
    _r("ashby", ("ashbyhq.com",), "url_host_name = 'jobs.ashbyhq.com'", (Pattern(r"^https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+(?:%20[A-Za-z0-9_.-]+)*)"),), "json", "https://api.ashbyhq.com/posting-api/job-board/{token}", "https://jobs.ashbyhq.com/{token}",
       # No limit parameter exists and `includeCompensation=false` is already
       # the default, so the whole board (1.29 MB for a 110-posting one) is the
       # only GET on offer. HEAD answers 200/404 with no body at all.
       light_method="HEAD",
       token_re=r"^[A-Za-z0-9][A-Za-z0-9._%-]{0,119}$"),
    _r("workday", ("myworkdayjobs.com",), "url_host_name LIKE '%.myworkdayjobs.com'", (
        Pattern(r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/wday/cxs/[^/]+/([^/?#]+)", (1, 2, 3)),
        Pattern(r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/[a-z]{2}(?:-[A-Za-z]{2})?/([^/?#]+)", (1, 2, 3)),
        Pattern(r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/([^/?#]+)", (1, 2, 3)),
    ), "json", "https://{t0}.{t1}.myworkdayjobs.com/wday/cxs/{t0}/{t2}/jobs", "https://{t0}.{t1}.myworkdayjobs.com/en-US/{t2}", method="POST", body={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
       absent_error_codes=("S22",),
       # Every shard in the token inventory, most-used first (counted
       # 2026-10-05: wd1 3,598 ... wd107 3), so a moved tenant is found early.
       shards=(
           "wd1", "wd5", "wd3", "wd12", "wd103", "wd108", "wd501", "wd503",
           "wd10", "wd105", "wd115", "wd502", "wd102", "wd504", "wd116", "wd107",
       ),
       zero_is_live=True,
       # The tenant is proven by the host, so `STOP` does not apply to it; the
       # site is what can be junk. Measured 2026-10-05 on 10,898 local tokens:
       # every site holding a dot was a file (`robots.txt`, `llms.txt`,
       # `*.html`; 98 tokens, none live), and no registry site has one.
       stop_words=False,
       token_rx=r"^[a-z0-9-]+/wd[0-9]+/[A-Za-z0-9_-]+$"),
    _r("smartrecruiters", ("smartrecruiters.com",), "url_host_name = 'jobs.smartrecruiters.com'", (
        # App paths that put the company id after a prefix. Every prefix probes
        # as no company (2026-10-05); the ids behind them are real boards
        # (HarvardUniversity, AbbVie, ALDIStores). A numeric id is the
        # internal one and the postings API answers 0 for it, so it is
        # dropped: the group requires a letter, and is empty otherwise.
        Pattern(r"^https?://jobs\.smartrecruiters\.com/(?:oneclick-ui|external-referrals)/company/((?:[0-9]*[A-Za-z_.-][A-Za-z0-9_.-]*)?)"),
        Pattern(r"^https?://jobs\.smartrecruiters\.com/(?:ni|my-applications)/([A-Za-z0-9_.-]*)"),
        # The same app paths in any other shape, and plain app paths: no
        # company id, so the empty group drops them.
        Pattern(r"^https?://jobs\.smartrecruiters\.com/(?:oneclick-ui|external-referrals|oneclick|cdn-cgi)(?:[/?#]|$)()"),
        Pattern(r"^https?://jobs\.smartrecruiters\.com/([A-Za-z0-9_.-]+)"),
    ), "json", "https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=1", "https://jobs.smartrecruiters.com/{token}",
       # The postings API answers a bogus id exactly like an empty company
       # (200, totalFound 0). The departments API does not: 404
       # RESOURCE_NOT_FOUND for a bogus id, 200 for a real one even with no
       # jobs. Matched the careers page (bogus -> 302 to the bare
       # jobs.smartrecruiters.com) on 42/42 ids, 2026-10-06; ids are
       # case-insensitive on both. developers.smartrecruiters.com/docs/departments
       zero_check_url="https://api.smartrecruiters.com/v1/companies/{token}/departments"),
    _r("workable", ("workable.com",), "url_host_name LIKE '%workable.com'", (
        # `/j/{shortcode}` is a global short link that names no account, and
        # a bare `apply.workable.com/` used to fall through to the subdomain
        # pattern as `apply`. Both now match here with an empty group, which
        # drops them.
        Pattern(r"^https?://apply\.workable\.com/j/()"),
        Pattern(r"^https?://apply\.workable\.com/?([A-Za-z0-9_-]*)"), Pattern(r"^https?://([a-z0-9-]+)\.workable\.com/"),
       # No `details=true`: it inflates every job with its full description HTML
       # (580 KB vs 42.6 KB on a 63-job board, measured 2026-08-20) and the
       # probe reads neither. `jobs[]` and the inline `name` -- the only two
       # things the json path looks at -- are both present without it.
    ), "json", "https://apply.workable.com/api/v1/widget/accounts/{token}", "https://apply.workable.com/{token}/"),
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
    # The HTML page can redirect to an employer site with broken DNS, a bot
    # wall, or its own 429. RSS stays on Teamtailor for those same tenants
    # (triggerfish and ckw, measured 2026-10-06), and is also what we harvest.
    # A valid empty RSS feed still proves the board exists.
    _r("teamtailor", ("teamtailor.com",), "url_host_name LIKE '%.teamtailor.com'", (Pattern(r"^https?://([a-z0-9-]+)\.teamtailor\.com"),), "xml", "https://{token}.teamtailor.com/jobs.rss", "https://{token}.teamtailor.com/jobs",
       light_url="https://{token}.teamtailor.com/jobs.rss?per_page=1",
       xml_required_markers=("<rss", "<channel")),
    # The branded iCIMS API token is the label after ``careers.``, not the
    # unrelated tenant slug in ``*.icims.com`` portal URLs.
    # It has no bounded vendor domain for a practical Common Crawl scan.
    _r("icims", (), "FALSE", (Pattern(r"^https?://careers\.([a-z0-9-]+)\.com(?:/|$)"),), "json", "https://careers.{token}.com/api/jobs?page=1", "https://careers.{token}.com/api/jobs?page=1"),
    _r("taleo", ("taleo.net",), "url_host_name LIKE '%.taleo.net'", (Pattern(r"^https?://([a-z0-9-]+)\.taleo\.net"),), "html", "https://{token}.taleo.net/careersection/", "https://{token}.taleo.net/careersection/"),
    # No Common Crawl scan: every career*.successfactors.* host serves
    # robots.txt "Disallow: /" (checked 2026-10-05), so CCBot never fetches a
    # board and 31 snapshots held none. Kept for probing and validation; the
    # crawl filter was hosts career*.successfactors.{com,eu} with company=.
    _r("successfactors", (), "FALSE", (Pattern(r"^https?://(career\d*\.successfactors\.(?:com|eu))/career\?(?:[^&]*&)*company=([A-Za-z0-9_-]+)", (1, 2)),), "html", "https://{t0}/career?company={t1}", "https://{t0}/career?company={t1}",
       # An unknown company still answers 200 with a ~3KB stub carrying this
       # sentence and no <title>; a real board is ~190KB. Without the marker
       # every company slug, real or not, classified as live.
       absent_markers=("an error occurred while processing your request",)),
    # PCS-X needs both the serving host and the board's domain query value.
    # Matching only URLs that expose both avoids inventing config for vanity
    # hosts, while still covering standard *.eightfold.ai boards and API URLs.
    _r("eightfold", ("eightfold.ai",), "url_host_name LIKE '%.eightfold.ai' AND url_query LIKE '%domain=%'", (
        Pattern(r"^https?://([A-Za-z0-9.-]+)/(?:api/pcsx/search|careers)(?:[^?#]*)\?(?:[^&]*&)*domain=([A-Za-z0-9.-]+)", (1, 2)),
    ), "json", "https://{t0}/api/pcsx/search?domain={t1}&query=&location=&start=0", "https://{t0}/careers?domain={t1}",
       count_path=("data", "count")),
    _r("oracle", ("oraclecloud.com",), "url_host_name LIKE '%.oraclecloud.com' AND url_path LIKE '/hcmUI/CandidateExperience/%'", (
        Pattern(r"^https?://([a-z0-9.-]+)\.oraclecloud\.com/hcmUI/CandidateExperience/[^/]+/sites/([A-Za-z0-9_-]+)", (1, 2)),
    ), "json", (
        "https://{t0}.oraclecloud.com/hcmRestApi/resources/latest/"
        "recruitingCEJobRequisitions?onlyData=true&finder=findReqs;"
        "siteNumber={t1},limit=1&expand=requisitionList"
    ), "https://{t0}.oraclecloud.com/hcmUI/CandidateExperience/en/sites/{t1}",
       count_path=("items", 0, "TotalJobsCount"),
       # recruitingCEJobRequisitions echoes `siteNumber` back but never applies
       # it: a nonexistent site returns the tenant's full requisition list, so
       # every token on a live tenant looked live (verified 2026-08-20 --
       # a bogus site and the real CX_1 returned byte-identical payloads apart
       # from the echoed SiteNumber). recruitingCESites *is* site-scoped and
       # answers count 1 for a real site, 0 for anything else.
       exists_url=(
           "https://{t0}.oraclecloud.com/hcmRestApi/resources/latest/"
           "recruitingCESites?onlyData=true&q=SiteNumber='{t1}'&limit=1"
       ),
       exists_count_path=("count",),
       # A board URL names its site by SiteNumber (`CX_4001`) or by its vanity
       # SiteURLName (`Aerospace`); 99 of 605 registry tokens are the latter,
       # and a SiteNumber-only check called every one absent (30 of 30 sampled,
       # 2026-10-04). Both fields match case-insensitively, so the lowercased
       # canonical token is safe. One `or`-ed `q` cannot replace the second
       # request: this endpoint answers count 0 to `SiteNumber='X' or
       # SiteURLName='X'` even when either half alone matches.
       exists_fallbacks=(
           "https://{t0}.oraclecloud.com/hcmRestApi/resources/latest/"
           "recruitingCESites?onlyData=true&q=SiteURLName='{t1}'&limit=1",
       )),
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
       }, count_path=("totalCount",)),
    _r("paylocity", ("paylocity.com",), "url_host_name = 'recruiting.paylocity.com'", (Pattern(r"^https?://recruiting\.paylocity\.com/recruiting/jobs/All/([0-9a-fA-F-]{36})"),), "html", "https://recruiting.paylocity.com/recruiting/jobs/All/{token}", "https://recruiting.paylocity.com/recruiting/jobs/All/{token}",
       absent_markers=("<title>job not found</title>",),
       # The probe parses window.pageData and verifies its LeadJoinUrl against
       # the requested GUID. No negative-control request is needed.
       html_required_markers=("job opportunities",)),
    _r("dayforce", ("dayforcehcm.com",), "url_host_name = 'jobs.dayforcehcm.com' AND regexp_matches(url_path, '^/[a-z]{2}(-[A-Za-z]{2})?/[A-Za-z0-9_-]+/CANDIDATEPORTAL(/|$)')", (
        Pattern(r"^https?://jobs\.dayforcehcm\.com/[a-z]{2}(?:-[A-Za-z]{2})?/([A-Za-z0-9_-]+)/CANDIDATEPORTAL(?:/|[?#]|$)"),
    ), "html", "https://jobs.dayforcehcm.com/en-US/{token}/CANDIDATEPORTAL", "https://jobs.dayforcehcm.com/en-US/{token}/CANDIDATEPORTAL", html_required_markers=("dayforce", "clientnamespace"), html_any_markers=("job board", "search jobs", "job postings")),
    # One Avature subdomain hosts any number of boards, each at a path the
    # customer names (`/careersau`, `/careersmarketplace`, `/jobs`), sometimes
    # behind a locale (`/ja_JP/careers`). The token is `company/board`; every
    # path seen becomes a candidate and the probe keeps the real boards --
    # forms and event pages lack the job-board markers and stay inconclusive.
    # The rule used to keep the subdomain alone and probe `/careers`, so a
    # board anywhere else read absent (bupaanz, bradyplus, baufest, 2026-10-06).
    # Paths fold case on Avature (`CareersAU` = `careersau`), and a bare
    # `company` token is `company/careers` -- see `_canonical_token`.
    _r("avature", ("avature.net",), "url_host_name LIKE '%.avature.net'", (
        Pattern(r"^https?://([a-z0-9-]+)\.avature\.net/([a-z]{2}_[A-Za-z]{2}/[A-Za-z0-9_-]+)(?:[/?#]|$)", (1, 2)),
        # A locale with no board after it names nothing: the empty group
        # leaves `company/`, which `token_rx` rejects.
        Pattern(r"^https?://([a-z0-9-]+)\.avature\.net/[a-z]{2}_[A-Za-z]{2}(?:[/?#]|$)()", (1, 2)),
        Pattern(r"^https?://([a-z0-9-]+)\.avature\.net/([A-Za-z0-9_-]+)(?:[/?#]|$)", (1, 2)),
    # Probed at `/SearchJobs`, the page the adapter reads: a landing page can
    # carry the job-board markers while its search 404s (event portals such
    # as bmcrecruit/pitchbmc), and the reverse (mercadona/es_ES/careers).
    ), "html", "https://{t0}.avature.net/{tail}/SearchJobs", "https://{t0}.avature.net/{tail}/SearchJobs",
       tail_default="careers",
       token_rx=r"^[a-z0-9-]+/(?:[a-z]{2}_[A-Za-z]{2}/)?[A-Za-z0-9_-]+$",
       html_required_markers=("avature.portal.",), html_any_markers=("search jobs", "find jobs", "/jobdetail/", "requesttype&quot;:&quot;job&quot;")),
    _r("zohorecruit", ("zohorecruit.com", "zohorecruit.eu"), "url_host_name LIKE '%.zohorecruit.com' OR url_host_name LIKE '%.zohorecruit.eu'", (Pattern(r"^https?://([a-z0-9-]+)\.zohorecruit\.(?:com|eu)"),), "html", "https://{token}.zohorecruit.com/jobs/Careers", "https://{token}.zohorecruit.com/jobs/Careers", fallbacks=("https://{token}.zohorecruit.eu/jobs/Careers",)),
    _r("homerun", ("homerun.co",), "url_host_name LIKE '%.homerun.co'", (Pattern(r"^https?://([a-z0-9-]+)\.homerun\.co"),), "html", "https://{token}.homerun.co/", "https://{token}.homerun.co/"),
    _r("join", ("join.com",), "url_host_name LIKE '%join.com' AND url_path LIKE '/companies/%'", (Pattern(r"^https?://(?:www\.)?join\.com/companies/([A-Za-z0-9_-]+)"),), "html", "https://join.com/companies/{token}", "https://join.com/companies/{token}"),
    _r("trakstar", ("trakstar.com",), "url_host_name LIKE '%.hire.trakstar.com'", (Pattern(r"^https?://([a-z0-9-]+)\.hire\.trakstar\.com(?:/|$)"),), "html", "https://{token}.hire.trakstar.com/jobs", "https://{token}.hire.trakstar.com/jobs"),
    _r("gem", ("gem.com",), "url_host_name = 'jobs.gem.com'", (
        Pattern(r"^https?://jobs\.gem\.com/([A-Za-z0-9_-]+)(?:/|$)"),
    ), "json", "https://api.gem.com/job_board/v0/{token}/job_posts/", "https://jobs.gem.com/{token}",
       # Deliberately NO light path. Every cheap alternative loses the one
       # distinction that matters (measured 2026-08-20):
       #   * HEAD on this endpoint: `athumanresources-com` and
       #     `brahma-consulting-group` answer 200 with a body of `[]` while
       #     jobs.gem.com/<token> is a hard "404 page not found" -- the board
       #     record survives the board being unpublished. Only the empty body
       #     separates them from `eqp`/`spherecast-ai`, which are 200 both
       #     ways. A HEAD probe would call all four live.
       #   * The public GraphQL board endpoint answers 200 with an empty list
       #     for a board that never existed at all.
       # So gem pays the full 213 KB and lets the zero_count guard rule.
       ),
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
    ), count_path=("meta", "totalNumber")),
    _r("bullhorn", ("bullhornstaffing.com",), "regexp_matches(url_host_name, '^public-rest[0-9]+\\.bullhornstaffing\\.com$') AND url_path LIKE '/rest-services/%/query/JobBoardPost'", (
        Pattern(r"^https?://public-rest([0-9]+)\.bullhornstaffing\.com/rest-services/([A-Za-z0-9_-]+)/query/JobBoardPost", (1, 2)),
    ), "json", (
        "https://public-rest{t0}.bullhornstaffing.com/rest-services/{t1}/query/JobBoardPost"
        "?fields=id,title&where=isOpen=true&start=0&count=1&showTotalMatched=true"
    ), "https://public-rest{t0}.bullhornstaffing.com/rest-services/{t1}/query/JobBoardPost",
       count_path=("total",)),
)

BY_SOURCE = {rule.source: rule for rule in RULES}

# Named groups of vendors, so a scan can target "tier1" instead of listing
# every source. Expanded by ``expand_sources`` before use.
SOURCE_GROUPS: dict[str, tuple[str, ...]] = {
    # icims is deliberately absent: its token is the label in ``careers.{token}.com``,
    # which has no bounded vendor domain to scan, so a crawl-driven group cannot
    # reach it (``domains=()``, ``where_sql="FALSE"``).
    "tier1": (
        "greenhouse", "workable", "workday", "ashby", "lever", "oracle",
        "eightfold", "smartrecruiters", "avature",
    ),
    # SMB payroll-ATS cluster that carries biotech (doc/raw_postings/criteria/
    # vendor-list.md). Discovery-only: none of these are in VALIDATED_SOURCES,
    # so their tokens stay `status = 'unknown'` until a rule is verified.
    "tier2": (
        "adp", "bamboohr", "ukg",
    ),
}

# Only these vendors may have their tokens probed.  A rule earns a place by
# demonstrating, against live data, that it can tell a real board from one that
# does not exist -- see `keyscan verify` and docs/issues.md.  Rules that cannot
# discriminate do not merely add noise: oracle and successfactors were each
# marking *every* token live, which is worse than leaving them unknown.
# Discovery is unaffected; tokens for other vendors are still recorded, they
# just keep `status = 'unknown'` until their rule is verified.
VALIDATED_SOURCES: frozenset[str] = frozenset({
    "greenhouse", "ashby", "lever", "oracle", "workday", "workable",
})


def expand_sources(names: tuple[str, ...]) -> tuple[str, ...]:
    """Expand any group names (e.g. ``tier1``) into their member sources."""
    expanded: list[str] = []
    for name in names:
        expanded.extend(SOURCE_GROUPS.get(name, (name,)))
    return tuple(dict.fromkeys(expanded))


def valid_token(token: str, rule: Rule | None = None) -> bool:
    """Python half of the token check; `token_filter_sql` is the DuckDB half."""
    if not re.match(rule.token_re if rule and rule.token_re else TOKEN_RE.pattern, token):
        return False
    if (rule is None or rule.stop_words) and token.split("/", 1)[0].lower() in STOP:
        return False
    return not (rule and rule.token_rx and not re.match(rule.token_rx, token))


def token_filter_sql(rule: Rule, column: str = "token") -> str:
    """DuckDB condition equal to ``valid_token(column, rule)``."""
    def quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"
    conditions = [f"{column} <> ''", f"regexp_matches({column}, {quote(rule.token_re or TOKEN_RE.pattern)})"]
    if rule.stop_words:
        stop = ", ".join(quote(value) for value in sorted(STOP))
        conditions.append(f"lower(split_part({column}, '/', 1)) NOT IN ({stop})")
    if rule.token_rx:
        conditions.append(f"regexp_matches({column}, {quote(rule.token_rx)})")
    return "(" + " AND ".join(conditions) + ")"


def extract(url: str) -> tuple[str, str] | None:
    """Return a normalized ``(source, key)`` from a public board URL."""
    url = _SCHEME_HOST.sub(lambda match: match.group(1).lower(), url, count=1)
    for rule in RULES:
        for pattern in rule.patterns:
            match = re.match(pattern.rx, url)
            if not match:
                continue
            token = "/".join(match.group(group) for group in pattern.groups)
            return (rule.source, token) if valid_token(token, rule) else None
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


def rule_fingerprint(rule: Rule) -> str:
    """Short hash of everything that decides what a Common Crawl scan of `rule`
    finds: its domains (the key ranges read), its row filter, its token
    extraction, and the shared token checks. A `scanned` row is only trusted
    while this still matches -- edit any of these and the pair is due again.
    """
    import hashlib
    import json

    parts: list[object] = [rule.domains, rule.where_sql, token_sql(rule), TOKEN_RE.pattern, sorted(STOP)]
    # Appended only when set, so adding these fields left every other
    # vendor's hash -- and its scanned history -- unchanged.
    if not rule.stop_words:
        parts.append("no_stop_words")
    if rule.token_rx:
        parts.append(rule.token_rx)
    if rule.token_re:
        parts.append(["token_re", rule.token_re])
    material = json.dumps(parts, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


SAMPLES: tuple[tuple[str, tuple[str, str] | None], ...] = (
    ("https://boards.greenhouse.io/stripe", ("greenhouse", "stripe")),
    ("https://jobs.lever.co/palantir", ("lever", "palantir")),
    ("https://jobs.lever.co:443/bisnow", ("lever", "bisnow")),
    ("https://jobs.lever.co/glow.co/0b1c", ("lever", "glow.co")),
    ("https://jobs.ashbyhq.com/openai/123", ("ashby", "openai")),
    ("https://jobs.ashbyhq.com/Citizen%20Health/eaa76517", ("ashby", "Citizen%20Health")),
    ("https://jobs.ashbyhq.com/A1%20Garage%20Door%20Service?utm=x", ("ashby", "A1%20Garage%20Door%20Service")),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite", ("workday", "nvidia/wd5/NVIDIAExternalCareerSite")),
    ("https://academy.wd1.myworkdayjobs.com/Careers", ("workday", "academy/wd1/Careers")),
    ("https://academy.wd1.myworkdayjobs.com/en-US/Careers/job/1", ("workday", "academy/wd1/Careers")),
    ("https://3m.wd1.myworkdayjobs.com/robots.txt", None),
    ("https://medtronic.wd1.myworkdayjobs.com/llms.txt", None),
    ("https://acme.wd5.myworkdayjobs.com/en-US/External.html", None),
    ("https://jobs.smartrecruiters.com/Palantir", ("smartrecruiters", "Palantir")),
    ("https://jobs.smartrecruiters.com/B6/743999906987124-senior-ios-engineer", ("smartrecruiters", "B6")),
    ("https://jobs.smartrecruiters.com/ni/HarvardUniversity/6d1eb209-research-assistant-i", ("smartrecruiters", "HarvardUniversity")),
    ("https://jobs.smartrecruiters.com/my-applications/ALDIStores/input-email?dcr_ci=ALDIStores", ("smartrecruiters", "ALDIStores")),
    ("https://jobs.smartrecruiters.com/oneclick-ui/company/3HPartners/publication/bd58bc2e?dcr_ci=3HPartners", ("smartrecruiters", "3HPartners")),
    ("https://jobs.smartrecruiters.com/external-referrals/company/AbbVie/publication/426a334d", ("smartrecruiters", "AbbVie")),
    ("https://jobs.smartrecruiters.com/oneclick-ui/company/111186967/job/1612706330/publication/1", None),
    ("https://jobs.smartrecruiters.com/oneclick-ui", None),
    ("https://jobs.smartrecruiters.com/oneclick/e876e37a/profile/edit", None),
    ("https://jobs.smartrecruiters.com/cdn-cgi/l/email-protection", None),
    ("https://apply.workable.com/deliveroo/", ("workable", "deliveroo")),
    ("https://apply.workable.com/3e/j/18DBBD15E0", ("workable", "3e")),
    ("https://acme.workable.com/", ("workable", "acme")),
    ("https://apply.workable.com/", None),
    ("https://apply.workable.com/?ref=inouts.com", None),
    ("https://apply.workable.com/j/00161ECF35", None),
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
    ("https://acme.avature.net/careers", ("avature", "acme/careers")),
    ("https://bupaanz.avature.net/careersau/JobDetail/Carer/66469", ("avature", "bupaanz/careersau")),
    ("https://bradyplus.avature.net/careersmarketplace/ApplicationMethods?jobId=1037", ("avature", "bradyplus/careersmarketplace")),
    ("https://astellasjapan.avature.net/ja_JP/careers/JobDetail/3869", ("avature", "astellasjapan/ja_JP/careers")),
    ("https://astellas.avature.net/en_GB", None),
    ("https://acme.avature.net/", None),
    ("https://acme.avature.net/robots.txt", None),
    ("https://www.avature.net/de/blogs/1", None),
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
