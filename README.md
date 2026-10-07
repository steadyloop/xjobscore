# xjobscore

Cross-board job aggregation with PostgreSQL-backed ingestion, deduplication,
inspection, and application tracking.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and PostgreSQL. From the repository
root:

```bash
cp .env.example .env
createdb xjobscore
psql -v ON_ERROR_STOP=1 --dbname=xjobscore --file=schema.sql
```

Set `DATABASE_URL` in `.env` if it differs from
`postgresql://localhost/xjobscore`. `uv run` installs each project's locked
dependencies automatically.

## Run the system

One command starts the scheduler, Bronze → Silver → Gold workers, token
heartbeat, and Inspector control plane at <http://127.0.0.1:8765>:

```bash
uv run xjobscore
```

For a database created before the unified runtime, apply its migration first:

```bash
psql -v ON_ERROR_STOP=1 --dbname=xjobscore \
  --file=xjobscore/migrations/20260923_unified_runtime.sql
```

Scheduler board groups (the named, ordered sections on the Scheduler page)
need their tables once on an existing database:

```bash
psql -v ON_ERROR_STOP=1 --dbname=xjobscore \
  --file=xjobscore/migrations/20261006_provider_groups.sql
```

Until then the board shows the plain provider list and grouping is switched off.

System → Link checks controls the independent missing-job queue: pause/resume,
concurrency, check starts/second, live rechecks, and failed-check retries.
Apply `xjobscore/migrations/20261006_link_check_queue.sql` once on an existing
database; it seeds missing jobs with the worker paused for rollout.

The Scheduler page is the persistent pause/resume control. A fresh database
runs immediately; an explicit pause survives restarts. Only one system process
may own a database at a time.

## Run OneShot

OneShot — the JSON API at <http://127.0.0.1:8766>:

```bash
uv run -m oneshot_ui
```

OneShot is a uv workspace member, so it shares the root lockfile and `.venv`. The
browser client lives in the separate oneshot-ui repo; set
`XJOBSCORE_ONESHOT_CLIENT_URL` to its origin so CORS admits it.

## Maintenance commands

`xjobscore.run` is a repair and debugging interface, not another service:

```bash
uv run python -m xjobscore.run [OPTIONS]
```

Each invocation performs one explicitly selected operation and exits. A bare
invocation refuses with a pointer to `uv run xjobscore`.

### Modes

| Mode | What it does |
|---|---|
| `--fetch-all` | Repair sweep: fetch every enabled, unpaused, unheld feed through Bronze, Silver, and targeted Gold, ignoring due time. |
| `--parse` | Replay stored raw postings through the current parsers without fetching. |
| `--enrich [NAME]` | Run posting enrichments (`job_type`, `skills`, `degrees`, `summaries`, and `focus`), or one named step. Dedup runs only when explicitly named. |
| `--reconcile` | Queue missing postings for the independent link-check worker; `--dry-run` previews eligible jobs. |

### Options

| Flag | Meaning |
|---|---|
| `--dry-run` | Perform the selected operation without database writes. By itself, checks currently due feeds end to end. |
| `--cycle-budget-s N` | Keep claiming due feeds for `N` seconds, then finish the polls in flight and exit; default: `30`. Each vendor gets its own worker pool, sized from that adapter's configured concurrency and `net.py`'s vendor caps. |
| `--concurrency N` | Run at most `N` simultaneous polls with `--fetch-all`; default: `8`. Host/vendor rate limits still apply. |
| `--lease-seconds N` | Hold each claimed feed for `N` seconds; default: `3600`. |
| `--dsn DSN` | Override `DATABASE_URL`. |
| `--user-agent VALUE` | Override the outbound HTTP user agent. |
| `--help` | Show the CLI help. |

For an existing database, install and populate the indexed Gold domain before
serving the Software/Finance filters:

```bash
psql -v ON_ERROR_STOP=1 --dbname=xjobscore \
  --file=xjobscore/migrations/20260910_job_domain.sql
uv run python -m xjobscore.run --enrich job_type
```

Apply the same migration to a separate thin serving database before publishing.

## Check missing and ATS-closed jobs

Preview the scope first; add `--apply` to fetch fresh listings and update it:

```bash
uv run python -m xjobscore.check_jobs
uv run python -m xjobscore.check_jobs --apply
uv run python -m xjobscore.check_jobs --source greenhouse --limit 100
uv run python -m xjobscore.check_jobs --feed greenhouse:example --apply
uv run python -m xjobscore.check_jobs --scope all --job-id 123 --job-id 456
```

`--scope oneshot` (default) selects the full publishing cohort across UI filters;
`--scope all` includes all stored jobs, including closed ones. Repeated source,
feed, and job-ID filters narrow that scope; `--limit` selects by ascending job ID.
The selected IDs are fixed at startup. Each board is fetched once, with at most
`--concurrency 8` boards in flight, and only selected jobs receive signal updates.

Absent jobs get their first `missing_since`; returned jobs clear it. Explicit ATS
closure sets `ats_closed_at` and closes the job, preserving its first timestamp.
Listed jobs also refresh `last_seen_at`. Missing jobs are not closed or reopened.
Selected raw payloads are saved for replay. Other job content is untouched.

Paused/disabled feeds are skipped unless `--include-paused` is passed. Existing
feed leases are respected, and the scheduler's running state is unchanged.
Incomplete/failed fetches write no signals. Unparseable items still establish
presence but leave ATS status unknown. Progress is JSON lines; an apply run exits
nonzero if any jobs were unchecked or any selected items were unparseable.
These are observation timestamps from this run, not historical closure dates.
Run the publisher afterward to send the resulting signals to OneShot.

## Publish OneShot locally and to Neon

`frontend.sql` defines the OneShot database, locally or on Neon; `schema.sql`
defines the pipeline database. Accounts, applications, feedback, and resumes
are defined only in `frontend.sql`. The five shared tables (`company`,
`company_logo`, `job_posting`, `job_posting_dup_group`, `coverage_snapshot`)
are defined in both because the pipeline produces the data that OneShot serves.
These files initialize separate empty databases; renaming or editing them does
not migrate an existing database. Set the target separately, then publish the active, deduplicated,
US/unknown-location early-career cohort:

```bash
export DATABASE_URL=postgresql://localhost/xjobscore
export PUBLISH_DATABASE_URL='postgresql://user:password@host/db?sslmode=require'
export FRONTEND_DATABASE_URL=postgresql://localhost/xjobscore_frontend
uv run xjobscore-publish
```

The command publishes the same cohort independently to each configured target,
upserting companies, logos, postings, and the coverage snapshot. It
marks previously published rows outside the current cohort as `status = 'closed'`,
then deletes closed postings unless an application or feedback references them.
Cleanup runs in the same transaction, so a failed publish or `--dry-run` rolls it
back. Run it after Gold so both targets receive completed enrichments and duplicate selection.

Initialize the local `xjobscore_frontend` database with `frontend.sql` and current
OneShot migrations. Before the first publication, apply the core `frontend_identity`
table definition and run `python -m xjobscore.frontend_setup`. This seeds shared
job data and the pipeline's serving-ID map from existing Neon records. It refuses
to overwrite a populated local frontend database and copies no personal tables.

Serving IDs are mapped by posting `(source, source_key)` and company identity;
pipeline numeric IDs can differ from Neon. Existing Neon IDs and application
links are retained. Both targets use the same allocated IDs. A failed target does
not roll back the other. The local copy also retains public job snapshots referenced
by central applications/feedback; accounts, tracker rows, and resumes remain on Neon.
If Neon is unavailable, local updates continue and local cleanup waits until
central references can be checked again.

For local OneShot, set its `DATABASE_URL` to Neon and `JOBS_DATABASE_URL` to the
local frontend database. Jobs, logos, and coverage are read locally; accounts,
application tracking, feedback, and resumes use Neon. The hosted app keeps only
its Neon `DATABASE_URL`, preserving its existing single-database configuration.

Only differences travel. Each side fingerprints its rows in SQL (every
published column except `last_seen_at`); postings, companies and logos whose
fingerprints match are neither fetched nor sent, and the coverage snapshot is
sent only when its `computed_at` moved. `last_seen_at` changes on nearly every
row every hour, so an otherwise unchanged posting is resent only once Neon's
copy lags by `--seen-drift-s` (default 6 h). The UI's "checked N ago" can
therefore read up to that much older than the home database.

The long-lived `xjobscore` process publishes automatically whenever
either `PUBLISH_DATABASE_URL` or `FRONTEND_DATABASE_URL` is set: once at startup, then every
`XJOBSCORE_PUBLISH_INTERVAL_S` (default 3600). A failed run retries after 60 s,
doubling up to the interval. `XJOBSCORE_PUBLISH_SEEN_DRIFT_S` sets the drift
bound, and the last run's counts appear under `publish` in the runtime status.
