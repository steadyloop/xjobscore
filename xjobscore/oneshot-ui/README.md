# oneshot-ui

A local job-discovery and application workspace backed directly by the
project's PostgreSQL `schema.sql`.

See [CLEANUP_AND_ROLLBACK.md](CLEANUP_AND_ROLLBACK.md) for the August 2026
cleanup record, compatibility changes, validation, and rollback procedures.

OneShot does not harvest or canonicalize postings. The core xjobscore pipeline
owns `feed`, `schedule`, `raw_posting`, and `job_posting`; this application
reads those tables and writes `job_application` and `job_application_event`.

## Database

Create the database and apply the root schema:

```bash
createdb xjobscore
psql -v ON_ERROR_STOP=1 --dbname=xjobscore --file=../../schema.sql
```

OneShot uses the schema's fixed local account while the product is single-user:

```text
00000000-0000-0000-0000-000000000001
```

Configure one of these DSN variables, in precedence order:

```bash
export XJOBSCORE_ONESHOT_DSN=postgresql://localhost/xjobscore
# or XJOBSCORE_DSN
# or DATABASE_URL
```

## Run

```bash
cd xjobscore/oneshot-ui
../../.venv/bin/python -m oneshot_ui
```

The server starts at <http://127.0.0.1:8766>. Opportunities, Applications,
and Source Coverage all use the live `/api` transport.

The standalone discovery concept is available at <http://127.0.0.1:8766/discover/>.

## Data contract

`job_posting.attributes` is the versioned enrichment contract consumed by the
Opportunities UI:

```json
{
  "version": 1,
  "opportunity_type": "summer_internship",
  "term": "Summer 2027",
  "workplace": "hybrid",
  "skills": ["Python", "PostgreSQL"],
  "deadline_at": "2027-02-01T23:59:59Z",
  "eligibility": "eligible"
}
```

Keys other than `version` may be absent while enrichment is pending. PostgreSQL
enforces their types and allowed enum values.

## Tests

```bash
cd xjobscore/oneshot-ui
../../.venv/bin/pytest
cd oneshot_ui/static
node --test app.test.js application/app.test.js source-coverage/app.test.js
```

The repository integration test is opt-in so the ordinary suite never touches
a developer database:

```bash
XJOBSCORE_TEST_DSN=postgresql://localhost/xjobscore_test \
  ../../.venv/bin/pytest tests/test_postgres_repository.py
```
