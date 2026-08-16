# Integration plan — the seven remaining unified-adapter candidates

Dated 2026-08-07. Successor to `adapter-recon.md`, whose work queue was exhausted:
every row it left open was **data-blocked, not engine-blocked**. This document
closes that data gap. Each source below was probed live, and five of the seven
were driven **through the real engine** (`json-api.harvest` / `docxtract.harvest`
plus `parse`) against the live endpoint, with the config injected into `CONFIGS`
in memory. No adapter file was modified to produce any result quoted here.

**Constraint honoured throughout: zero changes to `json-api.py` and `docxtract.py`.**
Every integration below is a dict entry in `json-api-configs.py` or
`docxtract-configs.py`, a source-registry entry, a fixture, and a test. Where that
constraint costs data, it is stated rather than worked around.

## Verdict table

| Source | Adapter | Config-only? | Live proof | Ship |
|---|---|---|---|---|
| **Gem** | json-api | yes | 6 items, 6,081-char description, epoch date | **1 — now** |
| **ADP Workforce Now** | json-api | yes | 50 items / 3 pages / 50 unique, 6,147-char description | **1 — now** |
| **Bullhorn** | json-api | yes | 773 items / 8 pages, descriptions + dates on 773/773 | **1 — now** |
| **USAJOBS** | json-api | yes | engine-ready; needs a free API key | **2 — write, `status=planned`** |
| **Recruit CRM** | json-api | yes | 133 items, but 46/133 descriptions, 0 dates | **3 — ship, flagged thin** |
| **Oleeo** | docxtract | partly | 28/31 descriptions on `type="text"`, **0/33** on `type="xhtml"` | **3 — text-type tenants only** |
| **Fountain** | json-api | yes | 36 items, **0 descriptions, 0 dates**, title = a place name | **4 — do not ship** |

Three calls I made rather than block on (raise them if you disagree):

1. **Fountain is not integrated.** It parses cleanly; the data is the problem. See §7.
2. **Oleeo ships against `type="text"` tenants only**, so the catalog contains
   only feeds whose descriptions actually survive `_atom`. See §6.
3. **Catalogs are seeded thin** — 2–4 field-verified feeds each, not the ~70
   Bullhorn tokens recon surfaced. Bulk tenant discovery is `keyscan`'s job, and
   `ashby.toml` (2,589/2,591 rows with `domain = "unknown"`) is the cautionary
   tale for seeding faster than you can verify.

---

## 1. Gem — `json-api`, priority 26

**Blocker cleared.** `adapter-recon.md` said the list operation had to be lifted
from the SPA bundle. It did. `jobBoards.j8beAAs-.v2.min.js` is only an ES-module
entry; it names a lazy chunk `ExternalJobBoardList.TinnB4Uw.v2.min.js`, which
carries the operation whole:

```graphql
query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings { id extId title
      locations { id name city isoCountry isRemote extId }
      job { id department { id name extId } locationType employmentType } } }
}
```

`$boardId` is the **vanity path** (`gem`), not the tracking UUID in the page HTML.
No headers, no auth. The batch endpoint takes an array body and returns an array,
which the engine handles: a list config value renders element-wise, and `_dig`
indexes lists numerically, so `items: "0.data..."` addresses the first operation.

The list carries no description, so descriptions come from a detail fan-out
reusing the bundle's own `ExternalJobPostingQuery($boardId, $extId)`. Verified:
`descriptionHtml` 6,081 chars, `firstPublishedTsSec` an int epoch that
`{"epoch_s": …}` converts, `job.requisitionId` present.

```python
"gem": {
    "list": {
        "method": "POST", "url": GEM_GQL, "url_override": False,
        "body": [{"operationName": "JobBoardList", "query": GEM_LIST_QUERY,
                  "variables": {"boardId": "$key"}}],
        "items": "0.data.oatsExternalJobPostings.jobPostings", "id": "@extId",
    },
    "detail": {
        "method": "POST", "url": GEM_GQL, "url_override": False,
        "body": [{"operationName": "ExternalJobPostingQuery", "query": GEM_DETAIL_QUERY,
                  "variables": {"boardId": "$key", "extId": "$id"}}],
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
    "extra": {"department": "@job.department.name", "team": "@job.teamDisplayName",
              "node_id": "@id", "start_date_ts": "@startDateTs",
              "is_unlisted_externally": "@isUnlistedExternally"},
}
```

`{id}` in `url_posting` resolves as `@id` against the payload — the base64 node
id — because `_format` prefers context then falls back to the payload. The board
is an SPA that 200s on any path, so the base64 form is chosen as the GraphQL node
identity rather than verified by status code. `company_raw` must come from
`feed.config`: nothing in the payload names the employer (`teamDisplayName` is a
team). This is the `ukg`/`eightfold` pattern.

**Catalog:** `gem` (6 postings) confirmed. `mission` resolves and answers but
returns `jobPostings: []` — a live-but-empty board, so it belongs at
`status = "planned"` until it has stock, not `"live"`.

## 2. ADP Workforce Now — `json-api`, priority 5

**Blocker cleared.** The path in `candidates.md` was right all along; recon had
only ever tried a synthetic `cid`. Five live tenants found, all sharing
`ccId=19000101_000001` (which is in fact optional — omitting it returns identical
data, while a *bogus* `ccId` returns `200` with an empty array).

Two behaviours the engine happens to fit exactly:

- **`$top` is hard-capped at 20.** Requesting 50/100/500 all return 20.
- **`$skip` is 1-based, and paging by 20 from 1 is what avoids the off-by-one.**
  `$skip=20` returns rows 20–39, overlapping page 1. Correct offsets are
  1, 21, 41 — which is precisely `page = {"start": 1, "step": 20}`.

Verified against Look Trailers (`d16ba13d-…`, 50 requisitions): **50 items,
50 unique ids, 3 pages, `truncated=False`.**

Descriptions are not in the list, so `requisitionDescription` needs the detail
fetch `…/job-requisitions/{itemID}?cid=…&ccId=…`. The detail response is the
object at the root (no wrapper, so no `payload` key) and is a strict superset of
the list row, so no `merge` is needed. `postDate` is
`"2026-08-03T15:37:00.000-04:00"` — offset-aware and `fromisoformat`-clean.

Use `postDate`, **not** `customFieldGroup.dateFields[].dateValue`: the latter is
`"2026-08-03T15:37Z"`, second-less, and only parses because 3.11 relaxed the rules.

```python
"adp": {
    "key_parts": ("cid", "ccId"),
    "list": {
        "url": ADP_REQS, "url_override": False,
        "params": {"cid": "$cid", "ccId": "$ccId", "$top": 20, "$skip": "$cursor"},
        "items": "jobRequisitions", "id": "@itemID",
        "page": {"start": 1, "size": 20, "step": 20, "total": "@meta.totalNumber", "max": 50},
    },
    "detail": {"url": ADP_REQS + "/{id}?cid={cid}&ccId={ccId}", "url_override": False,
               "id": "@itemID", "item_id": "@itemID"},
    "fields": {
        "title": "@requisitionTitle",
        "url_posting": {"template": ADP_POSTING_URL},   # recruitment.html?cid=…&ccId=…&jobId={itemID}
        "company_raw": "$company_raw",
        "location_raw": {"fallback": [
            "@requisitionLocations.0.nameCode.shortName",
            {"join": ["@requisitionLocations.0.address.cityName",
                      "@requisitionLocations.0.address.countrySubdivisionLevel1.codeValue"]}]},
        "description_html": "@requisitionDescription",
        "requisition_id": "@clientRequisitionID",
        "employment_type": "@workLevelCode.shortName",
        "published_at": {"date": "@postDate"}, "published_at_precision": "exact",
        "locations_extra": {"rest": {"map": "@requisitionLocations", "value": "@nameCode.shortName"}},
    },
    "extra": {"itemID": "@itemID", "workLevel": "@workLevelCode.shortName"},
}
```

`company_raw` again comes from `feed.config` — the payload never names the employer.
A dead `cid` returns **404 with an openresty HTML body**, which surfaces as a
`NetError` from `net.py` rather than a JSON parse failure. That is the correct
outcome for a retired board; no `catch_first_error` is wanted.

Cost note: detail fan-out is serial at 1 req/s per host, so a 200-requisition
tenant is a ~3.5-minute poll and every ADP tenant shares one host bucket. Set
`cadence_s` in hours.

## 3. Bullhorn — `json-api`, priority 26

**Blocker cleared, and the endpoint in `candidates.md` is wrong.**
`/search/JobBoardPost` does not exist — it returns
`"Unknown search entity JobBoardPost."` That error is nonetheless useful: it
proves the corpToken is *valid*, where an invalid one returns `"Bad corp token"`.
The working entity is **`/query/JobBoardPost`** with `where=isOpen=true`.

Verified against `44/CJAT1S`: **773 items, 773 unique, 8 pages, `truncated=False`,
`publicDescription` on 773/773 and `published_at` on 773/773.** Descriptions are
inline, so **no detail fetch** — this is the cheapest of the three tier-1 sources.

```python
"bullhorn": {
    "key_parts": ("swimlane", "corpToken"),
    "list": {
        "url": "https://public-rest{swimlane}.bullhornstaffing.com/rest-services/{corpToken}/query/JobBoardPost",
        "url_override": False,
        "params": {"fields": BULLHORN_FIELDS, "where": "isOpen=true", "start": "$cursor",
                   "count": 100, "orderBy": "-dateLastPublished", "showTotalMatched": "true"},
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
    "extra": {"category": "@publishedCategory.name", "salary": "@salary", "salaryUnit": "@salaryUnit",
              "payRate": "@payRate", "dateAdded": "@dateAdded", "yearsRequired": "@yearsRequired",
              "travelRequirements": "@travelRequirements", "benefits": "@benefits"},
}
```

Four traps to encode in the config comment:

- **`total` is emitted only when `showTotalMatched=true`.** Without it the engine
  falls back to the `size` heuristic and cannot tell a short page from the end.
- **All dates are integer epoch millis** — `{"epoch_ms": …}`, never `{"date": …}`.
- **Unrecognised `fields` names are silently dropped**, HTTP 200, key simply
  absent. A missing key does not mean null; it may mean a typo. The verified-good
  field list is the one in `BULLHORN_FIELDS`; do not extend it without re-probing.
- **`count` is clamped at 500** (with a `messages` warning), so 100 is safe.

**Swimlane is part of the key** (`44/CJAT1S`) because a token answers on exactly
one cluster and there is no cross-cluster fallback — structurally the same
sharding problem `ukg` has, solved the same way with `key_parts`.

`url_posting` has no source in the payload, so each catalog row needs a
`board_url` in `feed.config`. This is the one place a missing config value fails
silently (`json-api.py:119` substitutes `""`), so **every Bullhorn catalog row
must carry `board_url` or not be marked live.**

## 4. USAJOBS — `json-api`, priority 1

Engine-ready. Every previously-cited obstacle is now closed:

| Concern | Status |
|---|---|
| `Authorization-Key` header | `_request` forwards `headers` (engine gap 3, already closed) |
| `User-Agent` = registered email | per-request header overrides the client-level UA |
| `Host` header | not needed — HTTPS sends it already; the docs only show it for raw curl |
| 4-fraction-digit dates | `'2026-03-13T23:59:59.9970'` **parses on the venv's 3.12**; `requires-python = ">=3.12"` |
| Pagination | `Page` 1-based + `ResultsPerPage` ≤ 500 → integer cursor, `step: 1` |
| 10,000-row query cap | `page.max = 20` at 500/page, and the engine reports `truncated` when it bites |
| Descriptions | **inline** via `Fields=Full` — no detail fetch |

Path is `SearchResult.SearchResultItems[].MatchedObjectDescriptor`, total is
`SearchResult.SearchResultCountAll`. The description is
`UserArea.Details.MajorDuties`, a **`list[str]`** — `{"concat": …, "sep": "\n"}`
handles it. Not `PositionFormattedDescription`, which no longer carries a
`Content` key.

Secrets go in `feed.config` and are read as `$api_key` / `$ua_email`, exactly the
pattern `test_json_api_headers.py` already pins.

**Remaining blocker is yours alone:** a free, self-serve key from
`https://developer.usajobs.gov/apirequest/`, emailed to the address that then
becomes the `User-Agent`. So: **write the config, the test, and the catalog row
at `status = "planned"`** — it will not poll — and flip to `"live"` once the key
is in `feed.config`. The fixture can be built without the key from an archived
real `application/hr+json` response (`web.archive.org/web/20260301033502id_/…`),
which is a genuine 200 body, not a doc sample.

**Rejected alternative:** the unauthenticated `/api/historicjoa` +
`/announcementtext` pair is real and key-free, but paginates by opaque
`continuationToken`. The engine's cursor is integer-only, so that path is *not*
config-expressible — it would be an engine change, which is out of scope here.

## 5. Recruit CRM — `json-api`, priority 53

**Blocker cleared, with two corrections to `candidates.md`.**

1. **`batch=true` must be dropped.** With it, the response carries settings and
   the account object but **no `jobs` array at all**. Without it, the entire board
   comes back inline in one response.
2. **`Origin` is mandatory.** Without it the endpoint returns 200 and
   `{"message":"You are not allowed to access this URL.","status":"fail"}` — a
   soft failure that looks like a bad account slug. This is the second source
   (after USAJOBS) that only became possible once `_request` learned `headers`.

It is POST-only (GET → 405), and the engine ignores `params` on POST, so `account`
goes in the URL query string. The board is unpaginated — one POST returns
everything (verified: a 2,166-job tenant in a single body).

```python
"recruitcrm": {
    "list": {
        "method": "POST",
        "url": "https://albatross.recruitcrm.io/v1/external-pages/jobs-by-account/get?account={key}",
        "url_override": False,
        "headers": {"Origin": "https://careers.recruitcrm.io",
                    "Referer": "https://careers.recruitcrm.io/"},
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
}
```

The employer name lives on the response root, not the row, so it comes through
`envelope` — the `workable` pattern. Note the `{"fallback": [...]}` wrappers on
single expressions: they are deliberate, not noise. `jdtext`/`jobcode`/`city` are
empty **strings** rather than absent, and `fallback` is the only expression that
maps `""` to `None`. Without it these land as `""`, which is not the same as a
null column.

**Ship it, but record the quality honestly.** Verified on a 133-job tenant:
**46/133 descriptions, 0/133 dates, 0/133 locations.** There are no per-job date
fields anywhere in the schema — not in the list, not in the detail endpoint — so
`published_at` is permanently unknown for this source and latency can never be
measured from it. That is a property of Recruit CRM, not of the config.

## 6. Oleeo — `docxtract`, priority 49

**Blocker cleared; a new one found in its place.** `oleeo-jobs.tal.net` was empty
because it is a demo board. **Eleven populated feeds** exist, and the path is
simpler than assumed — the `lang-/mobile-/appcentre-/brand-` segments are not
needed, only the board number varies:

```
https://{tenant}.tal.net/candidate/jobboard/vacancy/{N}/feed
```

Good news for a config-only integration: there are **zero extension namespaces**.
Everything Oleeo emits is Atom core, so nothing is lost to `_atom`'s hardcoded
`ATOM_NS`. `published` is `2026-08-06T23:00:06Z` — clean ISO.

The new blocker is `atom:content`, and it is decisive:

| Tenant | `content` type | Descriptions recovered |
|---|---|---|
| `environmentagencyjobs` board 1 | `type="xhtml"` | **0 / 33** |
| `asp` board 14 | `type="text"` | **28 / 31** |

`_atom` reads `content.text`. For `type="xhtml"` the text is whitespace and the
payload sits in a child `{http://www.w3.org/1999/xhtml}div`, so the entire body —
salary, location, closing date, role type — is unreachable. Both were run through
the real engine; the counts above are measured, not predicted.

**Therefore: ship Oleeo, but seed the catalog only with `type="text"` boards.**
That keeps every live row descriptive and leaves the xhtml majority for whenever
`_atom` learns to descend into xhtml content — a one-function change, but an
adapter change, which this plan does not make.

```python
"oleeo": {
    "list": {"url": "https://{key}.tal.net/candidate/jobboard/vacancy/1/feed",
             "extract": "xml_items",
             "path": "{http://www.w3.org/2005/Atom}entry",
             "id": ("{http://www.w3.org/2005/Atom}id",)},
    "parser": "atom",
    "atom": {},
}
```

The board number varies per tenant and `docxtract._format` only substitutes
`{key}`, so non-`/1/` boards set `config = { url = "…/vacancy/14/feed" }` — the
sanctioned per-feed override at `docxtract.py:121`, which is still `{key}`-formatted.

Three properties to record in the config comment:

- **No `ETag`, no `Last-Modified`, `expires: 1970`.** Conditional GET can never
  hit; every poll transfers the whole feed. Feed-level `<updated>` tracks *request*
  time, so it is not a change signal either. Same situation as `successfactors` —
  set an hourly cadence, not the 900s default.
- **Not paginated**, and `?start=` is silently ignored. Feeds appear capped per
  tenant (50 on one, 270 on another); entries beyond the cap are unreachable.
- **`external_id` is a long absolute URL** and `url_posting` carries
  `?instant=apply`, deep-linking into the apply flow rather than the description.
- Location and closing date exist only as label-prefixed lines inside the content
  blob, with a **tenant-defined label set**. Not structured, not mappable.

## 7. Fountain — not integrated

Fountain parses fine. The config below runs, returns 36 items with correct ids,
URLs and pagination, and needs no engine change:

```python
"fountain": {
    "key_parts": ("account", "brand", "country"),
    "list": {"url": "https://web.fountain.com/c/{account}/{brand}/{country}.json",
             "url_override": False, "params": {"page": "$cursor"},
             "items": "openings", "id": "@id",
             "page": {"start": 1, "size": 25, "step": 1,
                      "total": "@pagination.total_count", "max": 40}},
    ...
}
```

The true path is `/c/{account}/{brand}/{COUNTRY}.json` — the middle segment is a
**brand** slug, often different from the account, and country is uppercase ISO-2.
Both are discoverable from `window.__reactComponentsData__` on the HTML board.

**It is not integrated because the endpoint does not list jobs.** Each "opening"
is a physical location: `title` is `"Acton"`, `"Balham"`, `"Battersea"`. Measured
through the engine: **0/36 descriptions, 0/36 dates**, and no date field exists
anywhere in the schema. Per-opening detail is HTML-only (`.json` → 406).

Every posting would therefore be a `description_html = NULL` pointer row whose
title is a place name — unscoreable by construction, and actively misleading in a
board view. The row belongs in `candidates.md` marked structurally unsuitable for
`json-api`, with this finding recorded so it is not re-probed a third time.

---

## Work order

Each numbered step is independently shippable and testable.

1. **Bullhorn** — no detail fetch, inline descriptions and dates; the highest
   data-per-line-of-config of the three. Fixture `fixtures/bullhorn.json`.
2. **Gem** — list + detail fixtures (`gem.json`, `gem_detail.json`). Both are
   POST-to-the-same-URL, so the test's `FixtureNet` must serve by call order, as
   `LinkFixtureNet` in `test_docxtract_html_links.py` already does.
3. **ADP** — list + detail fixtures. The test must assert **`$skip` = 1, 21, 41**
   from `net.get_calls[*]["params"]`; that off-by-one is the whole integration.
4. **USAJOBS** — config + fixture from the archived response + catalog row at
   `status = "planned"`. Test asserts the `Authorization-Key` / `User-Agent`
   headers are forwarded from `feed.config`, reusing `test_json_api_headers.py`'s
   contract, and that `MajorDuties` concatenates.
5. **Recruit CRM** — config + fixture. Test must assert the `Origin` header is
   sent and that an empty `jdtext` yields `description_html is None`, not `""`.
6. **Oleeo** — config + `fixtures/oleeo.atom` captured from `asp` board 14.
   Test asserts descriptions survive on `type="text"` **and** pins the known
   `type="xhtml"` loss, so the day `_atom` is improved, the test tells you.
7. **Fountain** — `candidates.md` amendment only. No code.

Per source, following `doc/SOURCES.md:231` and the `personio` precedent:

- one entry in `json-api-configs.py` (merged via `CONFIGS` at line 727) or
  `docxtract-configs.py` — registration is then automatic via `SPECS`
- one real vendor body in `xjobscore/fixtures/`
- tests in `xjobscore/sources/test_*.py` using the local `FixtureNet` pattern:
  assert the request (URL, params, headers, body), the demux (`external_id` list,
  `item.kind`, `http_status`, `truncated`), every parsed field **including
  deliberate `None`s**, and registration
- a registry ATS-catalog entry or flattened Direct/Board entry, thin and
  field-verified, with the per-feed `config` each config's `$name` expressions require:
  `company_raw` (gem, adp, bullhorn), `board_url` (bullhorn),
  `api_key` + `ua_email` (usajobs), `url` (oleeo non-`/1/` boards)
- a `[[ats]]`, `[[direct]]`, or `[[board]]` block in `registry/feeds.toml`, else `source_type()` returns
  `"Other"`
- check `router.VOLATILE` — Bullhorn's `dateLastPublished` moves without the
  posting changing, so it is a candidate for exclusion from the content hash

Verification, in order:

```bash
PYTHONPATH=. .venv/bin/pytest xjobscore/sources/          # fixture tests, no network
PYTHONPATH=. .venv/bin/python -m xjobscore.raw_inspection bullhorn   # live smoke, per source
```

`raw_inspection` pairs each source with the first registry feed and writes
`harvested/<source>.json` + `parsed/<source>.json`. Add an `UNRUNNABLE` entry for
`usajobs` while it has no key, alongside the existing `icims` / `workatastartup`
entries — that is the file's purpose: recording *why* a source cannot be driven,
so absence never looks like breakage.

## What this plan does not do

Two engine changes are worth their own decision later. Both are listed in
`architecture-updates.md`; neither is taken here.

- **`_atom` cannot read `type="xhtml"` content.** Costs Oleeo the majority of its
  tenants. The narrowest fix descends into the xhtml `div` and uses `itertext()`,
  branching per entry on the `type` attribute — a feed can mix both.
- **No opaque-cursor pagination.** Costs the key-free USAJOBS `/api/historicjoa`
  route, and will cost any future board that paginates by `next` token.

And one standing hazard this plan works around rather than fixes: neither
`*-configs.py` is schema-validated, so a misspelled `fields` key resolves to
`None` and a missing `feed.config` value becomes `""` in a URL. That is the exact
failure mode of the Oracle defect in `CLAUDE.md`. Until `CONFIGS` is validated at
load time, **every new config's URLs and templates must be verified against a real
response** — which is why all five shippable sources above were driven through the
live engine before this document was written.
