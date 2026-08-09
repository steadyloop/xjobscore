# cc_ats — Common Crawl → ATS board tokens

ATS boards are public and mostly documented, but no vendor publishes a tenant
directory. The token in `boards.greenhouse.io/{token}` is not secret, just not
enumerable — so building an exhaustive posting list reduces to building the
tenant list yourself. Common Crawl's URL index already contains the ATS URLs
the web links to, across every vendor at once, including the ones whose tokens
are opaque IDs that could never be guessed (Paylocity's are UUIDs).

Runs on a laptop over plain HTTPS. No AWS account, no Athena. **No Postgres
dependency** — this module does pure discovery and probing.

## Running it

```bash
# Discovery and probing (no DB needed)
python -m cc_ats selftest                        # check extraction patterns
python -m cc_ats discover --crawls 2             # ~300 index files per crawl
python -m cc_ats probe                           # hours; resumable

# Loading and binding (see feed_ats_bind)
export DATABASE_URL=postgres://...
python -m feed_ats_bind load                     # discovered tokens → feed_ats
python -m feed_ats_bind bind                     # match companies to boards
```

Scope a run to one vendor with `--vendors greenhouse,lever` (the flag goes
before the subcommand). Working files default to `work/cc_ats/` — worth adding
to `.gitignore`; the parquet output is regenerable but not small.

Each stage writes to disk, so a failure in a later one never costs you an
earlier one. `discover` skips vendors whose parquet already exists; `probe`
appends to `probe.jsonl` and skips tokens already in it.

## How the scan is cheap

The index parquet is globally sorted by `url_surtkey`, which reverses the host
labels (`boards.greenhouse.io` → `io,greenhouse,boards)/path`). A range
predicate on that column lets parquet footer statistics skip nearly every row
group, so a nominally ~300 GB scan touches a small fraction of it. The bounds
in `surt_range()` work on byte order: `)` < `,` < `-`, so `[R)`, `R-)` covers
the apex and every subdomain while excluding neighbours like `greenhouse2.io`.

Token extraction runs *inside* the scan as a DuckDB expression, so what comes
back is thousands of grouped tokens rather than millions of posting URLs.

Both the `warc` and `crawldiagnostics` subsets are scanned, and there is no
`fetch_status` filter. A token lives in the *request* URL and does not care
what the response was. Measured on CC-MAIN-2026-30, adding `crawldiagnostics`
yields 29% more Greenhouse tenants — almost entirely 301/302 from Greenhouse's
`boards` → `job-boards` host migration, which is invisible in `warc` alone.
404s come along too; the probe stage is what turns those into `absent`.

## The registry

`vendors.py` holds one regex per pattern, used in two places: compiled by
Python for the bind stage, and emitted as SQL for the scan. Same string, so
the two cannot drift. Neither is compiled with `IGNORECASE` — DuckDB's
`regexp_extract` is case-sensitive, and a flag on only one side is exactly the
drift this design exists to prevent; `extract()` lowercases the host instead.

Pattern order is load-bearing. Greenhouse's `?for=` embed and Workday's
`/wday/cxs/` form must be tried before the general path rule, or you get
tenants called `embed` and `en-US`. `selftest` guards this.

Tokens match `feed.key`: a bare slug where one part identifies the tenant,
slash-joined where more is needed — Workday's
`nvidia/wd5/NVIDIAExternalCareerSite` is the example already in `schema.sql`.

**Probe quality is not uniform.** About half the vendors have public JSON board
APIs returning a real job count and sometimes a display name. The rest
(Teamtailor, iCIMS, Taleo, Avature, Eightfold, and others marked
`probe_kind="html"`) only tell us the board URL returned 200 — no company
signal, and a higher false-positive rate on vendors that serve a generic "no
such board" page with a 200. `absent_markers` mitigates that; it does not fix
it. Treat html-vendor live rates as softer than JSON-vendor ones.

**Regional API hosts.** Several vendors run separate data-residency endpoints
and a tenant exists on exactly one of them — Lever EU tenants 404 on
`api.lever.co` and 200 on `api.eu.lever.co`. `probe_fallbacks` tries each in
turn and only concludes `absent` once all of them 404. This is not a nicety:
without it the probe marked 39 of 40 real Lever boards `absent`, and `absent`
is written once and never revisited. Adding a vendor with a `.eu`/`.de` twin
means adding its fallback.

## Binding: why it only goes one way

The board almost never says who it belongs to. Lever returns no company name
at all. Matching a display name against `company.name` is not certain — two
rows can both plausibly be "Apex" — so **name similarity never binds
anything**.

What is certain runs the other way: from a hostname the company controls, to
the board. If `careers.acme.com` redirects to a board, or `acme.com/careers`
links to one, nobody but Acme could have put that there. `bind.py` therefore
iterates *companies*, not tokens, checking redirect chains and page hrefs. It
costs O(companies), not O(200k tokens).

Consequence worth being clear about: **ingestion coverage is capped by how
many companies bind, not by how many boards are discovered.** Unbound boards
accumulate in `feed_ats` as a durable backlog — cheap to hold, and cheap to
re-bind later by re-running `bind` after `company` grows, with no rescan.

Discovery never writes to `company`. It writes `company_alias` only on certain
evidence, and everything else goes to `company_new`. Alias conflicts — a string
already mapping to a different company — are **reported, not swallowed**, per
the reasoning in the `company_alias` PK comment.

## Vendors Common Crawl cannot see

Some vendors block CCBot in `robots.txt`, and Common Crawl honours it — so
their boards are absent from the index entirely, and the scan correctly
returns almost nothing. **This looks exactly like a broken regex and is not
one.** Check before debugging:

```bash
python -m cc_ats robots
```

Measured on CC-MAIN-2026-30, one crawl, with a 40-token sample per vendor
probed across the `url_count` distribution (the head is obvious companies; the
tail is where bad extraction hides):

| vendor | tokens found | sampled live | note |
|---|---:|---:|---|
| greenhouse | 4,615 | 100% | names resolve for all live boards |
| ashby | 2,997 | 88% | board API returns no company name |
| lever | 75 | 90% | see below — the *count* is the problem, not the rate |

Lever is larger than Ashby in reality; the 75 are essentially all
`jobs.eu.lever.co`, which is not covered by the same rule. Those tenants have
to come from the bind stage (company careers pages, which are unaffected) or a
second source such as the Wayback CDX API.

The check is only conclusive for vendors on a shared board host. For
`{tenant}.vendor.com` vendors there is no host to test without a real tenant,
so `robots` reports them as unknown rather than passing them silently.

## Known gaps

- **Pure-CNAME white-labels** where the company's careers host serves the board
  under its own domain: the URL never reveals the tenant. Caught via hrefs on
  the page, not via the URL itself. Some will be missed.
- **Phenom and other fully white-labelled ATSes** have no stable vendor
  hostname to range-scan, so they are not in the registry at all.
- **A live board is not a live company.** `probe` confirms the board answers;
  it says nothing about whether the jobs on it are current.
- **Single-site academic boards** — MathJobs, AcademicJobsOnline, Interfolio —
  carry a large share of academic math postings but are not multi-tenant.
  There are no tokens to discover; they need direct `feed` rows instead.
- **Common Crawl is a sample.** It is the highest-yield single source, not a
  complete one. Wayback's CDX API answers the same host-prefix queries over a
  longer history and is the obvious next source to add.

## Politeness

Requests carry an identifying User-Agent, back off on 429 honouring
`Retry-After`, and are capped both per-vendor and globally. Transport errors
are never recorded as `absent` — a timeout is not evidence a board is gone, and
writing it as one would permanently burn the token on one flaky night.
