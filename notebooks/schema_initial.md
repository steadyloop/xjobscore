# Initial schema

Unified store for multi-source job discovery + application tracking.
Field inventories that justify these choices: [ATS-fields.md](ATS-fields.md), [nonATS-endpoints.md](nonATS-endpoints.md).

**12 tables.** Layered so that each can be rebuilt from the one above it:

```
L0 Registry   company · feed · role_umbrella · role_title_variant      authored
L1 Ingest     poll_run · raw_document                     bronze — append-only, replayable
L2 Canonical  posting                                     silver — the only layer the app reads
L3 Derived    posting_label                               gold — disposable, rebuildable
L4 User       profile · application · application_event · application_event_type
```

**Three rules that shape everything below**

> A **column** is a fact the source stated. Everything **derivable** is a label with provenance.

> Closure may only be inferred from a poll whose `status = 'ok'`.

> A **feed** is a pollable endpoint, not a company's board. Company is resolved **per posting**, uniformly, for every source — an ATS feed is just the degenerate case where the answer is always the same.

---

## L0 — Registry

### company
The only table holding *your* judgments rather than fetched facts, which is why it is never derived from a feed.

1. `id` — PK
2. `slug` — stable handle
3. `name` — canonical brand; never taken from the feed, which reports `"2100 NVIDIA USA"` and `LegalEmployer`
4. `aliases TEXT[]` — every string a source has called this company, **including tenant slugs** (`gohighlevel`, `joinroot`, `prentkeromich`); GIN-indexed
5. `tier` — your A/B/C judgment
6. `umbrella_hint` — default role umbrella, used to route generic titles like `Software Engineer Intern`
7. `website`

### feed
One pollable endpoint. **Holds no company reference** — see [Company resolution](#company-resolution).

1. `id` — PK
2. `source_key` — `greenhouse`, `ashby`, `janestreet`, `simplify_gh` … resolves to an adapter class in code, not a table
3. `config JSONB` — adapter input: `{board_token}` / `{tenant,wd_host,site}` / `{company_uid,token}` / `{careers_url}` / `{repo,path}`; optionally `{company_slug}` as the resolution hint of last resort. Shape differs per source, which is why it's JSON and why source #20 needs no DDL
4. `trust` — `origin` | `aggregator` | `pointer`; ranks which posting in a dedup group owns the Apply button
5. `enabled`
6. `poll_interval_s` — DEFAULT 900; HTML scrapes override to 1800
7. `etag` — conditional-GET validator
8. `last_modified` — conditional-GET validator
9. `last_success_at`
10. `consecutive_failures` — drives the "1 failing" indicator
11. `UNIQUE (source_key, config)` — no nullable member, so no `NULLS NOT DISTINCT` needed

### role_umbrella
1. `id`, `key`
2. `name` — "Quantitative Developer — Proprietary Trading"
3. `tier` — ★ marker
4. `notes` — timing/strategy line
5. `version` — bumped when variants are edited, so stale labels become findable

### role_title_variant
1. `umbrella_id` — FK
2. `title` — the literal string as posted
3. `is_generic` — true when the title appears under >1 umbrella; `Software Engineer Intern` appears under **seven**, which forces company-based routing
4. `company_id` — nullable; pins Citadel's "Core Developer" to one umbrella

---

## L1 — Ingest (bronze)

### poll_run
1. `id`, `feed_id`
2. `started_at`, `finished_at`, `duration_ms`
3. `status` — `ok` | `partial` | `error`
4. `http_status` — 200 vs 304 vs error
5. `items_listed`, `items_new`, `items_changed`, `items_gone`
6. `error_class`, `error_detail`

### raw_document
Also serves as the revision history — no separate `posting_revision` table.

1. `id`, `poll_run_id`, `feed_id`
2. `external_id`
3. `kind` — `list_item` | `detail` | `feed_item` | `envelope`
4. `content_hash` — sha256 over the **canonicalized** payload, volatile keys stripped
5. `payload_json JSONB` — for JSON sources
6. `payload_text TEXT` — for XML (Teamtailor), HTML (Jobvite, JSON-LD), MIME (Gmail)
7. `fetched_at`
8. `UNIQUE(feed_id, external_id, kind, content_hash)` — unchanged polls insert nothing

**Volatile keys excluded from the hash**, or every poll looks like a change:
Rippling `board.logo.url` (signed, rotates per request) · iCIMS `meta_data.opensearch.*`, `last_mod` · Oracle `Relevancy`, `SearchId`, facet counts · Workday `postedOn` (rolls daily)

`kind='envelope'` exists for Workable, whose company name lives at the response root rather than on any element.

---

## L2 — Canonical (silver)

### posting
`UNIQUE(feed_id, external_id)`

**Ours — observation & lifecycle**
1. `id` — PK
2. `feed_id` — the re-fetch address, and the scope for closure logic
3. `company_id` — **NOT NULL**, resolved per posting; the most-joined FK in the schema, never JSON
4. `group_id` — dedup grouping; repointed by the union-find pass
5. `first_seen_at` — the freshness clock the 72-hour window runs on
6. `last_seen_at` — last `ok` poll that still listed it
7. `last_seen_poll_run_id` — makes absence attributable
8. `detect_latency_s` — generated; `first_seen_at − published_at`, NULL unless precision is `exact`
9. `status` — `open` | `closed` | `unlisted`
10. `closed_at`
11. `closed_reason` — `source_flag` | `absent_from_poll` | `manual`
12. `dismissed_at`
13. `dismissed_reason`

**Universal — all 14 ATSs supply these**
14. `external_id` — TEXT always; ids are int/uuid/slug/hash across sources
15. `title`
16. `title_normalized` — adapter-written; the join key to `role_title_variant`
17. `url_posting`
18. `location_raw` — the display string exactly as given
19. `description_html` — **nullable**; Simplify and Gmail rows carry none, which is what makes them pointers

**Sparse — each defended individually**
20. `requisition_id` (6/14) — employer-assigned, so it's the cross-ATS dedup join key
21. `employment_type` (12/14) — raw string, unmapped
22. `workplace_type_raw` (6/14) — raw string; normalized value and day count are labels
23. `published_at` (12/14)
24. `published_at_precision` (14/14) — `exact` | `date` | `unknown`
25. `source_active` (5/14) — lets you close on fact rather than inference

**JSON**
26. `locations_extra` — Ashby `secondaryLocations[]`, iCIMS `additional_locations[]`
27. `content_sections` — Oracle's six description fields, SmartRecruiters' four, Comeet's ordered `details[]`
28. `source_extra` — everything else verbatim: Greenhouse `metadata[]`, iCIMS `tags1–4`, Oracle skills and flex fields, plus every column cut below

**Cut, and where each went:** `source_group_key`, `apply_email`, `department`, `team`, `language`, `updated_at_source`, `country`, `url_apply` → `source_extra` · `closes_at`, `is_internal`, `comp_*`, `description_text`, `city`, `region`, `country_code`, `is_remote`, `workplace_type` → `posting_label` · `content_hash`, `revision`, `first_seen_poll_run_id`, `seen_count` → derived

---

## L3 — Derived (gold)

### posting_label
Append-only. One table for roles, stack, and extracted signals, because all three are "a derived claim about a posting, with provenance."

1. `id`, `posting_id`
2. `kind` — `role` | `stack` | `signal`
3. `key` — used by `signal`: `season`, `sponsorship`, `duration`, `yoe_max`, `comp_min`, `city`, `workplace_type`, `closes_at`, `is_internal`, `start_date`, `degree`
4. `value` — TEXT
5. `provenance` — `human` | `model` | `rule` | `source`
6. `confidence`, `is_gold`
7. `match_method` — `exact_title` | `company_route` | `human` | `model`
8. `taxonomy_version` — which `role_umbrella.version` produced this
9. `created_at` — never updated; a view collapses to latest, human beating model

`provenance='source'` is how Ashby's structured compensation and Simplify's pre-labeled `terms`/`sponsorship` enter without pretending to be inferences.

---

## L4 — User

### profile
1. `id`, `name`
2. `yoe`, `needs_sponsorship`
3. `target_umbrellas`, `preferred_locations`

### application
1. `id`, `profile_id`
2. `posting_id` — **nullable**; manual entries, and to survive posting purges
3. `company_id`, `title_snapshot` — denormalized so history outlives the posting
4. `source_key`, `applied_via`
5. `resume_label`
6. `effort_minutes`, `created_at`

### application_event
Append-only; current state is a view.

1. `id`, `application_id`
2. `type` — FK to the lookup
3. `occurred_at`, `recorded_at`, `channel`, `note`

### application_event_type
A lookup table rather than an enum, so a new stage is an INSERT, not a migration.

1. `key`, `label`
2. `rank` — deepest-reached ordering; terminal rejections carry 0 so they never inflate depth
3. `is_terminal`
4. `is_response` — what makes response-rate and per-company baselines derivable

---

## Company resolution

`feed` holds no company. Every adapter runs the same four-step cascade per posting, and **earlier steps always win** — invert this and one Simplify poll stamps 14,627 rows with a single company.

```
1. element      company name or tenant slug on the posting itself
2. envelope     company name at the response root          (Workable)
3. config       feed.config->>'company_slug'               (Oracle, Jane Street)
4. create + flag  new company row, tier NULL, queued for your review
```

Resolution always ends in a lookup against `company.aliases`, which holds both display names and tenant slugs. **13 of 19 sources land on step 1.**

| Step | Sources | Resolves from |
|---|---|---|
| 1 — element | Greenhouse | `company_name` |
| | SmartRecruiters | `company.identifier` |
| | Jobvite | `hiringOrganization` |
| | iCIMS | `hiring_organization`, `client_code` |
| | Breezy | `company.name` |
| | Comeet | `company_name` |
| | Simplify / GitHub | `company_name` |
| | JSON-LD | `hiringOrganization` |
| 1 — url | Ashby | `jobUrl` → `jobs.ashbyhq.com/{slug}/` |
| | Lever | `hostedUrl` → `jobs.lever.co/{site}/` |
| | Workday | `externalUrl` → `{tenant}.wd5.myworkdayjobs.com` ⚠ *not* `hiringOrganization`, which is a payroll entity |
| | Rippling | `url` → `ats.rippling.com/{slug}/` |
| | Teamtailor | `<link>` → `{sub}.teamtailor.com` |
| 2 — envelope | **Workable** | root `name`; elements carry no company at all |
| | **BambooHR** | list elements have no URL — needs the detail call or the subdomain from `config` |
| 3 — config | **Oracle ORC** | no brand anywhere in the payload; `LegalEmployer` is a legal entity |
| | **Jane Street** | single-company site, nothing in the payload |
| 4 — flag | Gmail, HN | parsed from prose, always low-confidence |

---

## Audit

### 1. `title_normalized` cannot be a generated column — OPEN
Postgres generated columns must be pure immutable SQL, and Unicode TR39 confusables folding isn't expressible there. Jane Street serves `ꓟachine ꓡearning ꓣesearcher` — U+A4DF/A4E1/A4E3, Lisu letters standing in for `M`/`L`/`R`. Neither `lower()`, `unaccent()` nor NFKD touches them, so the posting would never match `role_title_variant`.

**Solution — adapter writes it as an ordinary column.**
```sql
title_normalized text NOT NULL          -- NOT generated
```
```python
NON_ASCII = re.compile(r'[^\x00-\x7F]')

def normalize_title(t: str) -> str:
    if NON_ASCII.search(t):
        log.warning("non-ascii in title, possible watermark: %r", t)
    t = fold_confusables(t)                       # Unicode TR39
    t = unicodedata.normalize('NFKC', t).casefold()
    return re.sub(r'[^a-z0-9]+', ' ', t).strip()
```
No `norm_version` column — when rules change, re-run over all rows; at ~6k postings that's sub-second, so versioning costs more than it saves. Keep the warning: it's how you spot the next site that watermarks.

### 2. NULL in the feed unique constraint — CLOSED
Resolved by removing `company_id` from `feed` entirely. `UNIQUE (source_key, config)` has no nullable member, so there is no `NULLS NOT DISTINCT`, no `board_key` generated column, and no PG15 floor.

### 3. Per-poll write load — OPEN
6k postings × 96 cycles/day = 576k row updates just to record "still there." Every one is a new MVCC tuple.

**Solution A — drop `seen_count` and derive it.** More correct anyway, since a stored counter miscounts across `partial` polls:
```sql
SELECT count(*) FROM poll_run r
WHERE r.feed_id = p.feed_id AND r.status = 'ok'
  AND r.started_at BETWEEN p.first_seen_at AND p.last_seen_at
```

**Solution B — keep `last_seen_at` writes HOT.** It's the observation itself and can't be derived, so make it never touch an index:
```sql
CREATE TABLE posting (...) WITH (fillfactor = 80);
-- and never index last_seen_at
```

**Solution C — batch one statement per feed, not per row:**
```sql
UPDATE posting SET last_seen_at = $1, last_seen_poll_run_id = $2
WHERE feed_id = $3 AND external_id = ANY($4);
```
38 statements per cycle, and HOT updates at `fillfactor=80` keep autovacuum comfortable.

### 4. Index plan — OPEN
**Solution — seven, and no more.**
```sql
CREATE UNIQUE INDEX ON posting (feed_id, external_id);
CREATE INDEX ON posting (first_seen_at DESC) WHERE status = 'open';  -- the queue
CREATE INDEX ON posting (group_id);                                  -- dedup + suppression
CREATE INDEX ON posting (title_normalized);                          -- role matching
CREATE INDEX ON posting USING gin (source_extra jsonb_path_ops);
CREATE INDEX ON company USING gin (aliases);
CREATE INDEX ON posting_label (posting_id, kind);
```
The partial index is the important one — closed postings drop out of it, so the hottest query keeps scanning only live rows after a year of accumulation.

### 5. Company resolution can silently mis-stamp — NEW
If the cascade is ever evaluated config-first, a single aggregator poll assigns one company to every row. And a step-4 miss silently creates near-duplicate companies (`"Jane Street"` vs `"Jane Street Capital"`).

**Solution — enforce order in one shared helper, never per adapter, and make step 4 visible:**
```python
def resolve_company(element, envelope, feed) -> tuple[int, str]:
    for src, val in (('element',  company_from_element(element)),
                     ('envelope', company_from_envelope(envelope)),
                     ('config',   feed.config.get('company_slug'))):
        if val and (cid := lookup_alias(val)):
            return cid, src
    return create_pending_company(element), 'flag'   # tier NULL, surfaced for review
```
Add a `v_pending_companies` view over `company WHERE tier IS NULL` so step-4 creations are reviewed rather than accumulating unnoticed.

---

## Dedup — three rules the views must encode

1. **Primary is by trust, not recency** — rank `origin > aggregator > pointer`, tiebreak on `source_active IS TRUE`, then `last_seen_at`. Without this a company mid-ATS-migration flips its Apply URL between polls.
2. **Group status follows the highest-trust member** — not OR/AND. Greenhouse says closed and Simplify hasn't noticed: the group is closed. "Any member open" keeps dead reqs alive forever.
3. **Application suppression is checked at group level** — `application.posting_id` correctly points at the posting you applied through, but the queue filter must test `group_id`, or the Ashby duplicate resurfaces the moment you apply via Greenhouse.

---

## Known and accepted

- **`posting_label` has no FK on role keys** — a typo becomes a silent phantom label; add the FK once the taxonomy stops churning.
- **No automation action log** — add `action(posting_id, kind, idempotency_key, occurred_at)` before anything auto-notifies, or a restart double-fires.
- **Latency is unavailable for 3 sources** — Workday (`"Posted Today"`), Comeet (no publish field), Jane Street (none in `main.json`). By design `published_at_precision` makes this explicit rather than fabricating a number.
- **Scoring is deferred entirely** — it arrives as new L3 tables reading `posting_label`, touching nothing above it.
