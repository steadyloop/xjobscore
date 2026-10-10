# Token validation — observed signals

Probed 2026-08-19, one run, one IP. Real token from the registry or
the historical live-token snapshot; invalid token = `zq7x4kplmv2wnrt9`.
The generated snapshot was removed during repository cleanup on 2026-10-07.

`target` is what the rule actually requests: `probe_url` for json/xml,
`board_url` for html. A page-based check can only prove the *page* is gone,
not the board.

## Resolvable

| vendor | target | valid | invalid |
|---|---|---|---|
| greenhouse | board | 200 + count | 404 |
| lever | board | 200, 36 | 404 (US and EU) |
| ashby | board | 200, 62 | 404 |
| recruitee | board | 200, 8 | 404 |
| workable | board | 200 + `name` inline | 404 |
| adp | board | 200, 7 | 404 |
| gem | board | 200, 6 | 404 |
| manatal | board | 200, 32 | 404 |
| pinpoint | board | 200, 83 | 404 |
| rippling | board | 200, 12 | 404 |
| ukg | board | 200, 328 | 404 |
| workday | board | 200, total 2000 | **404** bogus site · **422** bogus tenant |
| eightfold | board | 200, 572 | 404 bogus domain · NXDOMAIN bogus host |
| oracle | board | 200, 2125 | NXDOMAIN |
| oleeo | board | 200, 27 | NXDOMAIN |
| peopleadmin | board | 200, 112 | NXDOMAIN |
| icims | board | 200, 1163 | NXDOMAIN (host is the customer's own domain) |
| personio | board | 200, valid `<workzag-jobs>` root — real even at 0 jobs | 429 here; root element is the signal |
| bamboohr | board | 200 `application/json` | 200 but `text/html` marketing page |
| breezy | board | 200, 1 | **403**, not 404 |
| homerun | page | 200, 21 KB | 404 |
| join | page | 200, 112 KB | 404 |
| teamtailor | board (RSS) | 200, valid RSS including zero jobs | 404 |
| trakstar | page | 200, 88 KB | 404 |
| avature | page | 200, 95 KB | NXDOMAIN |
| jobvite | page | 200, 13 KB | 200, redirects to `?invalid=1` |
| paylocity | page | 200, 31 KB | 200, 4 KB |
| successfactors | page | 200, 191 KB | 200, 3 KB |
| zohorecruit | page | 200, 1.78 MB | 200, 2 KB |

## Not resolvable

| vendor | why |
|---|---|
| dayforce | 417 KB vs 405 KB — same SPA shell either way |
| taleo | page returns a 1,452 B `htmlredirection` JS shim, never the board |
| comeet | 400 on both; needs the two-step board-id → api-token handshake |
| recruitcrm | untested — the rule is `POST` with `count_path=('data','jobs')`; the probe run above sent GET and got 405, so no verdict either way |
| fountain, talentreef | no rule |
| bullhorn, vincere | no token in registry or the historical live-token snapshot |

## Notes

Observed behavior and follow-up fixes in the shared probe:

- **Closed 2026-10-06 — Paylocity sweep repeated and lost failed HTML controls.**
  The console fetched its nonexistent-board control for every token, with
  no shared cache. A control 429 went through HTTP retries and host cooldowns,
  but its final failure was ignored: the candidate's HTTP 200 could still be
  recorded as live without a successful comparison. A generic HTTP-200 error
  page could also pass when no control fingerprint was available.
  Paylocity now parses the embedded `window.pageData` JSON, requires a `Jobs`
  array, and verifies `LeadJoinUrl` against the requested board ID. This takes
  one GET, including a board with zero jobs, with no control fetch. The HTTP-200
  `Job Not Found` title is absent; a page without valid board data remains
  inconclusive. Other HTML probes now share one answered control per control
  URL, including concurrent requests; failures are propagated and not cached.
  After pausing a rate-limited sweep, three public boards
  answered in 0.264–0.465s; malformed and nonexistent GUID controls both
  redirected to the same 4,633-byte `Job Not Found` page. All three captured
  real responses passed the corrected probe and the nonexistent one was
  absent. This is a small correctness sample, not a sustainable-rate benchmark.
- **Closed 2026-10-06 — Teamtailor's redirected careers pages stalled validation.**
  `triggerfish.teamtailor.com/jobs` redirects through `jobb.triggerfish.se` to
  `www.triggerfish.se/karriar/jobs`, whose nginx returns 429; CKW's jobs page
  redirects to a Cloudflare challenge. Both tenants' `/jobs.rss?per_page=1`
  answered 200 with valid RSS directly on Teamtailor. The shared probe now uses
  `/jobs.rss`, with `per_page=1` for liveness-only checks. Valid empty feeds are
  live; malformed XML and generic HTML stay inconclusive. This is the same
  endpoint the harvesting adapter uses and is documented in
  [Teamtailor's RSS guide](https://support.teamtailor.com/en/articles/11171756-rss-feed-how-to-guide).
  A 429 or bot wall after redirecting outside the vendor's domains is also
  scoped to that token: retry at the back, skip after two refusals, and keep
  other tenants running. Direct vendor refusals retain their cooldown policy.
- **Closed 2026-10-05 — workday 422.** Workday answers 422 both for a bogus
  tenant and for a real one on the wrong `wdN` host (no redirect). The probe
  now tries the rule's other `shards`: a 200 elsewhere is `absent` /
  `wrong_shard` with `moved_to` naming the working token, which the console
  adds as live; 422 on every shard is `absent` / `tenant_not_found`. A disabled
  site's 403 `{"errorCode": "S22"}` is `absent` / `site_disabled`.
- **Closed 2026-10-06 — a dead tenant host.** A transport failure now asks
  DNS-over-HTTPS (Cloudflare and Google, A and AAAA) about the host the probe
  requested. Only if every answer is NXDOMAIN, or NOERROR with no address, is
  the token `absent` / `no_such_host`; anything else stays `transport`. The
  OS resolver cannot decide this: macOS `getaddrinfo` gives EAI_NONAME
  (errno 8) for NXDOMAIN, SERVFAIL and timeout alike (Libinfo
  `mdns_module.c`; dnssec-failed.org, servfail.nl and brokendnssec.net all
  measured errno 8), and Mullvad's local resolver rewrites NXDOMAIN to an empty
  NOERROR. NOERROR-with-no-address must count as gone because DNSSEC zones that
  use compact denial (RFC 9824) answer a missing name that way: dead avature
  and eightfold tenants do, dead taleo ones answer NXDOMAIN. Measured: the
  first 4 queued tokens each of avature, taleo and eightfold read `absent`; 0
  of 243 `live` tokens across 9 vendors read as gone. A redirect target is
  never resolved, so a teamtailor tenant whose own careers domain is dead stays
  `transport`. A dead peopleadmin tenant also stays `transport`: its host
  resolves (`wild.pacloud.net`) but never accepts a connection.
- **Closed 2026-10-06 — smartrecruiters' bare zero.** After a 200 with count
  0, the rule's `zero_check_url` asks
  `/v1/companies/{id}/departments`: 404 `RESOURCE_NOT_FOUND` is `absent` /
  `company_not_found`; 200 is `live` / `company_without_jobs`, with count 0.
  It agreed with the careers page (a bogus id redirects to the bare
  `jobs.smartrecruiters.com`) on 42/42 ids. Of 30 previously-unclear tokens,
  20 were real companies with no jobs and 10 were gone. Ids are
  case-insensitive.
- **Gap — taleo's HTML shim does not prove a live board.** Its rule declares no
  `absent_markers`, no `html_required_markers`, no `html_any_markers`. An invalid
  control subdomain does not resolve, so the control now fails closed with
  `transport` rather than letting the 1,452-byte `htmlredirection` shim read as
  live. It still needs affirmative board evidence to settle liveness.

Already considered, and deliberately:

- **personio at zero jobs** stays `live` because the xml path carries no
  zero-count guard — a valid root element proves the tenant. Correct as written.
- **successfactors, zohorecruit, jobvite** are covered by
  `_html_control`: it fetches a known-invalid token's page, fingerprints it with
  digits and whitespace normalized, and calls a match `generic_html`. That is
  the "size delta" and "redirect to `?invalid=1`" signals. A failed control now
  stays inconclusive rather than disabling that comparison.
- **dayforce** resolves to `inconclusive` both ways. Correct — the same SPA
  shell is served either way and it genuinely cannot be told apart.
- **breezy 403 and bamboohr's HTML** fall to `inconclusive`. (A host with no
  address is now `absent`; see the dead-tenant-host note above.)

Context that is not a keyscan issue:

- **429 is not death.** Workable 429'd every token, valid and invalid, on one IP
  and answered cleanly from another. A vendor-wide 429 burst means blocked.
- **NXDOMAIN reach is narrow.** Only 7 vendors can produce it. 12 put the token
  in the host but have wildcard DNS (bamboohr, breezy, homerun, jazzhr,
  personio, pinpoint, recruitee, teamtailor, trakstar, vincere, workday,
  zohorecruit); 18 never put it in the host at all.
- **eightfold's token is not derivable from a registry key.** The catalog stores
  `key = "ford"` with `host = "ford.eightfold.ai"` in `[feed.config]`;
  microsoft's is `apply.careers.microsoft.com`. No template produces those.
- **`greenhouse:10xgenomics` is dead** — `verified = true` in the registry,
  404 `Job board not found`.

## Probe weight

Measured 2026-08-20, one board per vendor, decompressed response bytes summed
over every request a probe makes. `heavy` is the crawl's path
(`probe_rule(..., liveness_only=False)`, which persists `job_count`); `light`
is what the console's Tokens page now asks for, since it stores `status` and
`checked_at` and nothing else.

| vendor | board | heavy | light | how |
|---|---|---|---|---|
| ashby | 42dot (110 posts) | 1,286,422 B | **0 B** | `HEAD` — no limit parameter exists |
| lever | palantir (308 posts) | 5,946,353 B | **12,017 B** | `&limit=1` (495×) |
| gem | bb-imaging (21 posts) | 213,368 B | 213,368 B | no light path — see below |
| greenhouse | 66degrees (22 posts) | 13,528 B | **0 B** | `HEAD` |
| workable | 1global (63 posts) | 42,640 B | 42,640 B | shared: dropped `details=true` (was 580,172 B) |
| smartrecruiters | ADVENS (27 posts) | 1,358 B | 1,358 B | already `?limit=1`; excluded from light on purpose |
| workday | 9dot/wd503/OFY_Careers | 3,762 B | 3,762 B | already `limit:1`; `limit:0` answers 400 |
| | **total** | **7,507,431 B** | **273,145 B** | **27×** |

Every vendor returned the same verdict on both paths, and a bogus token still
read `absent` on the light path (`inconclusive` for smartrecruiters, as
documented above).

Why only ashby, greenhouse and lever (plus workable's shared change):

- A rule may only set `light_method="HEAD"` where **every** token that is not a
  live board answers non-200 — a 404 for one that never existed is necessary
  but *not sufficient*. Two vendors fail the sufficient half:
  **smartrecruiters** (a bogus company id answers
  `200 {"totalFound":0,"content":[]}`) and **gem** (see the zero-job section
  below). Both keep the full path.
- The consequence of HEAD is that a **real board with zero open jobs reports
  `live`** rather than reaching the `zero_count` guard. That is the correct
  answer to "does this board still exist", which is the only question the
  Tokens page asks — but it is why the crawl does not take this path.
- gem has **no** usable cheap probe. Its GraphQL board endpoint (2.3 KB)
  answers `200` with an empty list for a board that never existed, and HEAD on
  its REST endpoint cannot see the empty body that is the only signal
  distinguishing an unpublished board from an empty one.
- lever stays a `GET` rather than `HEAD` so the EU fallback keeps a body it can
  tell apart from a 404; one posting is already cheap enough.

### Heavy/light agreement, measured

53 probes run through both paths on 2026-08-20, covering all three token
classes:

| class | source | n | disagreements |
|---|---|---|---|
| live | historical live-token snapshot, 6 boards per vendor | 36 | 0 |
| live, zero open jobs | dedicated sweep, see below | 22 | 1 by design (ashby), 1 bug (gem, now fixed) |
| dead — was real, now gone | `token.status = 'dead'` in Postgres, plus registry rows that have since 404'd | 5 | 0 |
| synthetic — never existed (`zq7x4kplmv2wnrt9`) | one per vendor | 7 | 0 |

One live board per vendor, re-measured against the final rules:
7,507,431 B heavy vs 273,145 B light — 27x.

The zero-job sweep is what earned gem its exclusion, so it is worth
spelling out.

### Zero-job boards vs N-job boards

A board with no open roles is the case a cheap probe is most likely to get
wrong, so it was checked separately: 25 tokens scanned per vendor to find real
zero-job boards, then each compared heavy vs light and cross-checked against
its public board page.

| vendor | 0-job found | heavy | light | agree |
|---|---|---|---|---|
| ashby | 5 | inconclusive | live | differs, and **light is right** |
| greenhouse | 2 | live | live | yes |
| lever | 3 | inconclusive | inconclusive | yes — `limit=1` still returns `[]` |
| smartrecruiters | 3 | inconclusive | inconclusive | yes (no light path) |
| workable | 8 | live | live | yes |
| workday-list | 3 | inconclusive | inconclusive | yes (no light path) |
| gem | 6 | inconclusive | **live** | differs, and **light was wrong** |

N-job boards agreed everywhere, across 1 to 5,974 postings.

**ashby and greenhouse are safe.** All 7 zero-job boards found across 140
scanned tokens serve a live public page (`jobs.ashbyhq.com/<t>` and
`job-boards.greenhouse.io/<t>`, all 200 with a real title). Reporting them
`live` off a HEAD is correct, and better than heavy's `inconclusive`.

**gem is not, and this is why it has no light path.** Its REST endpoint answers
`200` with a body of `[]` for two different things:

```
api.gem.com/.../eqp/job_posts/                  -> 200 "[]"   jobs.gem.com/eqp                  -> 200 "eqp Careers"
api.gem.com/.../spherecast-ai/job_posts/        -> 200 "[]"   jobs.gem.com/spherecast-ai        -> 200 "spherecast.ai Careers"
api.gem.com/.../athumanresources-com/job_posts/ -> 200 "[]"   jobs.gem.com/athumanresources-com -> 404 "404 page not found"
api.gem.com/.../brahma-consulting-group/...     -> 200 "[]"   jobs.gem.com/brahma-...           -> 404 "404 page not found"
api.gem.com/.../zq7x4kplmv2wnrt9/job_posts/     -> 404
```

The board record outlives the published board. A HEAD probe sees `200` for all
four and calls them live, including two that are gone. The empty body is the
only signal, so gem pays the full 213 KB and the `zero_count` guard rules —
`test_gem_keeps_the_full_probe` pins this.

A separate scan of 210 registry tokens (30 per vendor) found only one that had
died since the file was generated on 2026-08-13, which is why the dead-class
sample is small: the corpus is a week old. Dead and synthetic tokens are
indistinguishable at the HTTP level for all seven vendors anyway — both produce
the same 404 from the same endpoint.

Watch out when reading raw `curl`-style measurements against Lever: three
boards (`abzena`, `creatio`, `coinspaid`) 404 on `api.lever.co` and are live on
`api.eu.lever.co`. They look dead unless the EU fallback is followed, which is
why `light_fallbacks` mirrors `fallbacks` rather than dropping it.
