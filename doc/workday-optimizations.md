# Current Workday coverage policy — 2026-10-17

The user selected highest coverage with higher request costs after the search
experiments showed that a shared keyword filter could not be certified airtight.
The implementation now uses a full unfiltered list on every poll and fetches
details for every titled row except the explicit excluded title categories.
Equal counts no longer skip pagination. Result-window partitioning remains.
There are no company-specific retrieval or student-board allowlists.

Unchanged detail payloads can still be reused within 24 hours. New rows and
previously list-only rows fetch details immediately. A forced daily refresh
catches description-only changes. Reusing any detail prevents the successful
poll from resetting that refresh clock.

Eligibility now refines the existing title verdict using applicant requirements.
It recovers ordinary-title student/new-graduate roles and the audited international
aliases, rejects required advanced-degree study and known program-staff cases,
and preserves bachelor-or-master alternatives and preferred graduate study.
This is a heuristic classifier; the research does not establish perfect semantic
recall or precision for arbitrary future employer descriptions.

Verification:

- Actual adapter replay retrieved all 4,615 rows across eight captured complete
  boards, with zero omitted identities. It used 235 list requests and 4,608
  uncached detail requests; seven explicitly excluded rows skipped detail.
- All 68 reviewed description regression cases passed. This is a curated
  regression set, not an unbiased population accuracy estimate.
- Five live detail checks passed using exact requisition IDs, including the
  three ordinary-title HPE graduate opportunities, New School's undergraduate
  German tutor, and its excluded graduate-only math tutor. Multiple HPE roles
  share the Customer Account Specialist title; identity matching used requisition
  paths rather than title alone.
- Source and scheduler/persistence regression checks: 1,053 passed, one skipped,
  ten live tests deselected. Cache-refresh bookkeeping and same-count
  replacements beyond page one are covered.

A separate stratified review of 60 additional stored descriptions confirmed
five errors: one BS/BA co-op rejected because a PhD was only preferred, a
student-teaching instructor requiring a completed master's, a Continuity of
Operations (COOP) professional, a staff co-op advisor, and an MSc-only thesis
internship. Those five cases are now regression fixtures. This review was
selected to expose failures and does not measure population precision.

The largest active feed in the DB has about 42,000 potentially in-scope rows.
At the configured two requests per second per host, a cold detail pass can take
roughly six hours before retries. Harvest now renews a still-owned, unexpired
feed lease while fetching, stops fetching on lost ownership, and settles any
renewal in flight before passing its newest token to success/failure handling.
Detail fan-out uses a fixed worker count instead of a task per row. This avoids
reclaiming a long harvest after the default one-hour lease and keeps pending
task memory bounded. No request rate limits were increased.

These are workspace changes. No running service was restarted and no production
posting rows were reclassified by this implementation turn.

---

# Previous shared search rule — superseded

The configuration now contains **one shared query for every Workday feed**. Company-specific query/facet plans and the HPE split were removed at the user's request. Older experiments below are historical and no longer describe the selected configuration.

```text
intern OR coop OR "co-op" OR "co op" OR student OR apprentice OR summer OR fellowship OR "work study" OR workstudy OR undergraduate OR grad OR NCG OR "early career" OR "early talent" OR "emerging talent" OR "campus hire" OR "school leaver" OR "development program" OR "rotational program" OR "launch program" OR "industrial placement" OR trainee OR praktik OR werkstudent OR stagiaire OR alternance OR estagio OR estágio OR aprendiz OR becario OR pasantia OR pasantía OR enrolled OR "working towards"
```

This retrieves candidates, not student verdicts. Bare `intern` and `grad` keep singular Intern and bare Grad titles in the vocabulary, at the cost of CXS substring matches such as internal/grade. Active DB examples include Software DevOps Engineer - Grad, Technical Support Engineer Grad and Project Coordinator (GRAD). Specific graduate phrases reduced HPE's result count only from 1,202 to 1,187 (one page); the shared rule retains the broader term. No API NOT exclusions are used.

## Automatic behavior

- Changed, warm boards try the same query without board allowlists or facet IDs.
- Empty, failed or uneconomical probes fall back to full enumeration. Missing candidates on the initial unfiltered page trigger an unfiltered retry. Failed later pages remain partial and cannot establish closure.
- Cold, forced and daily reconciliation polls remain full scans. Unchanged uncapped boards retain the existing one-request count shortcut.
- Existing local classification supplies word boundaries and undergraduate exclusions. A search match does not automatically become a student job or trigger a detail request.

## Live inventory and adapter replay

Eight complete inventories contained **4,615 listings**. Replaying the captured API responses through the adapter retained **641/641 current detail candidates**, with **235 → 213 list requests (9.4% fewer)** and 3,993 retrieved rows. This includes the initial probe and fallback costs. Detail requests remain separate and gated/cached.

| Feed | Full-scan list requests | Shared strategy | Used search |
|---|---:|---:|---|
| hpe/wd5/jobsathpe | 79 | 62 | Yes |
| adobe/wd5/external_experienced | 16 | 17 | No; full fallback |
| aoins/wd5/autoowners | 14 | 11 | Yes |
| cmegroup/wd1/cme_careers | 4 | 5 | No; full fallback |
| plexus/wd504/plexus_careers | 20 | 13 | Yes |
| tencent/wd1/tencent_careers | 16 | 17 | No; full fallback |
| newschool/wd1/external | 10 | 11 | No; full fallback |
| greystar/wd1/external | 76 | 77 | No; full fallback |

Tencent and New School returned zero search hits despite live candidate jobs; fallback preserved them. Adobe, CME and Greystar queries did not save pages, so the adapter discarded their search probe and finished the board scan.

The shared rule requires less manual maintenance but retrieves substantially more noise than the historical per-board plans. These results establish neither complete student recall nor a minimum false-positive rate. The classifier still misses ordinary titles whose descriptions reveal eligibility (see the HPE examples below), and some existing candidate titles may be staff or graduate-only roles. No classification rules changed in this retrieval revision. Daily full scans discover list rows but do not fix description-only classification gaps; equal-count replacement jobs can still wait for reconciliation.

Source tests: 851 passed, 1 skipped. No service was restarted.

## Undergraduate recall audit — 2026-10-17

The current search rule cannot be called airtight. A new live CXS probe on four
boards returned the following totals (the same `limit=20`, `offset=0`, empty
facets on every call):

| Board | Unfiltered | Current long OR query | Short `intern OR student OR coop OR apprentice` | `student` alone | `intern` alone |
|---|---:|---:|---:|---:|---:|
| New School | 189 | 0 | 0 | 189 | 28 |
| Tencent | 313 | 0 | 0 | 34 | 194 |
| HPE | 1,575 | 1,202 | 998 | 111 | 997 |
| Auto-Owners | 270 | 194 | 117 | 104 | 103 |

The fallback correctly enumerates New School and Tencent when the OR search
returns zero. A single universal CXS OR string is therefore not a reliable
recall boundary. HPE's search did include the ordinary-title postings
`Embedded Software Engineer, Firmware` and both `Customer Account Specialist`
roles, but the title detail gate skipped them; their descriptions describe
fresh-graduate opportunities. Search retrieval and local classification are
distinct failure points.

The eight-board adapter replay retrieved 3,993 rows, of which only 641 passed
the current detail gate. Fetching details for every retrieved row would add
about 3,352 detail requests in this sample; the 24-hour full reconciliation
would need details for its other rows too. The list endpoint exposes title,
relative `postedOn`, location and bullet fields, but no applicant eligibility
text. The database contains 1,401,507 Workday postings, with descriptions on
35,473; the 1,366,012 untyped postings have no descriptions. Hence the DB
alone cannot certify recall of undergraduate roles among skipped listings.

Live detail sampling found current-title-gate misses in non-English internship
and apprenticeship titles and in ordinary university student-worker titles
such as `German Language Tutor` and `Athletics Coordinator (FWS)`. It also
found admitted master/MBA-only internships and staff jobs mentioning students.
An experimental title/description classifier matched 45 deliberately selected
training cases but only 14 of 17 separate holdout cases; its three misses were
the ordinary HPE titles above. Random inspection of its proposed degree veto
found legitimate undergraduate roles it would reject, including postings that
allow a bachelor's *or* master's student. This classifier remains outside
production.

**Conclusion:** no tested positive-keyword query plus title gate guarantees
all undergraduate opportunities with zero false positives. The only complete
retrieval boundary is the unfiltered inventory. Inspecting descriptions for
every listing, with refreshes for description changes, is necessary to decide
eligibility when the title is inconclusive. That costs substantially more
detail requests, especially on a cold board, and even a description-based
rule needs ambiguity handling.
The current broad query is an acceleration with fallbacks, not an airtight
student-opportunity rule.

### Fallback boundary checked against the adapter

A controlled adapter replay placed an `Undergraduate Research Intern` on the
last unfiltered page and omitted it from a nonempty search result. The warm
changed-board poll accepted that result (three list calls) because the first
page's student sentinel was present. An equal-count poll returned after one
call and also missed the new role. At 25 hours after the last full poll, the
five-call unfiltered reconciliation retrieved it. This proves that the
first-page sentinel detects some search omissions but cannot certify complete
coverage, and the count shortcut cannot detect equal-count replacements.

Uncached request-cost comparison over the eight captured inventories:
current shared strategy = 213 list calls + 641 gated detail candidates;
unfiltered inventory with a detail for every row = 235 list calls + 4,615
potential detail calls. The latter is a ceiling before explicit excluded
titles and malformed list stubs are removed. This is a snapshot/backfill
comparison, not the incremental cost of subsequent cached polls.

---

# Historical per-board experiments (superseded)

# Workday retrieval conclusions — 2026-10-16

- Search includes titles, descriptions, location values and requisition IDs. Quotes do not establish word boundaries: `intern` matches `internal`; `grad` matches `grade`. Field-qualified/regex-like syntax did not provide reliable title-only matching.
- A universal OR expression is unsafe. Tested OR queries returned zero on New School, Greystar and Tencent despite live candidate jobs. Tencent's structured Intern facet worked.
- Use the cheapest **validated per-board plan**: student/intern facets where complete, otherwise keyword search. Facets alone missed real roles at Adobe, HPE, BMO, Toro and Plexus. Enrollment terms recovered Adobe/HPE internships without bare `intern`; Plexus still needed it. `grads` recovered two Plexus roles. Omitting noisy `work study` at BMO and `development program` at HPE preserved the audited candidates.
- Investigated 17 boards; completed 13 unfiltered inventories. Enabled nine plans retaining **649/649 broader title candidates and 634/634 current detail candidates** across 4,330 listings. These are candidate-coverage checks, not an exhaustive semantic recall or precision estimate.

| Board | Selected filter | List requests before → after |
|---|---|---:|
| Adobe | Keywords + enrollment | 16 → 6 |
| Auto-Owners | Intern facet | 14 → 6 |
| BMO | Keywords; omit work-study phrase | 49 → 14 |
| CME | Intern worker type | 4 → 2 |
| HPE | Keywords; omit development-program phrase | 79 → 26 |
| Keppel | Both intern worker types | 11 → 6 |
| Plexus | Broad keywords | 20 → 12 |
| Tencent | Intern worker type | 16 → 6 |
| Toro | Keywords + enrollment | 12 → 8 |

**221 → 86 list requests (61.1% fewer); 4,330 → 1,444 retrieved jobs.** These are changed, warm sample boards, including the initial unfiltered count request. Detail requests are separate. Unchanged uncapped boards still cost one request; cold and daily full scans retain their original cost.

Unvalidated boards remain unfiltered. Empty, failed or uneconomical search probes fall back to the board; losing a live first-page candidate triggers a full retry. Later page failures remain partial. Filtered omissions never establish disappearance or reset the daily reconciliation clock. Unfiltered reconciliation remains mandatory every 24 hours.

No tested search guarantees all future student opportunities with zero false positives. Ordinary titles can hide eligibility in descriptions; periodic full discovery and local classification remain necessary. The existing equal-count shortcut can still delay replacement jobs until reconciliation.

## HPE refinement — 2026-10-17

Implemented a two-branch plan using the initial unfiltered response's worker subtype facets:

1. Fetch all Temp Intern, Reg Apprentice and Temp Apprentice rows: **158 rows / 8 requests**.
2. Search every other advertised worker subtype for `graduate OR "working towards"`: **70 rows / 4 requests**. Compute these subtype IDs from the current facets so newly advertised types join this branch automatically.
3. Including the initial unfiltered probe, this costs **13 list requests**, versus **26** for the previous query and **79** for a full scan. The union contains **228 rows**.

A fresh full inventory contained 1,575 listings. The union retained **189/189 candidates admitted by the current detail gate**. Replaying the captured responses through the actual adapter produced 13 list requests and 189 detail candidates, with `filtered_listing=True`, `full_listing=False`, and no truncation. This verifies current candidate recall, not complete undergraduate classification. Detail request caching continues to apply separately.

The standalone query `graduate OR "working towards"` overlaps the student facets; applying it only to their complement saves three requests compared with the previously proposed 16-request union.

### Why not add NOT exclusions?

On the complement branch, individually excluding `senior`, `director`, `principal`, or `PhD` returned 63, 70, 65, and 69 results respectively. Each still needs four pages. Combining them as `(graduate OR "working towards") AND NOT (senior OR director OR principal OR PhD)` returns 60 results and saves one more request, but removes useful opportunities:

- **Embedded Software Engineer, Firmware** explicitly welcomes fresh graduates and requires a bachelor's degree or equivalent.
- Both **Customer Account Specialist** postings describe a graduate career-start programme.
- **ASIC Design Engineer 2** requires a bachelor's degree, refers to school/intern projects, and offers guidance from senior engineers. A master's is preferred, not required.

These ordinary titles also illustrate a remaining limitation of title-based classification: they are outside the current 189-candidate gate. The 13-request plan retains their list rows; classification of those rows remains a separate issue. Do not use the 189/189 check to claim that every undergraduate opportunity is classified.

### Runtime safeguards

Use the split only when facet counts cover the entire board and the combined request estimate beats a full scan. Deduplicate the union before details. Missing/inconsistent facets, empty or failed probes, result caps, incomplete pagination or missing candidate sentinels cause an unfiltered fallback. Daily full reconciliation and unchanged-board count probes retain their existing behavior. This is the lowest-request plan validated here without the observed losses from NOT, not a proof of a global optimum.

Source tests: **864 passed, 1 skipped**. No running service was restarted as part of this change.
