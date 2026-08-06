# Company JSON API research

These are endpoint notes. Implemented JSON providers are profiles in
`json-api-configs.py`; the examples below are not a separate config dialect.

---

## Amazon

**Endpoint:** `https://www.amazon.jobs/en/search.json`  
**Method:** GET  
**Fixture:** `fixtures/amazon.json`

**Config shape:**
```json
{
  "endpoint": "https://www.amazon.jobs/en/search.json",
  "method": "GET",
  "query": {
    "base_query": "",
    "offset": 0,
    "result_limit": 100
  },
  "items_path": "jobs",
  "total_path": "hits",
  "pagination": {
    "type": "offset",
    "param": "offset",
    "size": 100,
    "max_pages": 200
  },
  "company_slug": "amazon",
  "fields": {
    "external_id": "id",
    "title": "title",
    "url_posting": {"template": "https://www.amazon.jobs{job_path}"},
    "location_raw": "normalized_location",
    "description_html": "description",
    "requisition_id": "id_icims"
  }
}
```

**Notes:**  
- Includes both basic and preferred qualifications as optional fields  
- Posted dates are human-readable strings (e.g. "April 14, 2026"), not ISO format  
- Includes facet data (business_category)  

---

## Apple

**Endpoint:** `https://jobs.apple.com/api/role/search`  
**Method:** POST  
**Fixture:** `fixtures/apple.json`

**Config shape:**
```json
{
  "endpoint": "https://jobs.apple.com/api/role/search",
  "method": "POST",
  "body": {
    "query": "",
    "filters": {},
    "page": 1,
    "locale": "en-us"
  },
  "items_path": "searchResults",
  "total_path": "totalRecords",
  "pagination": {
    "type": "page",
    "param": "page",
    "size": 50,
    "max_pages": 50
  },
  "company_slug": "apple",
  "fields": {
    "external_id": "positionId",
    "title": "postingTitle",
    "url_posting": {"template": "https://jobs.apple.com/en-us/details/{transformedPostingTitle}"},
    "location_raw": {"template": "{locations[0].city}, {locations[0].stateProvince}, {locations[0].countryName}"},
    "description_html": "jobSummary",
    "published_at": "postingDate"
  }
}
```

**Notes:**  
- Requires CSRF token in headers (obtained from first GET to `/en-us/search`)  
- Locations are nested arrays; use first location for simplicity  
- Department info is nested under `department.departmentName`  

---

## Microsoft

**Endpoint:** `https://gcsservices.careers.microsoft.com/search/api/v1/search`  
**Method:** GET  
**Fixture:** `fixtures/microsoft.json`

**Config shape:**
```json
{
  "endpoint": "https://gcsservices.careers.microsoft.com/search/api/v1/search",
  "method": "GET",
  "query": {
    "q": "",
    "l": "en_us",
    "pg": 1,
    "pgSz": 20
  },
  "items_path": "operationResult.result.jobs",
  "total_path": "operationResult.result.totalJobs",
  "pagination": {
    "type": "page",
    "param": "pg",
    "size": 20,
    "max_pages": 200
  },
  "company_slug": "microsoft",
  "fields": {
    "external_id": "jobId",
    "title": "title",
    "url_posting": {"template": "https://careers.microsoft.com/us/en/job/{jobId}"},
    "location_raw": {"template": "{properties.locations[0]}"},
    "description_html": "properties.description",
    "employment_type": "properties.jobType",
    "workplace_type_raw": "properties.discipline"
  }
}
```

**Notes:**  
- Deeply nested response: items and total are under `operationResult.result`  
- Locations array under `properties`; use first element  
- `isSuccess` flag in response root indicates healthy poll  

---

## Tesla

**Endpoint:** `https://www.tesla.com/cua-api/apps/careers/state`  
**Method:** GET  
**Fixture:** `fixtures/tesla.json`

**Config shape:**
```json
{
  "endpoint": "https://www.tesla.com/cua-api/apps/careers/state",
  "method": "GET",
  "items_path": "listings",
  "company_slug": "tesla",
  "fields": {
    "external_id": "id",
    "title": "t",
    "url_posting": {"template": "https://www.tesla.com/careers/search/job/{id}"},
    "location_raw": {"const": "lookup.locations.{l}"},
    "employment_type": {"const": "lookup.types.{type}"},
    "workplace_type_raw": {"const": "lookup.departments.{d}"}
  }
}
```

**Notes:**  
- Returns entire board in one response; no pagination needed  
- Uses single-letter keys that map to `lookup` dictionaries  
  - `t` = title, `d` = department, `l` = location, `type` = type  
- Must resolve lookups post-fetch (e.g., field `"d": "1"` → `lookup.departments["1"]`)  
- No pagination config needed  

---

## Uber

**Endpoint:** `https://www.uber.com/api/loadSearchJobsResults`  
**Method:** POST  
**Fixture:** `fixtures/uber.json`

**Config shape:**
```json
{
  "endpoint": "https://www.uber.com/api/loadSearchJobsResults",
  "method": "POST",
  "body": {
    "limit": 50,
    "page": "{page}",
    "params": {
      "location": [],
      "team": []
    },
    "query": ""
  },
  "items_path": "data.results",
  "total_path": "data.totalResults",
  "pagination": {
    "type": "page",
    "param": "page",
    "size": 50,
    "max_pages": 50
  },
  "company_slug": "uber",
  "fields": {
    "external_id": "id",
    "title": "title",
    "url_posting": "url",
    "location_raw": {"template": "{location.city}, {location.country}"},
    "description_html": "description",
    "published_at": "creationDate"
  }
}
```

**Notes:**  
- Requires session cookies to avoid 406  
- Interpolate `{page}` in body for pagination (0-indexed)  
- Description contains HTML fragments  
- Times in ISO format (e.g. `2023-10-01T12:00:00.000Z`)  

---

## Meta

**Endpoint:** `https://www.metacareers.com/graphql`  
**Method:** POST  
**Fixture:** `fixtures/meta.json`

**Notes:**  
- **Module: `sources/meta.py`** (separate bespoke adapter)  
- GraphQL query with rotating persisted-query `doc_id` (currently unpredictable, fragile)  
- Requires CSRF token and session cookies  
- Response shape in fixture is what a successful query yields; error responses are opaque and frequent  
- No shared config-driven adapter; implement as standalone fetch/parse pair  
- Document observed `doc_id` values and query shapes if reverse-engineered  

---

## Google

**Endpoint:** `https://www.google.com/about/careers/applications/jobs/sitemap.xml`  
**Method:** GET  
**Fixture:** `fixtures/google.xml`

**Notes:**  
- **Module: `sources/google.py`** (separate XML/sitemap-based adapter)  
- No JSON search API; only sitemap of individual posting URLs  
- Fetch sitemap XML, extract `<loc>` URLs and `<lastmod>` timestamps  
- Each URL points to a full job posting page with embedded JSON-LD schema (`schema.org/JobPosting`)  
- Parse JSON-LD from HTML via `<script type="application/ld+json">` tags  
- Implement as two-stage fetch (sitemap → per-posting HTML parse)  
- No shared config-driven adapter; implement as standalone fetch/parse pair
