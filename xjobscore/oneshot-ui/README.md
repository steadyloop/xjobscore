# oneshot-ui

A local job-discovery and application workspace backed directly by the
project's PostgreSQL `schema.sql`.

OneShot does not harvest or canonicalize postings. The core xjobscore pipeline
owns `feed`, `raw_posting`, and `job_posting`, and replaces the singleton
`coverage_snapshot` after each polling cycle. This application reads that
snapshot for source coverage and writes `job_application` and
`job_application_event`.

## Database

Create the database and apply the root schema:

```bash
createdb xjobscore
psql -v ON_ERROR_STOP=1 --dbname=xjobscore --file=../../schema.sql
```

`schema.sql` only runs against an empty database. On a live one, apply the new
statements by command — the auth columns, for instance:

```bash
psql -v ON_ERROR_STOP=1 -d xjobscore -c "
ALTER TABLE account
    ADD COLUMN IF NOT EXISTS email       text,
    ADD COLUMN IF NOT EXISTS google_sub  text,
    ADD COLUMN IF NOT EXISTS name        text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS picture_url text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS created_at  timestamptz NOT NULL DEFAULT now();
CREATE UNIQUE INDEX IF NOT EXISTS account_email_key      ON account (email);
CREATE UNIQUE INDEX IF NOT EXISTS account_google_sub_key ON account (google_sub);"
```

The unique indexes are separate statements because `ADD COLUMN ... UNIQUE` has no
`IF NOT EXISTS` form, so inlining it would make a re-run fail instead of no-op.

To align an existing database with the canonical opportunity types in
`registry/job-posting/types.yaml`, run the checked-in migration from the repository
root before replaying:

```bash
psql -v ON_ERROR_STOP=1 --dbname=xjobscore \
  --file=xjobscore/migrations/20260817_opportunity_types.sql
uv run python -m xjobscore.run --parse
```

An existing database needs the coverage snapshot migration before running the
updated poller or OneShot server:

```bash
psql -v ON_ERROR_STOP=1 --dbname=xjobscore \
  --file=xjobscore/migrations/20260902_coverage_snapshot.sql
```

An existing database, and the Neon serving database, needs the resume design
column before running the updated OneShot server:

```bash
psql -v ON_ERROR_STOP=1 --dbname=xjobscore \
  --file=xjobscore/migrations/20260928_resume_template.sql
```

An existing database, and the Neon serving database, needs the triage feedback
table before running the updated OneShot server. The jobs list reads it, so
apply it before deploying:

```bash
psql -v ON_ERROR_STOP=1 --dbname=xjobscore \
  --file=xjobscore/migrations/20260929_job_feedback.sql
```

Configure the database URL:

```bash
export DATABASE_URL=postgresql://localhost/xjobscore
```

`schema.sql` still seeds the account `00000000-0000-0000-0000-000000000001`, but
nothing DEFAULTs to it: `account_id` on `job_application` and the three `resume_*`
tables is `NOT NULL` with no default, so a write that fails to scope itself raises
a not-null violation instead of quietly landing in a shared account. It is nobody's
login either — `email` and `google_sub` are NULL, so no Google identity resolves to
it. It stays because a repository constructed without an explicit account still
falls back to it, which tests rely on.

## Sign-in

Public discovery stays open; persisted personal workspaces require a session:

| | |
|---|---|
| **Open** | jobs, Feed Status, health, and every page shell |
| **Session required** | `/api/auth/me`, `/api/applications/*`, job-to-application writes, and `/api/resume/*` except `POST /api/resume/render/pdf` |

Signed-out application and resume CRUD is implemented by the browser in a blank
`localStorage` workspace. It never reaches PostgreSQL. `session_account` rejects
unsigned API requests with 401, while the client routes those same operations to
its local store. Signing in switches transport to the account-scoped PostgreSQL
stores; local data remains in that browser but is not merged automatically.

`/api/auth/me` stays gated because "who are you" has no anonymous answer;
`nav-count.js` reads its 401 as "render the signed-out chip". A 401 from a private
route after an identity probe means the session expired, so the client redirects
to `/login/`.

Sign-in itself is still invite-only: the OAuth callback admits an address only if
Google reports it verified **and** it appears in `XJOBSCORE_ONESHOT_ALLOWED_EMAILS`.

Copy [`.env.example`](../../.env.example) to a repo-root `.env` and fill it in —
`load_settings()` reads that file, so nothing needs exporting:

```ini
XJOBSCORE_ONESHOT_GOOGLE_CLIENT_ID=...apps.googleusercontent.com
XJOBSCORE_ONESHOT_GOOGLE_CLIENT_SECRET=GOCSPX-...
XJOBSCORE_ONESHOT_SESSION_SECRET=...          # python -c 'import secrets; print(secrets.token_hex(32))'
XJOBSCORE_ONESHOT_ALLOWED_EMAILS=you@gmail.com,friend@gmail.com
DATABASE_URL=postgresql://localhost/xjobscore
```

A real environment variable overrides the file, so a deployment injecting its own
config is never shadowed by a stray `.env`. The loader runs inside
`load_settings()`, not at import, so constructing `Settings` directly — which every
test does — never depends on what is on a given developer's disk.

`XJOBSCORE_ONESHOT_PUBLIC_URL` defaults to `http://127.0.0.1:8766` and builds the
OAuth `redirect_uri`; set it only when the server is reached at another origin,
and register that exact string in the Google console.

The session is a signed cookie (Starlette `SessionMiddleware`), so there is no
session table. The OIDC exchange is hand-rolled over `httpx` in
[`oneshot_ui/auth.py`](oneshot_ui/auth.py): the `id_token` arrives on a direct
server-to-server TLS POST rather than through the browser, so it needs no
signature verification, and the profile is read from the userinfo endpoint.

## Run

```bash
uv run -m oneshot_ui
```

The server starts at <http://127.0.0.1:8766>. Opportunities, Applications,
and Feed Status all use the live `/api` transport.

## The browser client

The client lives outside this package, at the repo root in [`frontend/`](../../frontend),
so it can eventually be deployed on its own. This server still serves it during
local development, which keeps everything same-origin and the run command above
unchanged.

Set `XJOBSCORE_ONESHOT_STATIC_DIR` to serve the client from another path. If that
path does not exist, the page routes and the `/static` mount are skipped and this
process runs as a pure JSON API.

## Resume studio

<http://127.0.0.1:8766/resume/> is a resume workspace, account-scoped and stored
in PostgreSQL alongside everything else: `resume_workspace` holds the whole
content bank as one jsonb row, `resume_target` the jobs a resume is tailored
for, and `resume_document` the assembled resumes.

One row for the bank, rather than a table per content type, is what keeps
`resume_document.selection` valid: entries, bullets, and skills all draw ids from
a single `next_item_id` counter, so a saved selection keeps resolving. The list
logic lives in [`oneshot_ui/resume_workspace.py`](oneshot_ui/resume_workspace.py)
with no I/O, so the store and the test fake share one set of rules.

The flow: maintain a **content bank** (profile, entries, bullets with audience
tags and defense notes, skills), then in the **builder** assemble a named resume
as a selection of bullets/skills. **Tailor resume** on a tracked application
creates a target from it and opens a resume for that job. Each resume picks a
design (`main` or `barlow`), and the server renders it via
[Typst](https://typst.app) — the compiler ships as the `typst` wheel, so there
is no system binary to install. The Barlow design's fonts are vendored in
`oneshot_ui/fonts/` (SIL Open Font License).

Endpoints live under `/api/resume/*`; `GET /api/resume/resumes/{id}/pdf`
returns the compiled PDF (503 with the compiler's message if it fails).
Signed out, `POST /api/resume/render/pdf` renders a document sent in the
request body.

## Data contract

`job_posting.attributes` is the versioned enrichment contract consumed by the
Opportunities UI. Skills are stored as canonical taxonomy ids:

```json
{
  "version": 1,
  "job_type": "internship",
  "term": "Summer 2027",
  "workplace": "hybrid",
  "skills": ["python", "postgresql"],
  "summary": "Build and operate backend services for the data platform, own a scoped project from technical design through production rollout, and collaborate with infrastructure and product engineers to improve reliability. Currently enrolled in a computer science or related degree program with experience in Python and SQL.",
  "deadline_at": "2027-02-01T23:59:59Z"
}
```

Keys other than `version` may be absent while enrichment is pending. PostgreSQL
enforces their types and allowed enum values.

`GET /api/jobs` leaves those stored attributes unchanged, promotes `summary` to
the top level, and adds taxonomy metadata for presentation. It deliberately
omits the full source description; the canonical text remains in PostgreSQL for
re-enrichment, while the UI links to the original posting for complete details:

```json
{
  "summary": "Build and operate backend services for the data platform, own a scoped project from technical design through production rollout, and collaborate with infrastructure and product engineers to improve reliability. Currently enrolled in a computer science or related degree program with experience in Python and SQL.",
  "skills": [
    {"id": "python", "name": "Python", "category": "language"},
    {"id": "postgresql", "name": "PostgreSQL", "category": "stack"}
  ]
}
```

Historical display-name values and unknown taxonomy strings remain visible
during replay; they never cause the API to rewrite `job_posting.attributes`.

The same response collapses each non-rejected duplicate group with a populated
`primary_posting_id` into one opportunity. Counts, filters, and pagination use
that representative too. Active alternate postings are exposed without internal
group metadata through the additive `other_sources` field:

```json
{
  "other_sources": [
    {
      "source": "workday:example",
      "url": "https://example.com/job/42",
      "posted_at": "2026-08-20T00:00:00+00:00",
      "first_seen_at": "2026-08-20T00:05:00+00:00"
    }
  ]
}
```

Tracked and browser-excluded posting ids suppress their whole eligible group,
so a primary change does not make the same opportunity reappear.

## Tests

```bash
cd xjobscore/oneshot-ui
../../.venv/bin/pytest
cd ../../frontend
node --test
```

The PostgreSQL tests are opt-in so the ordinary suite never touches a developer
database. They cover what a fake cannot: that jsonb comes back as Python objects
rather than strings, that the workspace row lock survives concurrent writes, and
that one account cannot read or mutate another's data.

```bash
createdb xjobscore_test
psql -v ON_ERROR_STOP=1 --dbname=xjobscore_test --file=../../schema.sql
XJOBSCORE_TEST_DSN=postgresql://localhost/xjobscore_test ../../.venv/bin/pytest
```
