"""Plain configuration for the shared JSON ATS adapter.

Expression values: ``@path`` reads an item/response path; ``$name`` reads
engine context (``$key``, ``$id``, ``$cursor``, ``$envelope.*``).  Everything
else is a literal.  Keep a configuration declarative; only add engine support
when a pattern is shared by several vendors.
"""

SIMPLE: dict[str, dict] = {
    "ashby": {
        "list": {"url": "https://api.ashbyhq.com/posting-api/job-board/{key}?includeCompensation=true", "items": "jobs", "id": "@id"},
        "envelope": {"apiVersion": "@apiVersion"},
        "fields": {
            "title": "@title", "url_posting": "@jobUrl", "apply_url": "@applyUrl", "company_raw": "$key",
            "location_raw": "@location", "description_html": "@descriptionHtml", "employment_type": "@employmentType",
            "workplace_type_raw": "@workplaceType", "published_at": {"date": "@publishedAt"},
            "published_at_precision": "exact", "source_active": "@isListed",
            "locations_extra": {"map": "@secondaryLocations", "value": "@location"},
        },
        "extra": {"department": "@department", "team": "@team", "compensation": "@compensation", "isRemote": "@isRemote"},
    },
    "breezy": {
        "list": {"url": "https://{key}.breezy.hr/json?verbose=true", "items": "", "id": "@id"},
        "fields": {
            "title": "@name", "url_posting": "@url", "company_raw": "@company.name", "location_raw": "@location.name",
            "description_html": "@description", "employment_type": "@type.name", "published_at": {"date": "@published_date"},
            "published_at_precision": "exact", "locations_extra": {"map": "@locations", "value": "@name"},
        },
        "extra": {"department": "@department", "salary": "@salary", "is_remote": "@location.is_remote", "friendly_id": "@friendly_id"},
    },
    "comeet": {
        "list": {"url": "https://www.comeet.co/careers-api/2.0/company/{key}/positions?token={token}&details=true", "items": "", "id": "@uid"},
        "fields": {
            "title": "@name", "url_posting": "@url_comeet_hosted_page", "company_raw": "@company_name",
            "location_raw": {"fallback": ["@location.name", {"join": ["@location.city", "@location.state", "@location.country"]}]},
            "description_html": {"concat": {"map": "@details", "value": "@value"}},
            "employment_type": "@employment_type", "workplace_type_raw": "@workplace_type",
            "source_updated_at": {"date": "@time_updated"},
            "content_sections": {"sections": "@details", "sort": "@order", "heading": "@name", "html": "@value"},
        },
        "extra": {"department": "@department", "experience_level": "@experience_level", "categories": "@categories", "location_is_remote": "@location.is_remote"},
    },
    "greenhouse": {
        "list": {"url": "https://boards-api.greenhouse.io/v1/boards/{key}/jobs?content=true", "items": "jobs", "id": "@id"},
        "envelope": {"meta": "@meta"},
        "fields": {
            "title": "@title", "url_posting": "@absolute_url", "apply_url": "@absolute_url", "company_raw": "@company_name",
            "location_raw": "@location.name", "description_html": {"unescape": "@content"}, "requisition_id": "@requisition_id",
            "published_at": {"date": "@first_published"}, "published_at_precision": "exact",
            "source_updated_at": {"date": "@updated_at"}, "deadline_at": {"date": "@application_deadline"},
            "locations_extra": {"map": "@offices", "value": "@name"},
        },
        "extra": {"internal_job_id": "@internal_job_id", "departments": "@departments", "metadata": "@metadata", "language": "@language"},
    },
    "lever": {
        "list": {"url": "https://api.lever.co/v0/postings/{key}?mode=json", "items": "", "id": "@id"},
        "fields": {
            "title": "@text", "url_posting": "@hostedUrl", "apply_url": "@applyUrl", "company_raw": "$key",
            "location_raw": "@categories.location", "description_html": {"fallback": ["@descriptionBody", "@description"]},
            "employment_type": "@categories.commitment", "workplace_type_raw": "@workplaceType",
            "published_at": {"epoch_ms": "@createdAt"}, "published_at_precision": "exact",
            "locations_extra": "@categories.allLocations",
            "content_sections": {"sections": "@lists", "heading": "@text", "html": "@content"},
        },
        "extra": {"department": "@categories.department", "team": "@categories.team", "country": "@country"},
    },
    "workable": {
        "list": {"url": "https://apply.workable.com/api/v1/widget/accounts/{key}?details=true", "items": "jobs", "id": "@shortcode"},
        "envelope": {"name": "@name", "description": "@description"},
        "fields": {
            "title": "@title", "url_posting": "@url", "apply_url": "@application_url",
            "company_raw": {"fallback": ["$envelope.name", "$key"]},
            "location_raw": {"join": ["@city", "@state", "@country"]}, "description_html": "@description",
            "requisition_id": "@code", "employment_type": "@employment_type", "published_at": {"date": "@published_on"},
            "published_at_precision": "date", "source_updated_at": {"date": "@created_at"},
            "locations_extra": {"map": "@locations", "value": {"fallback": ["@city", "@country"]}},
        },
        "extra": {"department": "@department", "telecommuting": "@telecommuting", "function": "@function", "industry": "@industry"},
    },
    "icims": {
        "list": {
            "url": "https://careers.{key}.com/api/jobs?page={cursor}", "items": "jobs", "unwrap": "data", "id": "@req_id",
            "page": {"start": 1, "size": 10, "step": 1},
        },
        "envelope": {"totalCount": "@totalCount"},
        "fields": {
            "title": "@title", "url_posting": {"fallback": ["@meta_data.canonical_url", "@apply_url"]}, "apply_url": "@apply_url",
            "company_raw": {"fallback": ["@hiring_organization", "$key"]},
            "location_raw": {"fallback": ["@short_location", {"join": ["@city", "@state"]}]},
            "description_html": "@description", "requisition_id": "@req_id", "employment_type": "@employment_type",
            "published_at": {"date": "@posted_date"}, "published_at_precision": "exact",
            "source_updated_at": {"date": "@update_date"},
            "locations_extra": {"map": "@additional_locations", "value": {"join": ["@city", "@state"]}},
        },
        "extra": {
            "department": "@department", "categories": "@categories", "location_type": "@location_type",
            "meta_data": "@meta_data", "slug": "@slug", "client_code": "@client_code",
            "tags1": "@tags1", "qualifications": "@qualifications", "responsibilities": "@responsibilities",
            "full_location": "@full_location",
        },
    },
}
COMPANY_JSON: dict[str, dict] = {
    "amazon": {
        "list": {
            "url": "https://www.amazon.jobs/en/search.json",
            "params": {
                "base_query": "",
                "offset": "$cursor",
                "result_limit": 100,
                "is_intern[]": ["1"],
                "schedule_type_id[]": [
                    "Flex-Time", "Part-Time", "Reduced-Time", "Flex-Time (<19 hours)",
                    "Part-Time (20-29 hours)", "Reduced-Time (30-39 hours)",
                    "Flex Time (<19 hours)", "Reduced Time (30-39 hours)",
                ],
                "employee_class[]": ["Seasonal/short-term"],
            },
            "items": "jobs",
            "id": "@id",
            "page": {"total": "@hits", "size": 100, "step": 100, "max": 200},
        },
        "fields": {
            "title": "@title",
            "url_posting": {"template": "https://www.amazon.jobs{job_path}"},
            "company_raw": "Amazon",
            "location_raw": "@normalized_location",
            "description_html": "@description",
            "requisition_id": "@id_icims",
            "published_at": {"date": "@posted_date"},
            "published_at_precision": "date",
        },
        "extra": {
            "company_name": "@company_name",
            "business_category": "@business_category",
            "basic_qualifications": "@basic_qualifications",
            "preferred_qualifications": "@preferred_qualifications",
        },
    },
    "apple": {
        "list": {
            "method": "POST",
            "url": "https://jobs.apple.com/api/v1/search",
            "body": {
                "query": "",
                "filters": {"locations": ["postLocation-USA"]},
                "page": "$cursor",
                "locale": "en-us",
                "sort": "newest",
                "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"},
            },
            "items": "res.searchResults",
            "id": "@positionId",
            "page": {"total": "@res.totalRecords", "start": 1, "size": 20, "step": 1, "max": 400},
        },
        "fields": {
            "title": "@postingTitle",
            "url_posting": {
                "template": "https://jobs.apple.com/en-us/details/{positionId}/{transformedPostingTitle}"
            },
            "company_raw": "Apple",
            "location_raw": {"join": ["@locations[0].city", "@locations[0].stateProvince", "@locations[0].countryName"]},
            "description_html": "@jobSummary",
            "published_at": {"date": "@postDateInGMT"},
            "published_at_precision": "exact",
        },
        "extra": {"team": "@team"},
    },
    "tesla": {
        "list": {"url": "https://www.tesla.com/cua-api/apps/careers/state", "items": "listings", "id": "@id"},
        "envelope": {"lookup": "@lookup"},
        "fields": {
            "title": "@t",
            "url_posting": {"template": "https://www.tesla.com/careers/search/job/{id}"},
            "company_raw": "Tesla",
            "location_raw": {"lookup": ["$envelope.lookup.locations", "@l"]},
            "employment_type": {"lookup": ["$envelope.lookup.types", "@type"]},
            "workplace_type_raw": {"lookup": ["$envelope.lookup.departments", "@d"]},
        },
        "extra": {"department": {"lookup": ["$envelope.lookup.departments", "@d"]}},
    },
    "uber": {
        "list": {
            "method": "POST",
            "url": "https://www.uber.com/api/loadSearchJobsResults",
            "body": {
                "limit": 50,
                "page": "$cursor",
                "params": {"location": [], "team": []},
                "query": "",
            },
            "items": "data.results",
            "id": "@id",
            "page": {"total": "@data.totalResults", "start": 0, "size": 50, "step": 1, "max": 50},
        },
        "fields": {
            "title": "@title",
            "url_posting": "@url",
            "company_raw": "Uber",
            "location_raw": {"join": ["@location.city", "@location.country"]},
            "description_html": "@description",
            "published_at": {"date": "@creationDate"},
            "published_at_precision": "exact",
        },
        "extra": {"department": "@department", "region": "@location.region"},
    },
}
DETAIL_A: dict[str, dict] = {
    "eightfold": {
        # Eightfold's PCS-X public board API uses a board domain as its
        # discriminator.  Keep the local key human-readable and supply that
        # public domain from the catalog entry.
        "list": {
            "url": "https://{host}/api/pcsx/search",
            "params": {
                "domain": "$domain",
                "query": "$query",
                "location": "",
                "start": "$cursor",
            },
            "items": "data.positions",
            "id": "@id",
            "page": {"total": "@data.count", "size": 10, "step": 10},
        },
        "fields": {
            "title": "@name",
            "url_posting": {"template": "https://{host}{positionUrl}"},
            "apply_url": {"template": "https://{host}{positionUrl}"},
            "company_raw": "$company_raw",
            "location_raw": {"first": "@locations"},
            "locations_extra": {"rest": "@locations"},
            "requisition_id": "@displayJobId",
            "workplace_type_raw": "@workLocationOption",
            "published_at": {"epoch_s": "@postedTs"},
            "published_at_precision": "exact",
        },
        "extra": {
            "department": "@department",
            "standardizedLocations": "@standardizedLocations",
            "locationFlexibility": "@locationFlexibility",
            "atsJobId": "@atsJobId",
        },
    },
    "bamboohr": {
        "list": {
            "url": "https://{key}.bamboohr.com/careers/list",
            "items": "result",
            "id": "@id",
        },
        "detail": {
            "url": "https://{key}.bamboohr.com/careers/{id}/detail",
            "url_override": False,
            "id": "@id",
            "payload": "result.jobOpening",
        },
        "fields": {
            "title": "@jobOpeningName",
            "url_posting": "@jobOpeningShareUrl",
            "company_raw": "$key",
            "location_raw": {"join": ["@location.city", "@location.state"]},
            "description_html": "@description",
            "employment_type": "@employmentStatusLabel",
            "published_at": {"date": "@datePosted"},
            "published_at_precision": "date",
            "source_active": {"eq": ["@jobOpeningStatus", "Open"]},
        },
        "extra": {
            "jobOpeningStatus": "@jobOpeningStatus",
            "jobCategoryId": "@jobCategoryId",
            "departmentId": "@departmentId",
            "departmentLabel": "@departmentLabel",
            "atsLocation": "@atsLocation",
            "compensation": "@compensation",
            "minimumExperience": "@minimumExperience",
            "locationType": "@locationType",
            "seekPromoted": "@seekPromoted",
            "isRemote": "@isRemote",
        },
    },
    "oracle": {
        # A supplied feed config host/site wins over this slash-separated key.
        "key_parts": ("host", "site"),
        "list": {
            "url": (
                "https://{host}.oraclecloud.com/hcmRestApi/resources/latest/"
                "recruitingCEJobRequisitions?onlyData=true&finder=findReqs;siteNumber={site},"
                "facetsList=LOCATIONS;WORK_LOCATIONS;WORKPLACE_TYPES;TITLES;CATEGORIES;"
                "ORGANIZATIONS;POSTING_DATES;FLEX_FIELDS,limit=25,sortBy=POSTING_DATES_DESC&"
                "expand=requisitionList"
            ),
            "items": "items.0.requisitionList",
            "id": "@Id",
            "url_override": False,
        },
        "detail": {
            "url": (
                "https://{host}.oraclecloud.com/hcmRestApi/resources/latest/"
                "recruitingCEJobRequisitionDetails?expand=all&onlyData=true&"
                "finder=ById;siteNumber={site},Id=\"{id}\""
            ),
            "id": "@Id",
            "url_override": False,
            "payload": "items.0",
            "merge": True,
        },
        "envelope": {"TotalJobsCount": "@items.0.TotalJobsCount"},
        "fields": {
            "title": "@Title",
            "url_posting": {
                "template": "https://{host}.oraclecloud.com/hcmUI/CandidateExperience/en/sites/{site}/job/{Id}/"
            },
            "company_raw": "$company_raw",
            "location_raw": "@PrimaryLocation",
            "description_html": "@ExternalDescriptionStr",
            "requisition_id": "@Id",
            "employment_type": {"fallback": ["@JobType", "@WorkerType"]},
            "workplace_type_raw": "@WorkplaceType",
            "published_at": {"fallback": [{"date": "@PostedDate"}, {"date": "@ExternalPostedStartDate"}]},
            "published_at_precision": "date",
            "locations_extra": {"map": "@secondaryLocations", "value": "@"},
        },
        "extra": {
            "BusinessUnit": "@BusinessUnit",
            "Organization": "@Organization",
            "Category": "@Category",
            "RequisitionType": "@RequisitionType",
            "RequisitionId": "@RequisitionId",
            "JobFamily": "@JobFamily",
            "JobFunction": "@JobFunction",
            "ExternalQualificationsStr": "@ExternalQualificationsStr",
            "requisitionFlexFields": "@requisitionFlexFields",
            "skills": "@skills",
        },
    },
    "rippling": {
        "list": {
            "url": "https://api.rippling.com/platform/api/ats/v1/board/{key}/jobs",
            "items": None,
            "id": {"fallback": ["@uuid", "@id"]},
        },
        "detail": {
            "url": "https://api.rippling.com/platform/api/ats/v1/board/{key}/jobs/{id}",
            "url_override": False,
            "id": {"fallback": ["@uuid", "@id"]},
            "dedupe": {"fallback": ["@uuid", "@id"]},
        },
        "fields": {
            "title": "@name",
            "url_posting": "@url",
            "company_raw": {"fallback": ["@companyName", "@board.title", "$key"]},
            "location_raw": {"fallback": [{"first": "@workLocations"}, {"first": "@locations"}, "@workLocation.label"]},
            "locations_extra": {"fallback": [{"rest": "@workLocations"}, {"rest": "@locations"}]},
            "description_html": {"fallback": [{"concat": ["@description.role", "@description.company"]}, "@description"]},
            "employment_type": "@employmentType.label",
            "published_at": {"date": "@createdOn"},
            "published_at_precision": "exact",
        },
        "extra": {
            "description_parts": "@description",
            "department": "@department",
            "employmentType": "@employmentType",
            "activeJobApplication": "@activeJobApplication",
            "payRangeDetails": "@payRangeDetails",
            "jsonLd": "@jsonLd",
            "board": "@board",
            "eeocQuestionnaireEnabled": "@eeocQuestionnaireEnabled",
            "eeocQuestionnaireEnabledForJobPost": "@eeocQuestionnaireEnabledForJobPost",
            "applicationConfirmationTemplate": "@applicationConfirmationTemplate",
            "hasAIEvaluationsEnabled": "@hasAIEvaluationsEnabled",
            "unlistedFromSearch": "@unlistedFromSearch",
            "language": "@language",
        },
    },
}
DETAIL_B: dict[str, dict] = {
    "smartrecruiters": {
        "list": {
            "url": "https://api.smartrecruiters.com/v1/companies/{key}/postings",
            "params": {"limit": 100, "offset": "$cursor"},
            "items": "content",
            "id": "@id",
            # Some boards return fewer than the requested limit.  Advancing by
            # the returned count avoids both gaps and duplicate summaries.
            "page": {"total": "@totalFound", "size": 100, "step": "count"},
        },
        "detail": {
            "url": "https://api.smartrecruiters.com/v1/companies/{key}/postings/{id}",
            "url_override": False,
            "id": "@id",
            "dedupe": "@id",
            "item_id": "@id",
        },
        "fields": {
            "title": "@name",
            "url_posting": "@postingUrl",
            "company_raw": "@company.name",
            "apply_url": "@applyUrl",
            "location_raw": "@location.fullLocation",
            "description_html": {
                "concat": [
                    "@jobAd.sections.jobDescription.text",
                    "@jobAd.sections.qualifications.text",
                    "@jobAd.sections.companyDescription.text",
                    "@jobAd.sections.additionalInformation.text",
                ],
                "sep": "\n",
            },
            "requisition_id": "@refNumber",
            "employment_type": "@typeOfEmployment.label",
            "published_at": {"date": "@releasedDate"},
            "published_at_precision": "exact",
            "source_active": "@active",
        },
        "extra": {
            "industry": "@industry",
            "function": "@function",
            "department": "@department",
            "experienceLevel": "@experienceLevel",
            "customField": "@customField",
            "visibility": "@visibility",
            "ref": "@ref",
            "language": "@language",
            "referralUrl": "@referralUrl",
        },
    },
    "workday": {
        # Key remains ``tenant/wd_host/site``, as used by the previous source.
        "key_parts": ("tenant", "wd_host", "site"),
        "list": {
            "method": "POST",
            "url": "https://{tenant}.{wd_host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
            "url_override": False,
            "body": {
                "appliedFacets": {},
                "limit": 20,
                "offset": "$cursor",
                "searchText": "",
            },
            "items": "jobPostings",
            "id": "@externalPath",
            "catch_first_error": True,
            "page": {"total": "@total", "size": 20, "step": 20},
        },
        "detail": {
            "url": "https://{tenant}.{wd_host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{id}",
            "url_override": False,
            "id": "@externalPath",
            "dedupe": "@externalPath",
            "item_id": {"fallback": ["@jobPostingInfo.jobReqId", "@jobPostingInfo.jobPostingId"]},
        },
        "last_status": True,
        "fields": {
            "title": "@jobPostingInfo.title",
            "url_posting": "@jobPostingInfo.externalUrl",
            "apply_url": "@jobPostingInfo.externalUrl",
            "company_raw": "@hiringOrganization.name",
            "location_raw": "@jobPostingInfo.location",
            "description_html": "@jobPostingInfo.jobDescription",
            "requisition_id": "@jobPostingInfo.jobReqId",
            "employment_type": "@jobPostingInfo.timeType",
            "published_at": {"date": "@jobPostingInfo.startDate"},
            "published_at_precision": "date",
        },
        "extra": {
            "country": "@jobPostingInfo.country",
            "jobRequisitionLocation": "@jobPostingInfo.jobRequisitionLocation",
            "canApply": "@jobPostingInfo.canApply",
            "posted": "@jobPostingInfo.posted",
            "questionnaireId": "@jobPostingInfo.questionnaireId",
            "similarJobs": "@similarJobs",
            "jobPostingSiteId": "@jobPostingInfo.jobPostingSiteId",
            "includeResumeParsing": "@jobPostingInfo.includeResumeParsing",
            "id": "@jobPostingInfo.id",
            "postedOn": "@jobPostingInfo.postedOn",
            "hiringOrganizationUrl": "@hiringOrganization.url",
            "userAuthenticated": "@userAuthenticated",
        },
    },
}

CONFIGS = {**SIMPLE, **COMPANY_JSON, **DETAIL_A, **DETAIL_B}
