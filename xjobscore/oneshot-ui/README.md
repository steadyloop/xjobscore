# oneshot-ui

A local unified, deduplicated job discovery and application board.

## Run

```bash
.venv/bin/python -m xjobscore.oneshot_ui
```

```bash
cd xjobscore/oneshot-ui && ../../.venv/bin/python run.py
```

Starts the FastAPI server on http://127.0.0.1:8766

This mode needs PostgreSQL: the feed catalog lives there, and `AdapterGateway`
connects to it during startup. The board itself is SQLite (`var/oneshot.sqlite3`).

## Demo mode

Demo mode runs the whole product offline against a seeded database, with no
Postgres and no vendor requests, so you can see every view populated:

```bash
XJOBSCORE_ONESHOT_DEMO=1 ../../.venv/bin/python run.py
```

It swaps in an offline gateway, uses a **separate** database
(`var/demo.sqlite3` — `var/oneshot.sqlite3` is never touched), and seeds on
first start.

The corpus is fictional companies on real ATS vendors (Greenhouse, Lever,
Ashby, Workday, Workable, SmartRecruiters, iCIMS). Most of the seeded state is
produced by the shipping pipeline rather than written by hand: postings are
harvested and ingested through `ManualFetchCoordinator` and `BoardIngestor`, so
classification, stack extraction, eligibility gates, scoring, dedupe and
lifecycle all really run. A second fetch round revises two postings and drops
one from a complete/closing feed, which is what produces the "posting changed"
and "unlisted" states. `oneshot_ui/demo/topup.py` then writes only what the
pipeline provably cannot emit — the `one_shot` band needs a score of 90 and the
pipeline's ceiling is 82 — and each block there says which case it is.

### Managing the demo database

Run these from `xjobscore/oneshot-ui/`. Unlike the rest of the repo they do
**not** need `PYTHONPATH=.` — like `run.py`, the CLI puts the repository root on
`sys.path` itself:

```bash
../../.venv/bin/python -m oneshot_ui.demo reset    # rebuild from scratch
../../.venv/bin/python -m oneshot_ui.demo verify   # audit for unfilled data
../../.venv/bin/python -m oneshot_ui.demo path     # print the database path
```

`verify` is the check that keeps demo mode honest: it walks every table and
fails on any table with no rows, or any column that is NULL in every row.
Columns that are legitimately empty are listed with a reason in
`oneshot_ui/demo/audit.py`.

Editing `oneshot_ui/demo/dataset.py` means editing inputs to keyword-sensitive
gates — a title without a CS/Math family word fails `_role_family` and the
posting silently becomes ineligible. Re-run `reset` then `verify` after changes.

## Tests

```bash
cd xjobscore/oneshot-ui && PYTHONPATH=.:../.. ../../.venv/bin/pytest
cd oneshot_ui/static && node --test app.test.js     # frontend utilities
```

## Sample data

Use the **Sample data** switch at the bottom of the application menu to swap
the browser onto a four-job fictional dataset. The normal views and controls
continue to run. With the switch off, the frontend API client returns safe
empty stubs until a database transport is connected. `static/api-client.js`
defines the routes and the single transport seam that will be wired later.
