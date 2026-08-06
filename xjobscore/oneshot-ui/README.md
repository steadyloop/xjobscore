# OneShot Unified Job Board

OneShot is a localhost job-discovery and application-control board. It consumes
the repository's existing source catalog, adapters, parsers, records, and
networking layer; all new storage, product logic, API code, UI code, and tests
are contained in this directory.

The current runtime is deliberately **manual-fetch only**. Starting the server,
opening the board, searching, and viewing source coverage make no vendor
requests. A request is made only after **Fetch now** or **Fetch selected** is
clicked. Selected batches are limited to 25 feeds and use bounded concurrency.

## Run

From the repository root:

```sh
uv run --project xjobscore/oneshot-ui \
  python xjobscore/oneshot-ui/run.py
```

Then open <http://127.0.0.1:8766>.

The SQLite database defaults to `var/oneshot.sqlite3`. Override it without
editing configuration:

```sh
XJOBSCORE_ONESHOT_DB=/absolute/path/oneshot.sqlite3 \
  uv run --project xjobscore/oneshot-ui \
  python xjobscore/oneshot-ui/run.py
```

## Product surface

- **Today** ranks only actionable new matches, saved/queued jobs, approaching
  deadlines, posting changes, and application follow-ups. Each item states why
  it is in the queue. A submitted application is never represented as queued
  to apply.
- **Discover** provides the Sol-style master/detail board, explainable score,
  eligibility evidence, semantic stack, official apply URL, compact triage,
  keyboard navigation, and safe batch save/queue controls.
- **Pipeline** is backed by append-only application events, visible due-state
  cues, and a frozen-source event timeline. Opening an apply
  URL records nothing; submission requires explicit human confirmation and
  snapshots the selected source, URL, title, company, description, résumé, and
  submission time. Considering and preparing records can be started without
  claiming submission; an existing preparation record is advanced only by the
  confirmed submission flow.
- **Saved** isolates recently changed postings for re-review. **Dedupe review**
  preserves exact/review/new evidence and lossless reversal. **Source coverage**
  groups company tokens beneath their ATS, supports company and tri-state ATS
  toggles, and exposes explicit fetch and local replay controls without
  background work. Its default view contains only locally enabled companies;
  **Disabled** and **All available** are management views.
- Ambiguous opportunity terms remain visible under **Needs classification**;
  a human correction requires written evidence and is versioned.
- Review/ineligible or non-open roles cannot enter the queue, preparation, or
  submission workflow. Profile and cooldown changes re-evaluate persisted jobs
  locally and make no source requests.

Every relevant view supports these exact first-class filters:

1. Spring Internship
2. Spring Co-op
3. Student Researcher
4. Summer Internship
5. Fall Co-op
6. Fall Internship

## Architecture

```text
existing CatalogDB
       |
       v
explicit browser command -> ManualFetchCoordinator -> shared xjobscore.net.Net
                                      |                         |
                                      v                         v
                              raw SQLite history        existing adapter/parser
                                      |
                                      v
       lifecycle + taxonomy + eligibility + scoring + reversible dedupe
                                      |
                                      v
                          unified SQLite read model
                                      |
                                      v
                         FastAPI -> vanilla Sol-style UI
```

SQLite uses WAL mode, short-lived reader connections, and serialized writes.
Observation identity is `(source, feed key, external ID)`. UUID text IDs are
used across browser APIs. Provider IDs, canonical URLs, and employer-scoped
requisition IDs are the only automatic merge evidence; fuzzy similarity is a
review signal, never an automatic merge.

Complete/closing feeds are reconciled only after a successful, non-truncated
manual snapshot. The first absence becomes `unlisted`, the second becomes
`closed`, and reappearance reopens the observation while retaining events.
Suspicious mass drops are quarantined. Feeds without those explicit capability
flags never close jobs by absence.

### Source configuration

The repository catalog TOMLs are the read-only availability layer. OneShot
loads only the live, catalog-enabled feeds exposed by the existing `CatalogDB`;
it never edits those TOMLs. A separate `source_selections` table in the
OneShot SQLite database stores the user's local enabled/disabled overlay.

Newly discovered catalog feeds default to disabled in OneShot. Enabling a
company or an ATS is explicit and durable across restarts. Disabling a feed
stops future manual network fetches but does not delete its observations,
jobs, health, or fetch history. The backend re-checks this selection for both
single and batch fetch commands, so bypassing the browser cannot fetch a
disabled company. Local replay remains available because it performs no
network request.

The future scheduler design is documented in [scheduler.md](scheduler.md). It
is not active and no timer, due queue, or background task is implemented.

## API outline

- `GET /api/health`
- `GET /api/jobs` and `GET /api/jobs/{id}`
- `POST /api/jobs/{id}/triage`
- `POST /api/jobs/{id}/classification`
- `POST /api/jobs/{id}/applications`
- `GET /api/today`
- `GET /api/sources`
- `GET /api/source-groups`
- `PUT /api/sources/{feed_ref}/selection`
- `PUT /api/sources/vendor/{source}/selection`
- `POST /api/sources/{feed_ref}/fetch`
- `POST /api/sources/{feed_ref}/replay` (local reparse; no network)
- `POST /api/sources/fetch`
- `GET /api/dedupe` and `POST /api/dedupe/{id}/decision`
- `GET /api/applications` and `POST /api/applications/{id}/events`
- `GET /api/profile` and `PUT /api/profile`

`opportunity_type` is a repeatable query parameter. `needs_classification=true`
can be combined with any selected type to form a union.

## Verify

```sh
uv run --project xjobscore/oneshot-ui \
  pytest xjobscore/oneshot-ui/tests -q

node --test xjobscore/oneshot-ui/oneshot_ui/static/app.test.js
```

Tests are offline by default. Fixture gateways exercise startup, selected
manual fetches, all six filters, classification correction, exact and review
dedupe, reversal, partial-snapshot containment, lifecycle
absence/quarantine/reopening, application quality gates and snapshots, profile
re-evaluation, event history, source-selection persistence and fetch gates,
grouped ATS coverage, and batch-failure isolation without contacting a vendor.
