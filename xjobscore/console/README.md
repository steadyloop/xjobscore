# xjobscore Console

A localhost-only backend dashboard for the harvesting pipeline: fetch any feed
on demand, inspect the raw `harvest()` payload and the `parse()` preview side
by side, read per-request network telemetry (timing, retries, rate-limit
headers), and set polling policy. A Feed-detail fetch is a manual,
side-effect-free look at a feed — it stages its result in the console's own
schema and never touches `raw_posting` or `job_posting`. Scheduler actions only
write durable queue positions; the unified runtime owns all production work.

Console is always served by the unified process alongside the scheduler,
incremental Gold workers, dedup checkpoints, and token heartbeat.

Run it from the repository checkout:

```sh
uv run xjobscore
```

Then open <http://127.0.0.1:8765> (`XJOBSCORE_PORT` overrides).

## Pages

- **Closed jobs** (`/closures`) — interactive availability analytics: daily
  closures stacked by their earliest evidence, closure evidence shares,
  current missing postings by provider and age, and missing-to-confirmation
  timing. Clicking a chart drills into its postings; Unresolved and Review
  views expose verification gaps and closed postings listed again. Observation
  dates use the browser timezone. Charts begin with the available history;
  current backlog is independent of the selected closure period. Counts are
  source postings and may include duplicates. Reads use a one-minute cache and
  read-only transactions; opening the page never checks a link or changes a job.
- **Console** (`/system`; `/` and `/scheduler` redirect here) — the landing
  page and production operations console, top to bottom:
  - **Activity**: the run switch, the last Enrich (Gold catch-up) run, and
    **Link checks**, the missing-job queue.
  - **Scheduler**: feed status (ok / err / stale / new / paused / total), the
    last 15 minutes of polling (polled, feeds with new jobs, new postings,
    partial, errors), the **workers** block, then the scheduler board
    described under **Scheduler** below.
  - **Log**: one stream of process log lines, polls, and system runs, newest
    at the bottom.

  Worker slots, the 15-minute counts, and the server log live in process
  memory (`xjobscore/activity.py`) and reset on restart; everything else is
  read from PostgreSQL. Selecting a feed opens its detail as a drawer from
  the right.
- **Feeds** (`/feeds`) — every PostgreSQL feed, grouped by source with
  category/enabled/error filters and search.
  Per feed: the `feed.polling` switch and the last run's harvest
  status/items/elapsed; `feed.interval_s` and pause live in the detail
  pane. Feeds are created explicitly from Tokens or by an operator; there is
  no catalog import or local fallback.

  Each ATS group header carries the same switch for the whole vendor. It goes
  through `POST /api/feeds/bulk-policy`, one request and one round trip for the
  batch rather than a POST per feed. The
  header's **Enable all visible** button uses that same import-and-enable
  operation for every feed matching the current filters, guarded by a second
  confirmation click.

  Each ATS row also carries a **Drop** button — a hard `DELETE` of the feed
  row, which cascades to remove its `poll_log` history too
  (`ON DELETE CASCADE`). It's the only way a feed leaves `public.feed` once
  created; disabling just flips the switch, Drop actually removes the row.
  Direct/Board rows have no Drop control — out of scope for now.
- **Feed detail** (`/feed?ref=…`) — the three-tab view:
  - *Harvest*: item picker + verbatim payload JSON, with the response envelope
    as a synthetic first row.
  - *Parsed*: `parse()` run over the harvested items in-process, classified
    exactly like `silver.process` — resolved / feed-fallback / would-park
    company resolution (against PostgreSQL's `company_alias` table), secondhand,
    unsupported — with per-item parser errors isolated and rendered.
    Harvest-only sources show a "no parser" state, not an error.
  - *Network*: a latency line over the run (one point per attempt, placed at
    its start offset, retries dimmed; hover or focus a point to read its exact
    status and duration, click it for the full detail), the
    allowlisted response headers with `*ratelimit*`/`Retry-After` highlighted,
    and an elapsed-time sparkline over the retained run history.
- **Scheduler** (the board on the Console page; `/scheduler` redirects to
  `/system`) — the production polling operations console. The browser never
  receives the whole fleet: the server builds the board once per minute
  (`console/board.py`), keeps one gzip blob per vendor, and answers small
  questions about it.

  The **workers** block lists vendors with more than one worker
  (`sources.width` > 1): one tick per slot, longest poll first, green while
  polling, amber past 60 s, red past 3 min, then busy/width and the longest
  poll when it is slow or stuck. Workday's per-data-center lanes share one
  combined bar; hovering or clicking it opens the lanes, with a lane drawing
  429s marked amber. Single-worker vendors are left out; a slow poll there
  shows as `polling Ns` on the feed's row.

  The board lists vendors under the operator's groups, Ungrouped last. Each
  vendor row is a rollup: worst state, a `N err / N stale / … / N feeds`
  breakdown, interval range, soonest due, latest poll, and `new·1d` /
  `err·7d` sums that stay `—` without telemetry (`GET
  /api/scheduler/providers`). Opening a vendor shows one **status line** per
  state (err, stale, new, paused, ok) with the same columns rolled up; an
  err line of 12 feeds or fewer opens on its own. Opening a status line pages
  its feeds 25 at a time, worst first, with a filter box (`GET
  /api/scheduler/providers/{source}/feeds?state=&q=&page=`). The view pills
  (all / issues / failing / paused) narrow both vendors and status lines.
  Typing two or more characters in the search box asks the server for every
  feed whose company or key matches (`GET /api/scheduler/search`) and shows
  each matching vendor's hits; `group:name` narrows to groups.

  Vendor controls sit on an open vendor's last status line, each behind a
  confirmation: **interval** presets (5m, 15m, 1h, 6h, 24h) set every feed of
  the vendor in one statement (`POST /api/scheduler/providers/{source}/interval`),
  **fetch** brings every enabled feed of the vendor forward
  (`POST /api/scheduler/poll-now`), and **pause/resume** flips the whole
  vendor (`POST /api/scheduler/providers/{source}/pause`). Each resolves the
  vendor on the server, without loading the fleet. A vendor row's
  `move to ▾` files it in a group, or in a new one; clicking a group's name
  renames it, and a group disappears once nothing is filed in it.

  Selecting a feed opens a drawer with its last poll and result, strikes,
  new postings in 7 days, open postings, conditional-GET state, interval
  presets, recent polls, and Fetch / Pause / Mute / Drop. Cadence is entirely
  operator-owned: presets write `feed.interval_s` and nothing on the page
  estimates or changes a polling interval.

  Vendor HTTP protection is separate from feed cadence. `net.py` still caps
  per-host concurrency and reacts to 429 responses so an operator-set interval
  cannot disable production's rate-limit safeguards. The scheduler's per-vendor
  worker pools are sized from those same numbers (`sources.width` reads the
  adapter's configured concurrency and `net._VENDOR_CAPS`) rather than
  restating them, so a pool can never be wider than what `net.py` will pass.

  `run.py` appends one `poll_log` row per completed poll — status and
  first-sighting count, never a revision of an earlier row. Three states have to
  render correctly and are visibly distinct: PostgreSQL down, `poll_log`
  absent or empty (a database nothing has polled against yet), and populated. In
  the first two every unknown telemetry value reads "—", never `0` — a zero
  would claim the poller ran and found nothing. Connection, runner, and
  telemetry problems collapse into one prioritized alert rather than stacking.

  `feed.polling` is the membership test, and the Feeds page owns it: its toggle
  imports the feed on first flip and switches it off afterwards. Enabling a feed
  there is what puts it on the Scheduler; disabling it is what takes it off.
  Nothing here can widen or narrow that set — the Scheduler adjusts manual
  cadence and queue position only.

  Stopping a feed here is **Pause**, not disable: it sets `feed.paused_at`,
  so production stops claiming the feed while it stays enabled, stays listed,
  and keeps its history. Poll now is refused while held, and resuming leaves
  `due_at` where it was — in the past — so the feed is due immediately. The
  Feeds page shows a read-only `paused` badge; it never sets one, exactly as
  this page never sets `feed.polling`.

  A separate, global **Pause / Resume** button in the
  header controls `scheduler_control.running` — the one switch that decides
  whether *any* feed gets polled by the unified system, independent of
  every feed's own `enabled`/`paused_at`. It is what
  `runtime.SystemScheduler` reads on every tick. The header shows
  running/paused, whether a process is actually
  supervising it (a stale `running = true` with no live runner is reported,
  not hidden), and the last completed cycle.
- **Tokens** (`/tokens`) — the discovery inventory, and the one place an ATS
  board becomes a pollable one. `public.token` holds every `(ats, token)`
  pair ever seen, live or dead. Tokens are ATS-only; `public.feed` is the
  curated set the scheduler polls.
  Nothing joins them — there is no foreign key — so a token can sit in the
  inventory with no feed row, and until this page there was no way to see
  that or act on it.

  Every row carries a derived **feed state** (*not in feed* / *disabled* /
  *enabled*) and an **ATS** category. The
  default view is inactive live tokens — `status=live` + *not in feed* — which
  is exactly the enable backlog. The status filter is ordered **Inactive**,
  **Muted**, **Dead**, then **All (including active)**; filters also cover
  vendor and a search over token/company/vendor.

  **Promote selected** creates a feed row with polling **on** only while the
  token is live, unmuted, and absent from `feed`. Muting a token is sticky:
  it also switches an existing feed off, while unmuting never switches it
  back on.

  The token heartbeat applies the same guarded promotion after a fresh
  liveness probe: one leased batch of 20 every six hours. Its compact status
  strip persists the last outcome and counts in `public.token_heartbeat`;
  **Run now** queues one batch without bypassing the lease.

  The unified runtime owns the heartbeat. **Pause** / **Resume** writes
  `token_heartbeat.paused`, the operator's runtime switch: persistent across
  restarts and shared by every Console pointed at the database. A paused
  heartbeat still stamps `runner_seen_at` every tick, which is how the strip
  tells *paused* apart from *offline*; it claims no lease, and **Run now**
  refuses with 409 rather than queueing a batch nothing would pick up.

  A summary line carries the shape of the whole inventory — total, vendors,
  live/dead/muted, the unpromoted backlog — plus the freshness half that
  status alone cannot show: how many were checked today, and how many have
  never been checked at all. It is deliberately unfiltered, so the backlog
  number does not move when someone narrows the table.

  Under it sit two tails, side by side, both reading `public.token_log`
  (`checked_at`, `ats`, `token`, `status`) and differing only in which verbs
  they keep. One append-only table, two kinds of row.

  The **token check log** keeps the liveness verbs — `unknown`, `live`, `dead`,
  one row per probe. This is the only place a token's status history exists:
  `token.status`/`checked_at` hold just the latest answer, so a live → dead →
  live flip leaves three rows here and no trace there. The row is written in
  the same statement that updates `token`, so the two cannot disagree.

  The **feed in/out log** keeps `feed_entered` and `feed_exited`, written in the
  same statement as the `INSERT`/`DELETE` on `feed` that they describe. `feed`
  records neither — a row carries no birth timestamp and **Drop** is a hard
  delete — so without these rows an identity that came and went leaves nothing
  behind at all. `token_log` has no FK precisely so a `feed_exited` row can
  outlive the feed it describes. Entry means *a `feed` row was created*, not
  *`polling` was switched on*: flipping the switch on a feed that already exists
  logs nothing. Rendered as `→` and `←`, since direction is the whole content.

  Both are ordered by `id`, never `checked_at` — a batch probes three boards at
  once and their timestamps can tie, and a promote writes `live` and
  `feed_entered` from one statement, so theirs tie by construction. A missing
  `token_log` reads as *log unavailable*, never as an empty log; "nothing is
  recording" and "nothing was checked" are different statements. Nothing
  prunes it yet: at one batch of 20 every six hours it grows by roughly 2.4k
  rows a month, plus whatever **Check**, **Validate all**, and promotions add.

  The page is read + enable only — no delete. `token.status` belongs to the
  liveness prober here, separate from `feed.polling`; removing a feed
  entirely is **Drop**, on the Feeds page (see above) — a hard delete that
  also erases the feed's `poll_log` history.

  `token.config` is PostgreSQL data. A config-driven feed must have its adapter
  values stored on its token row before it is enabled.

- **Companies** (`/companies`) — the verification queue over `public.company`,
  and the only writer `company.reviewed` has ever had. Nothing set a verdict
  before this page, so every unreviewed row is a guess — names arrive as ATS
  token slugs (`8fleet-inc`, `a-place-for-mom`) and domains as unreviewed
  inferences (`8fleet-inc` → `eightfleet.com`).

  There is deliberately **no staging table**. A candidate *is* a `company` row
  carrying `reviewed = 'pending'`; confirming one writes that column rather
  than copying between tables. `job_posting.company_id` is `NOT NULL`, so every
  row here already backs live postings — this page decides whether the company
  that exists is *right*, and never creates or deletes one.

  The default **Queue** view is unverified rows that carry active serving
  postings, ordered by how many. That ordering is the point: verifying Micron
  once settles 441 postings, while alphabetical order would bury it behind
  hundreds of single-posting companies. The wider `unverified` view drops the
  posting requirement — an unreviewed company with nothing to apply to is real,
  but it is not backlog.

  Each row carries the evidence needed to decide without leaving the page, and
  the fields are editable in place rather than behind a dialog. The left column
  names the row: the token that leads somewhere (an `eightfold` token sorts
  first but is an unreadable URL fragment, so a `workday` token beside it wins),
  the active serving count, and the freshest posting's title. Beside it sit the
  links worth opening — the vendor's public board rebuilt from the token, the
  Greenhouse `boards-api` endpoint that simply returns the employer's own name,
  the sample posting, and a web search on the best term available.

  Three outcomes. **Save** writes the boxes and stamps the verdict; an omitted
  field is left alone and an emptied one is cleared, which are different
  instructions. A domain left blank is filled from the greyed guess in its
  placeholder, never from a value the operator cannot see. **Reject** marks the
  row and leaves it in place — `job_posting_company_id_fkey` is `NO ACTION`, so
  a company carrying postings cannot be deleted at all, and a DELETE would
  simply fail. **Reopen** returns either verdict to the queue.

  A verdict does not make the row jump out of the view. The written row stays
  where it was, tinted, so the operator keeps their place; the pill counts are
  adjusted locally by the same rules the server counts by.

  A rename always writes `company_alias`. `silver` resolves incoming
  `company_raw` strings through that table, and it keeps seeing the token slug
  on the wire long after the display name is fixed, so dropping the old
  spelling would break resolution for the postings the rename was meant to
  improve. Aliases are written `ON CONFLICT DO NOTHING`: an `alias_norm` already
  owned by another company stays owned by it, because a verification is not a
  merge.

  The verdict lives in columns — `reviewed` (`pending`/`verified`/`rejected`,
  NOT NULL, CHECKed), `reviewed_at`, `reviewed_by` — so unreviewed is a value
  rather than an absence and a typo'd verdict fails loudly. `tags` keeps only
  the original machine guess (`name_machine`, `domain_machine`), written once
  and never overwritten, so the share of domains an operator actually had to
  correct stays measurable after the fact; Reopen clears the verdict and keeps
  the provenance.

  Publishing is already safe without a filter here: `publish.py` derives its
  company set from the postings it publishes, so an unverified row with no
  serving postings never reaches production.

## Architecture

Everything is PostgreSQL, but in two deliberately separate places:

- **`public` — the core pipeline's tables.** `policy.py` writes exactly four
  operator-owned fields — `feed.polling`, `feed.interval_s`,
  `feed.paused_at`, and `scheduler_control.running` — plus explicit feed
  imports. The remaining `feed` columns (`etag`, `res_hash`,
  `strikes`, `hold_until`) belong to `run.py`'s workers and are read for
  display only *by that class*. `due_at` is the one documented exception: Poll
  now moves it, which is a queue *position* rather than derived state —
  `run.py` overwrites it unconditionally when the poll finishes. With
  PostgreSQL down the policy layer runs in degraded mode: policy controls
  disable, everything else works.

  That "policy only" boundary describes `policy.py`, not the process.
  `runtime.SystemScheduler` consumes the durable queue and writes
  `raw_posting`, `job_posting`, `poll_log`, and Gold outputs. HTTP requests
  never execute production workers.

- **The `console` schema — a bounded staging area the console owns.** One
  latest usable fetch per feed keeps its payload, envelope, validator, and
  detailed network telemetry. The last 50 attempts keep only
  latency/status/retry scalars; parse previews are recomputed from the staged
  payload and are never persisted (in memory, only the currently viewed
  feed's parse is cached).

Set `XJOBSCORE_CONSOLE_STORE_DSN` to move console staging into a different
database entirely; `XJOBSCORE_CONSOLE_SCHEMA` renames the schema and refuses
`public`.

## Unified runtime

`uv run xjobscore` is the only normal process. It holds a PostgreSQL advisory
lock, so a second system cannot accidentally double outbound traffic. The
persisted `scheduler_control.running` value is the sole polling switch: fresh
databases run, while an operator pause survives restarts.

Changed fetches run Bronze, Silver, and posting-local Gold. Failed Gold steps
remain stale and are retried without refetching. Global dedup is generation
tracked and runs after the queue drains and 30 seconds have been quiet, or
after five minutes at most. Scheduler buttons enqueue work and wake the loop;
they never perform it in an HTTP request.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `XJOBSCORE_PORT` | `8765` | HTTP port (loopback only) |
| `XJOBSCORE_HOST` | `127.0.0.1` | HTTP bind host |
| `DATABASE_URL` | — | PostgreSQL DSN; **required** — the result store no longer has a file fallback |
| `XJOBSCORE_CONSOLE_STORE_DSN` | the DSN above | Separate database for console staging data |
| `XJOBSCORE_CONSOLE_SCHEMA` | `console` | Schema owning the staging tables; may not be `public` |
| `XJOBSCORE_CONSOLE_KEYSCAN_DB` | `keyscan/work/keyscan.sqlite` | Keyscan database path |
| `XJOBSCORE_USER_AGENT` | `net.DEFAULT_USER_AGENT` | Outbound UA |
| `XJOBSCORE_TICK_S` | `60` | Seconds between scheduler checks |
| `XJOBSCORE_CYCLE_BUDGET_S` | `30` | Seconds one cycle keeps claiming feeds |
| `XJOBSCORE_LEASE_SECONDS` | `3600` | Feed claim lease duration |
| `XJOBSCORE_SHUTDOWN_GRACE_S` | `30` | Seconds active work may finish before cancellation |
| `XJOBSCORE_HEARTBEAT_TICK_S` | `60` | Seconds between heartbeat queue checks |
| `XJOBSCORE_HEARTBEAT_BATCH` | `20` | Maximum tokens revalidated in one cycle |
| `XJOBSCORE_HEARTBEAT_INTERVAL_S` | `21600` | Delay after a successful cycle (six hours) |
| `XJOBSCORE_HEARTBEAT_RETRY_S` | `900` | Delay after a system-level cycle failure |
| `XJOBSCORE_HEARTBEAT_LEASE_S` | `900` | Exclusive cycle lease duration |

`.env` files in the working directory or repo root are honored with
`setdefault` semantics. Runtime settings use the `XJOBSCORE_*` names above;
Console storage settings use `XJOBSCORE_CONSOLE_STORE_DSN` and
`XJOBSCORE_CONSOLE_SCHEMA`. The persisted Scheduler control is the polling
switch.

### Upgrading from Inspector

Python imports and package paths now use `xjobscore.console`, including
`ConsoleService` and `ConsoleNet`. The application entry point remains
`uv run xjobscore`.

The first startup using the default `console` history schema transactionally
renames an existing `inspector` schema, retaining all tables and history. Custom
schema names are left alone. If both default schemas already exist, startup
stops so their histories can be consolidated; setting
`XJOBSCORE_CONSOLE_SCHEMA=inspector` explicitly keeps using the old schema.

Replace `XJOBSCORE_INSPECTOR_STORE_DSN`, `XJOBSCORE_INSPECTOR_SCHEMA`, and
`XJOBSCORE_INSPECTOR_KEYSCAN_DB` with their `XJOBSCORE_CONSOLE_*` equivalents.
The old names remain deprecated aliases; an explicitly set new name takes
precedence. An explicit legacy schema value of `inspector` continues to use
that schema until the override is removed or changed to `console`.

Saved browser theme and feed-list width migrate to `console-*` keys on page
load, preserving any preferences already saved under the new names.

## Tests

```sh
PYTHONPATH=. .venv/bin/pytest xjobscore/console/
```

Route tests inject an inert runtime through `test_support.py`, so they cannot
poll feeds. The policy layer uses recording fakes, and runtime tests cover the
unified supervisor. Production `create_app(runtime=...)` always uses that
supervisor; there is no standalone scheduler or in-process dedup runner.

Store tests need a real PostgreSQL: each builds a throwaway `console_test_*`
schema and drops it on teardown, so they never see the pipeline's tables.
They read `XJOBSCORE_TEST_DSN`, falling back to the configured console DSN,
and skip entirely when neither is reachable.
