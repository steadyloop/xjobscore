# Feed inspector

The inspector is a localhost-only debugging console. It reads the source-owned
TOML catalogs for ATS feeds and creates one local feed for every other source,
then runs a selected source's
`harvest` function once. It has no PostgreSQL dependency: live catalog entries
become transient feeds, and sources without a live entry remain visible but
cannot be fetched. Configured feeds are grouped under their source; expanding
a source shows each company/token feed, with a search field for finding a
company or token. Fetches are never routed to `poll_run` or
`raw_document`; the returned items and envelope live in a bounded in-memory
cache (128 MiB, grouped by source, with one latest result per feed inside each
source).

For an authenticated local source, provide its feed config as JSON in an
environment variable. For example, set
`XJOBSCORE_INSPECTOR_WORKATASTARTUP_CONFIG` to a JSON object containing the
captured `headers` and `body`. This value is never placed in the shared catalog.

Results are also persisted in an inspector-owned SQLite file at
`~/.xjobscore-inspector.sqlite3` by default. Set `XJOBSCORE_INSPECTOR_DB` to
choose another path. PostgreSQL remains read-only except for the existing
application's own operations; inspector results never enter `raw_document`.

Run it from the repository checkout:

```sh
uv run python -m xjobscore.inspector
```

Then open <http://127.0.0.1:8765>. Results are persisted separately from the
application in the inspector's SQLite database. The package is intentionally
not wired into the normal poller or application entry points.
