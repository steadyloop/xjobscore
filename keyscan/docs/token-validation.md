# Token validation — observed signals

Probed 2026-08-19, one run, one IP. Real token from the registry or
`validated-live.toml`; invalid token = `zq7x4kplmv2wnrt9`.

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
| teamtailor | page | 200, 97 KB | 404 |
| trakstar | page | 200, 88 KB | 404 |
| avature | page | 200, 95 KB | NXDOMAIN |
| jobvite | page | 200, 13 KB | 200, redirects to `?invalid=1` |
| paylocity | page | 200, 31 KB | 200, 4 KB |
| successfactors | page | 200, 191 KB | 200, 3 KB |
| zohorecruit | page | 200, 1.78 MB | 200, 2 KB |

## Not resolvable

| vendor | why |
|---|---|
| smartrecruiters | invalid token returns 200 + count 0 + no name; only the name check separates it |
| dayforce | 417 KB vs 405 KB — same SPA shell either way |
| taleo | page returns a 1,452 B `htmlredirection` JS shim, never the board |
| comeet | 400 on both; needs the two-step board-id → api-token handshake |
| recruitcrm | untested — the rule is `POST` with `count_path=('data','jobs')`; the probe run above sent GET and got 405, so no verdict either way |
| fountain, talentreef | no rule |
| bullhorn, vincere | no token in registry or `validated-live.toml` |

## Notes

Checked against `_probe`. Two gaps; the rest is already handled.

- **Gap — workday 422 is not read as absence.** `_probe` treats only
  `{404, 410}` as absent, so a bogus workday tenant lands on `inconclusive`
  / `http_422`. Largest catalog in the registry at 87 feeds.
- **Gap — taleo can return a false `live`.** Its rule declares no
  `absent_markers`, no `html_required_markers`, no `html_any_markers`. The html
  path therefore falls through to `_html_control`, whose control fetch NXDOMAINs
  (an invalid taleo subdomain does not resolve), leaving `control = None` and
  skipping the fingerprint comparison. The only surviving check is
  `"<html" in body`, which the 1,452 B `htmlredirection` shim satisfies — so a
  page that never reached a board reads as live.

Already considered, and deliberately:

- **smartrecruiters' bare zero** is the exact case the `zero_count` guard plus
  the `name_url` second probe exist for.
- **personio at zero jobs** stays `live` because the xml path carries no
  zero-count guard — a valid root element proves the tenant. Correct as written.
- **paylocity, successfactors, zohorecruit, jobvite** are covered by
  `_html_control`: it fetches a known-invalid token's page, fingerprints it with
  digits and whitespace normalized, and calls a match `generic_html`. That is
  the "size delta" and "redirect to `?invalid=1`" signals, handled properly.
- **dayforce** resolves to `inconclusive` both ways. Correct — the same SPA
  shell is served either way and it genuinely cannot be told apart.
- **breezy 403, bamboohr's HTML, NXDOMAIN** all fall to `inconclusive`.
  Defensible: `ConnectError` also covers SERVFAIL and DNS timeout, so absence
  cannot be claimed without an explicit resolve.

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
is what the inspector's Tokens page now asks for, since it stores `status` and
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
| live | `validated-live.toml`, 6 boards per vendor | 36 | 0 |
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
