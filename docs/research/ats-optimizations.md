# ATS Optimizations

Per-ATS fetch/performance fixes, newest first. Subtitle = problem → result.

---

## bullhorn

### 2026-08-12 — 794 postings took 9.3s over 24 requests; now 4.1s over 2 (~2.3x)

- **Cause:** Not the throttle, for once — `count: 100` against a board that will serve
  500. Descriptions are inline (`publicDescription` is in the list projection, there is no
  detail leg), so page count *is* the cost: 8 requests for the verified test tenant, 24
  across the three recorded runs.
- **Fix:** `count` 100 → 500, with `page.size`/`step` following it and `max` 50 → 10 so the
  sweep keeps the same ~5,000-row ceiling. 500 is the vendor's own clamp, not a guess:
  `doc/integration-plan.md:221` records that a larger `count` is silently reduced to 500
  with a `messages` warning. No fan-out needed or added.
- **Verified:** live harvest, 792 postings in 4.1s over **2 requests** (was 8 for this
  tenant), all 200s, 0 retries, `truncated` false. 4.04s of the 4.1s is HTTP, so this is now
  bandwidth-bound and `page.concurrency` would buy nothing — measured, not assumed.
- **Note:** the general lesson, and it held everywhere it was checked — fewer requests beats
  more parallelism. A page-size raise removes throttle sleep *and* server round-trips, and
  cannot provoke a 429 the way widening can.

---

## eightfold

### 2026-08-12 — 1,933 postings took 195.7s; now 22.8s for 2,001 (~8.6x)

- **Cause:** 10 rows/page, no detail leg — 194 sequential list GETs, ~259ms each, so
  ~1s x 194 was our own throttle. The config also had **no `max`**, so an unexpectedly
  large tenant walked unbounded.
- **Page size is not available, measured not assumed.** `pcsx/search` sends no size
  parameter and `doc/source-list-opus-audit.md:227` documents `num=50` on Eightfold's
  *sibling* SmartApply endpoint. Probed it directly against microsoft: **10 rows with
  `num=50`, 10 rows without**, identical `data.count`. That lever does not exist on this
  path, so fan-out is the only one.
- **Fix:** `page.concurrency: 2` (measured 4-page probe: width 1 3.2s, 2 0.9s, 4 1.0s,
  8 0.9s — flat past 2, so 2), plus `max: 500`.
- **Verified:** live full board — 2,001 postings, 201 requests, 22.8s, 0 retries, clean
  200s, `truncated` false.
- **Note on the cap:** `max: 200` was tried first and truncated the board *exactly* — the
  tenant reports `data.count` 2,000 and 200 pages x 10 is 2,000. Caught by the live run
  reporting `truncated: True`, which is the whole reason that flag exists. 500 leaves
  real headroom.

---

## bamboohr, rippling, adp, workday, vincere, jazzhr

### 2026-08-12 — the serial-detail shape, one config line each; 3-7x measured

The shape smartrecruiters' note flagged as "each a one-line config edit away, but their
rate limits are unverified". Now verified.

- **Cause:** identical in all six — descriptions live only on the per-posting detail
  endpoint, so a run is 1 list request + one GET per posting, each paced by our own
  1 req/s per-host throttle. Workday's baseline is the clearest reading: 29.8s wall over
  29 requests at width 1.
- **Fix:** `detail.concurrency: 4` (bamboohr, rippling, adp, workday),
  `detail_concurrency` 2 for vincere and 4 for jazzhr.
- **Measured** (per-width, all clean 200s, no 429s, no retries, item counts stable across
  every width — a fan-out that changed the row count would matter more than the speed):

  | source | w1 | w2 | w4 | w8 | set |
  |---|---|---|---|---|---|
  | bamboohr | 14.3s | 5.0s | 4.5s | 1.3s | 4 |
  | rippling | 21.2s | 1.9s | 1.2s | 0.9s | 4 |
  | adp | 6.4s | 0.9s | 0.7s | 0.6s | 4 |
  | workday | 29.8s | 10.2s | 7.8s | 6.3s | 4 |
  | vincere | 12.1s | 5.0s | 4.6s | 4.4s | 2 |
  | jazzhr | 10.5s | 9.0s | 4.4s | 6.9s | 4 |

- **Why 4 and not the 8 several probes preferred:** these boards are 6-24 postings, so
  7-29 requests. That is enough to establish that the vendor *tolerates* the fan-out; it
  is not enough to rank widths, and none of these vendors publishes a rate limit. 4 is the
  ceiling oracle, talentreef and smartrecruiters already settled on against vendors that
  do. jazzhr's ladder is the evidence for the caution — width 8 came back *slower* than
  width 4 on 9 requests, which means the tail is noise, not signal.
- **Verified:** live full boards, no caps — bamboohr 15.8s → 3.1s (14 items), rippling
  20.2s → 1.3s (21), adp 6.5s → 0.9s (6), **workday 106.1s → 15.2s over 98 requests (93)**,
  vincere 7.7s (10), jazzhr 16.7s → 6.3s (7). Every run clean 200s, 0 retries.
- **Still serial:** `gem`. Its detail endpoint is literally `/api/public/graphql/batch`
  and its body is already a one-element array, so the right fix there is batching rather
  than width — see below.

---

## tooling

### 2026-08-12 — width was being chosen by guesswork; now measured per vendor

- **Cause:** `concurrency` defaults to 1 and is opt-in per config, so every source starts at
  the slowest possible setting. The right width is vendor-specific — apple 429s at 4 and is
  clean at 2, oracle sustains 4 — so setting it from anything but measurement is a guess
  aimed at someone else's server.
- **Fix:** `python -m xjobscore.raw_inspection.fanout <source> --leg page|detail` runs the
  same harvest at widths 1/2/4/8 and reports wall/requests/retries/status-histogram per
  width, then prints a paste-ready config comment. Climbs rather than descends, stops at the
  first width drawing a 429 or retry, caps at 8, raises its cooldown to any observed
  `Retry-After`, clamps `page.max` to a small probe, and has a `--dry-run` that prints the
  worst-case request count before issuing anything.
- **Three things it had to get right:** a **fresh `Net` per width**, because
  `_host_semaphores` caches a host's first `concurrency` for the life of the Net — reusing
  one would measure width 1 four times and report a flat line as a result. Config overrides
  by **deep-copy-and-rebind**, because `CONFIGS` is `{**SIMPLE, **COMPANY_JSON, …}`, a new
  outer dict over the *same* nested dicts, so an in-place patch would edit the source table.
  And a **local telemetry `Net`** rather than importing the inspector's, which drags in
  fastapi and asyncpg.
- **Also:** `detail.max` (json-api) and `detail_max` (docxtract) — the detail-leg counterpart
  of `page.max`. A detail board pays one request per row, so without a cap the only bound on
  a poll was the vendor's row count. Both report `truncated`, so a capped poll never reads as
  a complete one.
- **Verified:** reproduces apple's hand-measured result (width 2; 1.0s/request at width 1,
  0.7s total at width 2 over a 4-page probe). 22 offline tests, including that a crashed
  sweep restores the original config object and that the shared source tables are never
  mutated.
- **Caveat it reports on itself:** a clean probe is weaker evidence than a clean full sweep —
  apple drew no 429s over 4 pages at width 4 but did over 228. The tool says so in its own
  output rather than letting a small sample read as a green light.

---

## hn

### 2026-08-12 — one run measured 20.2s; no regression found, but payloads cut 48% and runs now self-diagnose

- **Cause (of the 20.2s): none in the adapter.** The run is still in inspector SQLite
  (`result_id a45383e5…`, `10:40:23Z`, `elapsed_ms 20243.0`, 227 items). Its stored
  `network_json` splits it: 2 requests, 2330.5ms + 2302.3ms = **4632.8ms of HTTP**,
  leaving **15610ms local**. The feed's three other runs are 1653.2 / 1737.7 / 2057.2ms;
  decoding the 1653.2ms one the same way gives 1421.5ms HTTP and **231.7ms local**.
  Re-running the identical work measured 1.84s end to end:
  harvest 1.73s, `parse_record` 0.09s, `_content_hash` 0.01s,
  `estimate_raw_size` 0.01s, `estimate_size` 0.00s; `ResultStore.save` against a copy of
  the real 852MB DB 0.03s; `alias_lookup` 0.28ms/call = 0.06s for a whole run. Both
  halves were degraded at once — HTTP 3–4x slow *and* local work 67x slow on identical
  input — i.e. a starved process, not code. The same DB corroborates: the next run
  (`simplify`, 10:41:35) recorded 162275.9ms of monotonic time across a 4h53m wall-clock
  span, so the machine suspended a minute later. This was the first fetch after a wake,
  with cold page cache, DNS and TLS.
- **Fix 1 — the inspector could not answer this question itself.** The split above took a
  hand-decode of `network_json` in SQLite. Every run now records a stage breakdown
  (`harvest` / `project` / `parse` / `serialize`, or `failed`) plus `summary.elapsed_ms`
  (total time inside HTTP), stored inside the existing `network_json` column — no schema
  migration. Surfaced in the Network tab as a stacked bar with **Waiting on HTTP** and
  **Local** chips. Cross-cutting: applies to every source, not just hn.
- **Fix 2 — real waste, ~0.3s/run and half the stored bytes.** Discovery
  (`_latest_story`) read `hitsPerPage=20` to use three fields: now `DISCOVERY_HITS = 5`,
  **153,048B → 34,051B** on the wire. And `_highlightResult` — Algolia's verbatim copy of
  author/comment_text/story_title wrapped in match metadata — was **47% of every stored
  hit** (0.40MB of 0.86MB); the queries carry no search term so every `matchLevel` is
  `"none"` and every `matchedWords` is empty. Algolia returns it whatever
  `attributesToRetrieve` asks, so it is dropped client-side in `harvest`.
- **Verified:** live post-change run — 229 items, `elapsed_ms 2166.6`, stages summing
  exactly to it (harvest 2074.7 / project 5.7 / parse 84.9 / serialize 1.3), HTTP 1493ms
  over 2 requests. Stored bytes **754,373 → 394,012 (48% smaller)**; `raw_size_bytes`
  735,750 → 387,405. Key-by-key across all 229 items: exactly one key dropped
  (`_highlightResult`), none invented, no value mutated, `children` preserved on all 76
  items that carry it. Envelope keeps the full story record (`children`, `story_text`,
  `num_comments`, `points`, `title`) — projecting discovery down to three fields via
  `attributesToRetrieve` would have saved a further ~24KB but shrunk that view, so it was
  rejected.
- **Note:** stripping `_highlightResult` changes `router._content_hash`, so the core
  pipeline sees every hn posting as changed once and re-ingests it. Harmless — dedup is by
  `external_id` — but a one-time churn.
- **Not the cause, for the record:** `Net`'s 1 req/s per-host throttle costs hn only
  ~0.1–0.55s (2 requests). It *is* the dominant cost elsewhere — `jobvite` averages 105
  requests / 28.1s of throttle sleep, `apple` 230 requests / 186s — which is what the
  entries below address. Also ruled out by measurement: `alias_lookup` opening a SQLite
  connection per posting, the 852MB DB size, `ResultStore.save`, `estimate_size`,
  `_content_hash`.
- **Still open:** ~1/3 of the comment payload is discarded — `nbHits` 342, only 229 are
  top-level and survive the `parent_id == story_id` filter. Untested whether Algolia can
  filter to direct children server-side. Separately, the inspector DB is 852MB
  (`inspector_item` 545MB, `inspector_parse` 143MB) with 29,665 free pages ≈ 116MB
  reclaimable by `VACUUM`.

---

## oracle

### 2026-08-12 — ~185s harvest; now 70.6s measured (~2.6x)

- **Cause:** Oracle server think time dominated: ~4.5s per list page and ~11s per 200-id detail batch, paid serially across 12 + 12 requests. Batch 200 is already optimal; transfer is negligible with gzip.
- **Fix:** Opt-in concurrency `4` for predictable-offset pages and detail batches; removed unused `facetsList`. Oracle sustained 6 concurrent requests with all 200s/no rate limits. Concurrency-capped requests are paced by `Net`'s per-host semaphore instead of the 1 req/s interval.
- **Verified:** 2,231/2,233 postings with full descriptions in 70.6s. Short probes predicted ~45–55s; the server queues more under a sustained K=4 than isolated bursts showed.
- **Kept:** Full raw payloads; a faster `fields=` projection was rejected to preserve fidelity.

---

## apple

### 2026-08-12 — 4,584 postings took 230.5s; now ~21s (~11x)

- **Cause:** No detail fetch — 20/page = 230 sequential list POSTs, paced by our own 1 req/s per-host throttle. Their server answers in ~192ms; 81% of the run (186s) was our sleep, not theirs.
- **Fix:** Opt-in `page.concurrency: 2`. Along the way, fixed a latent bug in the shared fan-out helper: it counted remaining requests by `step` (cursor stride) instead of `size` (items/page) — harmless for offset boards like talentreef/oracle where the two are equal, but for apple's page-number cursor (`step: 1`, `size: 20`) it would have fired ~4,564 requests instead of 229.
- **Verified:** 228 requests, 4,542 items, 0 retries, clean 200s at concurrency 2 (21.1s). Concurrency 4 was tried first and got 429s on 2/228 pages — Apple's ceiling is lower than talentreef/oracle's, so 2 stays.

---

## smartrecruiters

### 2026-08-12 — LinkedIn 318.3s / ServiceNow 516.5s; now 20.0s cold and 0.6–1.2s warm

- **Cause:** The public list endpoint returns summaries only; full descriptions (`@jobAd.sections.*`) require one `/postings/{id}` request per active posting. There is no public bulk-detail endpoint, so a cold full-fidelity harvest necessarily costs `ceil(postings / 100) + postings` requests. The original inspector ran detail requests serially behind a 1 req/s host throttle: persisted runs took 318.27s for LinkedIn (302 items, 306 requests) and 516.47s for ServiceNow (478 items, 483 requests), both with zero retries.
- **Cold-fetch fix (inspector only):** Added keyword-only `JsonApiHarvestOptions` without changing the registered `harvest(feed, net)` source contract. SmartRecruiters inspector runs use a 100-row page stride, eight concurrent list pages, and eight concurrent detail requests. List order and the existing contiguous-successful-prefix/truncated behavior are preserved. Production ingestion retains its current SmartRecruiters configuration and concurrency.
- **Warm-fetch fix:** The inspector supplies full items from the latest snapshot. After listing current postings, it reuses a prior payload byte-for-byte only when the posting ID is still active and both non-empty revision markers, `jobAdId` and `releasedDate`, match exactly. New, republished, or incompletely fingerprinted postings fetch fresh details; removed postings disappear. Force Full bypasses both the list ETag shortcut and item reuse. Incomplete snapshots may contribute reusable details but cannot authorize a 304 short circuit.
- **Unchanged shortcut:** A complete snapshot's first-page ETag is sent on an ordinary fetch. A 304 hydrates the retained payload immediately, so the unchanged path makes exactly one network request. When the list changes, remaining pages are fetched concurrently and only changed details are requested.
- **Vendor-wide safety:** Every inspector HTTP client now shares a process-wide semaphore of eight for `api.smartrecruiters.com`. The cap applies to list and detail calls across simultaneous scheduled and manual feeds, preventing two otherwise independent fetches from creating 16 in-flight requests.
- **Verified live against LinkedIn:** Force Full returned 300 items in 20.02s with 303 requests, zero reused items, zero retries, and no 429s. An immediate unchanged fetch returned all 300 items in 0.58s with one request and 300 reused items. A warm run with a deliberately stale list ETag fetched all three summary pages, reused all 300 matching details, and completed in 1.20s with three requests and zero retries.
- **Observability and rejected shortcuts:** `network_json.summary` now persists `reused_item_count`, and the inspector shows a **Reused** statistic. Per-detail ETags do not improve latency (a measured 304 took about the same 0.57s as a 200). SmartRecruiters' authenticated Job Board Feed API can stream bulk details, but only for publications purchased for that job board, so it cannot replace public LinkedIn or ServiceNow career-board access.

---

## jobvite

### 2026-08-12 — 54 postings took ~54s; now ~2.1s (~26x)

- **Cause:** No bulk feed exists — every path (`jobs.json`, `sitemap.xml`, …) 200s the same SPA shell, so full posting data is one detail-page fetch per job. The `html_links` loop fetched them sequentially, each paced by our own 1 req/s per-host throttle: ~1s × (N+1) requests.
- **Fix:** Detail pages fetched via `asyncio.gather` with new opt-in `detail_concurrency: 5` in docxtract config + `concurrency` on `Net.get()` (semaphore replaces the min-interval lock for that host; default 1 leaves all other sources unchanged).
- **Verified:** Jobvite/Cloudflare tolerates concurrency — 8 parallel requests all 200 in <300ms. Diminishing returns past ~5: latency ~250ms/page, so higher values save <1s while looking more bot-like.
- **Note:** vincere/jazzhr share the `html_links` shape but stay at 1 — only jobvite's tolerance was tested.

---

## talentreef

### 2026-08-12 — Listings took ~55s; now ~10–15s

- **Cause:** 5,000 postings fetched 100/page = 50 sequential POSTs, paced by our own 1 req/s per-host throttle. Their server answers in ~0.4s — the wait was all ours.
- **Fix:** 1000/page × 5 pages, pages 2–5 fetched in parallel (new opt-in `page.concurrency` in the json-api engine + `concurrency` on `Net.post()`).
- **Also:** added `jobId` sort tiebreaker — day-precision `createdDate` made page boundaries unstable (~77 duplicate ids per sweep, now 0).
- **Floor:** sweep is ~33 MB (84% = descriptions we keep, no gzip support), so time = their bandwidth; they occasionally shape transfers down and runs stretch accordingly.

---

<!-- Next entry: ## <ats> / ### <date> — <problem; now result> / Cause / Fix bullets. -->
