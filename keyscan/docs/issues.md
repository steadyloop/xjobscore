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

**Follow-up 2026-10-05 — vanity site names read absent.** A board URL names its
site by `SiteNumber` (`CX_4001`) or by its vanity `SiteURLName` (`Aerospace`);
99 of 605 oracle tokens in the historical live-token snapshot were the latter,
and the SiteNumber-only check called all 30 sampled `absent`. Added
`Rule.exists_fallbacks`: oracle now asks `SiteURLName='X'` when `SiteNumber='X'`
answers zero. Re-run: 27 of 30 live; the other 3 are vanity names the tenant no
longer has (the page redirects to its default `CX_*` site), so `absent` is right.
Both fields match case-insensitively. A single `q` with `or` does not work —
the endpoint answers 0 even when either half alone matches.

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

### workday, workable, smartrecruiters extracted junk tokens (2026-10-05)
- **workday**: `STOP` was checked against the tenant, dropping real ones
  (`academy`, 890 jobs), and never against the site, keeping files
  (`robots.txt`, `llms.txt`, `*.html` — 98 tokens). Now `stop_words=False` and
  a `token_rx` that rejects a dotted site.
- **workable**: the bare root fell through to the subdomain pattern as
  `apply`, and `/j/{code}` short links became `j`. Both now drop.
- **smartrecruiters**: `ni/`, `my-applications/`, `oneclick-ui/company/` and
  `external-referrals/company/` were stored as the company. The id after the
  prefix is now the token; numeric ids (API answers 0), `oneclick/` and
  `cdn-cgi/` drop. Short ids like `B6`/`PQ` are real and kept.

Per-rule checks live in `valid_token` / `token_filter_sql`, so Python and the
crawl's DuckDB filter stay identical; `selftest` now checks rejected URLs in
SQL too. Re-extracting all 704,670 stored URLs changed only these tokens.
Existing junk rows are not deleted.

## Open

### eightfold token namespace is not reconcilable by rule
`token.token` holds the catalog's `key` (`microsoft`); keyscan needs
`{host}/{domain}` (`apply.careers.microsoft.com/microsoft.com`). Both halves are
in the catalog's `[feed.config]`, while the database token row has no config
field.

**Data blocker removed 2026-08-27** by
`xjobscore/migrations/20260827_token_config.sql`, which moved the column from
`feed` to `token`. All 6 eightfold token rows now carry `config.host` and
`config.domain`, so the composite is derivable with a join instead of by hand.

**Still open:** no keyscan code reads it. `rules.py` keys eightfold on the
composite `host/domain` while `token.token` holds the bare key, and nothing
consults `token.config` to bridge them. The lookup is now possible, not done.

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
eightfold 403s from `netapp.` / `prudential.eightfold.ai`, surfacing as
`blocked_suspected`. The workday 403 once listed here
(`amplify.wd1.myworkdayjobs.com`) was not a block: it is Workday's answer for a
disabled site, a JSON `{"errorCode": "S22"}` body, now read as `absent` /
`site_disabled` (2026-10-05).

### smartrecruiters control is weak
Synthetic returns `inconclusive`, not `absent` → `warn_inconclusive_control`.
Not the oracle failure mode, but weaker than ashby/lever.

### unverified
successfactors, avature — `cc_unavailable` (403). Needs a re-run once the
crawl-side limit resets.

## Data

`avature/sandboxea` is stored `dead` in xjobscore but probes live (`checked_at`
NULL — likely never checked).
