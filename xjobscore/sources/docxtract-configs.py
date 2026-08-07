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
    "jobvite": {
        "list": {
            "url": "https://jobs.jobvite.com/{key}/jobs",
            "extract": "html_links",
            "pattern": r"/{key}/job/(?P<id>[A-Za-z0-9]+)",
        },
        "detail": {"extract": "jsonld", "id": "discovered"},
        "parser": "schemaorg",
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
        "parser": "schemaorg",
    },
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
