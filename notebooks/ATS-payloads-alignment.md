# ATS field alignment

Every field from [ATS-endpoints.md](ATS-endpoints.md), aligned so that **one row = one concept**. A cell holds that ATS's native key name; `·` means the source doesn't provide it. Dotted paths mean nesting (`location.city`). Fields marked ⚠ are false friends — same name, different meaning across sources.

**Stored where:** ✅ dedicated `posting` column · 📦 JSONB (`source_extra` / `locations_extra` / `content_sections`) · 🏷 also feeds a derived `posting_label` · ❌ dropped

Split into two tables purely for width; the row spine is identical, so row *n* is the same concept in both.

---

## Table A — Greenhouse · Ashby · Lever · Workday · SmartRecruiters · Workable · Rippling

| # | Concept | Greenhouse | Ashby | Lever | Workday | SmartRecruiters | Workable | Rippling |
|---|---|---|---|---|---|---|---|---|
| | **IDENTITY** | | | | | | | |
| 1 | ✅ Primary job id | `id` | `id` | `id` | `jobPostingInfo.id` | `id` | `shortcode` | `uuid` |
| 2 | ✅ Secondary id | `internal_job_id` | · | · | `jobPostingId` | `uuid`, `jobAdId`, `jobId` | · | `id` (legacy) |
| 3 | ✅ Requisition / ref no | `requisition_id` | · | · | `jobReqId` | `refNumber` | `code` | · |
| 4 | 📦 Slug / friendly id | · | · | · | `externalPath` | · | · | `board.slug` |
| 5 | ✅ Content hash | · | · | · | · | · | · | · |
| 6 | ✅ Company identifier | `company_name` | · | · | `jobPostingSiteId` | `company.identifier` | · | `board.slug` |
| | **TITLE & COMPANY** | | | | | | | |
| 7 | ✅ Title | `title` | `title` | `text` | `title` | `name` | `title` | `name` |
| 8 | 📦 Alternate title | · | · | · | · | · | · | · |
| 9 | ✅ Company name | `company_name` | · | · | `hiringOrganization.name` ⚠ | `company.name` | `name` | `companyName` |
| 10 | 📦 Company description | · | · | · | · | `jobAd…companyDescription` | `description` ⚠ | `description.company` |
| 11 | ❌ Company logo | · | · | · | · | · | · | `board.logo.url` ⚠ |
| | **DESCRIPTION** | | | | | | | |
| 12 | ✅ Description HTML | `content` ⚠ | `descriptionHtml` | `description` | `jobDescription` | `jobAd…jobDescription` | `description` | `description.role` |
| 13 | ✅ Description plain | · | `descriptionPlain` | `descriptionPlain` | · | · | · | · |
| 14 | 📦 Short description | · | · | · | · | · | · | · |
| 15 | 📦 Qualifications | · | · | · | · | `jobAd…qualifications` | · | · |
| 16 | 📦 Responsibilities | · | · | · | · | · | · | · |
| 17 | 📦 Additional / benefits | · | · | `additional`, `additionalPlain` | · | `jobAd…additionalInformation` | · | · |
| 18 | 📦 Structured sections | · | · | `lists[].text/.content` | · | `jobAd.sections` | · | · |
| 19 | 📦 Opening blurb | · | · | `opening`, `openingPlain` | · | · | · | · |
| | **URLS** | | | | | | | |
| 20 | ✅ Hosted posting URL | `absolute_url` | `jobUrl` | `hostedUrl` | `externalUrl` | `postingUrl` | `url`, `shortlink` | `url` |
| 21 | ✅ Apply URL | `absolute_url` | `applyUrl` | `applyUrl` | · | `applyUrl` | `application_url` | · |
| 22 | 📦 Referral URL | · | · | · | · | `referralUrl` | · | · |
| 23 | ✅ Apply email | · | · | · | · | · | · | · |
| 24 | 📦 Detail path fragment | · | · | · | `externalPath` | `ref` | · | · |
| | **CLASSIFICATION** | | | | | | | |
| 25 | ✅ Department | `departments[].name` | `department` | `categories.department` | · | `department.label` | `department` | `department.label` |
| 26 | ❌ Department hierarchy | `departments[].parent_id/child_ids` | · | · | · | · | · | `department.department_tree` |
| 27 | ✅ Team | · | `team` | `categories.team` | · | · | · | · |
| 28 | 📦 Division / business unit | · | · | · | · | · | · | `department.base_department` |
| 29 | 📦 Job family | · | · | · | facet `jobFamilyGroup` | · | · | · |
| 30 | 📦 Job function | · | · | · | · | `function.label` | `function` | · |
| 31 | 📦 Category | · | · | · | · | · | · | · |
| 32 | 📦 Industry | · | · | · | · | `industry.label` | `industry` | · |
| 33 | 📦🏷 Experience level | · | · | · | · | `experienceLevel.label` | `experience` | · |
| 34 | 📦🏷 Education level | `education` ⚠ | · | · | · | · | `education` ⚠ | · |
| 35 | 📦 Job level / grade | · | · | · | · | · | · | · |
| 36 | 📦🏷 Skills | · | · | · | · | · | · | · |
| 37 | 📦 Custom fields / tags | `metadata[]` | · | · | `bulletFields` | `customField[]` | · | · |
| | **EMPLOYMENT** | | | | | | | |
| 38 | ✅ Employment type | · | `employmentType` | `categories.commitment` | `timeType` | `typeOfEmployment.label` | `employment_type` | `employmentType.label` |
| 39 | 📦 Schedule | · | · | · | `timeType` | · | · | · |
| 40 | 📦 Shift | · | · | · | · | · | · | · |
| 41 | 📦 Contract type | · | · | · | facet `workerSubType` | · | · | · |
| 42 | 📦🏷 Worker type | · | · | · | facet `workerSubType` | · | · | · |
| 43 | 📦 Number of openings | · | · | · | · | · | · | · |
| 44 | 📦 Travel required | · | · | · | · | · | · | · |
| 45 | 📦 Work hours / days | · | · | · | · | · | · | · |
| | **LOCATION** | | | | | | | |
| 46 | ✅ Location (display) | `location.name` | `location` | `categories.location` | `locationsText` | `location.fullLocation` | · | `workLocation.label` |
| 47 | ✅ City | · | `address…addressLocality` | · | · | `location.city` | `city` | · |
| 48 | ✅ State / region | · | `address…addressRegion` | · | · | `location.region` | `state` | `locations[].state` |
| 49 | ✅ Country | · | `address…addressCountry` | `country` | `country.descriptor` | `location.country` | `country` | `locations[].country` |
| 50 | ✅ Country code | · | · | · | `…alpha2Code` | · | `locations[].countryCode` | `locations[].countryCode` |
| 51 | 📦 Postal code | · | · | · | · | · | · | · |
| 52 | 📦 Street address | · | · | · | · | · | · | · |
| 53 | 📦 Latitude / longitude | · | · | · | · | `location.latitude/longitude` | · | · |
| 54 | 📦 Timezone | · | · | · | · | · | · | · |
| 55 | 📦 Secondary locations | `offices[]` | `secondaryLocations[]` | `categories.allLocations` | · | · | `locations[]` | `workLocations[]` |
| 56 | ❌ Multi-location flag | · | · | · | · | · | · | · |
| 57 | ✅ Remote flag | · | `isRemote` | · | · | `location.remote`, `.hybrid` | `telecommuting` | · |
| 58 | ✅ Workplace type | · | `workplaceType` | `workplaceType` | facet `workplaceType` | · | · | `locations[].workplaceType` |
| | **TIME** | | | | | | | |
| 59 | ✅ Published / posted | `first_published` | `publishedAt` | `createdAt` ⚠ | `postedOn` ⚠ | `releasedDate` | `published_on` | `createdOn` |
| 60 | 📦 Created | · | · | `createdAt` | · | · | `created_at` | `createdOn` |
| 61 | ✅ Updated | `updated_at` | · | · | · | · | · | · |
| 62 | 📦 Posting start | · | · | · | · | · | · | · |
| 63 | ✅ Deadline / end | `application_deadline` | · | · | · | · | · | · |
| 64 | 📦🏷 Job start date | · | · | · | `startDate` | · | · | · |
| | **COMPENSATION** | | | | | | | |
| 65 | ✅ Salary min | · | `…minValue` | · | · | · | · | `payRangeDetails[]` |
| 66 | ✅ Salary max | · | `…maxValue` | · | · | · | · | `payRangeDetails[]` |
| 67 | ✅ Salary summary string | · | `compensationTierSummary` | · | · | · | · | · |
| 68 | ✅ Currency | · | `…currencyCode` | · | · | · | · | · |
| 69 | ✅ Interval | · | `…interval` | · | · | · | · | · |
| 70 | 📦 Comp components / tiers | · | `compensationTiers[]`, `summaryComponents[]` | · | · | · | · | · |
| 71 | ❌ Show-comp flag | · | `shouldDisplayCompensationOnJobPostings` | · | · | · | · | · |
| | **STATUS & FLAGS** | | | | | | | |
| 72 | ✅ Active / listed | · | `isListed` | · | `posted` | `active` | · | `unlistedFromSearch` |
| 73 | ✅ Status | · | · | · | · | · | · | · |
| 74 | 📦 Visibility | · | · | · | · | `visibility` | · | · |
| 75 | ✅ Internal / external | · | · | · | · | · | · | · |
| 76 | 📦 Applyable | · | · | · | `canApply` | · | · | · |
| 77 | ✅ Language | `language` | · | · | · | `language.code` | · | `language` (legacy) |
| 78 | 📦 Hot / trending | · | · | · | · | · | · | · |
| 79 | 📦 AI screening flag | `ai_disclaimer`, `ai_opt_out_request_url` | · | · | · | · | · | `hasAIEvaluationsEnabled` |
| 80 | ❌ EEO / compliance | `data_compliance`, `compliance` | · | · | · | · | · | `eeocQuestionnaireEnabled` |
| 81 | 📦 Screening questions | `?questions=true` | · | · | `questionnaireId` | · | · | `activeJobApplication.additionalQuestions` |
| | **ENVELOPE** | | | | | | | |
| 82 | ✅ Total count | · | · | · | `total` | `totalFound` | · | · |
| 83 | ❌ Pagination | · | · | `limit`/`skip` | `offset`/`limit` | `offset`/`limit` | · | · |
| 84 | ❌ Facets | · | · | · | `facets[]` | · | · | · |

---

## Table B — Jobvite · iCIMS · Oracle ORC · BambooHR · Breezy · Comeet · Teamtailor

| # | Concept | Jobvite | iCIMS | Oracle ORC | BambooHR | Breezy | Comeet | Teamtailor |
|---|---|---|---|---|---|---|---|---|
| | **IDENTITY** | | | | | | | |
| 1 | ✅ Primary job id | `identifier` | `slug` | `Id` | `id` | `id` | `uid` | `guid` |
| 2 | ✅ Secondary id | · | `id`, `uuid` | `ContentItemId` | · | · | · | · |
| 3 | ✅ Requisition / ref no | · | `req_id` | `RequisitionId` | · | · | `internal_use_custom_id` | · |
| 4 | 📦 Slug / friendly id | · | `slug` | · | · | `friendly_id` | · | · |
| 5 | ✅ Content hash | · | `ats_job_hash` | · | · | · | · | · |
| 6 | ✅ Company identifier | · | `client_code` | `SiteNumber` | · | `company.friendly_id` | `company_name` | · |
| | **TITLE & COMPANY** | | | | | | | |
| 7 | ✅ Title | `title` | `title` | `Title` | `jobOpeningName` | `name` | `name` | `<title>` |
| 8 | 📦 Alternate title | · | · | `OtherRequisitionTitle` | · | · | · | · |
| 9 | ✅ Company name | `hiringOrganization` | `hiring_organization` | `LegalEmployer` ⚠ | · | `company.name` | `company_name` | · |
| 10 | 📦 Company description | · | · | `CorporateDescriptionStr` | · | · | · | · |
| 11 | ❌ Company logo | · | · | `MediaThumbURL` | · | `company.logo_url` | `picture_url` | · |
| | **DESCRIPTION** | | | | | | | |
| 12 | ✅ Description HTML | `description` | `description` | `ExternalDescriptionStr` | `description` | `description` | `details[].value` | `<description>` |
| 13 | ✅ Description plain | · | · | · | · | · | · | · |
| 14 | 📦 Short description | · | · | `ShortDescriptionStr` | · | · | · | · |
| 15 | 📦 Qualifications | · | `qualifications` | `ExternalQualificationsStr` | · | · | `details[]` | · |
| 16 | 📦 Responsibilities | · | `responsibilities` | `ExternalResponsibilitiesStr` | · | · | `details[]` | · |
| 17 | 📦 Additional / benefits | · | · | `OrganizationDescriptionStr` | · | · | `details[]` | · |
| 18 | 📦 Structured sections | · | · | `requisitionFlexFields[]` | · | · | `details[].name/.order` | · |
| 19 | 📦 Opening blurb | · | · | · | · | · | · | · |
| | **URLS** | | | | | | | |
| 20 | ✅ Hosted posting URL | *(page URL)* | `meta_data.canonical_url` | *(constructed)* | `jobOpeningShareUrl` | `url` | `url_comeet_hosted_page` | `<link>` |
| 21 | ✅ Apply URL | · | `apply_url` | · | · | · | `url_active_page` | · |
| 22 | 📦 Referral URL | · | · | · | · | · | `url_recruit_hosted_page` | · |
| 23 | ✅ Apply email | · | · | `ExternalContactEmail` | · | · | `email`, `email_alias` | · |
| 24 | 📦 Detail path fragment | · | · | `Id` | `id` | · | `position_url` | · |
| | **CLASSIFICATION** | | | | | | | |
| 25 | ✅ Department | · | `department` | `Department` | `departmentLabel` | `department` | `department` | `tt:department` |
| 26 | ❌ Department hierarchy | · | · | `Organization`, `OrganizationId` | `departmentId` | · | · | · |
| 27 | ✅ Team | · | · | · | · | · | · | `tt:role` |
| 28 | 📦 Division / business unit | · | `brands` | `BusinessUnit`, `LegalEmployer` | · | · | · | `tt:division` |
| 29 | 📦 Job family | · | · | `JobFamily`, `JobFamilyId` | · | · | · | · |
| 30 | 📦 Job function | · | · | `JobFunction`, `JobFunctionCode` | · | · | · | · |
| 31 | 📦 Category | `industry` | `categories[]`, `category[]` | `Category` | `jobCategoryId` | · | `categories[]` | · |
| 32 | 📦 Industry | `industry` | · | · | · | · | · | · |
| 33 | 📦🏷 Experience level | · | `experienceLevels` | `JobLevel` | `minimumExperience` | · | `experience_level` | · |
| 34 | 📦🏷 Education level | · | · | `StudyLevel` | · | · | · | · |
| 35 | 📦 Job level / grade | · | · | `JobGrade`, `ManagerLevel` | · | · | · | · |
| 36 | 📦🏷 Skills | · | · | `skills[].Skill/.SkillType` | · | · | · | · |
| 37 | 📦 Custom fields / tags | · | `tags1`–`tags4` | `requisitionFlexFields[]` | · | · | `categories[].name/.value` | · |
| | **EMPLOYMENT** | | | | | | | |
| 38 | ✅ Employment type | `employmentType` | `employment_type` | `JobType` | `employmentStatusLabel` | `type.name` | `employment_type` | · |
| 39 | 📦 Schedule | · | · | `JobSchedule` | · | · | · | · |
| 40 | 📦 Shift | · | · | `JobShift` | · | · | · | · |
| 41 | 📦 Contract type | · | · | `ContractType` | · | · | · | · |
| 42 | 📦🏷 Worker type | · | · | `WorkerType`, `RequisitionType` | · | · | · | · |
| 43 | 📦 Number of openings | · | · | `NumberOfOpenings` | · | · | · | · |
| 44 | 📦 Travel required | · | · | `DomesticTravelRequired`, `InternationalTravelRequired` | · | · | · | · |
| 45 | 📦 Work hours / days | · | · | `WorkHours`, `WorkDays`, `WorkMonths`, `WorkYears` | · | · | · | · |
| | **LOCATION** | | | | | | | |
| 46 | ✅ Location (display) | · | `full_location`, `short_location` | `PrimaryLocation` | · | `location.name` | `location.name` | `tt:location.tt:name` |
| 47 | ✅ City | `…addressLocality` | `city` | · | `location.city` | `location.city` | `location.city` | `tt:city` |
| 48 | ✅ State / region | `…addressRegion` | `state` | · | `location.state` | · | `location.state` | · |
| 49 | ✅ Country | `…addressCountry` | `country` | `PrimaryLocationCountry` | `location.addressCountry` | `location.country.name` | `location.country` | `tt:country` |
| 50 | ✅ Country code | · | `country_code` | `WorkLocationCountryCode`, `CountryCode` | · | `location.country.id` | · | · |
| 51 | 📦 Postal code | · | `postal_code` | `WorkLocationZipCode` | `location.postalCode` | · | `location.postal_code` | `tt:zip` |
| 52 | 📦 Street address | · | `street_address` | · | · | · | `location.street_name/_number` | `tt:address` |
| 53 | 📦 Latitude / longitude | · | `latitude`, `longitude` | `Latitude`, `Longitude`, `primaryLocationCoordinates` | · | · | · | · |
| 54 | 📦 Timezone | · | · | · | · | · | `location.timezone` | · |
| 55 | 📦 Secondary locations | `jobLocation[]` | `additional_locations[]` | `secondaryLocations[]`, `otherWorkLocations` | · | `locations[]` | · | `tt:locations[]` |
| 56 | ❌ Multi-location flag | · | `multipleLocations` | · | · | `company.isMultipleLocationsEnabled` | · | · |
| 57 | ✅ Remote flag | · | `tags3` | · | `isRemote` | `location.is_remote` | `location.is_remote` | `remoteStatus` |
| 58 | ✅ Workplace type | · | `location_type` | `WorkplaceType`, `WorkplaceTypeCode` | `locationType` | · | `workplace_type` | `remoteStatus` |
| | **TIME** | | | | | | | |
| 59 | ✅ Published / posted | `datePosted` | `posted_date` | `PostedDate` | `datePosted` | `published_date` | · ⚠ | `<pubDate>` |
| 60 | 📦 Created | · | `create_date` | · | · | · | · | · |
| 61 | ✅ Updated | · | `update_date`, `last_mod`, `date_updated` | · | · | · | `time_updated` | · |
| 62 | 📦 Posting start | · | · | `PostingStartDate`, `ExternalPostedStartDate` | · | · | · | · |
| 63 | ✅ Deadline / end | · | · | `PostingEndDate`, `ExternalPostedEndDate` | · | · | · | · |
| 64 | 📦🏷 Job start date | · | · | · | · | · | · | · |
| | **COMPENSATION** | | | | | | | |
| 65 | ✅ Salary min | `baseSalary…minValue` | `salary_min_value` | · | · | · | · | · |
| 66 | ✅ Salary max | `baseSalary…maxValue` | `salary_max_value` | · | · | · | · | · |
| 67 | ✅ Salary summary string | · | `salary_value` | · | `compensation` | `salary` | · | · |
| 68 | ✅ Currency | `baseSalary.currency` | · | · | · | · | · | · |
| 69 | ✅ Interval | `baseSalary…unitText` | · | · | · | · | · | · |
| 70 | 📦 Comp components / tiers | · | · | · | · | · | · | · |
| 71 | ❌ Show-comp flag | · | · | · | · | · | · | · |
| | **STATUS & FLAGS** | | | | | | | |
| 72 | ✅ Active / listed | · | `searchable` | `ApplyWhenNotPostedFlag` | `jobOpeningStatus` | · | · | · |
| 73 | ✅ Status | · | · | · | `jobOpeningStatus` | · | · | · |
| 74 | 📦 Visibility | · | `jps_is_public` | · | · | · | · | · |
| 75 | ✅ Internal / external | · | `internal`, `external` | `InternalQualificationsStr`, `InternalResponsibilitiesStr` | · | · | `is_internal` | · |
| 76 | 📦 Applyable | · | `applyable`, `li_easy_applyable` | · | · | · | · | · |
| 77 | ✅ Language | · | `language`, `languages` | `Language`, `ContentLocale` | · | · | · | · |
| 78 | 📦 Hot / trending | · | `isFrontLineAIJob` | `HotJobFlag`, `TrendingFlag`, `BeFirstToApplyFlag` | `seekPromoted` | · | · | · |
| 79 | 📦 AI screening flag | · | `isFrontLineAIJob` | · | · | · | · | · |
| 80 | ❌ EEO / compliance | · | `gdpr` | · | · | · | `is_consent_needed`, `is_sms_consent_needed` | · |
| 81 | 📦 Screening questions | · | `meta_data.questionservice.id` | · | · | · | · | · |
| 82 | 📦 Hiring manager | · | · | `HiringManager`, `ExternalContactName` | · | · | · | · |
| 83 | ❌ Referral reward | · | · | · | · | · | `referrals_reward`, `is_reward` | · |
| | **ENVELOPE** | | | | | | | |
| 84 | ✅ Total count | · | `totalCount`, `numJobs` | `TotalJobsCount`, `TotalCount` | · | · | · | · |
| 85 | ❌ Pagination | · | `page` | `limit`, `offset`, `hasMore` | · | · | · | · |
| 86 | ❌ Facets | · | `facetList` | `Facets`, `*Facet` | · | · | · | · |

---

## ⚠ False friends

| Field | Looks like | Actually is |
|---|---|---|
| `education` (Greenhouse) | degree requirement | application-form config — "form has a required education section". Workable's `education` **is** a real degree requirement. |
| `content` (Greenhouse) | HTML | **double-escaped** HTML (`&lt;h2&gt;`) — unescape exactly once |
| `postedOn` (Workday) | timestamp | human string: `"Posted Today"` |
| `createdAt` (Lever) | ISO timestamp | epoch **milliseconds** |
| `hiringOrganization.name` (Workday) | brand | payroll entity — `"2100 NVIDIA USA"` |
| `LegalEmployer` (Oracle) | brand | legal entity, same trap |
| `description` (Workable, top level) | job description | **company** description; the job one is `jobs[].description` |
| `board.logo.url` (Rippling) | stable URL | signed URL, `Expires`/`Signature` rotate every request |
| Comeet publish date | exists | **doesn't** — only `time_updated` |

## Volatile — exclude from `content_hash`

`board.logo.url` (Rippling) · `meta_data.opensearch.*`, `last_mod` (iCIMS) · `Relevancy`, `SearchId`, `*Facet` counts (Oracle) · `postedOn` (Workday, rolls daily)

## Coverage counts

| Source | Fields | Publish precision | Active flag | Structured comp | Detail call |
|---|---|---|---|---|---|
| Greenhouse | 28 | exact | — | — | optional |
| Ashby | 35 | exact | `isListed` | ✅ full | none |
| Lever | 23 | exact (epoch ms) | — | — | none |
| Workday | 34 | **none** | `posted` | — | required |
| SmartRecruiters | 53 | exact | `active` | — | required |
| Workable | 25 | date only | — | — | inline |
| Rippling | 73 | exact | `unlistedFromSearch` | partial | required |
| Jobvite | 18 | date only | — | ✅ (often empty) | scrape |
| iCIMS | 101 | exact | `searchable` | ✅ (often `0`) | none |
| Oracle ORC | 140 | exact | `PostingEndDate` | — | required |
| BambooHR | 24 | date only | `jobOpeningStatus` | free string | required |
| Breezy | 18 | exact | — | free string | none |
| Comeet | 40 | **none** | — | — | inline |
| Teamtailor | 12 | exact | — | — | inline |
