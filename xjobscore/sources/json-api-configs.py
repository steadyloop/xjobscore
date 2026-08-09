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
    # One GET returns the whole board with descriptions and requirements inline
    # -- no pagination, no detail fetch. Verified against `transperfect`: 614
    # offers in a single 8.8 MB response, so set a cadence with that transfer in
    # mind rather than the 900s default.
    #
    # Every timestamp this board emits ("2026-08-06 15:57:36 UTC") is rejected by
    # `datetime.fromisoformat`, and the config vocabulary has no way to reshape a
    # string, so mapping one to `published_at` would raise inside `parse`. They
    # stay in `source_extra` verbatim; latency is not measurable from this source
    # until the engine can parse a format.
    "recruitee": {
        "list": {"url": "https://{key}.recruitee.com/api/offers/", "items": "offers", "id": "@id"},
        "fields": {
            "title": "@title", "url_posting": "@careers_url", "apply_url": "@careers_apply_url",
            "company_raw": "@company_name",
            "location_raw": {"fallback": ["@location", {"join": ["@city", "@state_name", "@country"]}]},
            "description_html": {"concat": ["@description", "@requirements"], "sep": "\n"},
            "requisition_id": "@guid", "employment_type": "@employment_type_code",
            "source_active": {"eq": ["@status", "published"]},
            "locations_extra": {"rest": {"map": "@locations", "value": "@name"}},
        },
        "extra": {
            "department": "@department", "category_code": "@category_code", "experience_code": "@experience_code",
            "education_code": "@education_code", "salary": "@salary", "tags": "@tags", "highlight": "@highlight",
            "remote": "@remote", "hybrid": "@hybrid", "on_site": "@on_site", "status": "@status",
            "created_at": "@created_at", "published_at": "@published_at", "updated_at": "@updated_at",
            "close_at": "@close_at",
        },
    },
    # Descriptions arrive split across four labelled blocks; concatenating them
    # keeps the whole ad in `description_html` while the headers stay in
    # `source_extra`. `deadline_at` is deliberately not mapped: it was null on
    # every posting across four tenants, so its format is unverified and a
    # non-ISO value would raise in `parse`.
    "pinpoint": {
        "list": {"url": "https://{key}.pinpointhq.com/postings.json", "items": "data", "id": "@id"},
        "fields": {
            "title": "@title", "url_posting": "@url", "company_raw": "$key",
            "location_raw": {"fallback": ["@location.name", {"join": ["@location.city", "@location.province"]}]},
            "description_html": {
                "concat": ["@description", "@key_responsibilities", "@skills_knowledge_expertise", "@benefits"],
                "sep": "\n",
            },
            "requisition_id": "@job.requisition_id", "employment_type": "@employment_type_text",
            "workplace_type_raw": "@workplace_type",
        },
        "extra": {
            "department": "@job.department.name", "division": "@job.division", "job": "@job",
            "compensation": "@compensation", "compensation_minimum": "@compensation_minimum",
            "compensation_maximum": "@compensation_maximum", "compensation_currency": "@compensation_currency",
            "workplace_type_text": "@workplace_type_text", "reporting_to": "@reporting_to",
            "location": "@location", "deadline_at": "@deadline_at", "path": "@path",
            "key_responsibilities_header": "@key_responsibilities_header",
            "skills_knowledge_expertise_header": "@skills_knowledge_expertise_header",
            "benefits_header": "@benefits_header",
        },
    },
    # A DRF paginator: `count` is the board total and `page` is a 1-based integer,
    # so the engine's own cursor drives it. `organization_name` is the department,
    # not the employer -- the employer is the board slug.
    "manatal": {
        "list": {
            "url": "https://www.careers-page.com/api/v1.0/c/{key}/jobs/",
            "params": {"page": "$cursor", "page_size": 50},
            "items": "results", "id": "@id",
            "page": {"total": "@count", "start": 1, "size": 50, "step": 1},
        },
        "fields": {
            "title": "@position_name",
            "url_posting": {"template": "https://www.careers-page.com/{key}/job/{hash}"},
            "company_raw": "$key",
            "location_raw": {"fallback": ["@location_display", {"join": ["@city", "@state", "@country"]}]},
            "description_html": "@description", "requisition_id": "@hash",
        },
        "extra": {
            "organization_name": "@organization_name", "address": "@address", "city": "@city",
            "state": "@state", "country": "@country", "zipcode": "@zipcode",
            "is_salary_visible": "@is_salary_visible", "is_pinned_in_career_page": "@is_pinned_in_career_page",
        },
    },
    # A platform-wide opportunity search, not a per-employer board: one feed
    # covers every listing, so `key` names the feed rather than a tenant and the
    # employer comes off each row. `approved_date` ("… GMT+0530") is not
    # ISO-parseable; `updated_at` and the registration window are, so those carry
    # the timestamps.
    #
    # `total` reports a flat 10,000 over exactly 200 pages on every query -- an
    # API ceiling, not the corpus size. Results come back newest-first (page 1 is
    # today, page 200 is roughly eight months back), so capping at 20 pages keeps
    # the newest 1,000 and still overlaps several days of postings between polls.
    # Without the cap a single poll walks all 200 pages.
    "unstop": {
        "list": {
            "url": "https://unstop.com/api/public/opportunity/search-result",
            "params": {"opportunity": "internships", "page": "$cursor", "per_page": 50},
            "items": "data.data", "id": "@id",
            "page": {"total": "@data.total", "start": 1, "size": 50, "step": 1, "max": 20},
        },
        "fields": {
            "title": "@title", "url_posting": "@seo_url", "apply_url": "@short_url",
            "company_raw": "@organisation.name",
            "location_raw": {"first": {"map": "@locations", "value": "@city"}},
            "locations_extra": {"rest": {"map": "@locations", "value": "@city"}},
            "description_html": "@details", "employment_type": "@subtype",
            "workplace_type_raw": "@region",
            "published_at": {"date": "@regnRequirements.start_regn_dt"}, "published_at_precision": "exact",
            "deadline_at": {"date": "@end_date"}, "source_updated_at": {"date": "@updated_at"},
            "source_active": {"eq": ["@status", "LIVE"]},
        },
        "extra": {
            "short_id": "@short_id", "public_url": "@public_url", "type": "@type", "subtype": "@subtype",
            "organisation": "@organisation", "organization_id": "@organization_id", "jobDetail": "@jobDetail",
            "required_skills": "@required_skills", "workfunction": "@workfunction", "filters": "@filters",
            "regnRequirements": "@regnRequirements", "approved_date": "@approved_date",
            "registerCount": "@registerCount", "viewsCount": "@viewsCount", "isPaid": "@isPaid",
        },
    },
    # An unauthenticated Elasticsearch proxy in front of the whole TalentReef
    # estate -- one feed, ~196k postings across every client, so there is no
    # tenant in the key. `_source` is the document, hence `unwrap`.
    #
    # Two deliberate choices:
    #   `url_posting` is built from clientId+jobId rather than read from `url`.
    #     `url` is empty on about a third of documents and relative on the rest;
    #     the canonical form apply.jobappnetwork.com/clients/{c}/posting/{j}/en is
    #     what those relative paths redirect to, and it is derivable for every row.
    #   `max` caps the sweep at the newest 5,000. No result-window limit was
    #     observed (from=10001 still answers), so the cap is a cost decision, not
    #     a server one: an exhaustive pass is ~1,970 requests. The engine reports
    #     `truncated` when it bites, so the shortfall is never silent.
    "talentreef": {
        "list": {
            "method": "POST",
            "url": "https://prod-kong.internal.talentreef.com/apply/proxy-es/search-en-us/posting/_search",
            "body": {
                "size": 100,
                "from": "$cursor",
                "query": {"match_all": {}},
                "sort": [{"createdDate": "desc"}],
            },
            "items": "hits.hits", "unwrap": "_source", "id": "@jobId",
            "page": {"total": "@hits.total", "size": 100, "step": 100, "max": 50},
        },
        "fields": {
            "title": "@title",
            "url_posting": {"template": "https://apply.jobappnetwork.com/clients/{clientId}/posting/{jobId}/en"},
            "company_raw": {"fallback": ["@clientName", "@brand"]},
            "location_raw": {"join": ["@address.city", "@address.stateOrProvince"]},
            "description_html": "@description",
            # positionId is a JSON number; requisition_id is typed str.
            "requisition_id": {"template": "{positionId}"},
            "employment_type": "@contractType",
            "published_at": {"date": "@createdDate"}, "published_at_precision": "date",
        },
        "extra": {
            "brand": "@brand", "brandId": "@brandId", "category": "@category", "department": "@department",
            "positionType": "@positionType", "position": "@position", "client": "@client",
            "clientId": "@clientId", "location": "@location", "address": "@address", "geo": "@geo",
            "shifts": "@shifts", "isSalaried": "@isSalaried", "internalOrExternal": "@internalOrExternal",
            "startDate": "@startDate", "endDate": "@endDate", "url": "@url",
        },
    },
    # A board is addressed by a host and two opaque values, so the key carries all
    # three: "recruiting.ultipro.com/COM1060COMOI/5cbca35a-adf1-993f-4fc9-41ad8639e2cb".
    # The host is not decoration -- tenants are sharded across recruiting,
    # recruiting2, ... and a board answers on exactly one of them (ARU1000ARUP is
    # 200 on recruiting2 and 404 on recruiting), so it cannot be hardcoded.
    #
    # The search POST needs no headers at all -- an empty body already answers 200
    # -- and `Skip` is a plain integer, so the engine's cursor drives pagination
    # directly.
    #
    # `description_html` is only the teaser. There is no JSON detail endpoint to
    # fan out to: the full body (~19 kB where BriefDescription gives 450 chars)
    # exists solely inside the Knockout page at OpportunityDetail, and five
    # plausible JSON paths -- LoadOpportunity, LoadOpportunityDetail,
    # GetOpportunityDetails, LoadOpportunities, Opportunity/{id} -- all 404.
    # Full descriptions need an HTML reader, which this engine is not; treat this
    # config as complete for discovery and truncated for content.
    #
    # Employment fields are all enums on the wire (`FullTime` boolean,
    # `OpportunityType`/`JobLocationType` integers) with no published mapping, so
    # they stay in `source_extra` rather than being guessed into
    # `employment_type`.
    # Bullhorn's public job-board REST is `/query/JobBoardPost` -- the more
    # obvious `/search/JobBoardPost` does not exist (400 "Unknown search
    # entity"). A corpToken answers on exactly one numbered cluster, so the
    # host is part of the key, same shape as UKG. `showTotalMatched=true` is
    # required or `total` is simply absent from the response and the engine
    # cannot tell a short page from the end. Descriptions and dates are
    # inline on every row (verified 773/773 on `44/CJAT1S`) -- no detail
    # fetch. Every date field is epoch *milliseconds*, never ISO.
    #
    # Bullhorn silently drops unrecognized `fields` names (HTTP 200, key just
    # absent) -- the field list below is the verified-good set; do not extend
    # it without re-probing live. The payload never names an employer or a
    # canonical posting URL, so both come from `feed.config`.
    "bullhorn": {
        "key_parts": ("swimlane", "corpToken"),
        "list": {
            "url": "https://public-rest{swimlane}.bullhornstaffing.com/rest-services/{corpToken}/query/JobBoardPost",
            "url_override": False,
            "params": {
                "fields": (
                    "id,title,publishedCategory(id,name),address(address1,city,state,zip,countryID),"
                    "employmentType,dateAdded,dateLastPublished,startDate,dateEnd,publicDescription,"
                    "isOpen,isPublic,isDeleted,salary,salaryUnit,payRate,benefits,yearsRequired,"
                    "travelRequirements,willRelocate"
                ),
                "where": "isOpen=true", "start": "$cursor", "count": 100,
                "orderBy": "-dateLastPublished", "showTotalMatched": "true",
            },
            "items": "data", "id": "@id",
            "page": {"start": 0, "size": 100, "step": 100, "total": "@total", "max": 50},
        },
        "fields": {
            "title": "@title",
            "url_posting": {"template": "{board_url}{id}"},
            "company_raw": "$company_raw",
            "location_raw": {"join": ["@address.city", "@address.state"]},
            "description_html": "@publicDescription",
            "employment_type": "@employmentType",
            "published_at": {"epoch_ms": "@dateLastPublished"}, "published_at_precision": "exact",
            "source_updated_at": {"epoch_ms": "@dateLastPublished"},
            "source_active": "@isOpen",
        },
        "extra": {
            "category": "@publishedCategory.name", "salary": "@salary", "salaryUnit": "@salaryUnit",
            "payRate": "@payRate", "dateAdded": "@dateAdded", "yearsRequired": "@yearsRequired",
            "travelRequirements": "@travelRequirements", "benefits": "@benefits", "startDate": "@startDate",
            "dateEnd": "@dateEnd", "willRelocate": "@willRelocate",
        },
    },
    # A single unauthenticated POST returns the whole board inline -- no
    # pagination (verified: a 2,166-job tenant in one response). Two
    # corrections to the endpoint as commonly documented: `batch=true` must be
    # dropped, or the response carries settings and the account object but no
    # `jobs` array at all; and `Origin`/`Referer` are mandatory, or a
    # recognized account still answers 200 with a soft
    # "You are not allowed to access this URL" failure that looks like a bad
    # slug rather than a missing header.
    #
    # There is no per-job date field anywhere in this schema (list or detail),
    # so `published_at` is permanently unmappable for this source, not a
    # config gap. Descriptions (`jdtext`) and location are populated on some
    # rows and empty strings on others -- `fallback` turns the empty string
    # into `None` rather than a blank field.
    "recruitcrm": {
        "list": {
            "method": "POST",
            "url": "https://albatross.recruitcrm.io/v1/external-pages/jobs-by-account/get?account={key}",
            "url_override": False,
            "headers": {"Origin": "https://careers.recruitcrm.io", "Referer": "https://careers.recruitcrm.io/"},
            "items": "data.jobs", "id": "@slug",
        },
        "envelope": {"account": "@data.account"},
        "fields": {
            "title": "@name",
            "url_posting": {"template": "https://app.recruitcrm.io/jobs/{key}/{slug}"},
            "company_raw": {"fallback": ["@companyname", "$envelope.account.title"]},
            "location_raw": {"fallback": [{"join": ["@city", "@locality"]}]},
            "description_html": {"fallback": ["@jdtext"]},
            "requisition_id": {"fallback": ["@jobcode"]},
        },
        "extra": {"srno": "@srno", "accountpage": "$envelope.account.accountpage"},
    },
    "ukg": {
        "key_parts": ("host", "companyCode", "boardGuid"),
        "list": {
            "method": "POST",
            "url": (
                "https://{host}/{companyCode}/JobBoard/{boardGuid}"
                "/JobBoardView/LoadSearchResults"
            ),
            "url_override": False,
            "body": {
                "opportunitySearch": {
                    "Top": 50,
                    "Skip": "$cursor",
                    "QueryString": "",
                    "OrderBy": [
                        {"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False},
                    ],
                    "Filters": [],
                },
                "matchCriteria": {
                    "PreferredJobs": [],
                    "Educations": [],
                    "LicenseAndCertifications": [],
                    "Skills": [],
                    "hasNoLicenses": False,
                    "SkippedSkills": [],
                },
            },
            "items": "opportunities",
            "id": "@Id",
            # Boards are usually well under this; the cap is a runaway guard.
            "page": {"total": "@totalCount", "size": 50, "step": 50, "max": 40},
        },
        "fields": {
            "title": "@Title",
            "url_posting": {
                "template": (
                    "https://{host}/{companyCode}/JobBoard/{boardGuid}"
                    "/OpportunityDetail?opportunityId={Id}"
                )
            },
            "company_raw": "$company_raw",
            "location_raw": {
                "join": ["@Locations[0].Address.City", "@Locations[0].Address.State.Code"]
            },
            "locations_extra": {
                "rest": {
                    "map": "@Locations",
                    "value": {"join": ["@Address.City", "@Address.State.Code"]},
                }
            },
            "description_html": "@BriefDescription",
            "requisition_id": "@RequisitionNumber",
            "published_at": {"date": "@PostedDate"},
            "published_at_precision": "exact",
        },
        "extra": {
            "JobCategoryName": "@JobCategoryName", "FullTime": "@FullTime",
            "OpportunityType": "@OpportunityType", "JobLocationType": "@JobLocationType",
            "Featured": "@Featured", "Locations": "@Locations", "MatchScore": "@MatchScore",
        },
    },
    # USAJOBS Search API -- the official federal system of record, one
    # platform-wide feed (no tenant in the key, same shape as unstop/bullhorn).
    # Unauthenticated GET returns 401; needs a free Authorization-Key (minted
    # against one registered email) plus a User-Agent set to that same email
    # -- see catalogs/usajobs.toml for why that pair must never be committed.
    #
    # JobCategoryCode is the highest-value fetch-side filter in this tree: it
    # turns an all-department federal board into an IT/CS/Math-only fetch at
    # the source. 2210=Information Technology Management, 1550=Computer
    # Science, 1520=Mathematics. Per the API reference
    # (developer.usajobs.gov/api-reference/get-api-search), multiple values
    # are semicolon-delimited in one string, not a repeated key or JSON list
    # -- confirmed against the live docs page, not guessed.
    #
    # Path is SearchResult.SearchResultItems[].MatchedObjectDescriptor, total
    # is SearchResult.SearchResultCountAll (confirmed against a real archived
    # 200 response, fixtures/usajobs.json). Fields=Full returns descriptions
    # inline, so no detail fetch. MajorDuties is a list[str], not a scalar --
    # {"concat": ..., "sep": "\n"} joins it; PositionFormattedDescription is
    # deliberately not used, since on this response shape it carries no
    # Content key. apply_url/employment_type are left unmapped rather than
    # guessed: no verified field path for either turned up in the captured
    # response.
    "usajobs": {
        "list": {
            "url": "https://data.usajobs.gov/api/search",
            "params": {
                "JobCategoryCode": "2210;1550;1520",
                "ResultsPerPage": 500,
                "Page": "$cursor",
                "Fields": "Full",
            },
            "headers": {"Authorization-Key": "$api_key", "User-Agent": "$ua_email"},
            "items": "SearchResult.SearchResultItems", "id": "@MatchedObjectId",
            "page": {
                "start": 1, "size": 500, "step": 1,
                "total": "@SearchResult.SearchResultCountAll",
                "max": 20,  # 10,000-row API ceiling at 500/page
            },
        },
        "fields": {
            "title": "@MatchedObjectDescriptor.PositionTitle",
            "url_posting": "@MatchedObjectDescriptor.PositionURI",
            "company_raw": {"fallback": [
                "@MatchedObjectDescriptor.OrganizationName",
                "@MatchedObjectDescriptor.DepartmentName",
            ]},
            "location_raw": "@MatchedObjectDescriptor.PositionLocationDisplay",
            "description_html": {"concat": "@MatchedObjectDescriptor.UserArea.Details.MajorDuties", "sep": "\n"},
            "requisition_id": "@MatchedObjectDescriptor.PositionID",
            "published_at": {"date": "@MatchedObjectDescriptor.PositionStartDate"},
            "published_at_precision": "exact",
            "deadline_at": {"date": "@MatchedObjectDescriptor.PositionEndDate"},
        },
        "extra": {
            "hiring_path": "@MatchedObjectDescriptor.UserArea.Details.HiringPath",
            "job_summary": "@MatchedObjectDescriptor.UserArea.Details.JobSummary",
            "qualification_summary": "@MatchedObjectDescriptor.QualificationSummary",
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
    # The list operation (`JobBoardList`) is not in the entry bundle the board
    # page loads -- it sits in a lazily-loaded chunk the entry bundle only
    # names (`ExternalJobBoardList.*.min.js`). `$boardId` is the vanity path
    # (the catalog key), not the tracking UUID embedded in the page HTML.
    # The batch endpoint takes an array body and returns an array; `items`
    # digs through the first (only) operation's result with a numeric index,
    # same as `_dig` treats any other list.
    #
    # The list carries no description, so a detail fetch reuses the board's
    # own `ExternalJobPostingQuery`, posted to the same URL. `{id}` in
    # `url_posting` resolves against the payload (the base64 GraphQL node id)
    # rather than context, because `_format` checks context first and there is
    # no `id` there for `url_posting`. Nothing in either response names the
    # employer.
    "gem": {
        "list": {
            "method": "POST",
            "url": "https://jobs.gem.com/api/public/graphql/batch",
            "url_override": False,
            "body": [{
                "operationName": "JobBoardList",
                "query": (
                    "query JobBoardList($boardId: String!) { "
                    "oatsExternalJobPostings(boardId: $boardId) { jobPostings { "
                    "id extId title "
                    "locations { id name city isoCountry isRemote extId } "
                    "job { id department { id name extId } locationType employmentType } } } }"
                ),
                "variables": {"boardId": "$key"},
            }],
            "items": "0.data.oatsExternalJobPostings.jobPostings", "id": "@extId",
        },
        "detail": {
            "method": "POST",
            "url": "https://jobs.gem.com/api/public/graphql/batch",
            "url_override": False,
            "body": [{
                "operationName": "ExternalJobPostingQuery",
                "query": (
                    "query ExternalJobPostingQuery($boardId: String!, $extId: String!) { "
                    "oatsExternalJobPosting(boardId: $boardId, extId: $extId) { "
                    "id title descriptionHtml extId startDateTs firstPublishedTsSec "
                    "companyUrl isUnlistedExternally "
                    "locations { id extId name city isoCountry isRemote } "
                    "job { id locationType employmentType requisitionId teamDisplayName "
                    "department { id extId name } } } }"
                ),
                "variables": {"boardId": "$key", "extId": "$id"},
            }],
            "id": "@extId", "payload": "0.data.oatsExternalJobPosting", "item_id": "@extId",
        },
        "fields": {
            "title": "@title",
            "url_posting": {"template": "https://jobs.gem.com/{key}/{id}"},
            "company_raw": "$company_raw",
            "description_html": "@descriptionHtml",
            "location_raw": {"first": {"map": "@locations", "value": "@name"}},
            "locations_extra": {"rest": {"map": "@locations", "value": "@name"}},
            "employment_type": "@job.employmentType",
            "workplace_type_raw": "@job.locationType",
            "requisition_id": "@job.requisitionId",
            "published_at": {"epoch_s": "@firstPublishedTsSec"},
            "published_at_precision": "exact",
        },
        "extra": {
            "department": "@job.department.name", "team": "@job.teamDisplayName",
            "node_id": "@id", "start_date_ts": "@startDateTs",
            "is_unlisted_externally": "@isUnlistedExternally",
        },
    },
    # `$top`/`$skip` are OData-style but not OData: `$top` is hard-capped at
    # 20 regardless of what is requested, and `$skip` is 1-based -- `$skip=20`
    # returns rows 20-39, one row of overlap with page 1. Paging from 1 by 20
    # (verified against a 50-requisition board: 3 pages, 50 unique ids) is
    # what avoids the overlap.
    #
    # `ccId` is optional (a bogus value returns 200 with an empty array, not
    # an error) and is kept only because every observed tenant emits it.
    # Descriptions are not in the list; the detail response is the bare
    # object (no wrapper), a superset of the list row, so no `merge` is
    # needed. Use `postDate`, not `customFieldGroup.dateFields[].dateValue`
    # ("2026-08-03T15:37Z", second-less) -- the payload never names the
    # employer.
    "adp": {
        "key_parts": ("cid", "ccId"),
        "list": {
            "url": (
                "https://workforcenow.adp.com/mascsr/default/careercenter/public/"
                "events/staffing/v1/job-requisitions"
            ),
            "url_override": False,
            "params": {"cid": "$cid", "ccId": "$ccId", "$top": 20, "$skip": "$cursor"},
            "items": "jobRequisitions", "id": "@itemID",
            "page": {"start": 1, "size": 20, "step": 20, "total": "@meta.totalNumber", "max": 50},
        },
        "detail": {
            "url": (
                "https://workforcenow.adp.com/mascsr/default/careercenter/public/"
                "events/staffing/v1/job-requisitions/{id}?cid={cid}&ccId={ccId}"
            ),
            "url_override": False,
            "id": "@itemID", "item_id": "@itemID",
        },
        "fields": {
            "title": "@requisitionTitle",
            "url_posting": {
                "template": (
                    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
                    "recruitment.html?cid={cid}&ccId={ccId}&jobId={itemID}&lang=en_US"
                )
            },
            "company_raw": "$company_raw",
            "location_raw": {"fallback": [
                "@requisitionLocations.0.nameCode.shortName",
                {"join": ["@requisitionLocations.0.address.cityName",
                          "@requisitionLocations.0.address.countrySubdivisionLevel1.codeValue"]},
            ]},
            "description_html": "@requisitionDescription",
            "requisition_id": "@clientRequisitionID",
            "employment_type": "@workLevelCode.shortName",
            "published_at": {"date": "@postDate"}, "published_at_precision": "exact",
            "locations_extra": {"rest": {"map": "@requisitionLocations", "value": "@nameCode.shortName"}},
        },
        "extra": {"itemID": "@itemID", "workLevel": "@workLevelCode.shortName"},
    },
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
