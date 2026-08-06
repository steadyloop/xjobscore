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
