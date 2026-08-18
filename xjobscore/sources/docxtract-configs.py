"""Configurations for the public document-board adapter.

``webboard`` deliberately supports only four extraction shapes: embedded
schema.org JSON-LD, HTML links leading to detail pages, RSS/XML items, and XML
links leading to detail pages.  Adding a compatible board is a configuration
change, not a new source module.
"""

from __future__ import annotations


CONFIGS: dict[str, dict] = {
    "jsonld": {
        "list": {"url": "{key}", "extract": "jsonld"},
        "parser": "schemaorg",
    },
    # Homerun publishes the complete board as a public Atom feed. Unlike the
    # lean Atom core, Homerun puts the full job body in <description> and adds
    # unqualified-in-practice extension fields inside the Atom namespace.
    "homerun": {
        "list": {
            "url": "https://feed.homerun.co/{key}",
            "extract": "xml_items",
            "path": "{http://www.w3.org/2005/Atom}entry",
            "id": ("{http://www.w3.org/2005/Atom}id",),
        },
        "parser": "atom",
        "atom": {
            "company_path": "author/name",
            "description_path": "description",
            "location_path": "location",
            "employment_type_path": "type",
            "extra_paths": {
                "department": "department",
                "salary_indication": "salary_indication",
                "updated": "updated",
            },
        },
    },
    # Trakstar Hire (formerly Recruiterbox) exposes a whole-board RSS feed.
    # The job extensions use the historical Recruiterbox namespace.
    "trakstar": {
        "list": {
            "url": "https://{key}.hire.trakstar.com/jobfeeds/{key}",
            "extract": "xml_items",
            "path": "channel/item",
            "id": ("guid", "link"),
        },
        "parser": "rss",
        "rss": {
            "namespace": "https://recruiterbox.com/rss/job/",
            "location_path": "locationCity",
            "employment_type_path": "positionType",
            "extra_paths": {
                "location_state": "locationState",
                "location_country": "locationCountry",
                "close_date": "closeDte",
                "team": "team",
            },
        },
    },
    "jobvite": {
        "list": {
            "url": "https://jobs.jobvite.com/{key}/jobs",
            "extract": "html_links",
            "pattern": r"/{key}/job/(?P<id>[A-Za-z0-9]+)",
        },
        "detail": {"extract": "jsonld", "id": "discovered"},
        "parser": "schemaorg",
        # jobs.jobvite.com has no bulk feed -- every path (sitemap.xml,
        # jobs.json, ...) 200s the same SPA shell, so full posting data only
        # exists per detail page. Verified tolerant of concurrent requests
        # (8 at once: all 200 in <300ms, no throttling from Cloudflare), so
        # this trades the default 1-req/s-per-host pacing for up to 5
        # in-flight detail fetches at once.
        "detail_concurrency": 5,
        "posting_url": "https://jobs.jobvite.com/{key}/job/{id}",
        "apply_url": True,
        "requisition_id": True,
        "location_fields": ("addressLocality", "addressRegion"),
        "source_extra": ("industry", "baseSalary"),
    },
    "teamtailor": {
        "list": {
            "url": "https://{key}.teamtailor.com/jobs.rss",
            "extract": "xml_items",
            "path": "channel/item",
            "id": ("guid", "link"),
        },
        "parser": "rss",
        "rss": {
            "namespace": "https://teamtailor.com/locations",
            "location_path": "locations/location",
            "extra_paths": {
                "department": "department",
                "role": "role",
                "division": "division",
            },
        },
    },
    # SAP SuccessFactors (Career Site Builder). `/sitemap.xml` is not a sitemap:
    # every CSB tenant serves an RSS 2.0 feed there carrying the whole board with
    # descriptions inline, so one GET is the complete listing and no detail fetch
    # is needed. `key` is the career host ("jobs.sap.com"), not a company slug,
    # because the feed is reached by domain and names its own employer.
    #
    # Two fields are deliberately dropped rather than mapped:
    #   g:expiration_date -- identical on every item in a feed (generation date
    #     plus 30 days), so it is a feed TTL, not a per-job deadline. Mapping it
    #     to deadline_at would fabricate a deadline on every posting.
    #   pubDate -- absent entirely, so published_at stays None and precision
    #     'unknown'. Latency cannot be measured from this source.
    #
    # The response carries no ETag or Last-Modified and sets no-store, so
    # conditional GET never hits: every poll transfers the full feed (~1.8 MB
    # gzipped for a 944-job tenant). Set a cadence in hours, not the 900s default.
    "successfactors": {
        "list": {
            "url": "https://{key}/sitemap.xml",
            "extract": "xml_items",
            "path": "channel/item",
            "id": ("guid", "link"),
        },
        "parser": "rss",
        "rss": {
            "namespace": "http://base.google.com/ns/1.0",
            "location_path": "location",
            "company_path": "employer",
            # descriptions are entity-escaped markup inside CDATA
            "unescape_description": True,
            # No apply_url. The job page's "Apply now" href,
            # /talentcommunity/apply/{id}/, looks derivable from <guid> but is a
            # no-JS fallback on a dialogApplyBtn: requested directly it 302s to
            # the career-site homepage, with or without ?locale=, with or
            # without cookies and Referer, and a nonexistent id behaves
            # identically -- so a 200 there proves nothing. The real target is
            # injected by JS into an empty #apply-CTA-container and lands on
            # career{N}.successfactors.eu behind a per-session `_s.crb` CSRF
            # token, which no template can produce. url_posting is the apply
            # entry point for this source; do not synthesize one.
            "extra_paths": {
                "employer": "employer",
                "job_function": "job_function",
                "expiration_date": "expiration_date",
            },
        },
    },
    # Vincere. The `/careers/` list page itself carries only `WebPage` JSON-LD,
    # but each `/careers/job/{id}/{slug}` detail page carries `['WebPage',
    # 'JobPosting']`. The JobPosting block has no `url` field of its own, so
    # posting_url is templated from key+id rather than left to the job.get("url")
    # fallback; the slug-free form 301s through to the same page (verified).
    "vincere": {
        "list": {
            "url": "https://{key}.vincere.io/careers/",
            "extract": "html_links",
            "pattern": r"/careers/job/(?P<id>\d+)/",
        },
        "detail": {"extract": "jsonld", "id": "discovered"},
        # Measured on zealhr (10 postings, 11 requests): width 1 12.1s, 2 5.0s,
        # 4 4.6s, 8 4.4s, all clean 200s. Past 2 the curve flattens -- 4 and 8
        # buy 0.6s between them while looking progressively more like a crawler,
        # which is the same call jobvite's note records.
        "detail_concurrency": 2,
        "parser": "schemaorg",
        "posting_url": "https://{key}.vincere.io/careers/job/{id}",
    },
    # JazzHR (Employ Inc). The RSS feed candidates.md lists is retired
    # (/apply/jobs/feed 404s, /apply/jobs.rss is 410 Gone), but /apply/jobs/
    # lists /apply/jobs/details/{id} links, and each detail page carries a
    # full JobPosting JSON-LD block alongside an Organization block -- _jsonld
    # already filters to @type == JobPosting.
    "jazzhr": {
        "list": {
            "url": "https://{key}.applytojob.com/apply/jobs/",
            "extract": "html_links",
            "pattern": r"/apply/jobs/details/(?P<id>[A-Za-z0-9]+)",
        },
        "detail": {"extract": "jsonld", "id": "discovered"},
        # Measured on dtexsystems (7 postings, 9 requests): width 1 10.5s,
        # 2 9.0s, 4 4.4s, 8 6.9s, all clean 200s. Noisy -- 8 came back slower
        # than 4, which on a 9-request sample means the differences past 4 are
        # not resolvable rather than that 8 is worse. 4 is the fastest reading
        # and matches the ceiling used elsewhere in this tree.
        "detail_concurrency": 4,
        "parser": "schemaorg",
    },
    # PeopleAdmin. `.json` 406s and the plain HTML search page has no
    # JSON-LD, but `/postings/search.atom` is a full board with descriptions
    # inline -- one GET, no detail fetch. `key` is the institution subdomain
    # ("marshall" -> marshall.peopleadmin.com).
    #
    # Every base field lives in the Atom namespace, unlike RSS, so this needs
    # the `atom` parser rather than `rss` -- see docxtract.py's `_atom`.
    # `content` is entity-escaped HTML but needs no unescape flag: XML parsing
    # already decodes it once (there is no CDATA double-wrap here, unlike
    # SuccessFactors' RSS feed). No `location_path` is configured -- postings
    # carry no place field at all, only the hiring unit in `author/name`,
    # which is too fine-grained to be company_raw (e.g. "Public Safety -
    # MU5500"), so it goes to `source_extra` instead and company_raw stays
    # the tenant key, matching every other per-tenant board in this file.
    "peopleadmin": {
        "list": {
            "url": "https://{key}.peopleadmin.com/postings/search.atom",
            "extract": "xml_items",
            "path": "{http://www.w3.org/2005/Atom}entry",
            "id": ("{http://www.w3.org/2005/Atom}id",),
        },
        "parser": "atom",
        "atom": {
            "extra_paths": {"hiring_unit": "author/name"},
        },
    },
    # Oleeo (formerly WCN). Public boards live on *.tal.net and expose one
    # whole-board Atom feed. The vacancy number varies by tenant, so board 1
    # is the default while catalog rows for other boards provide feed.config
    # `url`, which _list_url already treats as the sanctioned endpoint override.
    #
    # Only feeds whose entries use <content type="text"> are supported here.
    # Oleeo also emits type="xhtml" on some tenants; that payload lives below
    # an XHTML <div>, while the unchanged Atom parser deliberately reads only
    # content.text. Do not catalog an XHTML tenant as live until the parser
    # explicitly supports that shape. These feeds are unpaginated, may be
    # vendor-capped, and expose no useful validators, so use an hourly cadence.
    "oleeo": {
        "list": {
            "url": "https://{key}.tal.net/candidate/jobboard/vacancy/1/feed",
            "extract": "xml_items",
            "path": "{http://www.w3.org/2005/Atom}entry",
            "id": ("{http://www.w3.org/2005/Atom}id",),
        },
        "parser": "atom",
        "atom": {},
    },
    # Fly.io. `/jobs/feed.xml` is a real Atom feed (root envelope verified
    # live, no ETag/Last-Modified), currently carrying zero <entry> elements
    # because Fly.io has no open roles right now -- a valid, non-error state,
    # not a broken feed. The URL is a static, single-tenant literal like
    # `unstop`'s, so `key` is inert here; `_atom` falls back to `feed.key`
    # ("flyio") for `company_raw`, the same trade-off PeopleAdmin already
    # accepts, since Fly.io names itself nowhere on an entry. No fetch-side
    # filter is wired: the whole board is Fly.io's own engineering roles by
    # construction and volume is trivial even when non-empty.
    "flyio": {
        "list": {
            "url": "https://fly.io/jobs/feed.xml",
            "extract": "xml_items",
            "path": "{http://www.w3.org/2005/Atom}entry",
            "id": ("{http://www.w3.org/2005/Atom}id",),
        },
        "parser": "atom",
        "atom": {},
    },
    # Personio. `/xml` (also `.com`) is a bespoke `<workzag-jobs><position>`
    # schema, not RSS -- no namespace, and none of RSS/Atom's element names
    # (title is `name`, no `link`). `key` is the tenant subdomain.
    #
    # No `<url>` element exists on a position at all, so `posting_url` is
    # templated from key+id; verified live that `.../job/{id}` resolves to
    # the matching posting. `jobDescriptions` was empty on the one live
    # posting this was checked against (Personio's own tenant) -- the field
    # is mapped for tenants that populate it, but is unverified as non-empty.
    "personio": {
        "list": {
            "url": "https://{key}.jobs.personio.de/xml",
            "extract": "xml_items",
            "path": "position",
            "id": ("id",),
        },
        "parser": "xml_map",
        "posting_url": "https://{key}.jobs.personio.de/job/{id}",
        "xml_map": {
            "fields": {
                "title": "name", "description": "jobDescriptions", "published": "createdAt",
                "employment_type": "employmentType",
            },
            "location_paths": ("office", "additionalOffices/office"),
            "company_path": "subcompany",
            "extra_paths": {
                "department": "department", "recruiting_category": "recruitingCategory",
                "seniority": "seniority", "schedule": "schedule",
                "years_of_experience": "yearsOfExperience", "occupation": "occupation",
                "occupation_category": "occupationCategory",
            },
        },
    },
    # This repository's JSON is a shared historical/off-season store, not just
    # the rows rendered in its main README. The bespoke parser mirrors the
    # upstream active + visible + Summer 2027 + freshness selection while
    # retaining every fetched row in replayable bronze.
    "simplify": {
        "list": {
            "url": "https://raw.githubusercontent.com/{key}/dev/.github/scripts/listings.json",
            "extract": "json_items",
            "items": "",
            "id": "id",
        },
        "parser": "simplify",
        "source_extra": (
            "source", "category", "terms", "sponsorship", "degrees",
            "is_visible", "company_url", "date_posted", "date_updated",
        ),
    },
}
