# In-House & ATS Endpoints Directory

Comprehensive guide to public job board APIs for engineering-heavy companies. Includes standard ATS platforms (Workday, Greenhouse, Lever) and custom in-house endpoints organized by architecture and authentication requirements.

---

## Standard ATS Platforms

These companies use publicly accessible ATS vendor APIs that require only a company token or tenant name (no auth).

### Workday CXS REST API (POST)

Many enterprise career sites run on Workday's Custom Experience Services (CXS). Direct API queries bypass the UI entirely.

| Company | Sector Focus | Endpoint |
| :--- | :--- | :--- |
| **NVIDIA** | Hardware / AI | `nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs` |
| **Citadel** | Prop Trading | `citadel.wd5.myworkdayjobs.com/wday/cxs/citadel/Citadel_Careers/jobs` |
| **Capital One** | Fintech | `capitalone.wd1.myworkdayjobs.com/wday/cxs/capitalone/Capital_One/jobs` |
| **Epic Games** | Gaming / Unreal | `epicgames.wd5.myworkdayjobs.com/wday/cxs/epicgames/Epic_Games/jobs` |

**Payload:** `{"appliedFacets":{},"limit":20,"offset":0,"searchText":""}`

---

### Greenhouse Public Job Board API (GET)

Greenhouse exposes an unauthenticated REST API requiring only a board token.

| Company | Sector Focus | Endpoint |
| :--- | :--- | :--- |
| **Two Sigma** | Quant / ML | `boards-api.greenhouse.io/v1/boards/twosigma/jobs?content=true` |
| **Hudson River Trading** | Quant Trading | `boards-api.greenhouse.io/v1/boards/hudsonrivertrading/jobs?content=true` |
| **Stripe** | Fintech / Payments | `boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true` |
| **Databricks** | Data Eng / AI | `boards-api.greenhouse.io/v1/boards/databricks/jobs?content=true` |

**Note:** `?content=true` returns full HTML descriptions.

---

### Lever Public API (GET)

Lever's public API is identical to Greenhouse but includes rich internal tags (Engineering, ML, C++) making filtering trivial.

| Company | Sector Focus | Endpoint |
| :--- | :--- | :--- |
| **Palantir** | Data / Defense | `api.lever.co/v0/postings/palantir?mode=json` |
| **Anthropic** | AI Research | `api.lever.co/v0/postings/anthropic?mode=json` |
| **Scale AI** | ML Systems | `api.lever.co/v0/postings/scale?mode=json` |

---

## Custom Proprietary Endpoints

### REST APIs (GET)

In-house endpoints serving bespoke career page UIs. Clean JSON, no headers required.

| Company | Sector / Focus | Endpoint |
| :--- | :--- | :--- |
| **Netflix** | Streaming / Infra | `https://jobs.netflix.com/api/search?q=` |
| **Bloomberg** | Financial Engineering | `https://careers.bloomberg.com/api/v1/jobs` |
| **Spotify** | Recommendation Systems | `https://www.lifeatspotify.com/api/jobs/search` |
| **Datadog** | Observability / Distributed Systems | `https://careers.datadoghq.com/api/jobs` |
| **Riot Games** | Game Backend / Data | `https://www.riotgames.com/api/jobs` |
| **Roblox** | Engine Architecture | `https://corp.roblox.com/wp-json/roblox/v1/jobs` |
| **Morgan Stanley** | Quantitative Development | `https://morganstanley.tal.net/vx/mobile-0/brand-2/candidate/jobboard/vacancy/1/feed/json` |

---

### REST APIs (POST)

POST-based endpoints typically requiring JSON payloads (limit, offset, filters). Some may require browser headers (`User-Agent`, device IDs) for anti-bot bypass.

| Company | Sector / Focus | Endpoint | Headers Required |
| :--- | :--- | :--- | :--- |
| **ByteDance / TikTok** | ML / Backend | `https://jobs.bytedance.com/api/v1/search/jobs/` | Minimal |
| **JPMorgan Chase** | Quantitative / Trading | `https://careers.jpmorgan.com/api/v1/jobs/search` | `Phenom-Device-Id` |
| **Goldman Sachs** | Trading / Engineering | `https://www.goldmansachs.com/careers/professionals/search-jobs/api/v1/job-search` | Minimal |
| **Mastercard** | Fintech / Payments | `https://careers.mastercard.com/api/v1/jobs/search` | Phenom headers |

---

## Advanced Architectures

### GraphQL Endpoints

GraphQL APIs are heavily guarded. Require reverse-engineering of query structures or capturing rotating `doc_id` / `persistedQuery` hashes.

| Company | Endpoint | Notes |
| :--- | :--- | :--- |
| **Meta** | `https://www.metacareers.com/graphql` | Rotating `doc_id`, strict header matching, actively hostile to automation |
| **Pinterest** | `https://careers.pinterest.com/graphql` | Standard GraphQL with job taxonomy variables |
| **Coinbase** | `https://graphql.coinbase.com/` | Routes career data through primary engineering gateway |

---

### Next.js Data Payloads (`_next/data`)

Modern static-rendered career portals built with Next.js expose JSON props via `_next/data/{build_id}/` paths. Must scrape HTML first to extract current `build_id` from `<script id="__NEXT_DATA__">` tag.

**URL Structure:** `https://www.company.com/_next/data/{build_id}/careers.json`

| Company | Sector / Focus |
| :--- | :--- |
| **OpenAI** | Foundational Models / AI Safety |
| **D.E. Shaw** | Quantitative Trading / Mathematics |
| **Vercel** | Edge Computing / Web Infrastructure |
| **Plaid** | Fintech Infrastructure |

---

### Eightfold.ai Headless Proxies

Several major companies layer Eightfold.ai AI proxies over legacy ATS systems, providing standardized REST APIs with detailed JSON schemas.

**Base Endpoint:** `GET https://{careers_subdomain}/api/apply/v2/jobs`

| Company | Sector / Focus | URL |
| :--- | :--- | :--- |
| **Airbnb** | ML Systems / Pricing Algorithms | `https://careers.airbnb.com/api/apply/v2/jobs` |
| **Electronic Arts** | Rendering / Telemetry Pipelines | `https://careers.ea.com/api/apply/v2/jobs` |
| **Micron** | Hardware / Silicon Engineering | `https://micron.eightfold.ai/api/apply/v2/jobs` |
| **Nutanix** | Cloud Infrastructure | `https://careers.nutanix.com/api/apply/v2/jobs` |

---

## Summary

**Total Companies:** 36 unique endpoints

**By Category:**
- Workday CXS: 4 companies
- Greenhouse: 4 companies
- Lever: 3 companies
- Custom REST GET: 7 companies
- Custom REST POST: 4 companies
- GraphQL: 3 companies
- Next.js SSG: 4 companies
- Eightfold.ai: 4 companies

**Not Included (separate "Big 7" in-house adapters):**
- Amazon, Apple, Microsoft, Tesla, Uber, Meta, Google — see `inhouse.md`
