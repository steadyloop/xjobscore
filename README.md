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

The Scheduler page is the persistent pause/resume control. A fresh database
runs immediately; an explicit pause survives restarts. Only one system process
may own a database at a time.

## Run OneShot

OneShot — the API and browser frontend at <http://127.0.0.1:8766>:

```bash
uv run -m oneshot_ui
```

OneShot is a uv workspace member, so it shares the root lockfile and `.venv`. The
frontend is static and has no build process; OneShot serves it from `frontend/`.

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
| `--enrich [NAME]` | Run posting enrichments (`job_type`, `skills`, `degrees`, and `summaries`), or one named step. Dedup runs only when explicitly named. |
| `--reconcile` | Probe postings absent from complete listings and close only those confirmed gone. |

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

## Publish OneShot to Neon

`schema-thin.sql` is the serving schema: the full harvest stays in the home
database. Set the target separately, then publish the active, deduplicated,
US/unknown-location early-career cohort:

```bash
export DATABASE_URL=postgresql://localhost/xjobscore
export PUBLISH_DATABASE_URL='postgresql://user:password@host/db?sslmode=require'
uv run xjobscore-publish
```

The command upserts companies, logos, postings, and the coverage snapshot. It
never deletes a posting; a previously published row outside the current cohort
is changed to `status = 'closed'`. Run it after Gold so Neon receives completed
enrichments and duplicate selection.

Only differences travel. Each side fingerprints its rows in SQL (every
published column except `last_seen_at`); postings, companies and logos whose
fingerprints match are neither fetched nor sent, and the coverage snapshot is
sent only when its `computed_at` moved. `last_seen_at` changes on nearly every
row every hour, so an otherwise unchanged posting is resent only once Neon's
copy lags by `--seen-drift-s` (default 6 h). The UI's "checked N ago" can
therefore read up to that much older than the home database.

The long-lived `xjobscore` process publishes automatically whenever
`PUBLISH_DATABASE_URL` is set: once at startup, then every
`XJOBSCORE_PUBLISH_INTERVAL_S` (default 3600). A failed run retries after 60 s,
doubling up to the interval. `XJOBSCORE_PUBLISH_SEEN_DRIFT_S` sets the drift
bound, and the last run's counts appear under `publish` in the runtime status.
