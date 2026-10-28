# ATS poll rate

Measured findings on how fast each ATS can be polled. Sources: `net.py`,
`sources/*-configs.py`, `keyscan/docs/`, production `poll_log` (2026-10-05).

scheduler
---
- One worker pool per vendor; width = shared-host list concurrency, or `_VENDOR_CAPS` / one poll's fan-out, else 1.
- Unconfigured hosts fall to `Net`'s serial 1 req/s path — the most common cause of a slow vendor.
- Claim order is oldest `due_at` first; never-polled feeds are ahead of re-polls.
- Failures back off `interval_s × 2^strikes`, capped at 24 h.
- `_HostPace`: unpaced until a 429, then 0.125–2 s spacing, halves every 30 s, resets after 10 min.
- Per-success pace decay (old) left Workday oscillating at ~3.5 req/s for 80 min; decay is now time-based.
- httpx default of 100 connections silently queued requests (no 429, looks like a slow origin); now set explicitly.
- Global dedup in the scheduler process (all 1.49M postings, 45–77 min, back to back) cut polling from ~15.6K to ~1K/hr. Disabled via `runtime.DEDUP_ENABLED`.
- In-process Gold awaited per poll took ~27 s of a ~48 s Workday poll; now deferred (max 4 concurrent).
- Egress is a rotating Mullvad VPN exit (17 switches in 34 h, 2026-10-05/06). Limits are per exit; a switch mid-test shows as timeouts, then 429s on the new exit. Pin the exit for any measurement.
- Each `Net` has its own pacer and budgets; other in-process clients (token sweep, inspector) don't share the scheduler's.

workday
---
- Cloudflare rate rule on CXS; single tenant: ~400-request burst, then 429 at ~3.5–5 req/s.
- Budgets (`_VENDOR_RATES`, ~80% of clean): `wd1` 8, `wd5` 8, `wd3` 6 req/s; all other `wdN` share 3. Plus 2 req/s per tenant host (`_HOST_RATES`).
- One scheduler lane per budget (`workday:wd1`, `:wd3`, `:wd5`, `:other`) = 22 workers; was one 6-worker pool at 3 req/s total.
- Limit counts per data center (`wdN`) per IP, shared by every tenant on it — not per IP overall, not per tenant, not per Cloudflare edge (2026-10-05, load spread at ≤0.2 req/s per tenant).
- `wd1`: clean 10 req/s × 6 min (twice); tripped at 30 (after 14 s, minutes after a 22 req/s run); one 88 s run at ~22 was clean. The "tripped at 20" run was a VPN exit switch mid-test (timeouts at 23:46:14, switch 23:46:15, 429s on the new exit), not a measurement.
- `wd3` (EU, Paris/Hamburg/Madrid edges): clean 8 req/s × 6 min; tripped at ~11 after ~990 requests.
- `wd5`: clean 10 req/s × 6 min. `wd1` 10 + `wd5` 10 together through LAX: clean, so the edge is not the counter.
- Other `wdN`: untested.
- No IP-wide limit seen up to 60 req/s total across data centers.
- Trip = Cloudflare managed challenge: HTML 429, `cf-mitigated: challenge`, no `Retry-After`. Clears in ~1–2 min once traffic stops.
- Single-tenant ceiling (3.5–5 req/s above) was not re-tested; keep per-tenant rate well below it.
- Caps 24 (`wd1`, `wd5`, other) and 16 (`wd3`), page fan-out 4 → 6/6/6/4 feeds at once.
- Ungated detail leg was ~91% of requests and the only thing that ever drew 429s; gated detail is 1.6% of that.
- No ETag; unchanged `total` stops after page 1.
- 2,000-row result window per query.
- Observed: ~420 polls/hr at full rate.

smartrecruiters
---
- One shared host, Cloudflare, no rate-limit headers. Documented 10 req/s / 8 concurrent applies only to authenticated SmartAPI.
- 0 429s at 64 concurrent list (~104 req/s) and 96 concurrent detail (~125 req/s).
- List 16 + detail 32 = 48 in flight (~100 req/s). Sustained load unmeasured — halve both if 429s appear.
- `step: 100` pagination is parallel-safe (40 boards / 330 pages, no gaps or dupes).
- Detail is one request per posting, so a cold big board is expensive.
- Observed: ~460 polls/hr.

workable
---
- One GET per poll: `apply.workable.com/api/v1/widget/accounts/{key}?details=true` returns the whole board. No pagination, no ETag/Last-Modified.
- Rows are per location: a shortcode can repeat (alexander-dennis 44 rows / 32 shortcodes). Largest seen 1,001 rows / 998 shortcodes; a ~1,000-row cap is unverified.
- `details=false` drops only `description`: ~6.4× fewer bytes, same request count. Requests, not bytes, are what's limited.
- v3 `POST /api/v3/accounts/{key}/jobs`: 10 per page, `limit` rejected (400), no descriptions.
- No concurrency configured → serial 1 req/s, width 1. 12.8K daily feeds need ~0.17 req/s.
- Scheduler alone: 5,546 polls in 2 h at up to 61/min, 0 429s (2026-10-06).
- 429s track other Workable traffic on the same egress, mostly the in-process token sweep (own `Net`, own pacer, 1 req/s): 1 429 in 6,902 sweep-free polls vs 51 in 6,823.
- The sweep alone at 1 req/s also tripped (2026-10-06 13:01–13:07), so a shared 1 req/s total is not proven safe.
- No fixed threshold: ~100–120 req/min combined tripped in 1–5 min; after long quiet 500–800/min was answered; ~50–70/min re-tripped within ~15 min of a block.
- Blocks seen 2–27 min, but polling continued through them (4 attempts per poll, 30 s pace cap) and several ended at VPN exit changes, so the real TTL is unknown.
- `Retry-After: 0` on 429 (per code comment; no 429 headers or body have been saved). Treated as "no hint".
- A 429 costs a daily feed 24 h (backoff capped at the interval); 80 of 97 429s were first-ever polls.
- 429'd every token from one exit while another was clean; 429 rates differed by exit at similar load.
- Global `www.workable.com/boards/workable.xml`: gzip, Range ignored, Last-Modified + 304 on If-Modified-Since. Has shortcodes, no account key, `Vary: User-Agent`. Size and coverage unmeasured.

greenhouse
---
- Shared host; 357-board burst: no throttling at widths 30–100.
- 30–40 held ~95 req/s (p95 508 ms); 100 dropped to 46 req/s from queueing. Width 30.

ashby
---
- Shared host; full 700-board sweep up to width 700 drew no throttling. Ceiling never found; 44 is chosen.

lever
---
- Shared host; 0 429s at widths 10–160 (~6,700 requests), no rate headers.
- Knee at 80 (~114 req/s); 160 dropped to ~62. Width 40.
- First polls are bandwidth-bound (~110 Mbps); steady state ~91% 304s.

oracle
---
- Tenants map to 77 Akamai IPs → vendor-wide cap is the right shape.
- 0 errors at 96 concurrent list and 72 concurrent full polls; cap 48.
- ~4.5 s server time per 200-row page, ~11 s per 200-id detail batch.
- Fan-out 4 (not 8): most boards ≤ 100 jobs, so narrower fan-out = more feeds at once.

eightfold
---
- 10 rows/page fixed; fan-out is the only lever. Scales to 16, flat after.
- Width 32 drew a vendor-wide 405 on httpx (curl was fine) — TLS fingerprinting, not a UA issue.
- Page fan-out 8, vendor cap 16 → 2 feeds at once.

taleo
---
- All tenants share a few load balancers; vendor cap 12 (provisional, mirrors Oracle).
- Clean to width 24 on two tenants; textron starts queueing at 24. Width 12.

rippling
---
- Shared host, Cloudflare; 0 429s over ~12K requests.
- List flat to width 40 (~315 req/s); detail linear to 32, drops at 64. List 8 + detail 24.
- No ETag; every poll is a full list.

gem
---
- Shared Heroku host; no 429s, but queues past ~40 in flight (~200 → ~87 req/s at 80).
- List 20 + detail 4.

apple
---
- Page fan-out 4 drew 429s on 2 of 228 pages; 2 is clean. Apple's ceiling.

bamboohr / adp / jazzhr / vincere / jobvite
---
- One GET per posting; serial 1 req/s dominated run time.
- Small probes clean at width 8; 4 used as the house ceiling for vendors with no published limit (jobvite: 5).
- BambooHR vendor cap 8.
