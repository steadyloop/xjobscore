# Non-ATS sources

Same format as [ATS-endpoints.md](ATS-endpoints.md). Ordered by ROI, items 1–5.
All samples below were fetched live on **2026-07-30** unless marked otherwise.

---

# 1. GitHub internship lists
**Pattern**: `https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json`
* Plain unauthenticated GET on a raw file — no API, no key, no pagination, no rate limit worth worrying about.
* **14,627 records** in one 200 response; filter `terms` for `"Summer 2027"` → 511, of which **152 are `active: true`**.
* `url` points at the **real ATS posting** (Workday, Lever, iCIMS…), so this doubles as *board discovery*: it names companies you haven't registered yet.
* `date_posted`/`date_updated` are **Unix epoch seconds**; `active: false` means the maintainers saw it delist — a genuine closure signal you get for free.
* `sponsorship` is present but ~99% `"Other"` (14,465 of 14,627), so treat the three real values as high-signal and the rest as unknown.
* Mirrors with the same schema: `vanshb03/Summer2027-Internships`, `cvrve/Summer2027-Internships`.
* Category counts for 2027: Software 159, AI/ML/Data 150, **Quant 100**, Hardware 68.

<details><summary>Sample Outputs</summary>

GET .../listings.json (one active element)
---
```json
{
  "source": "Simplify",
  "category": "Software",
  "company_name": "Palantir",
  "id": "ada5c220-536e-454a-8ba0-1f7629d949e6",
  "title": "Forward Deployed Software Engineer – Internship - US Government",
  "active": true,
  "terms": [
    "Winter 2027",
    "Spring 2027",
    "Summer 2027",
    "Fall 2027"
  ],
  "date_updated": 1765581501,
  "date_posted": 1765581501,
  "url": "https://jobs.lever.co/palantir/315f695d-04d1-4a9a-848e-cb2bec7a997e/apply",
  "locations": [
    "Honolulu, HI"
  ],
  "company_url": "https://simplify.jobs/c/Palantir",
  "is_visible": true,
  "sponsorship": "Other",
  "degrees": []
}
```
Distinct `sponsorship` values across all 14,627
---
```
Other                          14465
Does Not Offer Sponsorship        90
Offers Sponsorship                39
U.S. Citizenship is Required      33
```
Quant hits it surfaced that a pure-ATS crawl would miss
---
```
Susquehanna International Group (SIG) | Quantitative Trader Intern
  https://careers-sig.icims.com/jobs/10717/job
Susquehanna International Group (SIG) | Quantitative Research Intern - PhD: Summer 2027
  https://careers-sig.icims.com/jobs/10817/job
```
</details>

<BR>

# 2. schema.org JSON-LD scraper
**Pattern**: `GET {careers_url}` → parse `<script type="application/ld+json">` → keep blocks where `@type == "JobPosting"`
* One adapter, unbounded company coverage; this is the same mechanism the Jobvite adapter already uses.
* **Verify before trusting.** Most careers pages carry JSON-LD that is *not* a JobPosting — Hudson River Trading's page has exactly one `ld+json` block and it's Yoast SEO `WebPage`/`@graph` boilerplate with zero job data (negative result below).
* Handle `@graph` wrappers: the block may be `{"@context":…, "@graph":[…]}` rather than a bare object, so walk the graph rather than reading `d["@type"]`.
* Pairs with sitemap discovery (`/sitemap.xml` → filter `<loc>` for `/careers/`, `/jobs/`) to find pages worth fetching.
* Fields when present are the schema.org set: `title`, `description`, `datePosted`, `validThrough`, `employmentType`, `hiringOrganization`, `jobLocation`, `baseSalary`.

<details><summary>Sample Outputs</summary>

Positive — JSON-LD extracted from a Jobvite-hosted page (see ATS-endpoints.md for the full block)
---
```json
{
  "@context": "http://schema.org",
  "@type": "JobPosting",
  "datePosted": "2026-07-15",
  "title": "VP, Corporate FP&A",
  "hiringOrganization": "Egnyte",
  "employmentType": "FT - Full Time",
  "identifier": "o16vAfwW",
  "jobLocation": [
    {
      "@type": "Place",
      "address": {
        "@type": "PostalAddress",
        "addressLocality": "Mountain View",
        "addressRegion": "California",
        "addressCountry": "United States"
      }
    }
  ],
  "baseSalary": {
    "@type": "MonetaryAmount",
    "currency": "USD",
    "value": { "@type": "QuantitativeValue", "minValue": "", "maxValue": "", "unitText": "" }
  }
}
```
Negative — hudsonrivertrading.com/careers/ has `ld+json` but no JobPosting
---
```json
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "WebPage",
      "@id": "https://www.hudsonrivertrading.com/careers/",
      "url": "https://www.hudsonrivertrading.com/careers/",
      "name": "Careers | Hudson River Trading",
      "isPartOf": { "@id": "https://www.hudsonrivertrading.com/#website" },
      "datePublished": "2025-11-24T18:56:42+00:00"
    }
  ]
}
```
</details>

<BR>

# 3. Bespoke quant career sites
**Pattern**: per-firm; probe order is **hidden JSON endpoint → JSON-LD → HTML selectors**
* Method: open the careers page with devtools on Network/XHR, or grep the page's bundled JS for `.json` / `/api/` paths. Jane Street was found by grepping `/assets/pg/open_positions-*.js`.
* Results of probing the anchor tier on 2026-07-30:

| Firm | Result | Endpoint |
|---|---|---|
| **Jane Street** | ✅ **solved** | `janestreet.com/jobs/main.json` (220 roles, full HTML) and `/jobs/internships.json` (46, summary) |
| Hudson River Trading | ❌ page 200, JSON-LD is Yoast boilerplate; no Greenhouse board (`hudsonrivertrading`, `hrt` both 404) | — |
| Citadel Securities | ❌ **403** to a plain UA — bot-blocked | — |
| Jump Trading | ✅ already on Greenhouse | `boards-api.greenhouse.io/v1/boards/jumptrading/jobs` |
| IMC, Akuna, Flow Traders | ✅ already on Greenhouse | `.../boards/{imc,akunacapital,flowtraders}/jobs` |
| SIG | ✅ on iCIMS (surfaced via source #1) | `careers-sig.icims.com` |

* ⚠ **Jane Street obfuscates some titles with Unicode homoglyphs.** `"ꓟachine ꓡearning ꓣesearcher"` uses U+A4DF/U+A4E1/U+A4E3 (Lisu letters MA/LA/ZHA) in place of `M`/`L`/`R`. NFKD does **not** fold these — `title_normalized` needs a Unicode TR39 confusables mapping or those postings will never match `role_title_variant`.
* Jane Street `id` values (`8596771002`) are Greenhouse-shaped, so the site is likely a Greenhouse front-end — but the board token is not public, and this JSON is the supported path.
* `min_salary`/`max_salary` exist but come back as the **string** `"None"`, not null.

<details><summary>Sample Outputs</summary>

GET https://www.janestreet.com/jobs/main.json (one internship element, overview trimmed)
---
```json
{
  "id": "8596771002",
  "position": "ꓟachine ꓡearning ꓣesearcher",
  "category": "Trading, Research, and Machine Learning",
  "availability": "Summer Internship",
  "city": "HKG",
  "team": "Machine Learning",
  "duration": "May-August",
  "min_salary": "None",
  "max_salary": "None",
  "overview": "<p><strong>About the position</strong></p>\n<p>Our goals are to give you a real sense of what it&#39;s like to work at Jane Street as a Machine Learning Researcher while also providing a truly unparalleled educational experience. You&#39;ll work side by side with experienced ML Researchers on projects that we&#39;ve selected for their combination of novel ML ideas and relevance to real-world systematic trading strategies…"
}
```
GET https://www.janestreet.com/jobs/internships.json (one element — summary only, no id)
---
```json
{
  "position": "Cybersecurity Analyst",
  "location": "NYC",
  "department": "Cybersecurity",
  "team": "Cybersecurity",
  "duration": "May-August",
  "type": "Internship",
  "status": "closed"
}
```
Note the two files disagree in shape: `main.json` has `id`/`city`/`availability`, `internships.json` has `location`/`type`/`status`.
Use `main.json` as the source of truth and `internships.json` only for its `status` field.
</details>

<BR>

# 4. Gmail job alerts
**Pattern**: Gmail API `users.messages.list?q={query}` → `users.messages.get?format=full` → parse `payload.parts[].body.data` (base64url)
* The only **pushed** source — reaches you for boards you never registered.
* Useful queries: `from:jobs-noreply@linkedin.com newer_than:7d`, `from:notifications@joinhandshake.com`, `subject:"new jobs for"`.
* Body arrives base64url-encoded per MIME part; prefer `text/html`, fall back to `text/plain`.
* Links are wrapped in tracking redirects, so resolve them before dedup — this is exactly cross-ATS strategy #2 (embedded ATS id) and #3 (redirect convergence).
* Treat every extracted posting as a **discovery hint**: re-fetch from the real ATS rather than trusting the digest, since alert emails omit close dates and are often stale by days.
* Not executed here — the shape below is the documented API envelope, not your mailbox.

<details><summary>Sample Outputs</summary>

Shape of `users.messages.get?format=full`
---
```json
{
  "id": "19a2f7c8b1e4d203",
  "threadId": "19a2f7c8b1e4d203",
  "labelIds": ["INBOX", "CATEGORY_UPDATES"],
  "internalDate": "1785493200000",
  "payload": {
    "mimeType": "multipart/alternative",
    "headers": [
      { "name": "From", "value": "LinkedIn Job Alerts <jobs-noreply@linkedin.com>" },
      { "name": "Subject", "value": "30+ new jobs for \"software engineer intern\"" },
      { "name": "Date", "value": "Wed, 29 Jul 2026 14:20:00 +0000" }
    ],
    "parts": [
      { "mimeType": "text/plain", "body": { "size": 4210, "data": "<base64url>" } },
      { "mimeType": "text/html",  "body": { "size": 88134, "data": "<base64url>" } }
    ]
  }
}
```
</details>

<BR>

# 5. YC Work at a Startup
**Pattern**: `POST https://www.workatastartup.com/companies/fetch` (session-authenticated)
* **Not solved unauthenticated.** Probed 2026-07-30: `POST /companies/fetch` with an empty body → **422**; `GET /companies/fetch?page=1` → **302** to login. A session cookie is required.
* `ycombinator.com/jobs` returns 200 but exposes no JobPosting JSON-LD.
* Practical approach: authenticate once, store the cookie in `board.config`, and treat the board as best-effort — expect `consecutive_failures` to climb whenever the session expires.
* Lower priority than it first appears: several of your neocloud/inference targets (Together, Fireworks) already have public Greenhouse boards, confirmed earlier.

<details><summary>Sample Outputs</summary>

Unauthenticated probe results
---
```
POST /companies/fetch  {}          -> 422 Unprocessable Entity (HTML error page)
GET  /companies/fetch?page=1       -> 302 (redirect to login)
GET  https://www.ycombinator.com/jobs -> 200, no application/ld+json JobPosting
```
</details>

---

## Schema implications

| Finding | Consequence |
|---|---|
| #1 has one feed → many companies | `board.company_id` must be **nullable**; company resolved per posting via `company.aliases` |
| #1 `date_posted` is epoch seconds | `published_at_precision = 'exact'`, but it's the *aggregator's* observation, not the employer's — arguably `date` |
| #1 `active: false` | A real closure signal from a source that isn't the ATS — `closed_reason = 'source_flag'` |
| #1 `url` targets the real ATS | Feeds cross-ATS dedup strategies #2 and #3, and seeds `board` rows |
| #3 Jane Street homoglyphs | `title_normalized` needs Unicode confusables folding, not just casefold + NFKD |
| #3 `min_salary: "None"` as a string | Adapter-level sentinel handling; never coerce blindly |
| #4 tracking-wrapped links | Resolve redirects before dedup |
| #5 cookie auth | `board.config` must accommodate credentials, and those boards are best-effort |

---

## Field alignment

Same concept spine as [ATS-fields.md](ATS-fields.md) — row numbers match, so row *n* here is the same concept as row *n* there and the two files can be read side by side. `·` means the source doesn't provide it.

**Stored where:** ✅ dedicated `posting` column · 📦 JSONB · 🏷 feeds a `posting_label` · ❌ dropped

| # | Concept | GitHub lists | JSON-LD | Jane Street | Gmail alerts | YC WaaS |
|---|---|---|---|---|---|---|
| | **IDENTITY** | | | | | |
| 1 | ✅ Primary job id | `id` (uuid) | `identifier` | `id` | `id` (message) | · |
| 2 | 📦 Secondary id | · | · | · | `threadId` | · |
| 5 | ✅ Content hash | · | · | · | · | · |
| 6 | ✅ Company identifier | `company_url` | `hiringOrganization` | *(implicit — one firm)* | `From` header | · |
| | **TITLE & COMPANY** | | | | | |
| 7 | ✅ Title | `title` | `title` | `position` ⚠ | *extracted from body* | · |
| 9 | ✅ Company name | `company_name` | `hiringOrganization` | *(implicit)* | *extracted* | · |
| | **DESCRIPTION** | | | | | |
| 12 | ✅ Description HTML | · | `description` | `overview` | `parts[text/html]` | · |
| 13 | 🏷 Description plain | · | · | · | `parts[text/plain]` | · |
| | **URLS** | | | | | |
| 20 | ✅ Hosted posting URL | `url` ⚠ | *(page URL)* | *(constructed)* | *extracted hrefs* ⚠ | · |
| 21 | 🏷 Apply URL | `url` | · | · | · | · |
| | **CLASSIFICATION** | | | | | |
| 25 | 📦 Department | · | · | `department` ᶦ | · | · |
| 27 | 📦 Team | · | · | `team` | · | · |
| 31 | 📦 Category | `category` | · | `category` | · | · |
| 32 | 📦 Industry | · | `industry` | · | · | · |
| 34 | 📦🏷 Education level | `degrees[]` | · | · | · | · |
| | **EMPLOYMENT** | | | | | |
| 38 | ✅ Employment type | · | `employmentType` | `availability`, `type` ᶦ | · | · |
| | **LOCATION** | | | | | |
| 46 | ✅ Location (display) | `locations[]` | · | `city`, `location` ᶦ | · | · |
| 47 | 🏷 City | · | `…addressLocality` | `city` | · | · |
| 48 | 🏷 State / region | · | `…addressRegion` | · | · | · |
| 49 | 🏷 Country | · | `…addressCountry` | · | · | · |
| 55 | 📦 Secondary locations | `locations[]` | `jobLocation[]` | · | · | · |
| | **TIME** | | | | | |
| 59 | ✅ Published / posted | `date_posted` ⚠ | `datePosted` | · | `internalDate` ⚠ | · |
| 61 | 📦 Updated | `date_updated` | · | · | · | · |
| 63 | 🏷 Deadline / end | · | `validThrough` | · | · | · |
| | **COMPENSATION** | | | | | |
| 65 | 🏷 Salary min | · | `baseSalary…minValue` | `min_salary` ⚠ | · | · |
| 66 | 🏷 Salary max | · | `baseSalary…maxValue` | `max_salary` ⚠ | · | · |
| 68 | 🏷 Currency | · | `baseSalary.currency` | · | · | · |
| 69 | 🏷 Interval | · | `baseSalary…unitText` | · | · | · |
| | **STATUS & FLAGS** | | | | | |
| 72 | ✅ Active / listed | `active` | · | `status` ᶦ | · | · |
| 74 | 📦 Visibility | `is_visible` | · | · | · | · |
| | **NON-ATS ONLY** — concepts no ATS supplies | | | | | |
| N1 | 🏷 **Term / season** | `terms[]` | · | `availability` | · | · |
| N2 | 🏷 **Sponsorship** | `sponsorship` | · | · | · | · |
| N3 | 🏷 Duration | · | · | `duration` | · | · |
| N4 | 📦 Aggregator provenance | `source` | · | · | `From` header | · |

ᶦ = from `internships.json` rather than `main.json`

### ⚠ Notes on marked cells

| Cell | Issue |
|---|---|
| #1 `url` | Points at the **real ATS**, not the aggregator — which is what makes it a dedup key and a board-discovery seed |
| #1 `date_posted` | Epoch **seconds**, and it's the *aggregator's* first sighting, not the employer's publish time → precision `date`, never `exact` |
| #3 `position` | Unicode homoglyphs (`ꓟachine ꓡearning ꓣesearcher`, U+A4DF/A4E1/A4E3) — needs TR39 confusables folding |
| #3 `min_salary` / `max_salary` | The **string** `"None"`, not null |
| #4 `internalDate` | Epoch **milliseconds**, and it's when the *email* arrived — unrelated to when the job posted |
| #4 extracted hrefs | Wrapped in tracking redirects; resolve before dedup |

### What this table shows

- **Rows N1–N2 are the payoff.** `terms` and `sponsorship` are things *no ATS supplies* and that you'd otherwise pay an LLM to extract from prose — source #1 hands them over pre-labeled, entering `posting_label` with `provenance='source'` rather than `'model'`.
- **Coverage is far thinner than any ATS** — GitHub lists fill 15 of ~30 concepts, Jane Street 11, JSON-LD 14, Gmail 6. These are discovery sources, not replacements.
- **No source here has a description worth scoring except #2 and #3**, which is the structural argument for treating #1 and #4 as pointers that get promoted to real `board` rows and re-fetched at origin.
