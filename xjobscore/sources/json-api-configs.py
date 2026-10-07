"""Plain configuration for the shared JSON ATS adapter.

Expression values: ``@path`` reads an item/response path; ``$name`` reads
engine context (``$key``, ``$id``, ``$cursor``, ``$envelope.*``).  Everything
else is a literal.  Keep a configuration declarative; only add engine support
when a pattern is shared by several vendors.
"""

# The SHARED job_type table of doc/raw_postings/regex/job_type.md, transcribed
# verbatim and lifted out of the configs so its consumers DERIVE from it rather
# than transcribe it again. Three read it: `workday`'s job_type, `workday`'s
# detail `only` gate, and `eightfold`'s job_type through `_job_type_from`.
#
# The gate re-runs this same ladder inside harvest(), on the raw list row, to
# decide which rows are worth a detail fetch -- it cannot read parse()'s
# `job_type`, because harvest returns a Record long before silver parses it.
# Hand-maintained copies would drift; one list and a comprehension cannot.
#
# `from` is `@title` here because that is Workday's key. A vendor that spells
# it differently rebinds with `_job_type_from` and changes nothing else.
_SHARED_JOB_TYPE: list[dict] = [
            # EXCLUDED first, and terminal. A licensure-, service- or degree-gated
            # posting is not a student one whatever else the title says, so
            # `is: None` voids the row rather than letting it fall through.
            # job_type.md:13-16
            {"from": "@title", "re": "skillbridge", "is": None},
            {"from": "@title", "re": "pharmacy|pharmacist", "is": None},
            {"from": "@title", "re": "rns?|nurses?|nursing|bsn", "is": None},
            {"from": "@title", "re": "ph\\.?d\\.?s?|postdoc(s|toral)?|doctoral", "is": None},
            # Co-op ahead of intern: co-op titles are a subset of the
            # intern-ish set, so the intern rule would otherwise consume
            # them. Both boundaries load-bearing -- dropping the trailing one
            # admits "Security Cooperation" (5) and "Design Technology
            # Co-Optimization (DTCO) Engineer" (1). job_type.md:20
            {"from": "@title", "re": "co-?ops?", "is": "coop"},
            # A research-flavoured internship, both word orders. The
            # [ /&,()-]+ and [^a-z] separators are load-bearing: matcher()
            # boundaries apply to the whole match only, so without them
            # "International ... Research" could parse as intern+al.
            # job_type.md:25
            {"from": "@title",
             "re": "research[a-z]*[ /&,()-]+[a-z0-9 /&,()-]*intern(s|ship|ships)?"
                   "|intern(s|ship|ships)?[^a-z][a-z0-9 /&,()-]*research[a-z]*",
             "is": "research"},
            # `intern(s|ship|ships)?`, not `interns?`: the shorter form fails
            # the trailing boundary on "internships" and misses ~30 rows of
            # one tenant's "X Internships: City - Fall 2026"
            # (workday.md:24-27). The boundaries reject "International" (88).
            # job_type.md:30
            {"from": "@title", "re": "intern(s|ship|ships)?", "is": "intern"},
            # An internship that never says "intern". job_type.md:33
            {"from": "@title", "re": "summer analysts?|summer associates?", "is": "intern"},
            # `not` SKIPS this rule rather than ending the scan, so a title
            # vetoed as student still reaches the new-grad rules below. That
            # fall-through is the doc's semantics; a terminal exclusion here
            # would silently cost new_grad rows. job_type.md:38-49
            {"from": "@title",
             "re": "students?|apprentice(s|ship|ships)?",
             "not": "student affairs|student success|student accounts|student accountability"
                    "|student engagement|student collective|student event"
                    "|community (manager|coordinator)|editorial (lead|manager)"
                    "|marketing manager|program coordinator"
                    "|recruiters?|recruiting|recruitment|talent acquisition"
                    "|externs?|externship|clerkship|student rotation"
                    "|pre.employment training|bpet"
                    "|nationals only|j1 students",
             "is": "student"},
            # New grad, two title groups sharing one `not` list: channel
            # naming first, then the named rotation into a first job. The
            # veto covers enrolled-at-the-school roles, the institution
            # itself, and anyone who RUNS the channel rather than entering
            # it -- but not a candidate role whose occupation is manager
            # (`New College Grad - Product Manager, HBM` is real).
            # job_type.md:55-57, 62-63, 68-72
            {"from": "@title",
             "re": "grad(uate|uates|s)?|ncg"
                   "|early careers?|early talent|emerging talent"
                   "|campus hires?|school leavers?",
             "not": "grad(uate)? (assistants?|assistantship)"
                    "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                    "|graduate medical education"
                    "|recruiters?|recruiting|recruitment|talent acquisition"
                    "|(program|programme) (managers?|leads?|directors?|coordinators?)",
             "is": "new_grad"},
            {"from": "@title",
             "re": "(leadership|analyst|engineer(ing)?|rotational) development program"
                   "|rotational program|launch program",
             "not": "grad(uate)? (assistants?|assistantship)"
                    "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                    "|graduate medical education"
                    "|recruiters?|recruiting|recruitment|talent acquisition"
                    "|(program|programme) (managers?|leads?|directors?|coordinators?)",
             "is": "new_grad"},
            # NO `$site` rules. The board names a recruiting channel, not the
            # audience of every row it carries, and measured live it was
            # 15-for-35 wrong. Removed 2026-08-26 with the Workday table.
]


# The detail `only` gate: the SAME ladder, same order, same `not` lists, with
# every verdict rewritten to True. A row the table types is worth one detail
# request; the excluded tier's `is: None` and the absent default both evaluate
# to "not True", which is exactly the skip the engine tests for
# (json-api.py: `if gate is not None and _value(gate, row, context) is not True`).
# Excluded rows are skipped ON PURPOSE -- a pharmacy or RN posting is out of
# scope, so it should not cost a request either.
_WORKDAY_DETAIL_ONLY: list[dict] = [
    rule if rule["is"] is None else {**rule, "is": True}
    for rule in _SHARED_JOB_TYPE
]


def _job_type_from(path: str) -> list[dict]:
    """The shared ladder read from a vendor's own title key.

    Order, patterns and `not` lists are untouched -- only `from` moves. A
    vendor whose title needs more than a rebind (a second field, an extra
    veto) writes its own table and says why; this helper exists for the case
    where the shared rules apply as-is to a differently-named field.
    """
    return [{**rule, "from": path} for rule in _SHARED_JOB_TYPE]

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
            # DERIVED -- job_type is the SHARED table in
            # doc/raw_postings/regex/job_type.md, transcribed verbatim and read
            # from `title` alone. This replaces the Ashby-measured table that
            # stood here until 2026-08-26, after a head-to-head over all 59,792
            # Ashby rows in raw_posting: the shared table fired on 1,498 rows to
            # the Ashby table's 896, agreed with it on 890, and disagreed on the
            # label for 2 (both SkillBridge -- the shared excluded tier voids
            # them where the Ashby table said `intern`). Of the 608 it adds, 493
            # are the excluded tier Ashby had no equivalent for and 115 are
            # career-stage catches Ashby's narrower patterns missed.
            #
            # KNOWN GAP, deliberately carried: the shared table misses 6 rows
            # the Ashby one claimed -- 4 German Werkstudent recruiting titles
            # its student `not` list vetoes on purpose, and 2 campus-ambassador
            # rows it has no rule for. The Ashby-only `campus( growth)?
            # ambassadors?` rule is NOT kept: the gap belongs upstream in the
            # shared doc, not in a per-vendor patch.
            #
            # TITLE ONLY. The `employmentType` enum leg, the department and team
            # legs, and the `descriptionPlain` leg all went with the old table.
            "job_type": {"classify": [
                # EXCLUDED first, and terminal. A licensure-, service- or degree-gated
                # posting is not a student one whatever else the title says, so
                # `is: None` voids the row rather than letting it fall through --
                # same shape as the workday adapter's skillbridge rule.
                # job_type.md:13-16
                {"from": "@title", "re": "skillbridge", "is": None},
                {"from": "@title", "re": "pharmacy|pharmacist", "is": None},
                {"from": "@title", "re": "rns?|nurses?|nursing|bsn", "is": None},
                {"from": "@title", "re": "ph\\.?d\\.?s?|postdoc(s|toral)?|doctoral", "is": None},
                # Co-op ahead of intern: co-op titles are a subset of the
                # intern-ish set, so the intern rule would otherwise consume
                # them. Both boundaries load-bearing -- dropping the trailing one
                # admits "Cooperative"/"Cooperation". job_type.md:20
                {"from": "@title", "re": "co-?ops?", "is": "coop"},
                # A research-flavoured internship, both word orders. The
                # [ /&,()-]+ and [^a-z] separators are load-bearing: matcher()
                # boundaries apply to the whole match only, so without them
                # "International ... Research" could parse as intern+al.
                # job_type.md:25
                {"from": "@title",
                 "re": "research[a-z]*[ /&,()-]+[a-z0-9 /&,()-]*intern(s|ship|ships)?"
                       "|intern(s|ship|ships)?[^a-z][a-z0-9 /&,()-]*research[a-z]*",
                 "is": "research"},
                # `intern(s|ship|ships)?`, not `interns?`: the shorter form fails
                # the trailing boundary on "internships". The boundaries reject
                # Internal/International. job_type.md:30
                {"from": "@title", "re": "intern(s|ship|ships)?", "is": "intern"},
                # An internship that never says "intern". job_type.md:33
                {"from": "@title", "re": "summer analysts?|summer associates?", "is": "intern"},
                # `not` SKIPS this rule rather than ending the scan, so a title
                # vetoed as student still reaches the new-grad rules below. That
                # fall-through is the doc's semantics; a terminal exclusion here
                # would silently cost new_grad rows. job_type.md:38-49
                {"from": "@title",
                 "re": "students?|apprentice(s|ship|ships)?",
                 "not": "student affairs|student success|student accounts|student accountability"
                        "|student engagement|student collective|student event"
                        "|community (manager|coordinator)|editorial (lead|manager)"
                        "|marketing manager|program coordinator"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|externs?|externship|clerkship|student rotation"
                        "|pre.employment training|bpet"
                        "|nationals only|j1 students",
                 "is": "student"},
                # New grad, two title groups sharing one `not` list: channel
                # naming first, then the named rotation into a first job. The
                # veto covers enrolled-at-the-school roles, the institution
                # itself, and anyone who RUNS the channel rather than entering
                # it. job_type.md:55-57, 62-63, 68-72
                {"from": "@title",
                 "re": "grad(uate|uates|s)?|ncg"
                       "|early careers?|early talent|emerging talent"
                       "|campus hires?|school leavers?",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                {"from": "@title",
                 "re": "(leadership|analyst|engineer(ing)?|rotational) development program"
                       "|rotational program|launch program",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                # The one non-title leg, and it is LAST on purpose -- it may only
                # speak for a row every title rule above left silent.
                #
                # ONLY the internship tier is read off `department`. The rest of
                # job_type.md measured badly here: run whole, department added 15
                # rows of which ~2 were right (`Student Support` -> Student
                # Success Coach, `Student Operations` -> Director of Strategy,
                # `Emerging Talent` -> an evergreen "Future Opportunities" req).
                # Restricted to this pattern it adds 3 rows and no false
                # positives -- 76 of its 95 matches are rows the title already
                # calls `intern`, and 13 more the title types better.
                #
                # The 3 are residency titles filed under an `Internships`
                # department: AI Research Resident, Thermodynamic Hardware
                # Residency, Thermodynamic Hardware Resident. The department is
                # the only place their internship status is stated.
                #
                # SkillBridge is safe without a guard here: the excluded tier is
                # terminal, so the 2 `Internships & Co-ops` SkillBridge rows stop
                # at the top of the table and never reach this rule. Moving this
                # rule any higher would revive them as `coop`.
                {"from": "@department", "re": "intern(s|ship|ships)?", "is": "intern"},  # 3
            ]},
            # The enum decides it -- "PartTime" lowercases to "parttime", which
            # `part.?time` matches with the separator absent.
            # COUNTS RECOUNTED 2026-08-26 against raw_posting, on the 50,449
            # DISTINCT ashby postings (latest fetch each), not the 18,715-row
            # survey the md: citations point at. The corpus is 2.7x the survey,
            # so every number below is a different denominator, not a drift.
            "part_time": {"classify": [
                {"from": "@employmentType", "re": "part.?time", "is": "yes"},  # 683 (md:93 measured 29)
                # TERMINAL: the title phrase disagrees with the enum on more than
                # half its rows -- "[CONTRACT] Part-Time Recruiting Coordinator"
                # is typed Contract (md:100). Trust the enum, so a Contract or
                # Temporary row stops here rather than reaching the title rule.
                # Now suppresses 15 title-says-part-time rows the survey never
                # saw: Physician (Part-Time, Off-hours), Telepsychiatrist
                # (Contract, Part Time), YouTube Podcast Producer (Part-Time
                # Contract) and siblings.
                {"from": "@employmentType", "re": "contract|temporary", "is": "unknown"},  # 2,079 (md:94 measured 239)
                # NO LONGER A TIE-BREAKER. md:101 prescribed this as cover for 4
                # FullTime rows; it now decides 142 -- a sixth of every part-time
                # verdict this table reaches. The rule is unchanged and still
                # correct (the enum has already claimed or killed everything it
                # disagrees with), but it is load-bearing now, not a rounding
                # error, so a change here moves real volume.
                {"from": "@title", "re": "part.?time", "is": "yes"},  # 142 (md:98-101 measured 4)
                # Uncovered by the survey's design: 1 row typed `Intern` with a
                # part-time title falls through to the title rule and reads yes.
            ], "default": "unknown"},
            "remote": {"classify": [
                # The closed 3-value enum is the field (md:114). `isRemote` is
                # derived from it and lossy -- it counts Hybrid as remote, 53% of
                # its true rows (md:121-125) -- so it stays in `extra`, unread.
                # OnSite terminates at unknown rather than being a `not` clause:
                # a structured on-site statement must also suppress the location
                # rules below, which contradict it on 15 rows (md:133).
                {"from": "@workplaceType", "re": "onsite", "is": "unknown"},  # 15,667 (md:115 measured 7,413)
                {"from": "@workplaceType", "re": "hybrid", "is": "hybrid"},  # 13,024 (md:116 measured 4,464)
                {"from": "@workplaceType", "re": "remote", "is": "yes"},  # 14,158 (md:117 measured 3,919)
                # The enum is missing on 7,600 of the 50,449 distinct postings,
                # and 710 of the 783 affected boards are mixed, so the gap is
                # per-posting, not per-board (md:118-119). Hybrid first, as on
                # every other vendor.
                #
                # THE LOCATION LEGS SHRANK IN ABSOLUTE TERMS while the corpus
                # grew 2.7x -- enum fill improved, so fewer rows reach them.
                # They are cover for a narrowing gap, not a growing one.
                {"from": "@location", "re": "hybrid", "is": "hybrid"},  # 119 (md:130 measured 300)
                # "work from home", "home-based" and "distributed" are all 0 here
                # -- Greenhouse's pattern list is deliberately not ported (md:131).
                {"from": "@location", "re": "remote|virtual", "is": "yes"},  # 640 (md:127-130 measured 2,148 + 5 + 2)
                # `anywhere` SPLIT 2026-08-26. It used to sit in the alternation
                # above and was the single largest error source in this table.
                # Validated against `workplaceType` on the rows that carry both:
                #
                #   bare "Anywhere"        20 Remote /  0 Hybrid  -> 100% remote
                #   "Anywhere in <place>"   5 Remote / 20 Hybrid  ->  20% remote
                #
                # "Anywhere in Belgium" is a hiring radius, not a remote
                # statement -- the employer means anywhere *within* the country
                # and types it Hybrid. Bare "Anywhere" is the real thing.
                # The veto costs nothing: a row that also says remote outright
                # ("Anywhere - remote US") is already claimed by the rule above.
                {"from": "@location", "re": "anywhere",
                 "not": "anywhere (in|-)", "is": "yes"},  # 6

                # Rows pairing a non-remote primary location with a remote
                # secondary; a single-site read calls all of them onsite
                # (md:138-144). Most are already resolved by the enum above, so
                # these two only decide what is left.
                {"from": {"concat": {"map": "@secondaryLocations", "value": "@location"}, "sep": " ; "},
                 "re": "hybrid", "is": "hybrid"},  # 2
                {"from": {"concat": {"map": "@secondaryLocations", "value": "@location"}, "sep": " ; "},
                 "re": "remote|anywhere|virtual", "is": "yes"},  # 142 net (md:139 measured 359 gross)
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
        # Two spellings of one board reach this adapter. Discovery promotes a
        # candidate to `board-id/api-token` (keyscan/probe.py _probe_comeet),
        # while an operator-entered row is `board-id` plus a config.token.
        # Splitting the key serves the first; config, applied last, still wins
        # for the second. Both render the same URL.
        "key_parts": ("company", "token"),
        "list": {"url": "https://www.comeet.co/careers-api/2.0/company/{company}/positions?token={token}&details=true", "items": "", "id": "@uid"},
        "fields": {
            "title": "@name", "url_posting": "@url_comeet_hosted_page", "company_raw": "@company_name",
            "location_raw": {"fallback": ["@location.name", {"join": ["@location.city", "@location.state", "@location.country"]}]},
            "description_html": {"concat": {"map": "@details", "value": "@value"}},
            "employment_type": "@employment_type", "workplace_type_raw": "@workplace_type",
            # DERIVED -- measured on vendors-tbd/comeet.md, 361 rows.
            "job_type": {"classify": [
                {"from": "@employment_type", "re": "co-?ops?", "is": "coop"},  # 0, md:24, forward cover
                {"from": "@name", "re": "co-?ops?", "is": "coop"},  # 0, md:24, forward cover
                # Closed 5-value seniority set with `Intern` as a member (md:17-22).
                # `intern` cannot reach "Intermediate" -- different stem.
                {"from": "@experience_level", "re": "intern", "is": "intern"},  # 1, md:22
                {"from": "@name", "re": "intern(s|ship|ships)?", "is": "intern"},  # 1, md:14
                # Student: enrolled, but not a fixed-term internship. Shared
                # across vendors from doc/raw_postings/regex/job_type.md -- the
                # intern and co-op rules above still win a title saying both.
                # The `not` list is the operator/gate cohort measured corpus-wide:
                # staff who serve students, roles that run the program, graduate
                # clinical rotations, trade pre-hire training, and nationality or
                # visa gates. A `not` hit skips this rule rather than ending the
                # scan, so those rows stay reachable by a later signal.
                {"from": "@name",
                 "re": "students?|apprentice(s|ship|ships)?",
                 "not": "student affairs|student success|student accounts"
                        "|student accountability|student engagement"
                        "|student collective|student event"
                        "|community (manager|coordinator)|editorial (lead|manager)"
                        "|marketing manager|program coordinator"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|externs?|externship|clerkship|student rotation"
                        "|pre.employment training|bpet"
                        "|nationals only|j1 students",
                 "is": "student"},
            ]},
            "part_time": {"classify": [
                {"from": "@employment_type", "re": "part.?time", "is": "yes"},  # 54, md:30
            ], "default": "unknown"},
            "source_updated_at": {"date": "@time_updated"},
            "content_sections": {"sections": "@details", "sort": "@order", "heading": "@name", "html": "@value"},
        },
        "extra": {"department": "@department", "experience_level": "@experience_level", "categories": "@categories", "location_is_remote": "@location.is_remote"},
    },
    "greenhouse": {
        # concurrency: single shared host (boards-api.greenhouse.io) for every
        # tenant, so this per-host semaphore is the vendor-wide cap too -- same
        # shape as ashby above, no _VENDOR_CAPS entry needed. 30 per
        # doc/networking/rate-limits.md: a 357-board burst drew zero throttle
        # responses at widths 30-100, and 30-40 held ~95 req/s at p95 508ms
        # while width 100 fell to 46 req/s on queueing alone. Without this key
        # the adapter takes `net`'s default serial path -- one request per
        # second to the one host every Greenhouse feed shares.
        "list": {"url": "https://boards-api.greenhouse.io/v1/boards/{key}/jobs?content=true", "items": "jobs", "id": "@id", "concurrency": 30},
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
            # DERIVED -- job_type.md applied FIELD-MAJOR: the full cascade runs
            # on `title` first, then Employment Type, Workday Job Profile and
            # departments. Title outranks the rest because a title says what the
            # role IS while a department names the team it SITS IN -- a Jane Street
            # "Machine Learning Researcher" typed Summer Internship is an intern,
            # not a researcher, and Verkada's "Intern/Co-op" enum is a combined
            # bucket that must not outrank a title saying Intern.
            #
            # `metadata` is a tenant-defined [{name, value, value_type}] bag, so
            # one entry is selected by predicate rather than by path.
            # `content` is DO NOT USE for co-op: 47 hits are Riot Games perks
            # boilerplate (doc/raw_postings/greenhouse.md:74-80).
            "job_type": {"classify": [
                # ---- title ----
                {"from": "@title",
                 "re": "skillbridge|pharmacy|pharmacist|rns?|nurses?|nursing|bsn"
                       "|ph\\.?d\\.?s?|postdoc(s|toral)?|doctoral",
                 "is": None},
                {"from": "@title",
                 "re": "co-?ops?",
                 "is": "coop"},
                {"from": "@title",
                 "re": "research[a-z]*[ /&,()-]+[a-z0-9 /&,()-]*intern(s|ship|ships)?"
                       "|intern(s|ship|ships)?[^a-z][a-z0-9 /&,()-]*research[a-z]*",
                 "is": "research"},
                {"from": "@title",
                 "re": "intern(s|ship|ships)?",
                 "is": "intern"},
                {"from": "@title",
                 "re": "summer analysts?|summer associates?",
                 "is": "intern"},
                # TERMINAL: `not` only skips a rule; these must stop the scan so
                # a department label below cannot claim the row.
                {"from": "@title", "re": "campus ambassadors?", "is": None},
                {"from": "@title",
                 "re": "recruiters?|recruiting|recruitment|talent acquisition",
                 "is": None},
                {"from": "@title",
                 "re": "students?|apprentice(s|ship|ships)?",
                 "not": "student affairs|student success|student accounts|student accountability"
                        "|student engagement|student collective|student event"
                        "|community (manager|coordinator)|editorial (lead|manager)"
                        "|marketing manager|program coordinator"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|externs?|externship|clerkship|student rotation"
                        "|pre.employment training|bpet|nationals only|j1 students",
                 "is": "student"},
                {"from": "@title",
                 "re": "grad(uate|uates|s)?|ncg",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                {"from": "@title",
                 "re": "early careers?|early talent|emerging talent",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                {"from": "@title",
                 "re": "campus hires?|school leavers?",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                {"from": "@title",
                 "re": "(leadership|analyst|engineer(ing)?|rotational) development program",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                {"from": "@title",
                 "re": "rotational program|launch program",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                # ---- Employment Type ----
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "skillbridge|pharmacy|pharmacist|rns?|nurses?|nursing|bsn",
                 "is": None},
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "co-?ops?",
                 "is": "coop"},
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "research[a-z]*[ /&,()-]+[a-z0-9 /&,()-]*intern(s|ship|ships)?"
                       "|intern(s|ship|ships)?[^a-z][a-z0-9 /&,()-]*research[a-z]*",
                 "is": "research"},
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "intern(s|ship|ships)?",
                 "is": "intern"},
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "summer analysts?|summer associates?",
                 "is": "intern"},
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "students?|apprentice(s|ship|ships)?",
                 "not": "student affairs|student success|student accounts|student accountability"
                        "|student engagement|student collective|student event"
                        "|community (manager|coordinator)|editorial (lead|manager)"
                        "|marketing manager|program coordinator"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|externs?|externship|clerkship|student rotation"
                        "|pre.employment training|bpet|nationals only|j1 students",
                 "is": "student"},
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "grad(uate|uates|s)?|ncg",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "early careers?|early talent|emerging talent",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "campus hires?|school leavers?",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "(leadership|analyst|engineer(ing)?|rotational) development program",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "rotational program|launch program",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                # Bare noun: unambiguous in this field, unlike a title (md:24).
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "campus",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},  # 16, md:25
                # ---- departments ----
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "skillbridge|pharmacy|pharmacist|rns?|nurses?|nursing|bsn",
                 "is": None},
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "co-?ops?",
                 "is": "coop"},
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "research[a-z]*[ /&,()-]+[a-z0-9 /&,()-]*intern(s|ship|ships)?"
                       "|intern(s|ship|ships)?[^a-z][a-z0-9 /&,()-]*research[a-z]*",
                 "is": "research"},
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "intern(s|ship|ships)?",
                 "is": "intern"},
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "summer analysts?|summer associates?",
                 "is": "intern"},
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "students?|apprentice(s|ship|ships)?",
                 "not": "student affairs|student success|student accounts|student accountability"
                        "|student engagement|student collective|student event"
                        "|community (manager|coordinator)|editorial (lead|manager)"
                        "|marketing manager|program coordinator"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|externs?|externship|clerkship|student rotation"
                        "|pre.employment training|bpet|nationals only|j1 students",
                 "is": "student"},
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "grad(uate|uates|s)?|ncg",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "early careers?|early talent|emerging talent",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "campus hires?|school leavers?",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "(leadership|analyst|engineer(ing)?|rotational) development program",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "rotational program|launch program",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},
                # Bare noun: unambiguous in this field, unlike a title (md:33).
                {"from": {"concat": {"map": "@departments", "value": "@name"}, "sep": " ; "},
                 "re": "campus|universit(y|ies)",
                 "not": "grad(uate)? (assistants?|assistantship)"
                        "|grad(uate)? (research|school|schools|studies|admissions|advisors?)"
                        "|graduate medical education"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|(program|programme) (managers?|leads?|directors?|coordinators?)",
                 "is": "new_grad"},  # 9 + 6, md:33
            ]},
            # `Time Type` is a closed set split three ways by casing and
            # hyphenation (md:100); the subject is lowercased and `.?` absorbs
            # the separator, so one pattern covers all three.
            "part_time": {"classify": [
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Time Type"]},
                                    "value": "@value"}},
                 "re": "part.?time", "is": "yes"},  # 29 + 13, md:49-50
                {"from": {"first": {"map": "@metadata",
                                    "where": {"eq": ["@name", "Employment Type"]},
                                    "value": "@value"}},
                 "re": "part.?time", "is": "yes"},  # 1 + 2, md:40
            ], "default": "unknown"},
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
        # concurrency: single shared host (api.lever.co) for every tenant, so
        # this per-host semaphore is the vendor-wide cap -- same shape as
        # greenhouse and ashby. Lever documents a limit only for application
        # POSTs (2/s), none for this GET. Measured 2026-10-05 over ~6,700
        # requests: zero 429s at widths 10-160 and no rate-limit headers; width
        # 80 peaked at ~114 req/s and 160 fell to ~62 on server-side queueing,
        # so 40 sits at half the knee. ~2,400 sustained requests at 40 drew no
        # errors. Full first polls are bandwidth-bound (~110 Mbps at width
        # 10-20; median board 108 KB, p95 1.3 MB); steady state is ~91% 304s.
        "list": {"url": "https://api.lever.co/v0/postings/{key}?mode=json", "items": "", "id": "@id", "concurrency": 40},
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
                {"from": "@categories.team", "re": "campus", "is": "new_grad"},  # 3 + 2, md:58-59
                # The doc measures the loose `new grad|graduate program|university
                # |campus` at 32 (md:36) and calls out "Recruiter, University and
                # Alternative Channel" as a false positive (md:42), so the bare
                # forms are dropped and only the phrases stay -- recall is below
                # that 32, not at it. `apprentice` is 0 here (md:37) and so has no
                # rule at all, unlike workday-list.
                {"from": "@text",
                 "re": "new grads?|graduate program|university talent|campus hire",
                 "is": "new_grad"},  # <=32, md:36
                # The CJK values 正社員 (58) and 契約社員 (2) separate full-time from
                # contract (md:87-88); neither is in this column's domain, so
                # there is nothing to write a rule against. `research` likewise
                # has no Lever signal and is never emitted.
            ]},
            # 1 row on the enum and 2 on the title. Thin, but measured.
            # `additionalPlain` is DO NOT USE -- shieldai's closing boilerplate
            # negates part-time on 366 rows, 15% of the corpus (md:95-97).
            "part_time": {"classify": [
                {"from": "@categories.commitment", "re": "part.?time", "is": "yes"},  # 1, md:84
                {"from": "@text", "re": "part.?time", "is": "yes"},  # 2, md:93
            ], "default": "unknown"},
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
            # DERIVED -- measured on vendors-tbd/workable.md, 136 rows.
            "job_type": {"classify": [
                {"from": "@title", "re": "co-?ops?", "is": "coop"},  # 0, md:89, forward cover
                # `experience` is the intern field here and agrees with the title
                # regex 8/8 in both directions (md:57-60). NOT `employment_type`:
                # md:64-68 measures the 8 interns landing on Temporary/''/Part-time,
                # so it would silently bucket them as temps.
                {"from": "@experience", "re": "intern(s|ship|ships)?", "is": "intern"},  # 8, md:59
                {"from": "@title", "re": "intern(s|ship|ships)?", "is": "intern"},  # 8, md:73
            ]},
            "part_time": {"classify": [
                {"from": "@employment_type", "re": "part.?time", "is": "yes"},  # 3, md:95
            ], "default": "unknown"},
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
            #
            # The TABLE is the shared one in doc/raw_postings/regex/job_type.md,
            # rebound onto `title`. It replaced the iCIMS-measured table on
            # 2026-08-27 after a head-to-head over all 3,496 distinct postings
            # in raw_posting. Typed 137 rows before, 158 after, and on the 111
            # both type they agree on the label EVERY time, 0 disagreements.
            #
            # Adds 29 new_grad, where the old rule needed the exact phrase
            # `new grads?|graduate programs?`: "Graduate Trainee - Systems
            # Design Engineer" and its siblings, plus "Commercial Sales Rep
            # (2026 NCG)" via `ncg`. Adds 6 student from bare `apprentice`
            # ("Apprentice, Private Equity", "Apprentice, Data Operations") --
            # md:48 recorded the word as absent, and it is not absent any more.
            # Moves 12 intern -> research (the UIUC Research Park block), a
            # refinement the old table had no tier for.
            #
            # Drops 10 to the excluded tier, 9 of them sig's PhD-gated quant
            # internships ("Quantitative Research Internship - PhD: Summer
            # 2027") plus "2027 PhD Research Engineering Intern/Co-op"; and 1
            # new_grad, "Early Career - Emerging Talent Programs and Recruiting
            # Coordinator", which runs the channel (job_type.md:71).
            #
            # KNOWN REGRESSION, deliberately carried: md:104-108 measured a
            # spaced-hyphen co-op form here ("Hardware Engineer University Co -
            # Op Placement 2027", 2 rows) and widened the pattern to
            # `co\\s?-?\\s?ops?` for it. The shared table's `co-?ops?`
            # (job_type.md:20) does not reach those two, so they go untyped.
            # The fix belongs upstream in the shared doc -- widening the pattern
            # there needs its own head-to-head across Workday and Eightfold
            # first -- not as a per-vendor override that re-forks this table.
            #
            # Bare `university|campus` noise md:52-54 measured here ("Sales
            # Advisor IV - University Village Seattle", "University Recruiting
            # Coordinator") stays out: the shared table carries neither bare
            # word, only `campus hires?`, and vetoes recruiters outright.
            "job_type": {"classify": _job_type_from("@title")},
            # Unlike INTERN, PART_TIME is filled by three tenants (costco 150,
            # rivian 33, amd 6) and is the only clean part-time field in the
            # corpus (md:120-122). The title agrees but adds nothing -- all 33
            # of its hits are one rivian retail family that fills both
            # (md:130-133) -- and the description is the Greenhouse trap,
            # "part-time" inside full-time benefits boilerplate (md:135-139).
            "part_time": {"classify": [
                {"from": "@employment_type", "re": "part.?time", "is": "yes"},  # 189, md:121
            ], "default": "unknown"},
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
            # DERIVED -- measured on vendors-tbd/recruitee.md, 606 rows.
            "job_type": {"classify": [
                {"from": "@title", "re": "co-?ops?", "is": "coop"},  # 0, md:38, forward cover
                # Closed 9-value enum, md:13-22. 11/11 `internship` rows also match
                # the title regex (md:27), so the two corroborate rather than compete.
                {"from": "@employment_type_code", "re": "intern(s|ship|ships)?", "is": "intern"},  # 11, md:18
                {"from": "@title", "re": "intern(s|ship|ships)?", "is": "intern"},  # 13, md:25
                # Apprenticeship is its own enum member and sits below every intern
                # signal, matching the ordering every other vendor uses.
                # NOT `experience_code = student_college` (10, md:34): that is a
                # seniority band saying the role accepts college students, not a
                # statement that the posting *is* a student program.
                # `employment_type_code` is a closed 9-value enum and the only
                # apprenticeship signal here: md:22's row is titled "Bakker",
                # which no title rule can reach. Ahead of the shared title rule.
                {"from": "@employment_type_code",
                 "re": "apprentices?|apprenticeships?", "is": "student"},  # 1, md:22
                # Student: enrolled, but not a fixed-term internship. Shared
                # across vendors from doc/raw_postings/regex/job_type.md -- the
                # intern and co-op rules above still win a title saying both.
                # The `not` list is the operator/gate cohort measured corpus-wide:
                # staff who serve students, roles that run the program, graduate
                # clinical rotations, trade pre-hire training, and nationality or
                # visa gates. A `not` hit skips this rule rather than ending the
                # scan, so those rows stay reachable by a later signal.
                {"from": "@title",
                 "re": "students?|apprentice(s|ship|ships)?",
                 "not": "student affairs|student success|student accounts"
                        "|student accountability|student engagement"
                        "|student collective|student event"
                        "|community (manager|coordinator)|editorial (lead|manager)"
                        "|marketing manager|program coordinator"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|externs?|externship|clerkship|student rotation"
                        "|pre.employment training|bpet"
                        "|nationals only|j1 students",
                 "is": "student"},
            ]},
            "part_time": {"classify": [
                {"from": "@employment_type_code", "re": "part.?time", "is": "yes"},  # 3, md:43-45
            ], "default": "unknown"},
            "source_active": {"lookup": [{"published": True, "closed": False, "archived": False}, "@status"]},
            "locations_extra": {"rest": {"map": "@locations", "value": "@name"}},
            # A Dutch primary location can coexist with genuine US offices.
            # Keep each explicit US address as independent normalization input;
            # fixing Nederland/Noord-Holland must not hide these mixed jobs.
            # Append only positive US evidence so other vendors and foreign
            # secondary-location presentation retain their existing behavior.
            "location_normalization_extra": {
                "map": "@locations", "where": {"eq": ["@country_code", "US"]},
                "value": {"join": ["@city", "@state", "United States"]},
            },
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
            "source_active": {"lookup": [{"LIVE": True, "CLOSED": False}, "@status"]},
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
            # DERIVED -- measured on vendors-tbd/talentreef.md, 8,519 rows.
            "job_type": {"classify": [
                {"from": "@title", "re": "co-?ops?", "is": "coop"},  # 0, md:22, forward cover
                # Boundaries are load-bearing here: bare /intern/ matches 15, i.e.
                # 11 extra, all Internal / Internet (md:15-17).
                {"from": "@title", "re": "intern(s|ship|ships)?", "is": "intern"},  # 4, md:13
                # NO `student` rule. The 141 `Courtesy Clerk (Bagger) - Student`
                # rows (md:19-20) are one grocery tenant's hourly job title, not a
                # student program -- the same retail-noise trap icms.md:52-54 documents.
                # Student: enrolled, but not a fixed-term internship. Shared
                # across vendors from doc/raw_postings/regex/job_type.md -- the
                # intern and co-op rules above still win a title saying both.
                # The `not` list is the operator/gate cohort measured corpus-wide:
                # staff who serve students, roles that run the program, graduate
                # clinical rotations, trade pre-hire training, and nationality or
                # visa gates. A `not` hit skips this rule rather than ending the
                # scan, so those rows stay reachable by a later signal.
                {"from": "@title",
                 "re": "students?|apprentice(s|ship|ships)?",
                 "not": "student affairs|student success|student accounts"
                        "|student accountability|student engagement"
                        "|student collective|student event"
                        "|community (manager|coordinator)|editorial (lead|manager)"
                        "|marketing manager|program coordinator"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|externs?|externship|clerkship|student rotation"
                        "|pre.employment training|bpet"
                        "|nationals only|j1 students",
                 "is": "student"},
            ]},
            # Closed 3-value set (md:27-30). NOT the inline title tags: md:36-39
            # measures "Server (PT/FT)" as the dominant form, which advertises
            # *either* schedule and means neither on its own.
            "part_time": {"classify": [
                {"from": "@contractType", "re": "part.?time", "is": "yes"},  # 3,192, md:29
            ], "default": "unknown"},
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
            # DERIVED -- measured on vendors-tbd/ukg.md, 500 rows. Every rule here
            # is forward cover: md:10-14 measures intern and co-op at 0.
            "job_type": {"classify": [
                {"from": "@Title", "re": "co-?ops?", "is": "coop"},  # 0, md:13
                {"from": "@Title", "re": "intern(s|ship|ships)?", "is": "intern"},  # 0, md:11
                # NO part_time rule. `FullTime` is the only signal (false on 51,
                # md:16-20) and it is a JSON *boolean*; the classify engine skips
                # non-str values, so a rule on it could never fire. Reading it would
                # also mean equating not-full-time with part-time, which the survey
                # does not measure -- seasonal and contract land in the same `false`.
                # Student: enrolled, but not a fixed-term internship. Shared
                # across vendors from doc/raw_postings/regex/job_type.md -- the
                # intern and co-op rules above still win a title saying both.
                # The `not` list is the operator/gate cohort measured corpus-wide:
                # staff who serve students, roles that run the program, graduate
                # clinical rotations, trade pre-hire training, and nationality or
                # visa gates. A `not` hit skips this rule rather than ending the
                # scan, so those rows stay reachable by a later signal.
                {"from": "@Title",
                 "re": "students?|apprentice(s|ship|ships)?",
                 "not": "student affairs|student success|student accounts"
                        "|student accountability|student engagement"
                        "|student collective|student event"
                        "|community (manager|coordinator)|editorial (lead|manager)"
                        "|marketing manager|program coordinator"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|externs?|externship|clerkship|student rotation"
                        "|pre.employment training|bpet"
                        "|nationals only|j1 students",
                 "is": "student"},
            ]},
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
    # Two shapes reach `fields` here. A row the `only` gate below declines is
    # emitted with its bare `jobPostings[]` payload; a row it admits is emitted
    # with the detail response merged OVER that list row (`merge`), so every
    # list path stays readable and `jobPostingInfo.*` is additionally present.
    # Read a list path unless detail genuinely carries a better value.
    "workday": {
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
            # 2,000-row result window -- a property of the endpoint, not of the
            # detail leg, so partitioning stays.
            "partition": {"window": 2000, "facets": "facets"},
            # 10 was the measured optimum when nothing but the host's own 429s
            # bounded rate (retries tracked total volume against Workday's
            # sustained limiter, not fan-out width). Rate is now set per data
            # center by net._VENDOR_RATES and per tenant by net._HOST_RATES
            # (2 req/s), and 4 in flight at Workday's ~0.6s replies already
            # outruns the tenant budget, so a wider fan-out buys nothing per
            # feed. What the narrower one buys is feeds: `sources.width`
            # divides each data center's cap by this, so a 24-slot lane runs
            # six Workday polls at once where 10 ran two.
            "page": {"total": "@total", "size": 20, "step": 20, "concurrency": 4},
        },
        # GATED detail. An ungated leg is one GET per posting and was ~91% of
        # the run -- that is why it was removed on 2026-08-22. Gated to the rows
        # the job_type table already types, it costs 2,518 requests over the
        # whole 153,325-row corpus (1.6%, a 98.4% cut): median 9 per feed, max
        # 148 (micron/wd1/external). Its concurrency matches the page leg's so
        # both share one per-host semaphore (`_host_semaphores` is keyed by
        # width).
        #
        # `merge` is load-bearing, not an optimisation. Without it a detail row
        # loses `externalPath` -- which is the LIST leg's `id`, so identity
        # would silently differ between gated and skipped rows -- along with
        # `locationsText`, `remoteType` and the live `title`. For the same
        # reason there is deliberately NO `item_id` override: the old ungated
        # config pointed it at `jobPostingInfo.jobReqId`, which under a gate
        # would give two rows of one feed two different identity schemes.
        #
        # Detail 404s on Workday's orphaned index stubs (a list row carrying
        # only `externalPath` + `bulletFields`, no title). They carry no title,
        # so every rule in the gate misses and they are skipped before costing a
        # request -- test_workday_detail_gate_skips_a_title_less_stub pins it.
        "detail": {
            "url": "https://{tenant}.{wd_host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{id}",
            "url_override": False,
            "id": "@externalPath",
            "dedupe": "@externalPath",
            "merge": True,
            "only": {"classify": _WORKDAY_DETAIL_ONLY},
            "concurrency": 4,
        },
        "last_status": True,
        "fields": {
            # List title is the live one; detail's is a backup for the rare row
            # whose list entry is degraded.
            "title": {"fallback": ["@title", "@jobPostingInfo.title"]},
            # Detail's `externalUrl` carries no locale segment (see
            # fixtures/workday_detail.json), so this template reproduces the
            # real candidate-facing form rather than inventing an /en-US/ one.
            # Templated from `externalPath`, which `merge` keeps on both shapes.
            "url_posting": {"template": "https://{tenant}.{wd_host}.myworkdayjobs.com/{site}{externalPath}"},
            "apply_url": {"template": "https://{tenant}.{wd_host}.myworkdayjobs.com/{site}{externalPath}"},
            # company_raw is deliberately unmapped on BOTH shapes. Detail
            # carries `hiringOrganization.name`, but mapping it would give
            # gated and skipped rows of one feed two different company sources.
            # Silver falls back to the feed's company for ATS sources, which is
            # the right answer for a tenant board either way.
            #
            # Detail's `location` is a concrete site; the list's `locationsText`
            # is an `N Locations` rollup on 13.6% of the corpus and absent on
            # another 1.7%. Prefer detail where it exists.
            "location_raw": {"fallback": ["@jobPostingInfo.location", "@locationsText"]},
            # Detail-only, and the whole answer to `locationsText`'s rollups. An
            # `N Locations` row carries the other N-1 sites here and nowhere
            # else: measured over the stored detail payloads, the key is present
            # on 290/290 rollup rows, absent on all 1,694 single-location rows,
            # and `1 + len(additionalLocations)` equals N on every one. No gate
            # needed -- it is empty exactly where it would contribute nothing.
            #
            # `map`, not a bare `@path`, on purpose: this field backs a
            # `list[str]` and is NOT in the absent-default list below, so a
            # plain path would hand silver `None` on every skipped row. `map`
            # returns [] when the key is missing.
            "location_normalization_extra": {"map": "@jobPostingInfo.additionalLocations"},
            # 98.9% fill on detail vs 29.1% at list-time (workday.md).
            "employment_type": {"fallback": ["@jobPostingInfo.timeType", "@timeType"]},
            # Raw and unmapped by contract; the derived value is `remote` below.
            "workplace_type_raw": "@remoteType",
            # Detail-only, and the reason the leg exists. NULL on a skipped row.
            "description_html": "@jobPostingInfo.jobDescription",
            "requisition_id": "@jobPostingInfo.jobReqId",
            # `startDate` is the only ISO date Workday exposes and it is
            # detail-only. On a skipped row `published_at` resolves to NULL and
            # the engine downgrades this precision to `unknown` for us, so
            # "Posted Today" is never faked into a date.
            "published_at": {"date": "@jobPostingInfo.startDate"},
            "published_at_precision": "date",
            # DERIVED -- the SHARED table in doc/raw_postings/regex/job_type.md,
            # transcribed verbatim and read from `title` alone. It replaced the
            # Workday-measured table on 2026-08-26 after a head-to-head over all
            # 153,325 rows in raw_posting: the shared table fires on 2,518 rows
            # to the Workday table's 6,118, and on the 2,092 both type it agrees
            # on the label EVERY time -- 0 disagreements. The whole difference is
            # which rows get typed at all.
            #
            # Drops 4,026: 3,901 to `pharmacy|pharmacist` (`Pharmacy Intern`,
            # one tenant), 118 to `rns?|nurses?|nursing|bsn` (`New Grad RN
            # Resident - <unit>`, one hospital system), 6 to the new-grad `not`
            # list, 1 to a real gap. Six of those 124 new-grad drops are rows
            # the Workday table got WRONG -- `University Talent Acquisition
            # Business Partner`, `Vice President, Asia Early Career Program
            # Manager` -- which its `university talent` and `launch program`
            # rules had no recruiter or programme-operator veto for.
            #
            # Adds 426: 328 from `grad(uate|uates|s)?|ncg` where the old rule
            # needed an exact phrase (`Chemist 1 New College Grad`), 61 from
            # bare `apprentice`, 23 from bare `students?`, 12 from the
            # development-program group, 2 from `early careers?`.
            #
            # KNOWN REGRESSIONS, deliberately carried and pinned by tests:
            # `industrial placement` (1 row) has no shared pattern; bare
            # `apprentice` re-admits 61 trades rows this vendor measured and
            # excluded. Both belong upstream in the shared doc's `not` lists,
            # not in a per-vendor override.
            #
            # TITLE ONLY. The two `@externalPath` slug legs went with the old
            # table: re-measured they add 45 rows, 20 of them junk the slug's
            # hyphenation smuggles past every `not` list (`J1-Students-Only`,
            # `Pharmacy-Intern`) or the location segment invents outright
            # (`/job/Zagreb-Grad-Zagreb-Croatia/` -> new_grad; "Grad" is
            # Croatian for city). Normalised the honest yield is 22, and 15 are
            # non-English titles whose slug is English. US-only: 7, none CS.
            "job_type": {"classify": _SHARED_JOB_TYPE},
            # The weakest signal of the set: a closed `timeType` field, only
            # 29.1% filled at list-time vs 98.9% on detail (workday.md).
            "part_time": {"classify": [
                {"from": {"fallback": ["@jobPostingInfo.timeType", "@timeType"]},
                 "re": "part time", "is": "yes"},  # 103
                {"from": {"fallback": ["@title", "@jobPostingInfo.title"]},
                 "re": "part.?time|per diem|prn|casual",
                 "is": "yes"},  # workday.md, blank-timeType rows
            ], "default": "unknown"},
            "remote": {"classify": [
                # THE TRAP (workday.md): the presence of "remote" in remoteType
                # is not remote-positive. "Office Worker (NOT Remote)" is the
                # canonical example. It terminates at `unknown` rather than
                # being a `not` clause, because a structured on-site statement
                # must also stop the weaker title rules below from firing.
                {"from": "@remoteType", "re": "not remote", "is": "unknown"},
                # Hybrid before remote, so "Remote/Hybrid" (99) lands on hybrid.
                {"from": "@remoteType", "re": "hybrid", "is": "hybrid"},  # 190
                {"from": "@remoteType", "re": "remote", "is": "yes"},  # 60
                # Multi-site postings collapse to a rollup like "3 Locations"
                # instead of naming the sites. Those rows match nothing, so the
                # location patterns are self-limiting to single-location rows.
                {"from": {"fallback": ["@jobPostingInfo.location", "@locationsText"]},
                 "re": "hybrid", "is": "hybrid"},
                {"from": {"fallback": ["@jobPostingInfo.location", "@locationsText"]},
                 "re": "remote|virtual|work at home|teleworker|nationwide",
                 "is": "yes"},
                # Low recall, high precision. "Remote Sensing Software Engineer"
                # (3) is satellite imagery, not a work location.
                {"from": {"fallback": ["@title", "@jobPostingInfo.title"]},
                 "re": "hybrid", "is": "hybrid"},
                {"from": {"fallback": ["@title", "@jobPostingInfo.title"]},
                 "re": "remote|virtual|work from home",
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
            # DERIVED -- measured on amazon.md, 445 rows. Co-op first, as everywhere:
            # 2 of the 6 co-op titles also say Intern (md:27-28).
            "job_type": {"classify": [
                {"from": "@title", "re": "co-?ops?", "is": "coop"},  # 6, md:23
                # md:15 -- bare /intern/ also matches 234 here, 0 false positives,
                # but the boundaries are kept: 445 rows cannot rule the trap out.
                {"from": "@title", "re": "intern(s|ship|ships)?", "is": "intern"},  # 234, md:11
                # Student: enrolled, but not a fixed-term internship. Shared
                # across vendors from doc/raw_postings/regex/job_type.md -- the
                # intern and co-op rules above still win a title saying both.
                # The `not` list is the operator/gate cohort measured corpus-wide:
                # staff who serve students, roles that run the program, graduate
                # clinical rotations, trade pre-hire training, and nationality or
                # visa gates. A `not` hit skips this rule rather than ending the
                # scan, so those rows stay reachable by a later signal.
                {"from": "@title",
                 "re": "students?|apprentice(s|ship|ships)?",
                 "not": "student affairs|student success|student accounts"
                        "|student accountability|student engagement"
                        "|student collective|student event"
                        "|community (manager|coordinator)|editorial (lead|manager)"
                        "|marketing manager|program coordinator"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|externs?|externship|clerkship|student rotation"
                        "|pre.employment training|bpet"
                        "|nationals only|j1 students",
                 "is": "student"},
            ]},
            # Closed 2-value set, md:30. NOT `is_intern` / `university_job`:
            # both keys exist and are null on 445/445 (md:17-19).
            "part_time": {"classify": [
                {"from": "@job_schedule_type", "re": "part.?time", "is": "yes"},  # 160, md:32
            ], "default": "unknown"},
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
            # DERIVED -- measured on apple.md, 4,656 rows.
            "job_type": {"classify": [
                # 0 live (md:27); forward cover, and the boundaries cost nothing.
                {"from": "@postingTitle", "re": "co-?ops?", "is": "coop"},  # 0, md:27
                # Boundaries reject 12 Internal/International/Internationalization
                # titles (md:20-21). The plural tail is load-bearing: md:15 notes
                # Internships-titled reqs are missed entirely without it.
                {"from": "@postingTitle", "re": "intern(s|ship|ships)?", "is": "intern"},  # 25, md:16
                # NO part_time rule. `standardWeeklyHours` is the only signal
                # (88 rows under 40, md:30-39) and it is a JSON *number*; the
                # classify engine skips any non-str value (json-api.py `_field`),
                # so a rule on it could never fire. It also needs a < threshold,
                # which the bare-alternation dialect cannot express.
                # Student: enrolled, but not a fixed-term internship. Shared
                # across vendors from doc/raw_postings/regex/job_type.md -- the
                # intern and co-op rules above still win a title saying both.
                # The `not` list is the operator/gate cohort measured corpus-wide:
                # staff who serve students, roles that run the program, graduate
                # clinical rotations, trade pre-hire training, and nationality or
                # visa gates. A `not` hit skips this rule rather than ending the
                # scan, so those rows stay reachable by a later signal.
                {"from": "@postingTitle",
                 "re": "students?|apprentice(s|ship|ships)?",
                 "not": "student affairs|student success|student accounts"
                        "|student accountability|student engagement"
                        "|student collective|student event"
                        "|community (manager|coordinator)|editorial (lead|manager)"
                        "|marketing manager|program coordinator"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|externs?|externship|clerkship|student rotation"
                        "|pre.employment training|bpet"
                        "|nationals only|j1 students",
                 "is": "student"},
            ]},
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
        #
        # Newer tokens carry both in the key as `host/domain` with an empty
        # config; legacy keys (`microsoft`) still get them from feed.config,
        # which harvest applies after key_parts and so overrides the split.
        "key_parts": ("host", "domain"),
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
            # netloc, so this cap is per tenant: several boards polling at once
            # is that many times this number in flight against one backend. 8 is
            # verified for a single board, and the vendor-wide cap that bounds
            # the rest now exists -- `_VENDOR_CAPS` holds eightfold.ai to 16,
            # which is what sizes `sources.width` here at a two-feed pool.
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
            # DERIVED -- the SHARED table in doc/raw_postings/regex/job_type.md,
            # rebound onto `name`, this vendor's title key. It replaced the
            # Eightfold-measured table on 2026-08-27 after a head-to-head over
            # all 9,719 distinct postings in raw_posting across the 6 boards.
            #
            # `name` is the ONLY intern signal in the payload: no employment-type
            # enum, no metadata bag, and no description at all (md:90-92), so
            # the shared table's title-only reading costs this vendor nothing.
            #
            # Typed 235 rows before, 248 after, and on the 224 both type they
            # agree on the label EVERY time -- 0 disagreements.
            #
            # Adds 16 new_grad (32 -> 48), every one from a pattern the old
            # table did not carry: 8 to `campus hires?` (Qualcomm's whole
            # "2026 Campus Hire_ Engineer_ HW" / "2027 Campus Hire_..." block),
            # 7 to `grad(uate|uates|s)?|ncg` where the old rule needed the
            # exact phrase `new grads?` or `graduate programs?` ("New Graduate-
            # AI SW SDK Engineer", "Graduate Geosolutions Geophysicist",
            # "Technology Consultant: Graduate Opportunities"), and 1 to
            # `rotational program` ("Equity Research - Associate, Rotational
            # Program (New York)").
            #
            # Moves 8 intern -> research, a REFINEMENT and not a loss: the old
            # table had no research tier, so "Research Intern - Self-Improving
            # AI" landed on plain intern.
            #
            # Voids 6 to the excluded tier, 3 of which the old table typed as
            # intern: "Applied Science PhD Intern - Clipchamp" and two siblings
            # are doctoral-enrolment-gated (job_type.md:16), plus 2 PhD/postdoc
            # rows and 1 nurse row that were already untyped. Nothing else is
            # dropped -- there are no plain gaps.
            #
            # `coop` (4) and `student` (4) are unchanged: the old table already
            # carried those two rules from this same shared doc verbatim.
            #
            # STILL NO part_time rule. md:127-131 calls it **Undetectable** --
            # no employment-type field exists and `name` matches part-time on
            # 4 rows in 7,949 -- and the shared table has no part_time tier
            # either, so the bucket keeps no rule.
            #
            # md:93-102 warns that word boundaries MISS real interns here,
            # because Qualcomm joins title tokens with underscores
            # ("Intern_Systems") and `_` is a word character in POSIX regex, so
            # Postgres `\M` fails on them. That warning is about the SURVEY's
            # dialect and does not apply to this one: classify.matcher() wraps
            # patterns in an explicit `(?![a-z0-9])` lookaround, and `_` is not
            # in that class -- so the shared `intern(s|ship|ships)?` still
            # reaches all 7 underscore rows while rejecting the 38 Internal /
            # International ones. Do NOT add the underscore alternative md:100
            # prescribes; test_classify_rules.py pins both halves of this.
            "job_type": {"classify": _job_type_from("@name")},
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
            # DERIVED -- measured on vendors-tbd/bamboohr.md, and the corpus is only
            # 16 rows. Every count below is an existence statement, not a rate.
            "job_type": {"classify": [
                {"from": "@jobOpeningName", "re": "co-?ops?", "is": "coop"},  # 0, md:41, forward cover
                {"from": "@jobOpeningName", "re": "intern(s|ship|ships)?", "is": "intern"},  # 1, md:21
                # Student: enrolled, but not a fixed-term internship. Shared
                # across vendors from doc/raw_postings/regex/job_type.md -- the
                # intern and co-op rules above still win a title saying both.
                # The `not` list is the operator/gate cohort measured corpus-wide:
                # staff who serve students, roles that run the program, graduate
                # clinical rotations, trade pre-hire training, and nationality or
                # visa gates. A `not` hit skips this rule rather than ending the
                # scan, so those rows stay reachable by a later signal.
                {"from": "@jobOpeningName",
                 "re": "students?|apprentice(s|ship|ships)?",
                 "not": "student affairs|student success|student accounts"
                        "|student accountability|student engagement"
                        "|student collective|student event"
                        "|community (manager|coordinator)|editorial (lead|manager)"
                        "|marketing manager|program coordinator"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|externs?|externship|clerkship|student rotation"
                        "|pre.employment training|bpet"
                        "|nationals only|j1 students",
                 "is": "student"},
            ]},
            # "Part-Time Temporary" (2), md:45-47. NOT `description`: md:36-37
            # measures 0 hits on part-time there and 0 on intern (md:30-32).
            "part_time": {"classify": [
                {"from": "@employmentStatusLabel", "re": "part.?time", "is": "yes"},  # 2, md:46
            ], "default": "unknown"},
            "published_at": {"date": "@datePosted"},
            "published_at_precision": "date",
            "source_active": {"lookup": [
                {"Open": True, "Filled": False, "Canceled": False, "Deleted": False},
                "@jobOpeningStatus",
            ]},
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
            # not reproduce. 8 was the widest measured, still error-free; 4
            # since 2026-10-05, because `sources.width` divides the vendor cap
            # by this fan-out and most boards are one or two pages (1,203 of
            # 1,810 hold <= 100 jobs), so 8 bought little but cost width.
            # max 60 (12,000 jobs): at 30 jpmc's 7,347-job board truncated.
            "page": {"total": "@items.0.TotalJobsCount", "size": 200, "step": 200, "max": 60, "concurrency": 4},
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
            # width-8 re-probe note there, and why 4 since 2026-10-05.
            "concurrency": 4,
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
            #
            # The TABLE is the shared one in doc/raw_postings/regex/job_type.md,
            # rebound onto `Title`. It replaced the Oracle-measured table on
            # 2026-08-27 after a head-to-head over all 14,129 distinct postings
            # in raw_posting. Typed 222 rows before, 469 after -- the largest
            # gain of any vendor swapped so far -- and on the 213 both type they
            # agree on the label EVERY time, 0 disagreements.
            #
            # 218 of the 247 adds are ONE rule the old table did not carry:
            # `summer analysts?|summer associates?` (job_type.md:33), an
            # internship that never says "intern". Two investment-bank boards
            # spell their entire summer programme that way and were invisible
            # here -- hdpc (Morgan Stanley) 172 rows, jpmc 46, titles like
            # "2027 | APEJ | Sydney | Operations | Summer Analyst".
            #
            # The rest: 23 student from bare `students?` where the old table
            # had only `apprentices?` ("Student - Mfg", "Placement Student -
            # Customer Engineering", "School to Work - Engineering (Student)"),
            # 11 new_grad from `grad(uate|uates|s)?|ncg` where it needed an
            # exact phrase ("Fresh Graduate", "Recent Grad/Full Time"), and
            # 4 intern -> research, a refinement the old table had no tier for.
            #
            # Drops 5, all to a `not` list or the excluded tier: "Intern -
            # Pharmacy" and "AI&I/Data Science Intern (PhD in progress)" are
            # licensure- and doctoral-gated (job_type.md:14,16); three "Early
            # Careers Recruiter" / "Early Career Development Program Manager"
            # rows run the channel rather than enter it (job_type.md:71-72),
            # which the old table had no veto for and typed wrong.
            #
            # The 267 Internal / International rows md:26-27 calls the largest
            # such gap of any vendor stay rejected: the shared `intern` pattern
            # carries the same boundaries the measured one did.
            "job_type": {"classify": _job_type_from("@Title")},
            # From JobSchedule -- a closed two-value field, null on the 7,006 rows
            # of the one board that never sets it, which simply means no rule
            # fires (md:67-70). The title agrees on 925 of its own 942 hits, so
            # it adds nothing worth its false-positive surface (md:72).
            "part_time": {"classify": [
                {"from": "@JobSchedule", "re": "part.?time", "is": "yes"},  # 724, md:69
            ], "default": "unknown"},
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
            # concurrency: single shared host (api.rippling.com, behind
            # Cloudflare) for list and detail alike, so this and the detail
            # width below are the vendor-wide caps -- `net` keys a semaphore per
            # (host, width), so the two never exceed 8 + 24 = 32 in flight.
            # Rippling publishes no limit for the public board API. Measured
            # 2026-10-05 over ~12,000 requests: zero 429/403/challenges; list
            # flat at p50 0.11s from width 5 to 40 (~315 req/s); detail linear
            # to 32 (~215 req/s) and 6,000 sustained requests at 32 clean;
            # detail at 64 fell to ~90 req/s on server-side queueing. No ETag
            # and `no-store`, so every poll is a full (gzip, small) list.
            "concurrency": 8,
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
            # house ceiling rather than the probe's favourite. Raised to 24 by the
            # 2026-10-05 vendor-wide measurement on the list leg above.
            "concurrency": 24,
        },
        "fields": {
            "title": "@name",
            "url_posting": "@url",
            "company_raw": {"fallback": ["@companyName", "@board.title", "$key"]},
            "location_raw": {"fallback": [{"first": "@workLocations"}, {"first": "@locations"}, "@workLocation.label"]},
            "locations_extra": {"fallback": [{"rest": "@workLocations"}, {"rest": "@locations"}]},
            "description_html": {"fallback": [{"concat": ["@description.role", "@description.company"]}, "@description"]},
            "employment_type": "@employmentType.label",
            # DERIVED -- measured on vendors-tbd/rippling.md, 448 rows. Read the two
            # encoding traps at md:14 before changing anything here.
            "job_type": {"classify": [
                {"from": "@name", "re": "co-?ops?", "is": "coop"},  # 0, md:84, forward cover
                # Boundaries are the whole rule: 1 true positive against 2
                # International false positives is a 67% error rate without them
                # (md:56-59). NOT `employmentType` -- md:53-55 measures `TEMP` as
                # "Temporary / Intern", one enum value for two concepts, and says
                # outright not to map it to intern without a title check.
                {"from": "@name", "re": "intern(s|ship|ships)?", "is": "intern"},  # 1, md:54
                # Student: enrolled, but not a fixed-term internship. Shared
                # across vendors from doc/raw_postings/regex/job_type.md -- the
                # intern and co-op rules above still win a title saying both.
                # The `not` list is the operator/gate cohort measured corpus-wide:
                # staff who serve students, roles that run the program, graduate
                # clinical rotations, trade pre-hire training, and nationality or
                # visa gates. A `not` hit skips this rule rather than ending the
                # scan, so those rows stay reachable by a later signal.
                {"from": "@name",
                 "re": "students?|apprentice(s|ship|ships)?",
                 "not": "student affairs|student success|student accounts"
                        "|student accountability|student engagement"
                        "|student collective|student event"
                        "|community (manager|coordinator)|editorial (lead|manager)"
                        "|marketing manager|program coordinator"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|externs?|externship|clerkship|student rotation"
                        "|pre.employment training|bpet"
                        "|nationals only|j1 students",
                 "is": "student"},
            ]},
            # md:88-89. The alternation covers both encodings the survey shows:
            # the human label ("Salaried, part-time") and the bare code, since
            # md:60-63 warns `label` is null on 2 rows and the id string is the
            # documented fallback.
            "part_time": {"classify": [
                {"from": "@employmentType.label",
                 "re": "part.?time|salaried_pt|hourly_pt", "is": "yes"},  # 2, md:89
            ], "default": "unknown"},
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
            # concurrency: single shared host (jobs.gem.com) for every tenant,
            # so this is the vendor-wide list width. Measured 2026-10-05 over
            # ~12,000 requests: no 429s, no rate-limit headers and no ETag at
            # any width, but the Heroku backend queues past ~40 in flight --
            # list and detail both held ~200 req/s at p95 0.27s at 40 and fell
            # to ~87 req/s at p95 2.5s at 80. 20 here plus detail's 4 (one
            # semaphore shared by every gem feed) keeps the vendor at <= 24.
            "concurrency": 20,
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
            # DERIVED -- measured on vendors-tbd/gem.md, 236 rows. Read the corpus-size
            # warning at md:14 before trusting any count here.
            "job_type": {"classify": [
                # md:73-75 -- 0 observed; the doc's instruction is to carry the
                # boundary-anchored form from Greenhouse rather than invent one.
                {"from": "@title", "re": "co-?ops?", "is": "coop"},  # 0, md:74
                # `INTERN` is a first-class enum member and the doc prefers it
                # outright: it needs no false-positive handling (md:47-48). The
                # title regex agrees 4/4 and finds nothing the enum misses.
                {"from": "@job.employmentType", "re": "intern", "is": "intern"},  # 4, md:43
                # NOT `fellow`: md:53-54 measures `Governance & Policy Fellow` as
                # FULL_TIME and warns against folding it in on the strength of the word.
                {"from": "@title", "re": "intern(s|ship|ships)?", "is": "intern"},  # 4, md:56
                # NO part_time rule. md:77-80 is explicit: `employmentType` has no
                # PART_TIME value in 236 rows and the title matches on 0, so whether
                # the enum even defines the value is unknown. Do not assume it.
                # Student: enrolled, but not a fixed-term internship. Shared
                # across vendors from doc/raw_postings/regex/job_type.md -- the
                # intern and co-op rules above still win a title saying both.
                # The `not` list is the operator/gate cohort measured corpus-wide:
                # staff who serve students, roles that run the program, graduate
                # clinical rotations, trade pre-hire training, and nationality or
                # visa gates. A `not` hit skips this rule rather than ending the
                # scan, so those rows stay reachable by a later signal.
                {"from": "@title",
                 "re": "students?|apprentice(s|ship|ships)?",
                 "not": "student affairs|student success|student accounts"
                        "|student accountability|student engagement"
                        "|student collective|student event"
                        "|community (manager|coordinator)|editorial (lead|manager)"
                        "|marketing manager|program coordinator"
                        "|recruiters?|recruiting|recruitment|talent acquisition"
                        "|externs?|externship|clerkship|student rotation"
                        "|pre.employment training|bpet"
                        "|nationals only|j1 students",
                 "is": "student"},
            ]},
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
            # Every board shares api.smartrecruiters.com, so this is both the
            # worker-pool width (`sources.width`) and the vendor-wide cap on
            # list requests in flight -- `page` shares the same (host, 16)
            # semaphore in Net. The public Posting API is Cloudflare-fronted
            # and sends no X-RateLimit-* headers; the documented 10 req/s / 8
            # concurrent is the authenticated SmartAPI policy, per credential.
            # Measured 2026-10-05: 0 429s at 64 concurrent list requests
            # (~104 req/s), ~480ms flat latency. Without this, every board
            # ran one at a time with pages 2+ on Net's 1 req/s throttle.
            "concurrency": 16,
            # A fixed integer step makes pagination parallel-eligible. Measured
            # 2026-10-05: offset+100 across 40 boards / 330 pages returned no
            # short pages, gaps or duplicates, so the old `step: "count"`
            # (which forced sequential paging) bought nothing.
            "page": {"total": "@totalFound", "size": 100, "step": 100, "concurrency": 16},
        },
        "detail": {
            "url": "https://api.smartrecruiters.com/v1/companies/{key}/postings/{id}",
            "url_override": False,
            "id": "@id",
            "dedupe": "@id",
            "item_id": "@id",
            # Descriptions live only in the detail payload, so it's one
            # request per posting. Every feed shares one (host, 32) semaphore
            # in Net, so this is the vendor-wide detail cap, not a per-board
            # one -- and deliberately a different width from the list's 16 so
            # the two legs get separate semaphores: a cold 10k-posting board's
            # detail flood never queues other boards' list requests behind it.
            #
            # Measured 2026-10-05: 0 429s at 96 concurrent detail requests
            # (~125 req/s). 16 + 32 = 48 in flight (~100 req/s) leaves
            # headroom under that; _HostPace still backs the whole host off on
            # any 429. Sustained (minutes-long) load is unmeasured -- if 429s
            # show up in poll_log, halve both widths.
            "concurrency": 32,
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
                # ENGLISH-ONLY. Four non-English intern rules were dropped here:
                #   [a-z]*praktikum|[a-z]*praktikant[a-z]*   26, md:63  (de)
                #   werkstudent[a-z]*                        part of 23, md:65 (de)
                #   est[aá]gi[a-z]*                          12, md:68  (pt-BR)
                #   นักศึกษาฝึกงาน                            7, md:68  (th)
                # Their surveys in smartRecruiters.md are unchanged and still
                # measure these; restore from there if non-English is re-enabled.
                # `working student` stays -- it is the English rendering German
                # and Nordic tenants use when the title itself is in English.
                # The 23 at md:65 counted werkstudent and working student
                # together, so this rule is now unmeasured on its own.
                {"from": "@name", "re": "working student", "is": "intern"},
                # md:47 measures `new grad|graduate program|university|campus|
                # early career` as one number (118), so the bare
                # `university|campus` share is unmeasured -- and where iCIMS did
                # split it out, the bare pair is mostly retail-store and
                # recruiter noise (icms.md:52-54). Only the phrase forms are
                # transcribed; the count is this corpus, not md:47's union.
                {"from": "@name",
                 "re": "apprentices?|apprenticeships?",
                 "is": "student"},
                {"from": "@name",
                 "re": "new grads?|graduate programs?|early careers?",
                 "is": "new_grad"},  # 4, md:47-48
                # The German apprenticeship, and the largest single non-English
                # bucket in the corpus. AFTER the intern tokens above, not
                # before: "Praktikum - HR Ausbildung, Recruiting &
                # Personalentwicklung" is an internship *in* the training
                # department and must resolve as intern. `[a-z]*` for the
                # compound, as above -- "Berufsausbildung" is the dominant form
                # and a bare `ausbildung` misses every one of them.
                #
                # ENGLISH-ONLY: the German apprenticeship rule
                # `[a-z]*ausbildung` -> `student` (84, md:66) was dropped.
                # md:66 counts `ausbildung|abschlussarbeit|referendar` together
                # (90); the other two tracks -- thesis (3) and legal clerkship
                # (1) -- were never transcribed and stay unread. smartRecruiters.md
                # still measures all three; restore from there if non-English
                # is re-enabled.
            ]},
            # From the enum only. `name` is DO NOT USE -- 246 hits, 107 of them
            # typeOfEmployment=permanent, because "Full/Part time" advertises
            # *either* schedule (md:98-100); so is jobDescription, 748 of its
            # 1,221 hits being permanent rows (md:104-110). Trust the field, not
            # the corpus rate: 4,846 of the 4,877 are dominos (md:88-90).
            "part_time": {"classify": [
                {"from": "@typeOfEmployment.id", "re": "part.?time", "is": "yes"},  # 4,877, md:91
            ], "default": "unknown"},
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
