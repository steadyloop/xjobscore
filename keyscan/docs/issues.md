# Findings — 2026-08-20

Measured against live endpoints, crawl `CC-MAIN-2026-30`.

## Fixed

### oracle marked every token on a live tenant as live
`recruitingCEJobRequisitions` echoes `siteNumber` back but never applies it. A
real site and a bogus one returned byte-identical payloads — same
`TotalJobsCount`, same job list — differing only in the echoed `SiteNumber`. No
inspection of that response can discriminate.

`recruitingCESites?q=SiteNumber='X'` *is* site-scoped: count 1 real, 0 otherwise.
Added `Rule.exists_url` / `exists_count_path` — an optional board-scoped
pre-check that short-circuits to `absent` before the job probe. Job counts kept.

Do **not** add `and StatusCode='ORA_ACTIVE'` to that filter: it returns count 0
for every site including real ones, which marks the whole vendor absent.

Open: a registered-but-`ORA_INACTIVE` site still reads live, though its public
board redirects to the tenant default.

### successfactors marked every company slug as live
Unknown companies answer 200 with a ~3KB stub (`An error occurred while
processing your request.`, no `<title>`) instead of 404; real boards are ~190KB.
The rule carried no markers at all. Added that sentence as an `absent_marker` —
confirmed absent from all three known-real boards.

### verify reported "no tokens found" when it never scanned
A non-200 on the crawl manifest was silently skipped, then fell through to
`no_tokens_found` — indistinguishable from a rule that genuinely finds nothing.
Four vendors were written off this way in one sweep; a back-to-back run gets
throttled at exactly this point.

Manifest fetches now retry (`default_request`); an unread crawl reports
`cc_unavailable` with the status. `no_tokens_found` now requires a completed scan.

Re-running the four afterwards: eightfold 58 tokens, smartrecruiters 676 tokens
— the rules were never the problem.

## Open

### eightfold token namespace is not reconcilable by rule
`token.token` holds the catalog's `key` (`microsoft`); keyscan needs
`{host}/{domain}` (`apply.careers.microsoft.com/microsoft.com`). Both halves are
in the catalog's `[feed.config]`, while the database token row has no config
field.

The bare key is a human-assigned label that appears in no URL, so composite →
key needs a lookup and key → composite is impossible. Reassembled by hand, all 6
boards probe live and the synthetic control is correctly rejected.

Writing the composite into `token.token` breaks
`LEFT JOIN token t ON t.ats = f.source AND t.token = f.key` (store.py, 3 sites) —
`feed.key` stays the bare slug, so every eightfold feed silently loses its
company. Needs a separate column, or `feed.key` updated in lockstep.

Note `apply.careers.microsoft.com` is a CNAME to `microsoft.eightfold.ai`. The
crawl rule (`%.eightfold.ai`) cannot see white-labelled hosts — but it is not
the reason eightfold reported no tokens; that was the manifest bug above.

### successfactors registry keys are a different product
Registry keys are Career Site Builder hosts (`jobs.sap.com`, RSS at
`/sitemap.xml`). The keyscan rule targets `career{N}.successfactors.com/career?company=`.
No redirect links them (`jobs.sap.com` stays put). Zero overlap — not derivable.

### vendor probe blocks
workday 403s on the first POST (`amplify.wd1.myworkdayjobs.com`); eightfold 403s
from `netapp.` / `prudential.eightfold.ai`. Both surface as `blocked_suspected`.

### smartrecruiters control is weak
Synthetic returns `inconclusive`, not `absent` → `warn_inconclusive_control`.
Not the oracle failure mode, but weaker than ashby/lever.

### unverified
successfactors, avature — `cc_unavailable` (403). Needs a re-run once the
crawl-side limit resets.

## Data

`avature/sandboxea` is stored `dead` in xjobscore but probes live (`checked_at`
NULL — likely never checked).
