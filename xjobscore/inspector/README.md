# Feed inspector

A localhost-only backend dashboard for the harvesting pipeline: fetch any feed
on demand, inspect the raw `harvest()` payload and the `parse()` preview side
by side, read per-request network telemetry (timing, retries, rate-limit
headers), and set polling policy. A Feed-detail fetch is a manual,
side-effect-free look at a feed — it stages its result in the inspector's own
schema and never touches `raw_posting` or `job_posting`. The Scheduler page is
the exception: its Poll now hands work to the production poller, and its Fetch
all runs the production path (`run.poll`) directly, one feed per request —
either way the real tables get written, not staging.

Production polling is a separate, opt-in concern: set `XJOBSCORE_INSPECTOR_RUNNER`
and this process will also run `run.py`'s real worker path — harvest through
`job_posting`, content-hash dedup and all — on a tick, started and stopped from
the Scheduler page's pause/resume button. See "Scheduler" below and
`runner.py`'s module docstring for exactly what that widens.

Run it from the repository checkout:

```sh
uv run python -m xjobscore.inspector
```

Then open <http://127.0.0.1:8765> (`XJOBSCORE_INSPECTOR_PORT` overrides).

## Pages

- **Feeds** (`/`) — every registry feed plus one local feed per non-registry
  source, grouped by source with category/enabled/error filters and search.
  Per feed: the `feed.enabled` switch and the last run's harvest
  status/items/elapsed; `feed.interval_s` and pause live in the detail
  pane. Flipping the switch on a feed PostgreSQL has never seen imports it
  first, so there is no separate Import control.

  Each ATS group header carries the same switch for the whole vendor, and it
  imports too — that is the only practical way to bring a catalog-only source
  online, since 2,591 of the 2,626 catalogued feeds are Ashby and none of them
  start out in PostgreSQL. It goes through `POST /api/feeds/bulk-policy`, one
  request and one round trip for the batch rather than a POST per feed. The
  header's **Enable all visible** button uses that same import-and-enable
  operation for every feed matching the current filters, guarded by a second
  confirmation click.

  Each ATS row also carries a **Drop** button — a hard `DELETE` of the feed
  row, which cascades to remove its `poll_observation` history too
  (`ON DELETE CASCADE`). It's the only way a feed leaves `public.feed` once
  created; disabling just flips the switch, Drop actually removes the row.
  Direct/Board rows have no Drop control — out of scope for now.
- **Feed detail** (`/feed?ref=…`) — the three-tab view:
  - *Harvest*: item picker + verbatim payload JSON, with the response envelope
    as a synthetic first row.
  - *Parsed*: `parse()` run over the harvested items in-process, classified
    exactly like `silver.process` — resolved / feed-fallback / would-park
    company resolution (against the registry mirror), secondhand,
    unsupported — with per-item parser errors isolated and rendered.
    Harvest-only sources show a "no parser" state, not an error.
  - *Network*: a latency line over the run (one point per attempt, placed at
    its start offset, retries dimmed; hover or focus a point to read its exact
    status and duration, click it for the full detail), the
    allowlisted response headers with `*ratelimit*`/`Retry-After` highlighted,
    and an elapsed-time sparkline over the retained run history.
- **Scheduler** (`/scheduler`) — what production polling actually did, per feed.
  Header tiles (active feeds, p95 discovery latency with a period-over-period
  delta, requests today, errors in 24 h) over a table sorted busiest-first:
  tier, λ/day, new postings in 7 d, last poll, p95 latency, and one status
  chip. The detail pane adds a 14-day per-day sparkline, the recent-poll event
  list, the tier control, and Poll now / Disable; the header carries Fetch all
  feeds behind a two-click arm.

  **Poll now** means now. It backdates `due_at` well past the whole backlog
  rather than to `now()` — `store.claim_due_feeds` orders by `due_at`
  *ascending*, so `now()` sorts an express feed *behind* everything already
  overdue — and then wakes the in-process runner so the poll happens in seconds
  instead of on the next tick. Because it promises a poll, it refuses (409, with
  the reason) rather than queueing one that could not happen: a paused scheduler,
  no runner listening, a disabled or paused feed, or a poll of that feed already
  in flight. The sentinel `due_at` never persists — the poll's own
  `finish_schedule_success`/`_failure` overwrites it.

  **Fetch all feeds** does not queue at all: the browser drives a small worker
  pool over the visible (filtered) feeds, one
  `POST /api/scheduler/feeds/{ref}/fetch` each, and every request claims a
  lease and runs `run.poll` — the production path, `raw_posting` through
  `poll_observation` — before answering. It needs PostgreSQL but neither the
  runner loop nor `scheduler_control.running`: the operator clicking it *is*
  the scheduler. Per-feed policy still holds — a paused, disabled, or
  already-claimed feed refuses (409) instead of polling, and paused rows are
  skipped client-side. A harvest that runs and fails is a 200 whose
  `status: "error"` the progress counter reports as failed. The button doubles
  as Stop while running.

  **Request budget by vendor** sits between the tiles and the table: one row
  per platform with its feed count, the requests/day its configured tiers ask
  for, what the same feeds would cost at the tiers λ recommends, and the
  percentage between them. Rate limits are a vendor-level resource — `net.py`
  caps concurrency per hostname suffix, and a board's tolerance belongs to the
  vendor rather than to any one tenant — but every column in the table below is
  per feed, so a thousand Ashby tenants at a 5m tier is a decision nobody makes
  explicitly and no row shows. Clicking a vendor filters the table to it.

  The totals are computed over every scheduled feed and deliberately ignore the
  filters below: the question is what the vendor actually receives, and that
  does not shrink because the table is narrowed to stale rows. Paused feeds are
  excluded from both cost columns and counted in the caption instead — a held
  feed issues no requests, so billing the vendor for its configured interval
  would overstate the load. A feed λ cannot rank yet carries its current cost
  into the recommended column, so the delta reads as "what retiering would
  change" rather than "what we failed to estimate", and the caption says how
  many rows that covers. The card is hidden when PostgreSQL is down, where
  every cost would be a dash the degraded banner has already explained.

  λ is an EWMA (half-life 21 d) over `poll_observation.new_postings`, shrunk
  toward the median λ of the same platform. It *recommends* a tier and never
  applies one — the "Auto" button is present but disabled because nothing
  implements adaptive tiering yet.

  `run.py` appends one `poll_observation` row per completed poll — status and
  first-sighting count, never a revision of an earlier row. Three states have to
  render correctly and are visibly distinct: PostgreSQL down, `poll_observation`
  absent or empty (a database nothing has polled against yet), and populated. In
  the first two every telemetry column reads "—", never `0` — a zero would claim
  the poller ran and found nothing.

  `feed.enabled` is the membership test, and the Feeds page owns it: its toggle
  imports the feed on first flip and switches it off afterwards. Enabling a feed
  there is what puts it on the Scheduler; disabling it is what takes it off.
  Nothing here can widen or narrow that set — the Scheduler adjusts cadence and
  queue position only.

  Stopping a feed here is **Pause**, not disable: it sets `feed.paused_at`,
  so production stops claiming the feed while it stays enabled, stays listed,
  and keeps its history. Poll now is refused while held, and resuming leaves
  `due_at` where it was — in the past — so the feed is due immediately. The
  Feeds page shows a read-only `paused` badge; it never sets one, exactly as
  this page never sets `feed.enabled`.

  A separate, global **Pause scheduler / Resume scheduler** button in the
  header controls `scheduler_control.running` — the one switch that decides
  whether *any* feed gets polled by this inspector process, independent of
  every feed's own `enabled`/`paused_at`. It is what `inspector.runner.
  SchedulerRunner` reads on every tick; see "Running the scheduler in-process"
  below. A "Scheduler" tile up front shows running/paused, whether a process is
  actually supervising it (a stale `running = true` with no live runner is
  reported, not hidden), and the last cycle's feed count.
- **Postings** (`/postings`) — browser and labeling queue over
  `public.job_posting` (read via the policy store's borrowed pool). Each entry
  shows the gold row's actual fields; the detail pane follows a keyboard
  cursor (`j`/`k`), and `1`/`2`/`3` write a triage label — `relevant` /
  `irrelevant` / `unsure` — that upserts immediately into
  `{schema}.posting_label`, keyed `(source, source_key)` so it survives a
  posting row being deleted and reinserted. Labels are hand-made ground truth
  for a future internship classifier; they live in the inspector's schema
  precisely because gold tables are rebuilt destructively. A shuffled order
  (seeded, stable across pagination) plus an "Unlabeled" filter and an
  "Intern titles" stratum filter make an unbiased labeling draw practical.
  The same page's **Duplicate review** mode pages by candidate group, nests
  every member beneath one group row, and compares location, ATS identity, and
  description evidence without loading full descriptions. `1`/`2`/`3` mark a
  group correct / incorrect / needs-review. Reviews live in
  `{schema}.duplicate_group_review`, keyed by group and evidence fingerprint;
  a rebuild that changes the evidence returns that group to the unreviewed
  queue while retaining its prior review history. Hidden groups leave the
  ordinary queues but remain recoverable through the **Hidden** filter. Press
  `?` for the keymap.
  A **Rebuild duplicate groups** button reruns the gold dedup step
  (`gold.steps("dedup")`) against `public.job_posting`, replacing
  `job_posting_dup_group` entirely. It is deliberately manual and separate
  from the poll loop: the rebuild is a destructive full-table recompute, too
  heavy to run on every tick. A completed rebuild refreshes Duplicate review
  automatically; Inspector reviews are not stored in the replaced gold table.
- **Review** (`/review`) — triage queue over every `job_posting` carrying an
  `attributes.opus` key: the CS-internship recon set, first tagged by a
  title-only regex pass into `tech` / `non-tech` / `ambiguous` / `other`.
  Unlike Postings' hand labels, the decision here lives inside
  `job_posting.attributes` itself, with a small provenance trio —
  `opus_by` (`machine` or `human`), `opus_at`, and `opus_machine` (the
  original regex guess, never overwritten by a review) — so a corrected row
  and its prior guess both stay visible. Same keyboard idiom as Postings:
  `j`/`k` moves the cursor, `1`–`4` writes a bucket and advances, `u` undoes
  the last write, `o` opens the posting. The default filter is
  **Unreviewed**, so the queue has somewhere to drain to; a **Changed**
  filter isolates rows where a human decision differs from the machine
  guess, which is what makes the correction rate visible. Press `?` for the
  keymap.
- **Query** (`/query`) — ad-hoc SQL over the pipeline database (`public`, via
  the same borrowed pool as Postings), for questions no purpose-built page
  answers. A schema sidebar (live from `information_schema`, with an
  approximate row count per table), a few starter snippets, and a
  browser-local history of past queries sit alongside a plain SQL editor;
  `⌘/Ctrl+Enter` runs, and clicking a result row expands its full JSON.

  Strictly read-only, enforced in `query.py` by three independent layers, not
  by inspecting the SQL string: (1) the statement must start with
  `SELECT`/`WITH`/`TABLE`/`EXPLAIN`; (2) it runs through asyncpg's extended
  query protocol, which PostgreSQL itself refuses to run more than one
  command through; (3) the whole thing executes inside a `START TRANSACTION
  READ ONLY` that is always rolled back, never committed — so even a
  data-modifying CTE that passes layer 1 fails at the database. A 10s
  `statement_timeout` and a 500-row cap (enforced by only ever fetching that
  many from the portal) keep a runaway query from hanging the page.
- **Fields** (`/fields`) — bronze field recon: what an adapter's payloads
  actually contain, and what each field collapses to downstream. It replaces
  the hand-written surveys under `doc/raw_postings/`, which were generated by
  ad-hoc SQL and went stale the moment a board changed.

  Pick an adapter to get a **path census** over `raw_posting.data->'payload'`
  — every JSON path, its type(s), coverage, fill rate, distinct count, and the
  canonical `Posting` field the adapter's config reads it into. Click a path
  for its **exact value histogram** across every current posting, the collapse
  chain (`workplaceType → Posting.workplace_type_raw → silver._remote() →
  attributes.remote ∈ {yes, hybrid}`), and — where the collapse is a pure
  function of the value alone — what each observed value actually becomes.
  Clicking a value shows whole payloads that carry it. The whole selection
  lives in the query string, so a finding is a link.

  **The census is sampled and the histogram is exact, and the page always says
  which.** That split is a measurement, not a preference: on the live database
  a full depth-3 walk of the largest adapter takes ~108 s, the same walk over
  2,000 payloads takes ~2 s, and an exact histogram of one chosen path takes
  ~1–4 s. The cost is detoasting `data` (a 69 MB heap in front of >1 GB of
  TOAST), not finding rows, so **no index helps** and none is proposed —
  filtering by adapter is ~100 ms of a query that runs for seconds. What does
  help is choosing *before* detoasting: every statement starts from a heap-only
  `DISTINCT ON` that resolves the latest revision per posting while touching no
  jsonb, and the sample is drawn by hashed id rather than by `LIMIT` on that
  `DISTINCT ON` — a bare limit would take whichever tenants sort first
  alphabetically and report one company's schema as the adapter's.

  Two honesty requirements the code enforces. The nine `xml`/`html` adapters
  store a document in a string, not a JSON object; they get an explicit "no
  field tree" state and their configured extraction paths, never an empty
  table. And an adapter whose parser is hand-written Python
  (`taleo`, `avature`, `hn`, …) reports that its mapping is not introspectable
  rather than reporting that it maps nothing.

  `mapping.py` is pure — it reads the config tables and the `registry/
  job-posting` canons, no database — so `/api/fields/mapping` keeps answering
  while PostgreSQL is down. `fields.py` never writes: every statement runs
  inside a rolled-back read-only transaction, and a path from the browser
  reaches SQL only as bind parameters (the parse decides *how many* laterals to
  emit, never any of the text).
- **Tokens** (`/tokens`) — the discovery inventory, and the one place an ATS
  board becomes a pollable one. `public.token` holds every `(ats, token)`
  pair ever seen, live or dead, across every registry type — ATS, Direct,
  and Board alike; `public.feed` is the curated set the scheduler polls.
  Nothing joins them — there is no foreign key — so a token can sit in the
  inventory with no feed row, and until this page there was no way to see
  that or act on it.

  Every row carries a derived **feed state** (*not in feed* / *disabled* /
  *enabled*) and a **category** (ATS/Direct/Board/Other, from the same
  registry taxonomy the Feeds page uses) that gates Enable-eligibility. The
  default view is `status=live` + *not in feed*, which is exactly the enable
  backlog; filters cover vendor, status, feed state, and a search over
  token/company/vendor.

  **Enable** creates a feed row with polling **on**, ATS tokens only — a
  Direct or Board row's checkbox is disabled, and if a non-ATS identity ever
  reaches the backend anyway it is silently skipped and reported separately
  (`skipped_non_ats`). It goes through `store.upsert_registry_feeds`, whose
  conflict branch is a no-op write on `source`, so a token that already has
  a feed row — enabled or disabled — is never touched again; sending it a
  second time is always safe. Selection survives pagination, so a
  multi-page backlog is one request.

  The page is read + enable only — no delete. `token.status` belongs to the
  liveness prober here, separate from `feed.enabled`; removing a feed
  entirely is **Drop**, on the Feeds page (see above) — a hard delete that
  also erases the feed's `poll_observation` history.

  One caveat the page states inline: `run.py` fills `Feed.config` from the TOML
  registry, so an enabled token absent from the registry polls with no adapter
  config. That is correct for key-only vendors (Greenhouse, Lever, Ashby,
  Workable, BambooHR, SmartRecruiters) and not for the config-driven ones
  (Workday, Taleo, Oracle, iCIMS, Dayforce, Avature).

## Architecture

Everything is PostgreSQL, but in two deliberately separate places:

- **`public` — the core pipeline's tables.** `policy.py` writes exactly four
  operator-owned fields — `feed.enabled`, `feed.interval_s`,
  `feed.paused_at`, and `scheduler_control.running` — plus feed import via
  the registry upsert. The remaining `feed` columns (`etag`, `res_hash`,
  `strikes`, `hold_until`) belong to `run.py`'s workers and are read for
  display only *by that class*. `due_at` is the one documented exception: Poll
  now moves it, which is a queue *position* rather than derived state —
  `run.py` overwrites it unconditionally when the poll finishes. Bulk
  registry→PostgreSQL seed is not a *policy* concern; `run.py` does it on every
  startup and `python -m xjobscore.run --seed` does it standalone.
  `SchedulerRunner` also does it — see below. With PostgreSQL down the policy
  layer runs in degraded mode: policy controls disable, everything else works.

  That "policy only" boundary describes `policy.py`, not the process. When
  `inspector.runner.SchedulerRunner` is enabled and `scheduler_control.running`
  is true, this same process also runs `run.cycle` — writing `raw_posting`,
  `job_posting`, `poll_observation`, and the runtime `feed` columns for
  whatever feeds it polls, exactly as `run.py`'s CLI would. The Scheduler
  page's Fetch all takes the same path per feed on demand
  (`SchedulerRunner.poll_feed_now`), with neither gate required. See "Running
  the scheduler in-process" below.

  It also syncs the registry (`feader.feed`) on the same TTL as its in-memory
  registry parse, because `run.cycle` alone is not self-sufficient: `_hydrate`
  stamps each `Feed` with a `company_id` read straight from the registry TOML,
  so without the write those IDs reference `company` rows that do not exist and
  every posting fails on `job_posting_company_id_fkey`. `run.py`'s CLI avoids
  this by syncing on every startup; a long-lived loop has no startup to hang it
  on, so it re-syncs with the parse it must already refresh.
- **The `inspector` schema — a bounded staging area the inspector owns.** One
  latest usable fetch per feed keeps its payload, envelope, validator, and
  detailed network telemetry. The last 50 attempts keep only
  latency/status/retry scalars; parse previews are recomputed from the staged
  payload and are never persisted (in memory, only the currently viewed
  feed's parse is cached). This schema also holds the registry company/alias
  mirror.

The separate schema is not cosmetic: the mirror's tables are named `company`
and `alias`, exactly like the pipeline's canonical rows in `public`. Set
`XJOBSCORE_INSPECTOR_STORE_DSN` to move inspector staging into a different
database entirely; `XJOBSCORE_INSPECTOR_SCHEMA` renames the schema and refuses
`public`.

## Running the scheduler in-process

Set `XJOBSCORE_INSPECTOR_RUNNER=1` and this inspector process becomes a valid
place to run production polling — `inspector.runner.SchedulerRunner` calls
`run.cycle` on a tick (`XJOBSCORE_INSPECTOR_RUNNER_TICK_S`, default 60 s),
reusing one long-lived `Net` (so adaptive per-host 429 pacing survives across
cycles, unlike a cron process that discards it every run) and one parsed
registry snapshot, refreshed every five minutes rather than reparsed on every
tick. Each refresh also runs `feader.feed` to sync that registry into
PostgreSQL — the write `run.cycle` depends on but does not perform itself.

Two independent switches gate every cycle:

- **`XJOBSCORE_INSPECTOR_RUNNER`** (env var, read once at startup) — whether
  *this process* is allowed to poll at all. Off by default, so a stray dev
  instance or the test suite can never accidentally poll production feeds.
- **`scheduler_control.running`** (PostgreSQL, read fresh every tick) — the
  operator's pause/resume, driven from the Scheduler page and persisted so a
  process restart cannot silently undo a deliberate pause. Defaults to `false`:
  a database freshly brought up to this schema polls nothing until told to.

A process with the env var off still serves the control API and the button
still works — it just reports `runner.alive: false`, which the Scheduler page
distinguishes from "paused" (nothing is listening vs. deliberately stopped).

The Scheduler page's Fetch all bypasses both switches on purpose:
`SchedulerRunner.poll_feed_now` claims a single-feed lease and runs `run.poll`
per request, sharing the runner's `Net` (one per-host pacing budget covers the
loop and the on-demand fetches together) and its registry snapshot. It works
with `XJOBSCORE_INSPECTOR_RUNNER` unset — the operator driving the browser is
the schedule — while per-feed `enabled`/`paused_at` and held leases are still
honored with a refusal.

The dedup step (`job_posting_dup_group`) is **not** part of this loop — it
stays a manual "Rebuild duplicate groups" action on the Postings page, gated
single-flight by `inspector.runner.GoldJob`, because it is a destructive
full-table rebuild too heavy for every tick.

Nothing else should be scheduling `run.py` once this is on — leases
(`FOR UPDATE OF s SKIP LOCKED`) make a second concurrent poller safe, but two
pollers still double the outbound request rate against every ATS.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `XJOBSCORE_INSPECTOR_PORT` | `8765` | HTTP port (loopback only) |
| `XJOBSCORE_INSPECTOR_DSN` / `XJOBSCORE_DSN` / `DATABASE_URL` | — | PostgreSQL DSN; **required** — the result store no longer has a file fallback |
| `XJOBSCORE_INSPECTOR_STORE_DSN` | the DSN above | Separate database for inspector staging data |
| `XJOBSCORE_INSPECTOR_SCHEMA` | `inspector` | Schema owning the staging tables; may not be `public` |
| `XJOBSCORE_INSPECTOR_USER_AGENT` | `net.DEFAULT_USER_AGENT` (a Chrome browser string) | Outbound UA |
| `XJOBSCORE_INSPECTOR_{SOURCE}_CONFIG` | — | JSON feed config for a credentialed local source, e.g. `XJOBSCORE_INSPECTOR_WORKATASTARTUP_CONFIG` with captured `headers`/`body`; never placed in the shared registry |
| `XJOBSCORE_INSPECTOR_RUNNER` | `false` | Allow this process to run `run.cycle` in-process; see "Running the scheduler in-process" |
| `XJOBSCORE_INSPECTOR_RUNNER_TICK_S` | `60` | Seconds between scheduler ticks |
| `XJOBSCORE_INSPECTOR_RUNNER_BATCH` | `20` | Feeds claimed per `run.cycle` call, same meaning as `run.py`'s `--batch` |
| `XJOBSCORE_INSPECTOR_RUNNER_LEASE_S` | `3600` | Schedule lease duration, same meaning as `run.py`'s `--lease-seconds` |

`.env` files in the working directory or repo root are honored with
`setdefault` semantics.

## Tests

```sh
PYTHONPATH=. .venv/bin/pytest xjobscore/inspector/
```

Route tests run over `create_app(...)` with injected fakes (catalog, policy,
runner, gold job) and no network. The policy layer is unit-tested against
recording fakes. `test_runner.py` covers `SchedulerRunner`'s pause, drain, and
error-backoff behavior, plus `GoldJob`'s single-flight rebuild — also with no
network and no real registry parse. `create_app()`'s defaults leave the runner
disabled, so nothing in this suite (or a bare `uv run python -m
xjobscore.inspector`) polls production without `XJOBSCORE_INSPECTOR_RUNNER`
set explicitly.

`test_mapping.py` and `test_fields.py` need neither a database nor fixtures:
the first asserts against the real source configs and canons, so a config edit
that changes an adapter's reads surfaces there; the second pins the path
compiler's contract, including that no path text ever reaches the SQL string.

Store tests need a real PostgreSQL: each builds a throwaway `inspector_test_*`
schema and drops it on teardown, so they never see the pipeline's tables.
They read `XJOBSCORE_TEST_DSN`, falling back to the configured inspector DSN,
and skip entirely when neither is reachable.
