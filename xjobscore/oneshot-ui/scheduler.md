# Deferred Scheduler Architecture

Status: design only. The current Oneshot application performs manual fetches
only. Nothing described here should start a timer, enqueue a feed, or make a
network request until scheduling is explicitly approved and enabled in a
future change.

## Purpose

The eventual scheduler should minimize time-to-discovery for high-value feeds
without starving long-tail discovery or bypassing the request policy already
implemented by `xjobscore.net.Net`.

The source catalog currently contains thousands of feeds, with most sharing a
small number of ATS hosts. Equal-frequency polling cannot meet a fifteen-minute
target under polite per-host throttling. Scheduling therefore has to allocate
latency by expected value instead of pretending every feed can be equally
fresh.

## Current manual-fetch boundary

The implemented application must retain these invariants until this design is
activated:

- Startup performs no network requests.
- A fetch runs only after an explicit user command.
- Only feeds enabled in OneShot's local `source_selections` overlay are
  fetchable. Catalog availability and local selection remain separate; a
  disabled selection retains all historical data.
- A user may fetch one feed or an explicitly selected, bounded set of feeds.
- A user may reparse the latest durable capture locally; replay never performs
  a network request and never becomes scheduled work.
- The existing adapter registry, parser registry, catalog, and shared `Net`
  client remain the only transport path.
- Manual fetch history, validators, parsed observations, source health, and
  errors are persisted so they can seed a future scheduler safely.

## Proposed components

```text
Catalog + local feed state
          |
          v
   Priority planner -----> due-work queue -----> bounded fetch workers
          ^                                           |
          |                                           v
 health / relevance <----- serialized ingest <----- adapters + parsers
          |
          v
   next-run policy + metrics
```

### Priority planner

The planner reads persisted feed state and produces immutable work items. It
does not fetch, parse, or write observations. Its candidate set is the
intersection of catalog-available feeds and the user's local enabled-source
overlay; discovering a new catalog feed never schedules it implicitly. Each
item contains the stable feed reference, priority class, reason, planned-at
time, and current state version.

Priority classes:

1. **Watchlist / one-shot — 15 minutes.** Explicit user watchlist, one-shot
   companies, and feeds attached to an active application.
2. **Known relevant — 60 minutes.** Feeds that recently produced an eligible
   role scoring at least 45 or a role the user saved.
3. **Long tail — six-hour sweep.** Every other configured feed, staggered by a
   deterministic hash of its stable feed reference.

HTML/document feeds may impose a slower source-specific floor. A manual fetch
always bypasses the due-time decision but never bypasses transport throttling.

### Due-work queue

The queue is persisted rather than kept only in memory. A work item is claimed
with a short lease so process crashes do not lose it and two processes cannot
poll the same feed concurrently. Expired leases return to the queue.

Ordering is by:

1. manual requests;
2. overdue watchlist feeds;
3. overdue relevant feeds;
4. long-tail age, with a starvation bonus;
5. stable feed reference for deterministic ties.

A weighted admission rule reserves capacity for the long tail whenever it is
behind schedule. High-priority feeds may preempt ordering, but cannot consume
every worker indefinitely.

### Fetch workers

- Use one process-wide `xjobscore.net.Net` instance so all feeds share the
  existing per-host throttle and connection pool.
- Bound global concurrency independently from per-host request spacing.
- Replace catalog feeds' empty state with persisted ETag/Last-Modified
  validators before calling the adapter.
- Call only `sources.get(feed.source)` and `sources.parser(feed.source)`;
  never import vendor modules directly.
- Send completed records to a single ingest queue. Workers do not write SQLite
  observations directly.

### Serialized ingest

One writer transaction records the poll result, raw revisions, current
snapshot membership, parsed observations, classification, dedupe evidence,
and feed health. WAL permits API readers while this transaction runs.

An ingestion failure makes the whole fetch attempt an error. It must not
advance validators, presence state, or the feed's successful-run timestamp.

## Scheduling state

Activating this design would require a new migration beneath this directory,
leaving the manual-fetch schema intact. Proposed additions:

```text
feed_schedule(
    feed_id,
    priority_class,
    priority_reason,
    cadence_seconds,
    next_due_at,
    last_planned_at,
    state_version,
    enabled
)

fetch_work(
    id,
    feed_id,
    kind,                 -- scheduled | manual
    priority,
    planned_at,
    leased_until,
    worker_id,
    status,               -- queued | running | completed | failed | cancelled
    attempt
)
```

Existing persisted validators, consecutive failures, source health, and poll
runs remain the source of truth; the queue must not duplicate them.

## Outcome and backoff policy

- `ok`: update validators, reset consecutive failures, and schedule from the
  successful completion time.
- `not_modified`: preserve observation membership and reset failures. Never
  interpret an empty 304 body as absence.
- `partial`: retain returned data for inspection but perform no absence-based
  lifecycle changes; increment failures and back off.
- `error`: preserve the last good snapshot and validators unless the response
  supplied a valid replacement; increment failures and back off.
- Missing parser: record a visible configuration error and do not repeatedly
  fetch the source until its parser status changes or a user retries it.

Backoff is exponential from the class cadence and capped at 24 hours. A
successful manual fetch resets the same failure counter a scheduled fetch
would use.

## Reconciliation safeguards

Absence is evaluated only after a successful, non-truncated response from a
source explicitly configured as both complete and authoritative.

- First valid absence: mark the source observation `unlisted`.
- Second consecutive valid absence: permit closure if no authoritative source
  remains open.
- Explicit employer closure: close that observation immediately and recompute
  the entity's provenance-aware status.
- Reappearance: reopen without deleting lifecycle history.
- 304, partial, error, aggregator absence, and unknown source capability:
  never close by absence.

Quarantine a snapshot before reconciliation when it unexpectedly returns zero
items or drops by a configured percentage from a sufficiently large prior
snapshot. A quarantined result remains inspectable and requires a confirming
poll or manual approval.

## Relevance promotion and demotion

Promotion to `known relevant` occurs when a successful ingest produces an
eligible score-band role, saved role, queued application, or explicit user
watch. Promotion is immediate.

Demotion is deliberately slow. A feed returns to the long tail only after a
full campaign window with no qualifying roles and no user relationship.
One-shot/watchlist status is never removed automatically.

Scoring-version changes should recompute relevance from stored observations;
they should not trigger network requests by themselves.

## Operations and observability

The source-coverage view should report:

- last successful and attempted fetch;
- priority class and reason;
- next due time and current lease, if any;
- response status, item count, and duration;
- consecutive failures and backoff expiry;
- parser availability;
- quarantined snapshots;
- manual versus scheduled origin.

Required health metrics include queue age by class, long-tail sweep duration,
per-host request starts, fetch outcomes, parse failures, ingest latency, and
the count of feeds without a working parser. Metrics are operational facts,
not a separate analytics dashboard.

## Activation sequence

1. Keep manual fetch as the only production path while collecting real item
   counts, durations, errors, and host behavior.
2. Add the scheduling migration and planner behind a disabled configuration
   flag. Verify that planning makes no requests.
3. Run a dry planner that records proposed work without leasing it; compare
   projected host load and long-tail sweep time.
4. Enable scheduled fetching for a small explicit watchlist while manual fetch
   remains available.
5. Add relevant-feed promotion, then a capped long-tail slice.
6. Expand only after fairness, restart recovery, reconciliation quarantine,
   and per-host limits pass live canaries.

There must be one kill switch that prevents new scheduled leases without
disabling manual fetch or the read-only board.

## Required tests before activation

- Application startup and planner-disabled mode perform zero fetches.
- Manual fetch remains functional while scheduling is disabled or killed.
- Deterministic staggering distributes thousands of same-host feeds across the
  six-hour window.
- Watchlist latency is maintained without starving long-tail work.
- Shared `Net` throttling applies across every worker.
- Leases recover after crashes and prevent duplicate concurrent fetches.
- Validators survive restarts and 304 responses preserve membership.
- Partial/error/quarantined results cannot close observations.
- First absence, second absence, explicit closure, conflict, and reopening
  follow the lifecycle policy.
- Backoff caps at 24 hours and success resets it.
- Relevance promotion is immediate and demotion respects the campaign window.
