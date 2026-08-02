"""Per-company configs for the shared house_ats adapter.

Add a new company by adding a dict to CONFIGS below. No code changes needed.
See sources/planned/house-ats.md for endpoint details and fixtures.
"""

CONFIGS = {
    "amazon": {
        "endpoint": "https://www.amazon.jobs/en/search.json",
        "method": "GET",
        "query": {
            "base_query": "",
            "offset": 0,
            "result_limit": 100,
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
        "endpoint": "https://jobs.apple.com/api/role/search",
        "method": "POST",
        "body": {
            "query": "",
            "filters": {},
            "page": 1,
            "locale": "en-us",
        },
        "items_path": "searchResults",
        "total_path": "totalRecords",
        "pagination": {
            "type": "page",
            "param": "page",
            "size": 50,
            "max_pages": 50,
        },
        "company_slug": "apple",
        "fields": {
            "external_id": "positionId",
            "title": "postingTitle",
            "url_posting": {"template": "https://jobs.apple.com/en-us/details/{transformedPostingTitle}"},
            "location_raw": {"template": "{locations[0].city}, {locations[0].stateProvince}, {locations[0].countryName}"},
            "description_html": "jobSummary",
            "published_at": "postingDate",
        },
    },
    "microsoft": {
        "endpoint": "https://gcsservices.careers.microsoft.com/search/api/v1/search",
        "method": "GET",
        "query": {
            "q": "",
            "l": "en_us",
            "pg": 1,
            "pgSz": 20,
        },
        "items_path": "operationResult.result.jobs",
        "total_path": "operationResult.result.totalJobs",
        "pagination": {
            "type": "page",
            "param": "pg",
            "size": 20,
            "max_pages": 200,
        },
        "company_slug": "microsoft",
        "fields": {
            "external_id": "jobId",
            "title": "title",
            "url_posting": {"template": "https://careers.microsoft.com/us/en/job/{jobId}"},
            "location_raw": {"template": "{properties.locations[0]}"},
            "description_html": "properties.description",
            "employment_type": "properties.jobType",
            "workplace_type_raw": "properties.discipline",
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