# Architecture

`schema.sql` is the storage contract. The implemented core path is:

```text
registry → feed/schedule → fetch → raw_posting → job_posting → OneShot
```

Normal polls run bronze and silver together. Bronze remains independently
replayable with `python -m xjobscore.run --parse` after a parser fix.

## Registry and feed inventory

`sources/registry/` owns companies, aliases, source taxonomy, feed inventory,
company relationships, and adapter configuration. Worker startup synchronizes
its durable projection into `company`, `alias`, `token`, `feed`, and `schedule`.

- Existing `schedule.interval_s` values are preserved; new rows use the schema
  default of 900 seconds.
- Registry verification seeds `feed.enabled` the first time a feed is synced.
  After that the column is operator state, owned by the inspector: re-syncing
  the registry leaves it alone rather than resurrecting a feed that was
  switched off. The registry decides which feeds exist, not which ones poll.
- Removed feeds are disabled and removed tokens become inactive. Historical
  companies, tokens, raw rows, and canonical postings are retained.
- Adapter configuration is loaded from the validated registry at runtime and
  is not stored in PostgreSQL.

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

`Feed` carries registry configuration and schema-backed scheduler state:
source, key, company metadata, ETag, response hash, interval, strikes, due
time, and lease. Conditional requests use ETag only.

## Scheduler

The worker uses an `asyncpg` pool. Due rows are claimed atomically with
`FOR UPDATE SKIP LOCKED` by setting `schedule.hold_until`; the default lease is
one hour and is configurable.

A claim requires an enabled feed, a due schedule, no `paused_at`, and no
active lease. A successful attempt clears strikes and the lease, stores the
latest ETag, and advances `last_poll_at` and `due_at`. `last_change_at` and
`res_hash` advance only after a changed snapshot has parsed successfully.

Partial fetches and network/parser failures preserve committed bronze rows,
increment strikes, clear the lease, and use exponential backoff capped at 24
hours. They never set `paused_at` — that column is the operator's, written only
from the inspector's Scheduler page, and a feed is never given up on
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
- recognized workplace and deadline values enter versioned `attributes`.

The canonical `content_hash` includes only stored fields. Upserts preserve
`first_seen_at` while refreshing `last_seen_at`, canonical values, status,
hash, and `raw_id`.

ATS and direct-company feeds fall back to the registry/token company. Boards
resolve `Posting.company_raw` through `alias`; unresolved board rows stay in
bronze and are logged/counted without failing the feed. Secondhand postings
remain bronze-only until the schema has a provenance model. `Target`, `Label`,
and `Fact` remain parser-compatible outputs but are logged/counted as
unsupported and do not create runtime feeds.

A 304 or unchanged response skips parsing and touches `last_seen_at` for the
source instance's existing postings.

## Replay

`--parse` selects feed instances that have bronze rows, reads the latest raw
revision for each `source_key`, reconstructs `Item` and `envelope`, and runs the
same silver upsert path. It does not change scheduler state.

## Modules

| Module | Responsibility |
|---|---|
| `records.py` | Immutable feed, raw, and parser contracts. |
| `net.py` | HTTP, ETag conditional requests, retry, and rate limiting. |
| `work.py` | Pure interval/strike/backoff calculations. |
| `store.py` | All PostgreSQL operations and atomic claims. |
| `feader.py` | Authoritative registry projection into `schema.sql`. |
| `router.py` | Versioned bronze wrapper, hashes, persistence, reconstruction. |
| `silver.py` | Parser dispatch, company resolution, canonical projection/upsert. |
| `gold.py` | Derived artifacts rebuilt from silver; currently dedup candidates. |
| `run.py` | Startup sync, polling, inline ingestion, and replay orchestration. |
| `oneshot-ui/` | Reads `job_posting`; writes application-tracker tables. |

Reconciliation by absence, enrichment, scoring, notifications, dynamic
targets, labels, and facts remain deferred.

## Gold

`gold.py` holds artifacts that are pure functions of `job_posting` and are
maintained by full rebuild, never incremental edit. Nothing whose value cannot
be recovered from silver belongs here -- human judgements in particular, which
a rebuild erases.

`--gold` runs every step; `--gold-step <name>` runs one. Each step gets its own
transaction, so a failing step neither aborts nor half-writes the others.

Cross-source deduplication is at stage one and **generates candidates only**.
Postings are grouped by `(company_id, normalize_title(title))` into
`job_posting_dup_group` with `state = 'pending'`; keys with more than ten
members are dropped as placeholders rather than identities. Group ids are
`uuid5` of the key, so the pass is idempotent.

The key is deliberately the cheapest signal that fires on every posting. The
precise signals -- ATS identity extracted from the posting URL, a normalized
description hash, normalized location -- are recorded in `evidence` but are
**not** keyed on, so their real coverage and agreement can be measured before
any of them is promoted. URL resolution is extraction, not string comparison,
and fails closed: a careers landing page shared by every posting at a company
yields no identity rather than merging them.

Nothing is confirmed, merged, or deleted, and `primary_posting_id` stays null.
`requisition_id` is parsed by adapters but never reaches `job_posting`, so it
is absent from the evidence set.
