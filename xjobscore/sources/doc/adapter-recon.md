# adapter recon — 2026-08-06

Per-row evidence behind the `Adapter` column in `candidates.md`. Every ⬜ candidate was
resolved to `json-api`, `docxtract`, or `bespoke`, and marked 🆗 (clean conclusion) or
⚠️ (concluded, with a caveat recorded below).

The 31 Origin ATS rows were **probed live**. The 29 Web Board rows were desk-researched
with opportunistic probing; in practice nearly all of them got a real request too, and
every status quoted here is one this pass observed.

Tally: **12 json-api · 5 docxtract · 43 bespoke**.

**Since this pass:** all six field-verified `json-api` rows — Recruitee, Pinpoint,
Manatal, Unstop, TalentReef and UKG — are implemented as configs in
`json-api-configs.py`, with fixtures and `test_json_api_boards.py`. They have moved to
the implemented tables in `candidates.md`; their sections below are kept as the record
of what was probed, amended where implementation disproved something.

**Since that pass (2026-08-06, session 2):** the suggested order of work below was
carried out through step 6. Vincere and JazzHR shipped as `docxtract` configs
(`test_docxtract_html_links.py`). All three engine gaps closed: an `atom` parser and a
namespace-free `xml_map` parser were added to `docxtract.py`, and `_request` in
`json-api.py` now forwards `headers=`. PeopleAdmin (`test_docxtract_atom.py`) and
Personio (`test_docxtract_xml_map.py`) are implemented and field-verified against live
tenants. Oleeo stayed blocked — the atom parser is ready for it, but the one reachable
tenant (`oleeo-jobs`) still has 0 `<entry>` elements, so its field shape remains
unconfirmed. USAJOBS is unblocked at the engine level (headers now send) but still
needs a real `Authorization-Key` — a free key only the project owner can register for —
before a config can be field-verified against a live response.

## The rubric

A row is `json-api`/`docxtract` only if it fits what the engines already express.

`json-api` (`json-api.py`): one GET (url + `params`) or POST (JSON body rendered from
context); integer-cursor pagination (`page = {start, size, step, max, total}`); optional
per-item detail fan-out; conditional GET on the first list request; `key_parts` for
compound keys; secrets/overrides from `feed.config`. It **cannot** send request headers
(`net.get/post` accept `headers=`, but `_request` at json-api.py:132 never passes them),
cannot follow opaque/token cursors, cannot hold a cookie or CSRF session, and calls
`response.json()` unconditionally.

`docxtract` (`docxtract.py`): exactly one GET, then one of `jsonld`, `xml_items`,
`json_items`, `html_links` (regex link discovery → per-link detail fetch → JSON-LD on
the detail page); parsers `schemaorg`, `rss`, `simplify`. **No** pagination, POST, or
headers, and no CSS/XPath extraction — an HTML board qualifies only if it carries
`JobPosting` JSON-LD.

## Three engine gaps worth fixing (they unblock 4 sources between them)

All three closed in session 2 (2026-08-06). Kept as the record of what blocked what.

1. **No Atom parser.** ~~`_rss` (docxtract.py:261) is RSS-2.0-shaped: it reads
   unqualified `title`/`link`/`description`/`pubDate`, so on Atom every field comes
   back empty (namespaced tags, and the URL lives in `link/@href`, not element
   text).~~ Fixed: `_atom` in `docxtract.py`, config key `"parser": "atom"`. Unblocked
   **PeopleAdmin** (implemented, field-verified). Does not unblock **Oleeo** by
   itself — that source is still waiting on a populated tenant, not the parser.
2. **No generic XML field map.** ~~A non-RSS XML feed extracts but cannot be
   parsed.~~ Fixed: `_xml_map` in `docxtract.py`, config key `"parser": "xml_map"`, a
   flat field-name-to-element-path map with no namespace handling. Unblocked
   **Personio** (implemented, field-verified).
3. **No request headers.** ~~`_request` (json-api.py:132) never passes `headers=` even
   though `net.get`/`net.post` accept them.~~ Fixed: `_request` now resolves an
   optional `request["headers"]` dict through the same `$`-context machinery as
   `params`/`body` and forwards it. Unblocks **USAJOBS** at the engine level; the
   config itself still needs a live `Authorization-Key` to field-verify.

---

## json-api — verified (6)

Field paths for these were re-checked against live payloads; every field a config would
read is present and non-empty.

| Source | Endpoint proved | Evidence |
|---|---|---|
| **Recruitee** #3 | `https://transperfect.recruitee.com/api/offers/` | 200 JSON, **614 offers**, 8.8 MB, one GET, descriptions inline. `{"offers":[…]}`, id `@id`. Has `title`, `careers_url`, `description`, `requirements`, `location`/`city`/`country`, `created_at`, `published_at`, `department`, `employment_type_code`, `status`, `close_at`, `tags`, `salary`. Tenant guesses mostly 404 with `{"error":"Not Found"}`; `tellent` exists but is empty. |
| **Pinpoint** #4 | `https://workwithus.pinpointhq.com/postings.json` | 200 JSON 33 KB, `{"data":[…]}`, id `@id`; `title`, `url`, `description`, `location`, `benefits` inline. Confirmed on a second tenant (`cfra`, 36 KB). |
| **UKG / UltiPro** #9 | POST `…/UNI1076UNFI/JobBoard/86df2700-…/JobBoardView/LoadSearchResults` | POST `{}` already returns 200 `{"opportunities":[],"totalCount":0}` — **no headers needed**. With a real body: `totalCount` 10, items carry `Id`, `Title`, `RequisitionNumber`, `PostedDate`, `BriefDescription`, `Locations`, `JobCategoryName`. Paginates on `Skip` (integer → `$cursor`), total from `@totalCount`. Needs a **3**-part key `host/companyCode/boardGuid`: tenants are sharded across `recruiting`, `recruiting2`, … and a board answers on exactly one (`ARU1000ARUP` is 200 on `recruiting2`, 404 on `recruiting`). **Correction to this pass's first reading:** a detail fan-out for full text is not possible. `BriefDescription` is a 450-char teaser against ~19 kB of real body, and that body exists only inside the Knockout `OpportunityDetail` page — `LoadOpportunity`, `LoadOpportunityDetail`, `GetOpportunityDetails`, `LoadOpportunities` and `Opportunity/{id}` all 404. Complete for discovery, truncated for content. |
| **Manatal** #30 | `https://www.careers-page.com/api/v1.0/c/manatal/jobs/?page=1&page_size=50` | 200 JSON 140 KB, DRF paginator: `{count, next, previous, results:[…]}`, `count` 29. Items: `id`, `hash`, `position_name`, `description`, `organization_name`. Integer `page` → `$cursor`, `total` from `@count`. |
| **TalentReef** #59 | POST `https://prod-kong.internal.talentreef.com/apply/proxy-es/search-en-us/posting/_search` | An **open Elasticsearch proxy**: POST `{"size":5,"query":{"match_all":{}}}` returns 200, `hits.total` **196,648**, no auth, no tenant, no headers. Items at `hits.hits`, `unwrap: "_source"`, id `@_id`. `_source` has `description`, `clientName`, `address`, `geo`, `category`, `contractType`, `createdDate`, `endDate`. Paginate with `from` (integer). Easily the largest single win in this pass. |
| **Unstop** #61 | `https://unstop.com/api/public/opportunity/search-result?opportunity=internships&page=1&per_page=15` | 200 JSON 157 KB, Laravel paginator at `data`: `data.data` (15 rows), `data.total`, `data.current_page`, `data.last_page`. Items: `id`, `public_url`, `seo_url`, `organisation`, `locations`, `details`, `jobDetail`, `end_date`, `required_skills`. Integer `page` → `$cursor`. |

## json-api — shape fits, not fully verified (6) ⚠️

| Source | Status | What is missing |
|---|---|---|
| **USAJOBS** #1 | `401 application/problem+json` on a bare GET | Needs an `Authorization-Key` header (free key) plus a UA email. **Engine gap 3** — otherwise a plain paginated JSON API and the single highest-priority row in the file. |
| **ADP Workforce Now** #5 | `404` (openresty) on the documented path with a synthetic `cid` | No live `cid`/`ccId` pair found, so it is unclear whether the path is stale or merely tenant-gated. Re-probe once a real career-center URL is in hand. |
| **Bullhorn** #26 | `400 application/json` with a structured `{errorMessage, errorMessageKey}` on a bad token | Endpoint is live and JSON. Plain REST with integer `start`/`count` paging; `corpToken` belongs in `feed.config`. Field shape unverified — a real corpToken is issued by Bullhorn support, though public career portals embed one. |
| **Gem** #27 | `400` with generic errors on a synthesized query | The `/api/public/graphql/batch` array body **is** expressible as config. The blocker is data, not engine: the list operation must be lifted from `static.gem.com/scripts/jobBoards.*.min.js`. That bundle exposes `ExternalJobPostingQuery($boardId, $extId)` and `JobBoardTheme`, so the list query likely sits in `ExternalJobBoardList.*.min.js`. Live tenants: `gem`, `mission`. |
| **Recruit CRM** #53 | `200` + `{"message":"You are not allowed to access this URL.","status":"fail"}` for `account=test` | Endpoint alive and JSON-shaped; needs a real account slug to see fields. |
| **Fountain** #60 | `404 application/json` `{"status":404,"error":"Not Found"}` for a guessed tenant | Endpoint answers JSON, so the shape is plausible; no live `{company}/{country}` tuple found. |

## docxtract (5)

| Source | Verdict | Evidence |
|---|---|---|
| **JazzHR** #25 ✅ | `html_links` + `jsonld`, exactly like the `jobvite` profile | The RSS in `candidates.md` is **gone**: `/apply/jobs/feed` 404s (redirects to `app.applytojob.com/notfound.html`) and `/apply/jobs.rss` returns **410 Gone** — confirmed on two tenants (`dtexsystems`, `raptive`), so it is a product retirement, not a per-tenant quirk. But `/apply/jobs/` lists `/apply/jobs/details/{id}` links, and each detail page carries a full `JobPosting` JSON-LD block (`title`, `description`, `datePosted`, `employmentType`, `jobLocation`, `validThrough`, `uniqueJobCode`). Pattern: `/apply/jobs/details/(?P<id>[A-Za-z0-9]+)`, `detail.id = "discovered"`. Implemented (`test_docxtract_html_links.py`). |
| **Vincere** #57 ✅ | `html_links` + `jsonld` | `candidates.md` called this "No — HTML portal"; wrong. The `/careers/` list page carries only `WebPage` JSON-LD, but `/careers/job/{id}/{slug}` detail pages carry `['WebPage','JobPosting']`. Verified on `zealhr.vincere.io` job 62146. Pattern: `/careers/job/(?P<id>\d+)/`. Implemented (`test_docxtract_html_links.py`). |
| **PeopleAdmin** #14 ✅ | `xml_items` + `_atom` | Also called "No — HTML search"; also wrong. `/postings/search.atom` returns **200 `application/atom+xml`, 302 KB, 115 entries** on `marshall.peopleadmin.com` — a whole board with descriptions inline. Entry shape: `id` (= posting URL), `published`, `updated`, `link/@href`, `title`, `content` (HTML description), `author`. `.json` returns 406; the plain HTML page has no JSON-LD. Implemented (`test_docxtract_atom.py`). |
| **Personio** #24 ✅ | `xml_items` + `_xml_map` | `https://personio.jobs.personio.de/xml` → 200 `text/xml`, root is `<workzag-jobs><position>` with `<id>`, `<subcompany>`, `<office>`, `<additionalOffices>` — a bespoke schema, **not** RSS. No `<url>` element on a position; `posting_url` is templated from key+id (verified the detail page resolves). The one live posting had an empty `<jobDescriptions>`, so non-empty description content is mapped but unverified. Implemented (`test_docxtract_xml_map.py`). Probing other tenants (n26, trivago, babbel, workmotion, contentful) hit a Vercel bot checkpoint (429) on `*.jobs.personio.de`, not confirmation the tenants don't exist. |
| **Oleeo** #49 ⚠️ | `xml_items` + `_atom` | The listed `{company}.oleeo.com` is the vendor's marketing site; boards live on `*.tal.net`. `…/candidate/jobboard/vacancy/1/feed` returns 200 `application/atom+xml`, so the transport is Atom and the parser now exists — but the one reachable tenant (`oleeo-jobs`) still has **0 entries** as of 2026-08-06, so entry shape remains unconfirmed. `morganstanley.tal.net/…/feed/json` (from `planned/house-endpoints.md`) 404s — that note is stale. Blocked only by the need for a populated tenant. |

## bespoke (43)

### Endpoint in `candidates.md` is wrong or dead (8) ⚠️

Worth correcting in the table regardless of adapter fit; each of these was probed.

| Source | Listed | Actual |
|---|---|---|
| **JobAps** #48 | `{state}.jobaps.com` | Does not resolve. Live host is `www.jobapscloud.com/{STATE}/` — 200 HTML 303 KB, no JSON-LD. |
| **HireTouch** #51 | `{institution}.hiretouch.com` | **`hiretouch.com` no longer resolves at all.** Product appears retired. |
| **SilkRoad OpenHire** #52 | `{company}.openhire.silkroad.com` | Does not resolve. Boards live at `jobs.silkroad.com/{tenant}/` — 200 HTML 117 KB, no JSON-LD. |
| **Ceipal** #55 | `{company}.ceipal.com/jobs` | Does not resolve. Live shape is `candidateportal{region}.ceipal.com/jobs/job_description/{base64}` — 200 HTML, no JSON-LD. |
| **Loxo** #56 | `{company}.loxo.co/jobs` | Live shape is `app.loxo.co/{agency-slug}` — 200 HTML 69 KB, no JSON-LD. |
| **ApplicantPro** #28 | `{company}.applicantpro.com/core/jobs/{domain_id}` | `/core/jobs/` returns 404 (26-byte body). `/jobs/` is a JS-rendered Vue board with no JSON-LD and no job links in static HTML. Now branded isolved Talent Acquisition. |
| **PageUp** #13 | POST `…/search/`, "paginated JSON" | The POST **302s to a GET** HTML route (`/{board}/caw/en/search`) — 200 HTML 169 KB, no JSON-LD. It is not a JSON API. |
| **JobAdder** #58 | `{company}.jobadder.com/careers` | No public tenant board found; JobAdder distributes through embedded widgets on client sites rather than a first-party board host. |

### Blocked or session-bound (6) ⚠️

| Source | Evidence |
|---|---|
| **Dayforce** #6 | `403 Forbidden` on the documented POST, both bare and with `Origin`/`Referer`/browser UA. Portal HTML has no JSON-LD. |
| **Taleo** #12 | `jobsearch.ftl` serves 48–145 KB HTML with no JSON-LD (`tgh`, `cu`); the REST `searchjobs` call is session/CSRF-bound. Distinct product from `oracle` in `json-api-configs.py` (Oracle Cloud HCM). |
| **BrassRing** #10 | `500` on a widely published `partnerid=25240&siteid=5011` pair; search runs as session-backed AJAX. |
| **Cornerstone (CSOD)** #29 | Career site is a 5.5 KB SPA shell (`henkel`), and `…/services/x/career-site/v1/search` returns **401**. |
| **Meta Careers** #22 | Plain GET returns **400** — needs browser headers/cookies. |
| **Wayfair** #31 | **429** on the first request: bot-protected. |

### HTML or JS boards with no JSON-LD (17)

Probed, 200, no `JobPosting` JSON-LD anywhere reachable — so outside `docxtract` by
construction, whatever the transport underneath.

`Avature` #11 (199 KB HTML, `deloittebe`) · `Paylocity` #7 (38 KB) · `Paycom` #8
(197 KB; job **detail** page also has none) · `JobDiva` #54 (~10 KB JS shell) ·
`Zintellect` #15 (77 KB) · `Google Careers` #18 (400 KB+) · `NASA STEM Gateway` #21
(Salesforce Experience Cloud `/s/`, 80 KB) · `Adobe Research` #32 (43 KB) ·
`Extern` #42 (160 KB) · `Smithsonian SOLAA` #44 (34 KB) · `CalCareers` #45 (70 KB) ·
`StateJobsNY` #46 (12.5 KB shell) · `NYC Jobs` #47 (67 KB) · `Trac / NHS Jobs` #50
(29 KB, JS-driven search, no `jobadvert` links in static HTML) · `Fly.io` #62 (107 KB;
bare `/jobs` 301s to `/jobs/`) · `YC jobs` #16 ⚠️ (Inertia `data-page` attribute is
present, but it is the `WaasLandingPage` payload, not the job list; `X-Inertia: true`
returns **409** on version mismatch — needs a header *and* version negotiation) ·
`HN "Who is hiring?"` #17 ⚠️ (the Algolia API itself is clean paginated JSON — `hits`,
`nbPages`, integer `page` — but one HN comment is free text, and turning it into
`title`/`company_raw` is not expressible as field mapping, so the parse is what forces
bespoke, not the transport).

### Program pages with no enumerable listing (12) ⚠️

Application/landing pages: nothing to enumerate, so there is no posting feed to adapt at
any fidelity. All probed 200 with no `JobPosting` JSON-LD; several are bare shells.

`NIH SIP` #19 · `SULI` #20 · `Google Summer of Code` #33 (org catalog, not postings) ·
`CIA Student Programs` #34 · `IC-wide student programs` #35 (1.0 KB shell) ·
`Naval STEM Interns` #36 (9 KB) · `AEOP` #37 · `SMART Scholarship` #38 (1.4 KB shell) ·
`Coding it Forward` #39 (`WebSite`/`LocalBusiness` JSON-LD only) · `NSIN X-Force` #40 ·
`NOAA Hollings` #43 · `Forage` #41 (the sitemap is real — `<urlset>`, 411 locs — but it
is a URL list of marketing and `/simulations?companies=…` pages, not postings, and
`docxtract` cannot fan out from sitemap XML: `html_links` reads `<a href>` in HTML).

---

## Suggested order of work

1. ✅ **TalentReef** — one config, no tenant, no auth, 196k postings.
2. ✅ **Recruitee, Pinpoint, Manatal, Unstop** — four plain configs, all field-verified.
3. ✅ **UKG** — same, plus a 3-part key; accept teaser-only descriptions.
4. ✅ **Vincere, JazzHR** — two `docxtract` link-discovery profiles, no engine change.
5. ✅ **Atom parser** (engine gap 1) → unlocked **PeopleAdmin** (115-entry boards with
   descriptions inline). Did not unlock **Oleeo** — its one reachable tenant is empty.
6. ✅ **Header support** (engine gap 3, plus a generic XML field map for **Personio**
   along the way) → engine-level unlock for **USAJOBS**, the #1 priority row. Still
   needs a live `Authorization-Key` from the project owner before the config itself
   can be written and field-verified — that is the only remaining blocker on this row.
