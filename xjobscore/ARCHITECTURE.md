# Architecture

`schema.sql` is the pipeline storage contract. `frontend.sql` defines the
OneShot database, including its accounts, applications, feedback, and resumes.
Both define the five shared tables for companies, logos, postings, duplicate
groups, and the coverage snapshot; the publisher copies job data between databases.
The pipeline's `frontend_identity` table maps natural job/company identities to
stable serving IDs seeded from Neon. A publication allocates IDs once, then sends
the same cohort independently to the local frontend database and Neon. OneShot
can read job data locally while keeping its personal data connection on Neon.
The `oneshot-ui` repository owns the browser client and the sole request-facing
API in its `api/` package. This repository owns the pipeline and Console.
The implemented core path is:

```text
feed (PostgreSQL) → fetch → raw_posting → job_posting → Gold → OneShot
```

The unified runtime runs changed polls through Bronze, Silver, and targeted
Gold. Bronze remains independently replayable with
`python -m xjobscore.run --parse` after a parser fix.

## Feed inventory

PostgreSQL owns companies, aliases, feed inventory, source metadata, and
per-feed adapter configuration. A feed is created explicitly from an ATS token
or by an operator; workers never synchronize or seed feed rows.

- `token.config` holds endpoint-specific adapter values. It sits on the
  token, not the feed, because promotion rebuilds a feed from its token and
  config is the one input nothing re-derives; `token` + `company` therefore
  reproduces a feed completely.
- `token.company_id` is the feed's company identity and resolves its
  description from `company`; `feed.type` is feed runtime metadata.
- `feed.interval_s` defaults to 900 seconds; `feed.polling` is operator state.
- `backup.py` exports the durable seed -- `company`, `company_alias`, `token` --
  as JSONL and re-imports it, remapping company identity through a natural key
  (`domain`, else the lowercased name) because `company.id` is an identity
  column that means nothing in another database. `feed` is deliberately not
  exported: promotion rebuilds it. The file carries live API credentials in
  `token.config`, so the default path is under the gitignored `local/`.
- Deleting a feed does not delete its token, company, raw rows, or postings.

A feed instance is identified everywhere downstream as
`"{feed.source}:{feed.key}"`. The adapter's external ID remains unchanged as
`source_key`. This lets two tenants of the same ATS use the same vendor-local
job ID without colliding.

## Source boundary

Every source exposes:

```python
async def harvest(feed: Feed, net: Net) -> Record
def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]
```

`harvest` retrieves and demultiplexes a response without translating its
fields. `parse` is synchronous and pure. It receives reconstructed bronze data
and emits `Posting`, `Target`, `Label`, or `Fact` values.

`Feed` carries PostgreSQL configuration and scheduler state:
source, key, company metadata, ETag, response hash, interval, strikes, due
time, and lease. Conditional requests use ETag only.

## Scheduler

`uv run xjobscore` owns the scheduler, workers, token heartbeat, and Console.
One PostgreSQL advisory lock permits exactly one active system per database;
`scheduler_control.running` is the sole persisted polling switch.

The worker uses an `asyncpg` pool. Due rows are claimed atomically with
`FOR UPDATE SKIP LOCKED` by setting `feed.hold_until`; the default lease is
one hour and is configurable.

One cycle runs a worker pool per vendor rather than a single queue. Each pool
is `sources.width(source)` workers wide, and every worker claims one due feed
*of its own source*, polls it, and immediately claims the next -- so no feed
waits on a slower sibling, and a vendor that cannot keep up cannot crowd the
others out by accumulating the oldest `due_at` values. Widths are derived, not
declared: a shared-host vendor takes its list leg's concurrency, a per-tenant
vendor takes its `net._VENDOR_CAPS` ceiling divided by one poll's own fan-out,
and anything unmeasured is one. A vendor in `net._VENDOR_RATES` (Workday) also
has a request-rate budget that every tenant host shares; it, not the width,
sets that vendor's throughput, and it is spent below the vendor's limiter so
the steady state has no 429s. Workday's limiter counts per data center
(`wdN`), so each measured data center has its own budget and cap, the rest
share one, and each tenant host also has its own budget (`net._HOST_RATES`).
A vendor partitioned that way runs one pool per partition rather than per
vendor (`sources.lanes`: `workday:wd1`, …, `workday:other`), each claiming
only its own feeds, so one data center's backlog never parks the workers
another data center's budget could use. Vendor slots and budget tokens are taken per
attempt, around the wire call only, so a tenant waiting out its own 429
cooldown or retry backoff holds nothing its siblings need.
`feed_source_due_idx` serves the per-source claim. A cycle is bounded by `--cycle-budget-s` (default 30) rather than a feed
count, since 20 feeds is minutes of Workday and seconds of Greenhouse; the
budget stops claiming while polls in flight finish, and a cycle that ran out of
time with feeds still due reports `drained=False` so the caller runs another
immediately.

A claim requires an enabled, due feed with no `paused_at` and no
active lease. A successful attempt clears strikes and the lease, stores the
latest ETag, and advances `last_poll_at` and `due_at`. `last_change_at` and
`res_hash` advance only after a changed snapshot has parsed successfully.

Partial fetches and network/parser failures preserve committed bronze rows,
increment strikes, clear the lease, and use exponential backoff capped at 24
hours. They never set `paused_at` — that column is the operator's, written only
from the Console page's scheduler board, and a feed is never given up on
automatically. A parser failure deliberately leaves the
old `res_hash`, so the next poll retries the snapshot.

## Bronze

Every observed item is stored in `raw_posting.data` as:

```json
{
  "version": 1,
  "kind": "json",
  "payload": {},
  "envelope": {}
}
```

XML, HTML, and MIME payloads use the same wrapper with a string `payload`.
PostgreSQL receives the unmodified wrapper. `data_hash` is computed from a
normalized copy with source-specific volatile fields removed. Identical
observations reuse the existing `(source, source_key, data_hash)` row.

The listing response hash is computed from sorted `(external_id, data_hash)`
pairs, so response ordering cannot manufacture a change.

### Parse-time derivation

`job_type` and `remote` are derived by the adapter, in `parse()`, and carried
through silver untouched into the `job_posting` columns of the same name.

They are derived per vendor, never by one cross-vendor rule. Which field carries
a signal, how well it is filled, and which false positives it produces are
vendor facts, measured per source in `doc/raw_postings/`: Ashby publishes a
closed five-value `employmentType`, Greenhouse has no structured field and hides
it in a tenant-prefixed `metadata[]` bag, Workday's list leg carries a slug and a
12.9%-filled `remoteType`. Only the matching primitive is shared -- see
`xjobscore/classify.py` and the `{"classify": [...]}` form in the json-api
engine. Rule tables live beside the adapter that uses them and are transcribed
from that vendor's own doc.

The surveys are written in Postgres regex and a rule table is not: boundaries,
case and anchors all differ, and a pattern in the wrong dialect matches nothing
rather than failing. `classify.matcher` rejects the wrong dialect outright, and
`doc/raw_postings/transcribing.md` is the translation.

A vendor not yet transcribed emits `job_type` NULL and `remote` `'unknown'`.
That gap is deliberate and visible; a generic fallback would be a guess wearing
a measurement's clothes.

`remote` is never `'no'`. A stated on-site signal is not reliable enough to
assert, so it stays `'unknown'` -- indistinguishable from no signal at all.

## Silver

For a changed, complete snapshot, the worker first commits every bronze item
and obtains its durable `raw_posting.id`. It then parses each item and upserts
`job_posting` with that `raw_id`.

Canonical mapping is intentionally limited to columns in `schema.sql`:

- title and URLs map directly;
- HTML descriptions become readable plain text;
- raw location becomes `location_text`;
- published time becomes `posted_at`;
- explicit closure becomes `closed`; all other rows are `active`;
- `job_type` and `remote` are carried through from the adapter, not derived here;
- the Greenhouse adapter resolves `location.name` through its code-owned alias
  vocabulary into `country`; missing, ambiguous, and multi-country
  values become `XX`;
- recognized workplace and deadline values enter versioned `attributes`.

The canonical `content_hash` includes only stored fields. Upserts preserve
`first_seen_at` while refreshing `last_seen_at`, canonical values, status,
hash, and `raw_id`.

ATS and direct-company feeds fall back to the feed/token company. Boards
resolve `Posting.company_raw` through `company_alias`; unresolved board rows stay in
bronze and are logged/counted without failing the feed. Secondhand postings
remain bronze-only until the schema has a provenance model. `Target`, `Label`,
and `Fact` remain parser-compatible outputs but are logged/counted as
unsupported and do not create runtime feeds.

A 304 or unchanged response skips parsing and touches `last_seen_at` for the
source instance's existing postings, scoped to those whose `last_seen_at` is at
or after `feed.last_change_at`. A posting that predates the last snapshot
movement was absent from the snapshot being reconfirmed, so an unchanged
response must not stamp it; doing so would erase the absence signal that
`--reconcile` reads.

## Replay

`--parse` selects feed instances that have bronze rows, reads the latest raw
revision for each `source_key`, reconstructs `Item` and `envelope`, and runs the
same silver upsert path. It does not change scheduler state or run gold
enrichment.

Replay never advances `last_seen_at`. `latest_raw` returns every `source_key` a
feed has ever produced, not the ones it returns today, so a replay observes no
listing and says nothing about what is still present.

## Closure signals and reconciliation

`job_posting` keeps four independent timestamps together in the schema's
**Closure signals** section. Multiple signals may coexist on one posting:

| Signal | Column | Effect |
|---|---|---|
| Missing from a complete board listing | `missing_since` | Flag only; cleared if listed again |
| Job link returns 404/410 | `linked_dead_at` | Close |
| ATS explicitly reports inactive/closed | `ats_closed_at` | Close |
| Page states closure or redirects | `paged_closed_at` | Close |

Closure timestamps preserve the first detection in each channel. They are not
part of the canonical content hash. `link_checked_at` records the latest check,
including blocked or inconclusive requests; it is not a closure signal.
Apply `20261006_job_posting_paged_closed.sql`, then
`20261006_job_posting_closure_signal_names.sql`, to existing home and thin
databases before running the updated publisher. The latter renames the two
legacy columns in place; it preserves their values and can be rerun. The
verification timestamp is not a closure signal. Apply
`20261006_job_posting_remove_unused_review_columns.sql` to remove the unused
`link_marked_at` and `removal_marked_at` columns. User Skip and Report actions
are stored separately in `job_feedback`.

`python -m xjobscore.run --reconcile` queues missing jobs through the same durable queue.
Polling enqueues each missing episode atomically. The independently controlled
worker checks links, retries inconclusive results, and revisits live pages;
System → Link checks controls its concurrency, pacing and timing.
It selects active postings absent from their feed's latest complete `ok` poll.
A failed or truncated poll does not establish disappearance. Reconciliation
prefers each posting's stored source/apply URL and falls back to an adapter's
probe API if there is no page URL. No fetch is added to normal board ingestion.

One page request can detect a dead link, a closed-page message, or a redirect.
Per product policy, a changed page URL counts as page closure; URL fragments
and a trailing slash alone do not. Explicit English closure messages are read
from visible HTML text, excluding scripts, styles, and translation bundles.
JavaScript-only pages remain inconclusive rather than being assumed open.
HTTP 403/429, server errors, and transport failures are inconclusive even when
they occur after a redirect.

Confirmed observations are stored in bronze as `_probe` tombstones with the
requested/final URLs, HTTP status, matched message or redirect reason, and
original observation time. Silver replays these before vendor parsing and
writes only the corresponding closure timestamp, status, and check time.
A tombstone never overwrites the title, description, URLs, last-seen time, or
ATS closure signal. CLI totals distinguish link and page closures.

## Modules

| Module | Responsibility |
|---|---|
| `records.py` | Immutable feed, raw, and parser contracts. |
| `net.py` | HTTP, ETag conditional requests, retry, and rate limiting. |
| `work.py` | Pure interval/strike/backoff calculations. |
| `store.py` | All PostgreSQL operations and atomic claims. |
| `router.py` | Versioned bronze wrapper, hashes, persistence, reconstruction. |
| `silver.py` | Parser dispatch, company resolution, canonical projection/upsert. |
| `gold.py` | Derived enrichment and dedup artifacts computed from silver. |
| `probe.py` | Three-valued origin-URL check backing reconciliation by absence. |
| `run.py` | Polling engine plus explicit maintenance and replay commands. |
| `runtime.py` | Unified lifecycle, scheduler supervision, and process lock. |
| `pipeline.py` | Incremental Gold, retry batches, dedup generations, and run telemetry. |

Enrichment, scoring, notifications, dynamic targets, labels, and facts remain
deferred. `gold.py` does not read `status`, so closed postings are still
enriched and deduped.

## Gold

`gold.py` holds artifacts recoverable from `job_posting`. Nothing whose value
cannot be recovered from silver belongs here -- human judgements in particular.

Changed Silver rows immediately run the posting-local Gold steps: job-domain
classification, skills, degrees, and summaries. They run beside polling, not
inside the worker that polled: the poll hands its change set to
`GoldCoordinator.defer` and claims its next feed, with at most four feeds'
Gold running at once and the hand-off waiting once 64 are queued. Each step commits independently;
a failure degrades the pipeline run without turning a successful source fetch
into a source failure. Version stamps are the durable retry queue, and the
catch-up worker processes stale rows in bounded batches after restarts.

Dedup is generation-tracked global work. Silver increments the generation in
its own transaction; dedup runs after 30 quiet seconds when the queue drains,
or after five minutes at most. A change arriving during a rebuild leaves a
newer generation pending.

The Silver `job_type` remains vendor-derived. Gold's step named `job_type`
materializes the separate `job_domain` classification used by OneShot. Because
its quant-employer prior is company-wide, an incremental change reclassifies
all typed postings at every affected company.

The `skills` step extracts canonical aliases from each plain-text description
and writes `attributes.skills` plus `attributes.skills_meta` directly onto the
posting. The metadata records the full description hash and parse time. A row
also records the matcher version and a hash of `skills.yaml`; a row is skipped
only when both are current. Changing the dictionary therefore makes existing
rows eligible on the next skills pass. When a later silver upsert changes the
description, the upsert removes the skill result and stamp so the next gold pass
enriches it once again. Other source or parsed-field changes leave
description-derived Gold data intact. Empty skill lists are stamped too, so
“processed with no matches” is distinct from “not processed.”

The `degrees` step uses the degree patterns in `registry/job-posting/types.yaml` to record
the configured fields explicitly mentioned in a description as
`attributes.degree`. A field's `patterns` see the description alone -- a title
names the role, not its degree requirement -- so a title reaches the matcher only
through that field's explicit `title_patterns`.

Its staleness lifecycle differs from skills: the taxonomy revision is stamped
into the indexed `job_posting.degree_version` column, while `degree_meta`
records the version, parse time, and a hash of the exact title and description
used by the matcher. The step scans the scalar column on every pass, avoiding a
JSONB scan over a multi-gigabyte table, and bumping the version makes every row
eligible again. The upsert clears `attributes.degree`, `degree_meta`, and
`degree_version` whenever the title or description behind them changes, so a
title edit still re-enriches the
row.

The `summaries` step deterministically selects 40–50 words of role and
qualification evidence from classified student postings. It recognizes common
description headings, rejects company, compensation, benefits, application,
and legal boilerplate, and never invents prose. When credible role evidence is
missing it stamps an `insufficient_evidence` result rather than forcing a
summary. `attributes.summary_meta` records the extractor version, input hash,
status, and parse time; title or description changes invalidate both fields.
The exact scoring, selection rules, live-data findings, and tradeoffs are in
[`ROLE_SUMMARIES.md`](ROLE_SUMMARIES.md).

Cross-source deduplication is at stage one and **generates candidates only**.
Its key hierarchy is provider-native ATS requisition identity, then an exact
apply URL when the ATS is unsupported. Both require the same company identity.
Rows without either URL identity stay ungrouped: matching titles and locations
can describe distinct openings, so they never establish a duplicate match.
Fallback keys with more than ten members are dropped as placeholders;
authoritative ATS identities are not frequency-capped. Group ids are `uuid5`
of the versioned key, so the pass is idempotent.

URL identity extraction fails closed: a careers landing page shared by every
posting at a company yields no ATS identity. The exact-link fallback preserves
the full query string because some ATSs keep the requisition identifier there.
Location and normalized description hashes remain supporting evidence.

Nothing is confirmed, merged, or deleted. Gold selects `primary_posting_id` by
preferring the direct ATS row, then newest `posted_at`, then newest
`first_seen_at`. Exact ties remain unselected; database ID is not a
tie-breaker.
`requisition_id` is parsed by adapters but never reaches `job_posting`, so it
is absent from the evidence set.
