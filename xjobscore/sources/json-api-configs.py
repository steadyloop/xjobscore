"""Plain configuration for the shared JSON ATS adapter.

Expression values: ``@path`` reads an item/response path; ``$name`` reads
engine context (``$key``, ``$id``, ``$cursor``, ``$envelope.*``).  Everything
else is a literal.  Keep a configuration declarative; only add engine support
when a pattern is shared by several vendors.
"""

SIMPLE: dict[str, dict] = {
    "ashby": {
        # concurrency: single shared host (api.ashbyhq.com) across all tenants,
        # so this per-host semaphore is the vendor-wide cap too -- no separate
        # _VENDOR_CAPS entry needed the way multi-hostname vendors (Workday,
        # Eightfold, Oracle) require one. 44 per doc/networking/rate-limits.md:
        # burst sweeps of the full 700-board catalog up to width 700 drew zero
        # throttle responses; the ceiling itself was never found, so this is a
        # chosen value, not a measured one.
        "list": {"url": "https://api.ashbyhq.com/posting-api/job-board/{key}?includeCompensation=true", "items": "jobs", "id": "@id", "concurrency": 44},
        "envelope": {"apiVersion": "@apiVersion"},
        "fields": {
            "title": "@title", "url_posting": "@jobUrl", "apply_url": "@applyUrl", "company_raw": "$key",
            "location_raw": "@location", "description_html": "@descriptionHtml", "employment_type": "@employmentType",
            "location_normalization_raw": {"join": [
                "@location",
                "@address.postalAddress.addressLocality",
                "@address.postalAddress.addressCountry",
            ]},
            "location_normalization_extra": {
                "map": "@secondaryLocations",
                "value": {"join": [
                    "@location",
                    "@address.postalAddress.addressLocality",
                    "@address.postalAddress.addressCountry",
                ]},
            },
            "workplace_type_raw": "@workplaceType", "published_at": {"date": "@publishedAt"},
            "published_at_precision": "exact", "source_active": "@isListed",
            "locations_extra": {"map": "@secondaryLocations", "value": "@location"},
            # DERIVED -- every pattern and count below is measured on Ashby's own
            # corpus (doc/raw_postings/ashby.md, 18,715 rows / 696 boards) and is
            # not portable. The `classify` form is shared across vendors; its
            # contents never are.
            "job_type": {"classify": [
                # Co-op FIRST, ahead of `employmentType` -- 4 of the 7 co-op
                # titles are typed `Intern` (md:82), so the enum rule below would
                # consume them. Both boundaries load-bearing: dropping them adds
                # 4 "Cooperative"/"Cooperation" titles (md:80-81).
                {"from": "@title", "re": "co-?ops?", "is": "coop"},  # 7, md:77
                # The enum is a closed 5-value set with no false positives on
                # `Intern` (md:52), so it outranks the title regex. Research is
                # a subject tag, not a mutually exclusive opportunity type:
                # the current Gold audit found every Ashby `research` row was
                # explicitly an internship, including seven that welcome
                # undergraduates. They therefore stay visible as `intern`.
                {"from": "@employmentType", "re": "intern", "is": "intern"},  # 145, md:48
                # `intern(s|ship|ships)?`, not `interns?`: the shorter form fails
                # the trailing boundary on "internships". Boundaries reject the
                # 66 Internal/International titles (md:34-36).
                {"from": "@title", "re": "intern(s|ship|ships)?", "is": "intern"},  # 276, md:27
                # Student titles begin after the posting-level internship
                # signals (`employmentType` and title), but before department /
                # team. That distinction matters on current Statista rows whose
                # exact role is Working Student while the deliberately mixed
                # bucket says "Working students / Interns / Trainees". Of the
                # 10 survey Working Student/Werkstudent titles, 3 are explicit
                # internships and resolve above; the other 7 are enrolled-
                # student jobs (md:Student Programs). `werkstudent` keeps its
                # inflected tail because WerkstudentIn / Werkstudent/in are 3
                # of the 4 German hits.
                {"from": "@title",
                 "re": "working students?|werkstudent[a-z]*",
                 "is": "student_program"},  # 7 net / 10 gross, md:Student Programs
                # `new grad` in the survey count also includes the four spelled
                # "new graduate". College/university-grad and named graduate
                # programs are the same undergraduate recruiting channel. An
                # explicit PhD title is outside this bucket and terminates before
                # any generic title or department label can claim it.
                {"from": "@title", "re": "ph\\.?d\\.?", "is": None},
                {"from": "@title",
                 "re": "new grad(uate)?s?|new college grad(uate)?s?"
                       "|university grad(uate)?s?|graduate programs?",
                 "is": "student_program"},
                # Unlike bare `campus` (which catches recruiters and physical
                # sites), this title phrase is the student-held role itself.
                # It was absent from the 2026-08-19 survey and validated on the
                # two live Ashby rows added by 2026-08-21.
                {"from": "@title", "re": "campus( growth)? ambassadors?",
                 "is": "student_program"},
                # department and team are equal on 62% of rows (md:61); querying
                # both covers the 38% where they differ. These broader grouping
                # labels sit below role-specific title/enum evidence. Boundaries
                # reject "Internal Ops" (13) and "Internal IT" (md:59).
                {"from": "@department", "re": "intern(s|ship|ships)?", "is": "intern"},  # 17 + 12 + 4, md:58
                {"from": "@team", "re": "intern(s|ship|ships)?", "is": "intern"},  # 8 + 4, md:63
                {"from": "@department",
                 "re": "early career|new grads?|campus recruit(ing|ment)",
                 "is": "student_program"},  # 9 + 6 + 4, md:58
                {"from": "@team",
                 "re": "early career|new grads?|campus recruit(ing|ment)",
                 "is": "student_program"},  # 10 + 6 + 4, md:63
                # One residency explicitly welcomes "strong undergrads"; the
                # exact phrase is safe where broad description phrases such as
                # "currently enrolled" are not. A second exact phrase covers
                # the live Bachelor's-level Student Ambassador without claiming
                # the law-school ambassador rows (md:Student Programs).
                {"from": "@descriptionPlain",
                 "re": "strong undergrads|currently enrolled at a university[ -]+bachelors? level",
                 "is": "student_program"},
                # LAST: part-time. The enum decides it -- "PartTime" lowercases to
                # "parttime", which `part.?time` matches with the separator absent.
                {"from": "@employmentType", "re": "part.?time", "is": "part_time"},  # 29, md:93
                # TERMINAL: the title phrase disagrees with the enum on more than
                # half its rows -- "[CONTRACT] Part-Time Recruiting Coordinator"
                # is typed Contract (md:100). Trust the enum, so a Contract or
                # Temporary row stops here rather than reaching the title rule.
                {"from": "@employmentType", "re": "contract|temporary", "is": None},  # 239, md:94
                # Which leaves the title as a tie-breaker for the 4 FullTime rows
                # only, exactly as md:101 prescribes.
                {"from": "@title", "re": "part.?time", "is": "part_time"},  # 4, md:98-101
                # `research` has no Ashby signal, and hourly `compensation`
                # interval is explicitly NOT a part-time signal (md:103-106), so
                # neither gets a rule.
            ]},
            "remote": {"classify": [
                # The closed 3-value enum is the field (md:114). `isRemote` is
                # derived from it and lossy -- it counts Hybrid as remote, 53% of
                # its true rows (md:121-125) -- so it stays in `extra`, unread.
                # OnSite terminates at unknown rather than being a `not` clause:
                # a structured on-site statement must also suppress the location
                # rules below, which contradict it on 15 rows (md:133).
                {"from": "@workplaceType", "re": "onsite", "is": "unknown"},  # 7,413, md:115
                {"from": "@workplaceType", "re": "hybrid", "is": "hybrid"},  # 4,464, md:116
                {"from": "@workplaceType", "re": "remote", "is": "yes"},  # 3,919, md:117
                # The enum is missing on 2,919 rows, and 231 of the 246 affected
                # boards are mixed, so the gap is per-posting (md:118-119). The
                # payoff is the 261 remote-in-location rows with no enum at all
                # (md:135). Hybrid first, as on every other vendor.
                {"from": "@location", "re": "hybrid", "is": "hybrid"},  # 300, md:130
                # "work from home", "home-based" and "distributed" are all 0 here
                # -- Greenhouse's pattern list is deliberately not ported (md:131).
                {"from": "@location", "re": "remote|anywhere|virtual", "is": "yes"},  # 2,148 + 5 + 2, md:127-130
                # 359 rows pair a non-remote primary location with a remote
                # secondary; a single-site read calls all of them onsite (md:138-144).
                # 245 already resolved above via the enum, so this adds ~114.
                {"from": {"concat": {"map": "@secondaryLocations", "value": "@location"}, "sep": " ; "},
                 "re": "hybrid", "is": "hybrid"},
                {"from": {"concat": {"map": "@secondaryLocations", "value": "@location"}, "sep": " ; "},
                 "re": "remote|anywhere|virtual", "is": "yes"},  # 359 gross, md:139
                # No polarity trap: 0 rows say non-remote / not remote (md:132).
            ], "default": "unknown"},
        },
        "extra": {"department": "@department", "team": "@team", "compensation": "@compensation", "isRemote": "@isRemote"},
    },
    "breezy": {
        "list": {"url": "https://{key}.breezy.hr/json?verbose=true", "items": "", "id": "@id"},
        "fields": {
            "title": "@name", "url_posting": "@url", "company_raw": "@company.name", "location_raw": "@location.name",
            "description_html": "@description", "employment_type": "@type.name", "published_at": {"date": "@published_date"},
            "published_at_precision": "exact", "locations_extra": {"map": "@locations", "value": "@name"},
            "workplace_type_raw": {"lookup": [{"True": "remote"}, "@location.is_remote"]},
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
        # Reconcile-only, never fetched during a poll: the list already carries
        # full content, so there is no detail leg to reuse. Greenhouse publishes
        # no closed flag and no tombstone -- a delisted job simply stops being
        # returned -- but this endpoint answers 404 {"error":"Job not found"}
        # for one and 200 with the full payload for a live one, which is the
        # only closure evidence the vendor gives us. Measured 2026-08-20.
        "probe": {"url": "https://boards-api.greenhouse.io/v1/boards/{key}/jobs/{id}"},
        "envelope": {"meta": "@meta"},
        "fields": {
            "title": "@title", "url_posting": "@absolute_url", "apply_url": "@absolute_url", "company_raw": "@company_name",
            "location_raw": "@location.name", "description_html": {"unescape": "@content"}, "requisition_id": "@requisition_id",
            "published_at": {"date": "@first_published"}, "published_at_precision": "exact",
            "source_updated_at": {"date": "@updated_at"}, "deadline_at": {"date": "@application_deadline"},
            "locations_extra": {"map": "@offices", "value": "@name"},
            # Normalize offices independently from location.name. Unioning the
            # resulting tuples is monotonic; concatenating the strings first
            # lets an unrelated office city suppress the posting's primary
            # country/state signal in the normalizer's global city ranking.
            "location_normalization_extra": {"map": "@offices", "value": "@name"},
            # DERIVED -- measured on Greenhouse's own corpus
            # (doc/raw_postings/greenhouse.md, 32,753 rows / 165 boards).
            #
            # `metadata` is a tenant-defined [{name, value, value_type}] bag, so
            # one entry is selected by predicate rather than by path. The three
            # entries read below are the only ones the doc measures a signal on.
            "job_type": {"classify": [
                # Eligibility / relevance exclusions that remain exclusions even
                # if a future title also contains Intern or Co-op.
                {"from": "@title", "re": "skillbridge|campus ambassadors?", "is": None},
                # Co-op FIRST -- co-op titles are a subset of the intern-ish set.
                # Boundaries are load-bearing: dropping the trailing one admits
                # "Software Engineer, Scoop Core", since Scoop contains coop (md:67).
                {"from": "@title", "re": "co-?ops?", "is": "coop"},  # 5, md:65
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "co-?ops?", "is": "coop"},  # 1 "Winter Co-Op", md:72
                # `intern(s|ship|ships)?`, not the doc's `intern|interns|internship`:
                # the trailing boundary rejects the plural of the last alias.
                # Boundaries reject 261 Internal/International titles (md:24-26).
                # Research describes the subject, not a separate opportunity
                # type: all 64 active Greenhouse research rows explicitly carry
                # this internship title signal (md:Student Programs).
                {"from": "@title", "re": "intern(s|ship|ships)?", "is": "intern"},  # 456, md:20
                # Substring, not equality -- tenant-prefixed values exist
                # ("Jump Trading - Intern", 60 rows) and classify searches (md:39).
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "intern(s|ship|ships)?", "is": "intern"},  # 88 + 60 + 42 + 4 + 3 + 2, md:29-37
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Workday Job Profile"]},
                                    "value": "@value"}},
                 "re": "intern(s|ship|ships)?", "is": "intern"},  # 28 + 4 + 4 + 4 + 4 + 3 + 3, md:52-59
                # departments is an array of {name}; flattened so one rule reads
                # all of them. Boundaries reject "Internal Audit" (25), "Internal
                # Fleet Operations" (14) and "International G and A" (10) (md:50).
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "intern(s|ship|ships)?", "is": "intern"},  # 47 + 18, md:43
                # The ten active AQR Summer Analyst titles are one recruiting
                # family; nine descriptions explicitly call it an internship.
                # The bounded phrase claims the tenth consistently without
                # admitting generic analyst roles (md:Student Programs).
                {"from": "@title", "re": "summer analyst", "is": "intern"},
                # Stronger title-level exclusions precede tenant grouping labels.
                # An internship/co-op above still wins when its title carries one
                # of these words (for example, a PhD research internship).
                {"from": "@title", "re": "ph\\.?d\\.?s?|postdocs?|doctoral", "is": None},
                {"from": "@title",
                 "re": "recruiters?|recruiting|recruitment|talent acquisition",
                 "is": None},
                # Role-specific student evidence precedes the broader metadata
                # and department buckets. Apprenticeships are deliberately kept
                # broad for early usage so niche technical programs are not lost;
                # SkillBridge is excluded at the start of the table.
                {"from": "@title",
                 "re": "working students?|werkstudent[a-z]*"
                       "|new grad(uate)?s?|new college grad(uate)?s?"
                       "|university grad(uate)?s?|graduate programs?"
                       "|apprentices?|apprenticeships?",
                 "is": "student_program"},
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "new grad(uate)?s?|campus", "is": "student_program"},  # 28 + 23, md:32-33
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "campus|universit(y|ies)|early career|new grads?",
                 "is": "student_program"},  # 35 + 21 + 20 + 14 + 13 + 10, md:42-49
                # LAST: part-time. `Time Type` is a closed set split three ways by
                # casing and hyphenation (md:100); the subject is lowercased and
                # `.?` absorbs the separator, so one pattern covers all three.
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Time Type"]},
                                    "value": "@value"}},
                 "re": "part.?time", "is": "part_time"},  # 56 + 20, md:97-98
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "part.?time", "is": "part_time"},  # 2, md:89
                # `content` is DO NOT USE for both co-op and part-time: 47 co-op
                # hits are dominated by Riot Games' perks boilerplate (md:74-80)
                # and "hours per week" appears in full-time benefits copy (md:85).
            ]},
            "remote": {"classify": [
                # `Location Type` fills only 2.8% of rows but is the one
                # structured statement. On-Site terminates at unknown so it also
                # suppresses the location string below, which is the far
                # higher-recall and far noisier signal.
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Location Type"]},
                                    "value": "@value"}},
                 "re": "on.?site", "is": "unknown"},  # 763, md:106
                # "Hybrid (Travel-Required)" -- hybrid before remote throughout.
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Location Type"]},
                                    "value": "@value"}},
                 "re": "hybrid", "is": "hybrid"},  # 14, md:108
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Location Type"]},
                                    "value": "@value"}},
                 "re": "remote", "is": "yes"},  # 18, md:107
                # hybrid must be excluded before a remote match is trusted, and
                # the location string is where the two collide (md:123).
                {"from": "@location.name", "re": "hybrid", "is": "hybrid"},  # 627, md:123
                # The full pattern list is measured here, unlike on ashby and
                # lever: "Distributed" alone is 87 rows (md:120). Multi-site
                # strings such as "San Francisco, CA, US; Remote, US" are
                # remote-eligible and counted in the 4,624 (md:116, md:124).
                {"from": "@location.name",
                 "re": "remote|virtual|work from home|home.?based|anywhere|distributed",
                 "is": "yes"},  # 4,624, md:110-121
                # No polarity trap: 0 rows say non-remote / not remote (md:122).
            ], "default": "unknown"},
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
            # DERIVED -- measured on Lever's own corpus (doc/raw_postings/lever.md,
            # 2,366 rows / 16 boards). Small and skewed: four boards are 78% of
            # it, so every count below is directional (md:7-8).
            "job_type": {"classify": [
                # Co-op FIRST. Only 1 row in the corpus, and boundaries are not
                # load-bearing there -- but Greenhouse proves the Scoop trap is
                # real and they cost nothing (md:72-73).
                {"from": "@text", "re": "co-?ops?", "is": "coop"},  # 1, md:71
                # Boundaries reject the 7 Internal/International titles, including
                # the 4 "(Cedacri International)" suffixes (md:39-41).
                {"from": "@text", "re": "intern(s|ship|ships)?", "is": "intern"},  # 47, md:35
                # Tenant free text, 98.7% fill. Bare /intern/ would add 62 rows of
                # "International Office Entity" and "International EOR" (md:49).
                # Adds only 1 row over the title -- the co-op above -- but that is
                # a row, so it stays (md:50).
                {"from": "@categories.commitment", "re": "intern(s|ship|ships)?", "is": "intern"},  # 46, md:47
                # "Zoox Internships" (5): department is the only intern signal on
                # those rows, title and commitment both miss (md:53-55).
                {"from": "@categories.department", "re": "intern(s|ship|ships)?", "is": "intern"},  # 5, md:54
                {"from": "@categories.team", "re": "campus", "is": "student_program"},  # 3 + 2, md:58-59
                # The doc measures the loose `new grad|graduate program|university
                # |campus` at 32 (md:36) and calls out "Recruiter, University and
                # Alternative Channel" as a false positive (md:42), so the bare
                # forms are dropped and only the phrases stay -- recall is below
                # that 32, not at it. `apprentice` is 0 here (md:37) and so has no
                # rule at all, unlike workday-list.
                {"from": "@text",
                 "re": "new grads?|graduate program|university talent|campus hire",
                 "is": "student_program"},  # <=32, md:36
                # LAST: part-time, 1 row on the enum and 2 on the title. Thin, but
                # measured. `additionalPlain` is DO NOT USE -- shieldai's closing
                # boilerplate negates part-time on 366 rows, 15% of the corpus
                # (md:95-97).
                {"from": "@categories.commitment", "re": "part.?time", "is": "part_time"},  # 1, md:84
                {"from": "@text", "re": "part.?time", "is": "part_time"},  # 2, md:93
                # The CJK values 正社員 (58) and 契約社員 (2) separate full-time from
                # contract (md:87-88); neither is in this column's domain, so
                # there is nothing to write a rule against. `research` likewise
                # has no Lever signal and is never emitted.
            ]},
            # 100% fill on a closed 3-value enum: "this is the field; no string
            # parsing, no keyword list. Nothing else on Lever comes close"
            # (md:103-104). Because the enum never misses, no rule below it could
            # ever fire -- so `categories.location` deliberately has none. It
            # mentions remote on 6 rows of 2,366, and 2 of those 5 literal
            # "Remote" strings contradict the enum (md:112-118). Greenhouse's
            # pattern list is not ported: it gets 4,624 location matches on 33k
            # rows, Lever gets 6 on 2.4k.
            "remote": {"classify": [
                # onsite terminates at unknown, consistent with every other
                # vendor, so this stays correct if a weaker rule is ever added.
                {"from": "@workplaceType", "re": "onsite", "is": "unknown"},  # 887, md:106
                {"from": "@workplaceType", "re": "hybrid", "is": "hybrid"},  # 679, md:107
                {"from": "@workplaceType", "re": "remote", "is": "yes"},  # 800, md:106
                # CAVEAT (md:108-110): 716 of the 800 remote rows are veeva alone.
                # The enum is reliable; its distribution is a tenant artifact, so
                # do not calibrate a remote-rate prior on this corpus.
            ], "default": "unknown"},
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
            "workplace_type_raw": {"lookup": [{"True": "remote"}, "@telecommuting"]},
        },
        "extra": {"department": "@department", "telecommuting": "@telecommuting", "function": "@function", "industry": "@industry"},
    },
    "icims": {
        "list": {
            "url": "https://careers.{key}.com/api/jobs?page={cursor}", "items": "jobs", "unwrap": "data", "id": "@req_id",
            # `total` duplicates the envelope's own totalCount field so
            # _parallel_eligible can see an integer bound; page.size=10 is
            # the vendor's own page width, unverified as raisable.
            "page": {"start": 1, "size": 10, "step": 1, "total": "@totalCount", "concurrency": 4},
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
            # DERIVED -- measured on the iCIMS corpus
            # (doc/raw_postings/icms.md, 21,918 rows / 5 tenants).
            #
            # Title-driven, because every richer signal this vendor offers turns
            # out to be one tenant's private vocabulary:
            #   employment_type=INTERN -- 47 rows, ALL of them sig (md:66), and
            #      sig also tags experienced roles INTERN: "Quantitative
            #      Systematic Trader | Experienced Hire", "Senior Trader -
            #      Prediction Markets | Experienced Hire" (md:70-71). Confirmed
            #      still live. A documented precision failure on a single-tenant
            #      field is not a signal.
            #   categories.name -- "Interns + Co-ops" (47), "New Graduates"
            #      (30), "Internships" (6): zero false positives, but every one
            #      of those values is a single tenant's job-family naming
            #      (md:73-79). `category` additionally prefixes every value with
            #      a literal space (md:81-84).
            #   tags1..tags4 -- the same slot carries unrelated meanings per
            #      tenant; tags4 is a careers-site name on amd and an office
            #      city on sig (md:97-102).
            "job_type": {"classify": [
                # Spelling is NOT uniform here: the corpus carries a
                # spaced-hyphen form ("University Co - Op Placement") that the
                # plain co-?op pattern misses, so the separators are optional on
                # both sides (md:104-108). No Scoop-style false positive exists
                # in this corpus, but the boundaries stay regardless.
                {"from": "@title", "re": "co\\s?-?\\s?ops?", "is": "coop"},  # 2, md:107
                # Boundaries reject only 3 extra rows here -- Internal Audit and
                # "Rivian Internal Mobile App" (md:49-51) -- the smallest such
                # gap of any vendor, but the pattern is the same one everywhere.
                {"from": "@title", "re": "intern(s|ship|ships)?", "is": "intern"},  # 57, md:46
                # `apprentice|apprenticeship` is 0 -- md:48 records that the word
                # does not occur -- so it earns no rule of its own here beyond
                # the shared phrase list. Bare `university|campus` is explicitly
                # excluded: md:52-54 measures it as retail-store and recruiter
                # noise ("Sales Advisor IV - University Village Seattle",
                # "University Recruiting Coordinator (Contract)").
                {"from": "@title",
                 "re": "new grads?|graduate programs?|early careers?",
                 "is": "student_program"},  # 6, md:47
                # LAST: part-time. Unlike INTERN above, PART_TIME is filled by
                # three tenants (costco 150, rivian 33, amd 6) and is the only
                # clean part-time field in the corpus (md:120-122). The title
                # agrees but adds nothing -- all 33 of its hits are one rivian
                # retail family that fills both (md:130-133) -- and the
                # description is the Greenhouse trap, "part-time" inside
                # full-time benefits boilerplate (md:135-139).
                {"from": "@employment_type", "re": "part.?time", "is": "part_time"},  # 189, md:121
            ]},
            # No `remote` rule. The survey finds nothing to key one on:
            # `location_type` is flagged DO NOT USE -- despite matching the
            # Greenhouse metadata key name it is a geocoding-precision hint
            # (LAT_LNG 21,914 / ANY 4, md:142-145); the only genuine
            # work-arrangement field is ONE tenant's tags3, with 3 remote rows
            # (md:147-148); the four location fields carry 5 remote rows between
            # them (md:150-153); and the title matches remote and hybrid ZERO
            # times -- "iCIMS tenants do not put arrangement in the title"
            # (md:158). `remote` stays at its 'unknown' default.
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
            "workplace_type_raw": {
                "fallback": [
                    {"lookup": [{"True": "remote"}, "@remote"]},
                    {"lookup": [{"True": "hybrid"}, "@hybrid"]},
                    {"lookup": [{"True": "on_site"}, "@on_site"]},
                ]
            },
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
    # `total` reports a flat 10,000 -- an API ceiling, not the corpus size.
    # Results come back newest-first, so five 200-row pages keep the newest
    # 1,000 and still overlap several days of postings between polls. Live
    # probes measured a 200-row response at ~4s and pages 2-5 together at ~4s
    # with width 4 (all 200, no retries); one 1,000-row response took ~20.5s.
    # The bounded fan-out therefore keeps the same window in roughly two round
    # trips instead of 20 serial requests.
    "unstop": {
        "list": {
            "url": "https://unstop.com/api/public/opportunity/search-result",
            "params": {"opportunity": "internships", "page": "$cursor", "per_page": 200},
            "items": "data.data", "id": "@id",
            "page": {
                "total": "@data.total", "start": 1, "size": 200, "step": 1,
                "max": 5, "concurrency": 4,
            },
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
    #   `max` caps the sweep at the newest 5,000: five pages of 1,000, the first
    #     paced normally and the remaining four in flight at once (`concurrency`),
    #     so the whole sweep is a couple of round trips instead of 50 throttled
    #     ones. No result-window limit was observed (from=10001 still answers)
    #     and size=1000 answers in ~2.3s, so both the cap and the burst are
    #     cost/politeness decisions, not server ones: an exhaustive pass would
    #     be ~197 requests. The engine reports `truncated` when the cap bites,
    #     so the shortfall is never silent.
    "talentreef": {
        "list": {
            "method": "POST",
            "url": "https://prod-kong.internal.talentreef.com/apply/proxy-es/search-en-us/posting/_search",
            "body": {
                "size": 1000,
                "from": "$cursor",
                "query": {"match_all": {}},
                # createdDate is day-precision, so ties are page-order-unstable
                # without the jobId tiebreaker: a 5,000-row sweep came back
                # with 77 duplicate ids before it was added.
                "sort": [{"createdDate": "desc"}, {"jobId": "desc"}],
            },
            "items": "hits.hits", "unwrap": "_source", "id": "@jobId",
            "page": {"total": "@hits.total", "size": 1000, "step": 1000, "max": 5, "concurrency": 4},
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
                # 500 is the vendor's own clamp, not a guess: doc/integration-plan.md:221
                # records that a larger `count` is silently reduced to 500 with a
                # `messages` warning. Descriptions are inline here (no detail leg),
                # so page size is the whole cost -- 5x fewer requests for the same
                # rows beats any amount of fan-out. `max` drops 50 -> 10 to hold the
                # sweep at the same ~5,000-row ceiling it had at count=100.
                "where": "isOpen=true", "start": "$cursor", "count": 500,
                "orderBy": "-dateLastPublished", "showTotalMatched": "true",
            },
            "items": "data", "id": "@id",
            "page": {"start": 0, "size": 500, "step": 500, "total": "@total", "max": 10},
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
    # Fountain's directory endpoint is a complete, paginated list, but each
    # row is only a pointer into an HTML application flow. The JSON has no
    # description or date field, so deliberately leave both unmapped: these
    # records cannot be scored and the feed must remain unverified/disabled in
    # the catalog until a reusable HTML detail reader exists.
    #
    # Account, brand and country are distinct route parts. In particular,
    # brand often differs from account and country is uppercase ISO-2. The
    # application route uses `to_param`, not the UUID in `id`.
    "fountain": {
        "key_parts": ("account", "brand", "country"),
        "list": {
            "url": "https://web.fountain.com/c/{account}/{brand}/{country}.json",
            "url_override": False,
            "params": {"page": "$cursor"},
            "items": "openings", "id": "@id",
            "page": {
                "start": 1, "size": 25, "step": 1,
                "total": "@pagination.total_count", "max": 40,
            },
        },
        "fields": {
            "title": "@title",
            "url_posting": {"template": "https://web.fountain.com/{account}/apply/{to_param}?brand_id=all"},
            "apply_url": {"template": "https://web.fountain.com/{account}/apply/{to_param}?brand_id=all"},
            "company_raw": "$company_raw",
            "location_raw": {"fallback": ["@location_address", "@location_name"]},
            "employment_type": "@job_type",
        },
        "extra": {
            "to_param": "@to_param", "location_name": "@location_name",
            "location_address": "@location_address", "location_state_code": "@location_state_code",
            "additional_job_types": "@additional_job_types",
            "amazon_afp_job_types": "@amazon_afp_job_types",
        },
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
    # -- these credentials must be configured in PostgreSQL, never committed.
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
    # The `workday` list leg with the detail leg deleted. `workday` (the detail
    # leg) has since been retired outright -- there is no second source left to
    # move tenants across from any more, so its 87-tenant catalog moved into
    # this source's endpoint configuration wholesale.
    #
    # The detail leg was ~91% of a workday run's requests and the only leg that
    # has ever provoked 429s (see the tuning note on `workday` below). Dropping
    # it costs the description outright, the ISO posted date (`startDate` is
    # detail-only; List's `postedOn` is a relative bucket), and fill on
    # `timeType` (98.9% -> 29.1%) and `remoteType` (17.6% -> 12.9%). Those
    # numbers are measured -- doc/raw_postings/workday-fields.md, 4,748 sampled
    # list rows against 100,068 stored detail payloads.
    #
    # With no detail leg the list row *is* the stored payload, so every path
    # below reads a `jobPostings[]` element directly.
    "workday-list": {
        # Compound key: tenant/wd_host/site. Workday treats all three parts
        # case-insensitively, so ingestion stores the identity in lowercase.
        "key_parts": ("tenant", "wd_host", "site"),
        "list": {
            "method": "POST",
            "url": "https://{tenant}.{wd_host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
            "url_override": False,
            "body": {
                "appliedFacets": "$appliedFacets",
                "limit": 20,
                "offset": "$cursor",
                "searchText": "",
            },
            "items": "jobPostings",
            # A list row carries no jobReqId. `externalPath` is the only
            # 100%-filled unique field (4,748/4,748 sampled); `bulletFields[0]`
            # is *usually* the requisition id but the corpus has rows where it
            # is the string "Posted 30+ Days Ago", so it is not an identity.
            "id": "@externalPath",
            "catch_first_error": True,
            # CXS sends no ETag/Last-Modified and explicitly says no-store.
            # During this window, an unchanged, uncapped `total` stops after
            # the first page. A changed count paginates immediately; expiry
            # forces a full reconciliation even when the count stayed equal.
            "count_probe": {"window_s": 86_400},
            # Same 2,000-row result window as `workday` -- it is a property of
            # the endpoint, not of the detail leg, so partitioning stays.
            "partition": {"window": 2000, "facets": "facets"},
            # Held at `workday`'s measured optimum. Its note establishes that
            # retries track total request volume against Workday's sustained
            # limiter rather than fan-out width, and dropping the detail leg
            # already cuts volume ~11x, so 10 is safe here by construction.
            # Re-measure through the inspector before raising it.
            "page": {"total": "@total", "size": 20, "step": 20, "concurrency": 10},
        },
        "last_status": True,
        "fields": {
            "title": "@title",
            # Detail's `externalUrl` carries no locale segment (see
            # fixtures/workday_detail.json), so this template reproduces the
            # real candidate-facing form rather than inventing an /en-US/ one.
            "url_posting": {"template": "https://{tenant}.{wd_host}.myworkdayjobs.com/{site}{externalPath}"},
            "apply_url": {"template": "https://{tenant}.{wd_host}.myworkdayjobs.com/{site}{externalPath}"},
            # company_raw is deliberately unmapped: the list carries no
            # hiringOrganization, and silver falls back to the feed's company
            # for ATS sources, which is the right answer for a tenant board.
            "location_raw": "@locationsText",
            "employment_type": "@timeType",
            # Raw and unmapped by contract; the derived value is `remote` below.
            "workplace_type_raw": "@remoteType",
            # No ISO date exists on a list row, so `published_at` stays NULL
            # rather than being faked from a "Posted Today" bucket.
            "published_at_precision": "unknown",
            # DERIVED -- every pattern and count below is measured on Workday's
            # own corpus (doc/raw_postings/workday.md, 4,748 sampled list rows)
            # and is not portable to any other vendor. The `classify` *form* is
            # shared; its contents never are. Another ATS names different
            # fields, fills them at different rates, and produces different
            # false positives, so each one is transcribed from its own doc.
            #
            # With no detail leg there is no `jobDescription`, so the whole
            # description-level signal is unavailable here by construction --
            # which is convenient, since that field ran ~85% false positives for
            # co-op (senior reqs that *supervise* co-ops, a product named
            # "Co-op", and "(COOP)" for Continuity Of Operations Plan).
            "job_type": {"classify": [
                # Co-op FIRST. workday.md:57-58: co-op titles are a subset of
                # the intern-ish set, so this has to resolve before the intern
                # rule ever sees them. Both boundaries are load-bearing --
                # dropping the trailing one admits "Security Cooperation" (5)
                # and "Design Technology Co-Optimization (DTCO) Engineer" (1).
                {"from": "@title", "re": "co-?ops?", "is": "coop"},  # 36
                {"from": "@externalPath", "re": "co-?ops?", "is": "coop"},  # 30
                # `research` is a research-flavoured internship, not an
                # undergraduate research post: (undergraduate|student) research
                # is 1 row corpus-wide, and `research assistant|associate` is a
                # clinical career title (Clinical Research Associate II), never
                # a student one. Two branches for both word orders;
                # `research[a-z]*` admits "Researcher Intern". The
                # [ /&,()-]+ and [^a-z] separators are load-bearing -- matcher()
                # boundaries apply only to the whole match, so without them
                # "International ... Research" could parse as intern+al.
                # md:83
                {"from": "@title",
                 "re": "research[a-z]*[ /&,()-]+[a-z0-9 /&,()-]*intern(s|ship|ships)?"
                       "|intern(s|ship|ships)?[^a-z][a-z0-9 /&,()-]*research[a-z]*",
                 "is": "research"},  # 11, md:83
                # `intern(s|ship|ships)?`, not `interns?`: the shorter form
                # fails the trailing boundary on "internships" and misses ~30
                # rows of one tenant's "X Internships: City - Fall 2026"
                # (workday.md:24-27). The boundaries reject "International" (88).
                {"from": "@title", "re": "intern(s|ship|ships)?", "is": "intern"},  # 555
                # The slug survives a title edited after generation -- 2 known
                # cases carry "Intern" here but not in the live title.
                {"from": "@externalPath", "re": "intern(s|ship|ships)?", "is": "intern"},
                # German tenants: "Werkstudent (m/w/d) im Bereich E-Commerce".
                # The [a-z]* tails cover WerkstudentIn / Praktikanten inflections.
                {"from": "@title",
                 "re": "werkstudent[a-z]*|praktikum|praktikant[a-z]*|trainee",
                 "is": "intern"},  # 115 with the bucket above
                {"from": "@title",
                 "re": "new grads?|graduate program|university talent|campus hire"
                       "|apprentices?|apprenticeships?",
                 "is": "student_program"},  # 134 + 31
                # PROGRAM-NAMED STUDENT ROLES, ENGLISH ONLY. Measured on the
                # 69,421 live `workday-list` rows, not on the 4,748-row survey.
                # Every rule below fires only on rows the seven rules above leave
                # at NULL, so none can retype an existing verdict: 0 regressions
                # over the full corpus. +153 rows, +11.4% on the 1,339 the table
                # types today.
                #
                # These are buckets a list-only feed loses *for free* -- all are
                # readable from the title, so none needs the detail leg back. The
                # description-only signal that motivated this survey was worth
                # only 32 further rows corpus-wide, and ~85% of naive description
                # matches are false (a staff req that says "internship experience
                # does not apply", or a clinician who "supports the dietetic
                # internship program").
                #
                # Non-English student vocabularies were measured and are
                # deliberately NOT carried: German `ausbildung|duales studium|
                # dhbw` (92 rows), French `alternance|stagiaire|apprenti` (48),
                # CJK `实习生|인턴|インターンシップ` (43). 183 rows stay NULL as a
                # result. Note the `werkstudent|praktikum|praktikant` rule above
                # predates this block and still carries German; left as found.
                #
                # "Working Student" is the English rendering German and Nordic
                # tenants use when the title itself is in English, so it stays.
                {"from": "@title",
                 "re": "student worker|working student|student trainee|student assistant"
                       "|student placement|placement student|industrial placement"
                       "|student employee",
                 # NOT `student researcher`: the research rule above documents
                 # that phrase as 1 row corpus-wide and a career title, not a
                 # student one. test_workday_research_is_a_research_flavoured_internship
                 # pins it.
                 "is": "intern"},  # 24
                # Finance-sector naming: an internship that never says "intern".
                # 0 rows in the corpus today because no bulge-bracket tenant has
                # a live list feed -- Citi's 3,307 detail rows carry 8 of these
                # and its list leg is not configured. Cheap to carry, and the
                # bucket is unreachable without it the day one is added.
                {"from": "@title",
                 "re": "summer analysts?|summer associates?|summer scholars?|spring insight"
                       "|off.?cycle analyst|vacation scheme|vacation work"
                       "|sandwich (year|placement)|year in industry|placement year",
                 "is": "intern"},  # 0 live, forward cover
                # US DoD SkillBridge: a real internship for transitioning service
                # members. 11 rows already say "Internship" in the title and are
                # caught above; these 44 say only "SkillBridge". Retarget to
                # `student_program` if the product means "student" strictly.
                {"from": "@title", "re": "skillbridge", "is": "intern"},  # 44
                {"from": "@externalPath", "re": "skillbridge", "is": "intern"},
                # Early-career program branding. Title only: the slug carries no
                # early-career signal on any corpus row (0 slug-only hits), so a
                # slug leg would be dead weight.
                {"from": "@title",
                 "re": "early career|early talent|emerging talent|entry.level program",
                 "is": "student_program"},  # 82
                {"from": "@title",
                 "re": "graduate (scheme|analyst|trainee|development program)|school leaver"
                       "|leadership development program",
                 "is": "student_program"},  # 5
                # LAST RESORT: the site is known for free from the request URL,
                # so this costs nothing, but it is weak recall and fires only
                # when title and slug are both silent. Boeing's INTERN site also
                # carries non-internship pathway programs (workday.md:44-46), so
                # this is the first rule to drop if it proves noisy.
                {"from": "$site", "re": "intern", "is": "intern"},  # 38
                {"from": "$site",
                 "re": "campus|campus_careers|graduate_careers|university_talent"
                       "|workday_early_career",
                 "is": "student_program"},  # 41 + 5 + 4 + 5 + 1
                # LAST: part-time is the weakest signal of the set (closed
                # `timeType` field but only 29.1% fill at list-time, vs 98.9%
                # on Detail -- workday.md:79-83) and never overlaps with the
                # intern/coop/student_program buckets above, so it can safely
                # sit after them without a priority conflict.
                {"from": "@timeType", "re": "part time", "is": "part_time"},  # 103
                {"from": "@title",
                 "re": "part.?time|per diem|prn|casual",
                 "is": "part_time"},  # workday.md:86-89, blank-timeType rows
                # `research` has no Workday signal at all -- no field, no title
                # bucket -- so this vendor never emits it. Absence is the
                # honest answer; it arrives with a vendor whose doc measures one.
            ]},
            "remote": {"classify": [
                # THE TRAP (workday.md:99-101): the presence of "remote" in
                # remoteType is not remote-positive. "Office Worker (NOT Remote)"
                # is the canonical example. It terminates at `unknown` rather
                # than being a `not` clause, because a structured on-site
                # statement must also stop the weaker title rules below from
                # firing on the same row.
                {"from": "@remoteType", "re": "not remote", "is": "unknown"},
                # Hybrid before remote, so "Remote/Hybrid" (1) lands on hybrid.
                {"from": "@remoteType", "re": "hybrid", "is": "hybrid"},  # 190
                {"from": "@remoteType", "re": "remote", "is": "yes"},  # 60
                # 97.4% fill but coarse: multi-site postings collapse to a
                # rollup like "3 Locations" (487/187) instead of naming the
                # sites. Those rows simply match nothing, so the location
                # patterns are self-limiting to single-location rows.
                {"from": "@locationsText", "re": "hybrid", "is": "hybrid"},
                {"from": "@locationsText",
                 "re": "remote|virtual|work at home|teleworker|nationwide",
                 "is": "yes"},
                # Low recall, high precision. "Remote Sensing Software Engineer"
                # (3) is satellite imagery, not a work location.
                {"from": "@title", "re": "hybrid", "is": "hybrid"},
                {"from": "@title", "re": "remote|virtual|work from home",
                 "not": "remote sensing", "is": "yes"},  # 456 + 62 + 14
            ], "default": "unknown"},
        },
        "extra": {
            "postedOn": "@postedOn",
            "bulletFields": "@bulletFields",
            "externalPath": "@externalPath",
            "timeLeftToApply": "@timeLeftToApply",
            "jobPostingEndDateAsText": "@jobPostingEndDateAsText",
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
            # posted_date is rendered, not ISO: "August 13, 2026".
            "published_at": {"date": "@posted_date", "fmt": "%B %d, %Y"},
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
            # A page-number cursor, not an offset: `step` is 1 (page 1, 2, 3...)
            # while `size` is the 20 rows Apple serves per page -- the body has
            # no page-size parameter, so 20 is the server's, not ours.
            #
            # The USA filter alone returns ~4,580 postings, so an exhaustive
            # sweep is ~230 pages. Sequentially that cost 230.5s, of which only
            # 44.1s was HTTP (~192ms/page): the other 81% was the 1s/host
            # min-interval in Net. Pages 2+ fan out, which skips that throttle.
            #
            # Width 2, not the 4 talentreef and oracle use: at 4 Apple answered
            # 2 of 228 pages with 429 (13.1s wall, retries absorbing them). At
            # 2 the same 228-page sweep took 21.1s with a clean 200 on every
            # page. So the extra width buys 8s in exchange for provoking the
            # board -- this ceiling is Apple's, not a politeness choice.
            "page": {"total": "@res.totalRecords", "start": 1, "size": 20, "step": 1,
                     "max": 400, "concurrency": 2},
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
            # 10 rows/page is the server's, not ours: `pcsx/search` takes no
            # size parameter. A `num=50` param is documented for Eightfold's
            # sibling SmartApply endpoint (doc/source-list-opus-audit.md:227)
            # and probed here against microsoft -- it changed nothing, 10 rows
            # with it and 10 without, so that lever does not exist on this path.
            # Fan-out is therefore the only one.
            #
            # The earlier "flat past 2" reading came from a 4-page probe, too
            # small to show scaling. On whole boards it scales cleanly:
            # ford (59 requests)     width 2 24.3s, 4 11.8s, 8 5.5s, 16 2.9s
            # qualcomm (196 requests) width 2 28.0s, 8 6.7s, 16 3.5s, 32 2.7s
            # Item counts identical at every width; returns flatten past 16.
            #
            # 8 and not 16, and emphatically not 32: sweeping to 32 drew a
            # sustained 405 on our httpx client across five of six tenants --
            # including ones never probed -- while curl from the same host and
            # UA still got 200. It cleared on its own, but tenants share edge
            # infrastructure, so the ceiling is Eightfold's and it is enforced
            # vendor-wide, not per tenant. `Net._host_semaphores` is keyed by
            # netloc, so this cap is per tenant: `run.cycle` gathers a batch of
            # feeds, and all six boards polling at once is 6x this number in
            # flight against one backend. 8 is verified for a single board;
            # raising it further needs a vendor-wide cap first.
            #
            # `max` exists because this config had none -- at 10 rows/page an
            # unexpectedly large tenant walked unbounded. 500 pages = 5,000 rows,
            # chosen so it does not bite a real board: Microsoft reports
            # `data.count` 2,000 and a 200-page cap truncated it exactly, which
            # is the mistake this value avoids. The engine reports `truncated`
            # if it ever does bite, so a short sweep is never silent.
            "page": {"total": "@data.count", "size": 10, "step": 10,
                     "max": 500, "concurrency": 8},
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
            # DERIVED -- measured on Eightfold's own corpus
            # (doc/raw_postings/eightfold.md, 7,949 rows / 6 boards).
            #
            # `name` is the ONLY intern signal in the payload: no employment-type
            # enum, no metadata bag, and no description at all (md:90-92).
            "job_type": {"classify": [
                # Co-op first for consistency, though it is effectively absent
                # here -- and unusually the bounded and unbounded regexes agree
                # exactly, so there is no Cooper St trap in this corpus (md:122).
                {"from": "@name", "re": "co-?ops?", "is": "coop"},  # 2, md:124
                # md:93-102 warns that word boundaries MISS real interns here,
                # because Qualcomm joins title tokens with underscores
                # ("Intern_Systems") and `_` is a word character in POSIX regex,
                # so Postgres `\M` fails on them. That warning is about the
                # SURVEY's dialect and does not apply to this one:
                # classify.matcher() wraps patterns in an explicit
                # `(?![a-z0-9])` lookaround, and `_` is not in that class -- so
                # the plain pattern already matches all of Intern_Systems,
                # Intern_OneIT and Internship_SW_CDC while still rejecting the
                # 34 Internal / International rows. Do NOT add the underscore
                # alternative md:100 prescribes; test_classify_rules.py pins
                # both halves of this.
                {"from": "@name", "re": "intern(s|ship|ships)?", "is": "intern"},  # 151, md:101
                # md:104-106 measures `new grad|graduate program|university|
                # campus|early career|apprentice|trainee|student` together (95).
                # Only the phrase forms are transcribed -- bare `student` alone
                # would claim "Student Intern" for the wrong bucket, and bare
                # `university|campus` is the pair iCIMS measured as recruiter
                # noise (icms.md:52-54). "Early Careers - ..." is SLB's program
                # prefix and the highest-yield of the phrases (md:106).
                {"from": "@name",
                 "re": "new grads?|graduate programs?|early careers?"
                       "|apprentices?|apprenticeships?",
                 "is": "student_program"},  # 44, md:104-106
                # No part_time rule: md:127-131 calls it **Undetectable** -- no
                # employment-type field exists and `name` matches part-time on
                # 4 rows in 7,949. A bucket the survey measures nothing for gets
                # no rule.
            ]},
            # A 100%-fill closed 3-value enum, but `onsite` is the UNSET DEFAULT
            # rather than a statement: Microsoft is 2,228 of 2,228 `onsite`, zero
            # hybrid, zero remote, which is not a credible description of its
            # actual arrangements (md:44-50). So `hybrid` and `remote_local` are
            # trustworthy positives and `onsite` gets no rule at all -- it falls
            # to the `unknown` default, which is where the domain's "never assert
            # a negative" rule would put it anyway, here for the independent
            # reason that the negative is indistinguishable from missing data.
            #
            # `locationFlexibility` is NOT a refinement of this enum (md:56-62):
            # 183 of its 231 rows are `onsite`, and it means "we will consider
            # candidates anywhere in this country", i.e. relocation. Unread.
            # `locations[]` is free-form and tenant-formatted with no canonical
            # field order and a literal "Multiple Locations" placeholder
            # (md:64-77) -- too unstable to key a rule on.
            "remote": {"classify": [
                {"from": "@workLocationOption", "re": "hybrid", "is": "hybrid"},  # 277, md:42
                {"from": "@workLocationOption", "re": "remote_local", "is": "yes"},  # 17, md:42
            ], "default": "unknown"},
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
            # BambooHR's list endpoint has no ETag and no cache-control that
            # would let a validator help (verified live: `no-store, no-cache,
            # must-revalidate`), but its envelope carries `meta.totalCount`
            # over the full, unpaginated `result` array -- the same shape
            # Workday's `count_probe` already consumes. `page.total` exists
            # here only to populate `context["total"]`; there is no true
            # pagination (`result` is always the whole board), and `_more`
            # verifies that itself once `len(rows) == total`.
            "page": {"total": "@meta.totalCount"},
            "count_probe": {"window_s": 86_400},
        },
        "detail": {
            "url": "https://{key}.bamboohr.com/careers/{id}/detail",
            "url_override": False,
            "id": "@id",
            "payload": "result.jobOpening",
            # The list carries no description, so it is one GET per posting and
            # the run is ~1s x N of our own throttle. Measured on datapelago
            # (14 postings, 15 requests): width 1 14.3s, 2 5.0s, 4 4.5s, 8 1.3s,
            # every width clean -- no 429, no retry, no Retry-After.
            #
            # 4, not the 8 the probe liked best: 15 requests is too thin a
            # sample to rank widths, and BambooHR publishes no rate limit. What
            # the probe does establish is tolerance, and 4 is the ceiling the
            # rest of this tree settled on against vendors that do publish one.
            # Revisit with a large tenant if 8 is ever worth the argument.
            "concurrency": 4,
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
            "workplace_type_raw": {"lookup": [{"True": "remote"}, "@isRemote"]},
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
            # limit/offset must live inside the finder value -- a top-level
            # query param binds to the outer (always 1-item) collection and
            # is silently ignored by the finder. 200 is the server-side cap;
            # larger values are clamped, not rejected.
            # No facetsList: we only read requisitionList + TotalJobsCount,
            # and skipping facet computation shaves a little server time.
            "url": (
                "https://{host}.oraclecloud.com/hcmRestApi/resources/latest/"
                "recruitingCEJobRequisitions?onlyData=true&finder=findReqs;siteNumber={site},"
                "limit=200,offset={cursor},"
                "sortBy=POSTING_DATES_DESC&expand=requisitionList"
            ),
            "items": "items.0.requisitionList",
            "id": "@Id",
            "url_override": False,
            # Top-level "hasMore" describes the outer 1-item collection, not
            # the board, so pagination is driven off TotalJobsCount instead.
            # Each page is ~4.5s of server think time. Original measurement
            # found "mild queueing at 6"; re-probed at 5, 6, and 8 (10+ live
            # runs, cummins/963 items and mayo/1366 items): zero retries,
            # zero 429s at any width, and 5-vs-8 A/B runs interleaved with no
            # reproducible per-request slowdown, so that queueing point did
            # not reproduce. Left at 8 -- widest measured, still error-free.
            "page": {"total": "@items.0.TotalJobsCount", "size": 200, "step": 200, "max": 30, "concurrency": 8},
        },
        "detail": {
            # Ids are comma-joined into one quoted finder value -- {id} here
            # is the whole batch, not a single Id. Verified against Oracle's
            # own career site up to ~360 ids/request; 200 keeps responses to
            # a few MB. Response items are matched back to list rows by Id
            # ("match"), since batch order is not guaranteed.
            "url": (
                "https://{host}.oraclecloud.com/hcmRestApi/resources/latest/"
                "recruitingCEJobRequisitionDetails?expand=all&onlyData=true&"
                "finder=ById;siteNumber={site},Id=\"{id}\""
            ),
            "id": "@Id",
            "url_override": False,
            "batch": 200,
            # ~11s of server think time per 200-id batch dominates the poll;
            # same measured parallelism headroom as the list pages -- see the
            # width-8 re-probe note there.
            "concurrency": 8,
            "match": "@Id",
            "payload": "items",
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
            # DERIVED -- measured on Oracle's own corpus
            # (doc/raw_postings/oracle.md) and not portable to any other vendor.
            #
            # Title and two closed vendor-controlled fields only. The three
            # richer-looking signals are deliberately unread:
            #   RequisitionType   -- tenant-configured vocabulary, not a vendor
            #      enum. "HR4HR", "Shop", "Full Service Professional" are one
            #      instance's setup, and "Campus" is a *recruiting channel*
            #      rather than an employment type: 453 Campus rows, only 97 of
            #      which the title agrees are interns (md:39). Mapping it would
            #      type ~350 experienced hires as students.
            #   requisitionFlexFields:"Job Type" -- 6,382 rows, ONE board
            #      (md:41-45). A rule keyed on it is a rule about one company.
            #   StudyLevel -- 0.16% fill, 40 rows. md:47 says not usable.
            "job_type": {"classify": [
                # Co-op first, as on every vendor. Only 2 rows here, and one of
                # them ("Summer Intern Co-op Conversion") also matches the
                # intern rule below -- which is exactly why the order matters.
                {"from": "@Title", "re": "co-?ops?", "is": "coop"},  # 2, md:60
                # Finance-sector naming: an internship that never says "intern".
                # Same rule as workday-list (md:workday.md, 0 live there) -- here
                # it is live: Goldman Sachs (162) and JPMorganChase (40) title
                # their programs this way exclusively, e.g. "2027 | Americas |
                # Dallas Metro Area | Investment Banking Quantitative Strats |
                # Summer Analyst".
                {"from": "@Title",
                 "re": "summer analysts?|summer associates?|summer scholars?"
                       "|spring insight|off.?cycle analyst|vacation scheme"
                       "|vacation work|sandwich (year|placement)|year in industry"
                       "|placement year",
                 "is": "intern"},  # 202
                # Boundaries reject the 267 Internal / International rows that a
                # bare /intern/ admits -- the largest such gap of any vendor
                # surveyed (md:26-27).
                {"from": "@Title", "re": "intern(s|ship|ships)?", "is": "intern"},  # 135, md:21
                # md:24-25 measures `new grad|graduate program|university|campus`
                # (21) and `apprentice|apprenticeship` (60) separately. Only the
                # phrase forms and the apprentice pair are transcribed: the bare
                # `university|campus` share of the first number is unmeasured,
                # and it is the same pair iCIMS measured as retail-store noise
                # (icms.md:52-54). Count is this corpus, not md's union.
                {"from": "@Title",
                 "re": "new grads?|graduate programs?|early careers?"
                       "|apprentices?|apprenticeships?",
                 "is": "student_program"},  # 38, md:24-25
                # LAST: part-time, from JobSchedule -- a closed two-value field,
                # null on the 7,006 rows of the one board that never sets it,
                # which simply means no rule fires (md:67-70). The title agrees
                # on 925 of its own 942 hits, so it adds nothing worth its
                # false-positive surface (md:72).
                {"from": "@JobSchedule", "re": "part.?time", "is": "part_time"},  # 724, md:69
            ]},
            # WorkplaceTypeCode, not WorkplaceType: same 3,451 rows, but the
            # code is machine-readable and closed, while the label spells a
            # fourth value ("On-site with Flexibility") that maps onto
            # ORA_ON_SITE anyway (md:83-88).
            #
            # requisitionFlexFields:"Remote worker" is ONE board (md:90-93) and
            # gets no rule. Title matches \mremote\M on 93 rows, all already
            # covered by the code on the boards that set it (md:95).
            "remote": {"classify": [
                # On-site terminates at `unknown` rather than falling through,
                # consistent with every other vendor: a structured on-site
                # statement is not evidence of anything weaker being true.
                {"from": "@WorkplaceTypeCode", "re": "ora_on_site", "is": "unknown"},  # 1,237, md:86
                {"from": "@WorkplaceTypeCode", "re": "ora_hybrid", "is": "hybrid"},  # 411, md:86
                {"from": "@WorkplaceTypeCode", "re": "ora_remote", "is": "yes"},  # 136, md:87
            ], "default": "unknown"},
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
            # List rows carry no description, so it is one GET per posting; the
            # whole run was our own 1s/host throttle. Measured on
            # heads-up-technologies (21 postings, 22 requests): width 1 21.2s,
            # 2 1.9s, 4 1.2s, 8 0.9s, all clean. Same reasoning as bamboohr --
            # 22 requests establishes tolerance, not the best width, so take the
            # house ceiling rather than the probe's favourite.
            "concurrency": 4,
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
            # No cap declared elsewhere in this file goes over 5; gem's own
            # boards are small (largest measured: 109 postings), so this
            # stays inside every other vendor's precedent.
            # Production compares the list/detail fields declared in
            # sources.REVISION_FIELDS and reuses matching details for at most
            # 24 hours. Expiry forces every detail so description-only edits
            # remain bounded rather than becoming permanently stale.
            "concurrency": 4,
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
            # One GET per requisition, so a run is ~1s x N of our own throttle.
            # Measured on a 6-requisition tenant (7 requests): width 1 6.4s,
            # 2 0.9s, 4 0.7s, 8 0.6s, all clean 200s. The board is too small to
            # separate 2 from 8 -- what it shows is that ADP tolerates the
            # fan-out, and 4 is this tree's ceiling for a vendor that publishes
            # no limit. Note `$top` is hard-capped at 20 (integration-plan.md:118),
            # so the list leg cannot be made cheaper; only this one can.
            "concurrency": 4,
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
            # Descriptions live only in the detail payload, so it's one
            # request per posting. SmartRecruiters documents 10 req/s and 8
            # concurrent requests (429 on excess); every feed shares one
            # per-host semaphore in Net (all 18 boards are one hostname), so
            # this is the vendor-wide cap, not a per-board one.
            #
            # 6, not 8: at the documented ceiling a single co-running request
            # tips us over, and sustained 429s drive _HostPace toward its 2.0s
            # spacing ceiling -- net slower than staying under. Most postings
            # now skip detail entirely via revision reuse (REVISION_FIELDS in
            # sources/__init__.py), so width matters less than it used to.
            "concurrency": 6,
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
            # DERIVED -- every pattern and count below is measured on
            # SmartRecruiters' own corpus (doc/raw_postings/smartRecruiters.md,
            # 31,326 rows / 18 boards) and is not portable. The `classify` form
            # is shared across vendors; its contents never are.
            #
            # Only vendor-controlled, ~100%-fill fields get a rule. `customField`
            # holds the richest student-track signal in the corpus ("Entry
            # Level", 962 rows) and is deliberately unread: of 40+ labels only
            # two span more than three boards, so a rule keyed on one is a rule
            # about one company (md:170-185).
            "job_type": {"classify": [
                # Co-op FIRST, ahead of both enums -- "Engineering Lab CO-OP"
                # and "CO-OP EHS" carry experienceLevel=internship, so the enum
                # rule below would consume them. Boundaries on BOTH sides:
                # dropping them adds 15 rows, all Domino's street addresses
                # containing "Cooper St" (md:79-80).
                {"from": "@name", "re": "co-?ops?", "is": "coop"},  # 5, md:77
                # Three independent intern signals, none subsuming the others --
                # union 175, title alone 111, experienceLevel 139,
                # typeOfEmployment 97 (md:28-29). The enums are 100%-fill closed
                # vendor sets, so they outrank the title, and they are what
                # reaches the 64 non-English rows an English-only title rule
                # misses: Praktikum, Pflichtpraktikum, Estagio, Abschlussarbeit,
                # "en alternance" (md:57-71). `language.code` does not gate
                # those -- match the structured field instead of porting a
                # multilingual regex.
                {"from": "@experienceLevel.id", "re": "internship", "is": "intern"},  # 139, md:33
                {"from": "@typeOfEmployment.id", "re": "intern", "is": "intern"},  # 97, md:39
                # `intern(s|ship|ships)?`, not `interns?`: the shorter form fails
                # the trailing boundary on "internships". Boundaries reject the
                # 91 Internal/International rows and, on this vendor uniquely,
                # the Domino's "11951 International Dr" street addresses
                # (md:50-55).
                {"from": "@name", "re": "intern(s|ship|ships)?", "is": "intern"},  # 112, md:46
                # The non-English student vocabulary md:57-71 measures. 777 rows
                # (2.5%) are non-English across 20 language codes, and the enums
                # above rescue only some of them -- these four patterns are the
                # only signal on the rest. md:69-71 is emphatic that
                # `language.code` cannot gate them: "Werkstudent (m/w/d) im
                # Bereich R&D Engineering" is tagged `en` and "Praktikant /
                # Werkstudent (m/w/d)" is tagged `en-GB`, because the code
                # describes the ad, not the title. Match unconditionally.
                #
                # Leading `[a-z]*` on the German stems is load-bearing: these
                # compound, and matcher()'s left boundary rejects the compound
                # form otherwise -- "Pharmaziepraktikum" and "Pflichtpraktikum"
                # both fail a bare `praktikum`. Pflichtpraktikum is a *mandatory*
                # university placement, i.e. always a student role (md:63-64).
                {"from": "@name", "re": "[a-z]*praktikum|[a-z]*praktikant[a-z]*", "is": "intern"},  # 26, md:63
                # Term-time part-time student work (md:65). `werkstudent[a-z]*`
                # for the same reason ashby needs it: WerkstudentIn and
                # Werkstudent/in carry no right boundary.
                {"from": "@name", "re": "werkstudent[a-z]*|working student", "is": "intern"},  # 23, md:65
                {"from": "@name", "re": "est[aá]gi[a-z]*", "is": "intern"},  # 12, md:68, pt-BR
                # Non-Latin scripts are exactly why classify.py uses an explicit
                # lookaround rather than `\\b` -- this pattern is unmatchable
                # under a word-boundary escape.
                {"from": "@name", "re": "นักศึกษาฝึกงาน", "is": "intern"},  # 7, md:68, th
                # md:47 measures `new grad|graduate program|university|campus|
                # early career` as one number (118), so the bare
                # `university|campus` share is unmeasured -- and where iCIMS did
                # split it out, the bare pair is mostly retail-store and
                # recruiter noise (icms.md:52-54). Only the phrase forms are
                # transcribed; the count is this corpus, not md:47's union.
                {"from": "@name",
                 "re": "new grads?|graduate programs?|early careers?"
                       "|apprentices?|apprenticeships?",
                 "is": "student_program"},  # 4, md:47-48
                # The German apprenticeship, and the largest single non-English
                # bucket in the corpus. AFTER the intern tokens above, not
                # before: "Praktikum - HR Ausbildung, Recruiting &
                # Personalentwicklung" is an internship *in* the training
                # department and must resolve as intern. `[a-z]*` for the
                # compound, as above -- "Berufsausbildung" is the dominant form
                # and a bare `ausbildung` misses every one of them.
                #
                # md:66 counts `ausbildung|abschlussarbeit|referendar` together
                # (90), but they are three unrelated tracks -- apprenticeship,
                # thesis, legal clerkship -- so only the one with an unambiguous
                # bucket is transcribed and the count is this corpus, not md's
                # union. abschlussarbeit (3) and referendar (1) stay unread.
                {"from": "@name", "re": "[a-z]*ausbildung", "is": "student_program"},  # 84, md:66
                # LAST: part-time, from the enum only. `name` is DO NOT USE --
                # 246 hits, 107 of them typeOfEmployment=permanent, because
                # "Full/Part time" advertises *either* schedule (md:98-100); so
                # is jobDescription, 748 of its 1,221 hits being permanent rows
                # (md:104-110). Trust the field, not the corpus rate: 4,846 of
                # the 4,877 are dominos (md:88-90).
                {"from": "@typeOfEmployment.id", "re": "part.?time", "is": "part_time"},  # 4,877, md:91
            ]},
            # `location.remote` / `location.hybrid` are real JSON booleans at
            # 100% fill and mutually exclusive -- remote=true,hybrid=true is 0
            # rows, so there is no ambiguity to resolve. The survey calls this
            # the cleanest remote signal of any vendor surveyed (md:115-121).
            #
            # Not a `classify` table: that form skips a non-string `from`, and
            # these are booleans. `lookup` stringifies its key, so the ordered
            # fallback below is the same first-match-wins shape by other means.
            # A `False` misses the table and yields None, which `fallback` skips.
            #
            # fullLocation is NOT a signal (md:123-128) -- a remote=true row
            # still carries a physical hiring office, and 19 of 31,326 rows
            # contain the word at all. jobDescription is DO NOT USE: 83 of its
            # 110 hits are remote=false polarity traps (md:136-142).
            "remote": {"fallback": [
                {"lookup": [{"True": "yes"}, "@location.remote"]},  # 855, md:118
                {"lookup": [{"True": "hybrid"}, "@location.hybrid"]},  # 2,153, md:117
                "unknown",
            ]},
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
}

CONFIGS = {**SIMPLE, **COMPANY_JSON, **DETAIL_A, **DETAIL_B}
