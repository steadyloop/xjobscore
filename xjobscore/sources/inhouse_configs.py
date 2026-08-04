"""Per-company configs for the shared inhouse adapter.

Add a new company by adding a dict to CONFIGS below. No code changes needed.
See sources/planned/inhouse.md for endpoint details and fixtures.
"""

CONFIGS = {
    "amazon": {
        "endpoint": "https://www.amazon.jobs/en/search.json",
        "method": "GET",
        "query": {
            "base_query": "",
            "offset": 0,
            "result_limit": 100,
            "is_intern[]": ["1"],
            "schedule_type_id[]": [
                "Flex-Time",
                "Part-Time",
                "Reduced-Time",
                "Flex-Time (<19 hours)",
                "Part-Time (20-29 hours)",
                "Reduced-Time (30-39 hours)",
                "Flex Time (<19 hours)",
                "Reduced Time (30-39 hours)",
            ],
            "employee_class[]": ["Seasonal/short-term"],
        },
        "items_path": "jobs",
        "total_path": "hits",
        "pagination": {
            "type": "offset",
            "param": "offset",
            "size": 100,
            "max_pages": 200,
        },
        "company_slug": "amazon",
        "fields": {
            "external_id": "id",
            "title": "title",
            "url_posting": {"template": "https://www.amazon.jobs{job_path}"},
            "location_raw": "normalized_location",
            "description_html": "description",
            "requisition_id": "id_icims",
        },
    },
    "apple": {
        "endpoint": "https://jobs.apple.com/api/v1/search",
        "method": "POST",
        "body": {
            "query": "",
            "filters": {
                "locations": ["postLocation-USA"],
            },
            "page": "{page}",
            "locale": "en-us",
            "sort": "newest",
            "format": {
                "longDate": "MMMM D, YYYY",
                "mediumDate": "MMM D, YYYY",
            },
        },
        "items_path": "res.searchResults",
        "total_path": "res.totalRecords",
        "pagination": {
            "type": "page",
            "param": "page",
            "size": 20,
            "max_pages": 400,
        },
        "company_slug": "apple",
        "fields": {
            "external_id": "positionId",
            "title": "postingTitle",
            "url_posting": {"template": "https://jobs.apple.com/en-us/details/{positionId}/{transformedPostingTitle}"},
            "location_raw": {"template": "{locations[0].city}, {locations[0].stateProvince}, {locations[0].countryName}"},
            "description_html": "jobSummary",
            "published_at": "postDateInGMT",
        },
    },
    "tesla": {
        "endpoint": "https://www.tesla.com/cua-api/apps/careers/state",
        "method": "GET",
        "items_path": "listings",
        "company_slug": "tesla",
        "fields": {
            "external_id": "id",
            "title": "t",
            "url_posting": {"template": "https://www.tesla.com/careers/search/job/{id}"},
            "location_raw": {"lookup": "locations", "key": "l"},
            "employment_type": {"lookup": "types", "key": "type"},
            "workplace_type_raw": {"lookup": "departments", "key": "d"},
        },
    },
    "uber": {
        "endpoint": "https://www.uber.com/api/loadSearchJobsResults",
        "method": "POST",
        "body": {
            "limit": 50,
            "page": "{page}",
            "params": {
                "location": [],
                "team": [],
            },
            "query": "",
        },
        "items_path": "data.results",
        "total_path": "data.totalResults",
        "pagination": {
            "type": "page",
            "param": "page",
            "start": 0,
            "size": 50,
            "max_pages": 50,
        },
        "company_slug": "uber",
        "fields": {
            "external_id": "id",
            "title": "title",
            "url_posting": "url",
            "location_raw": {"template": "{location.city}, {location.country}"},
            "description_html": "description",
            "published_at": "creationDate",
        },
    },
}
