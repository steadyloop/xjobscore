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

## Run the interfaces

Inspector — adapter inspection and scheduler controls at
<http://127.0.0.1:8765>:

```bash
uv run python -m xjobscore.inspector
```

OneShot — the API and browser frontend at <http://127.0.0.1:8766>:

```bash
uv run --directory xjobscore/oneshot-ui python -m oneshot_ui
```

`--directory` selects OneShot's separate `pyproject.toml` and lockfile. The
frontend is static and has no build process; OneShot serves it from `frontend/`.

## Run the ingestion worker

```bash
uv run python -m xjobscore.run [OPTIONS]
```

Each invocation syncs the registry, performs one selected operation, and exits.
Use cron or another process manager to repeat polling.

### Modes

| Mode | What it does |
|---|---|
| Fetch (default; no flag) | Fetch up to 20 due feeds and persist raw and canonical postings. |
| `--sync-registry` | Sync the TOML registry to PostgreSQL, then exit without fetching. |
| `--parse` | Replay stored raw postings through the current parsers without fetching. |
| `--enrich [NAME]` | Run all enrichments, or only `job_type`/`skills`/`degrees`/`dedup` when named. |

### Options

| Flag | Meaning |
|---|---|
| `--dry-run` | Perform the selected operation without database writes. Fetch mode still makes network requests. |
| `--batch N` | Process at most `N` due feeds; default: `20`. |
| `--lease-seconds N` | Hold each claimed feed for `N` seconds; default: `3600`. |
| `--dsn DSN` | Override `XJOBSCORE_DSN` or `DATABASE_URL`. |
| `--user-agent VALUE` | Override the outbound HTTP user agent. |
| `--help` | Show the CLI help. |
