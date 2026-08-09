# OneShot UI cleanup and rollback record

This document records the cleanup performed on August 9, 2026. It exists so
future maintainers can understand why files and compatibility paths were
removed, identify likely regression points, and selectively restore prior
behavior if an unknown consumer is discovered.

## Scope and intent

The cleanup reduced `oneshot-ui` to the code and assets used by its current
PostgreSQL-backed pages:

- `/` — Opportunities
- `/discover/` — standalone, disconnected discovery concept
- `/static/application/index.html` — Applications
- `/static/source-coverage/index.html` — Source Coverage

The subtree was approximately 30 MB before cleanup and approximately 276 KB
after cleanup. Most of the size reduction came from an accidental nested
virtual environment and generated caches. Production and test code was also
reduced by removing unreachable decision-engine code and UI remnants.

The worktree already contained tracked modifications, tracked deletions, and
untracked files before this cleanup began. For that reason, no `git reset` was
used and Git's index was not treated as the pre-cleanup source of truth.

## Pre-cleanup snapshot

An exact recursive copy was made before the first deletion and verified with
`diff -qr`:

```text
/private/tmp/oneshot-ui-precleanup-2026-08-09
```

The snapshot includes tracked, untracked, ignored, modified, and generated
files as they existed immediately before cleanup. It is the safest source for
an immediate selective rollback on the machine where the cleanup occurred.

`/private/tmp` may be cleared by the operating system. Once this cleanup is
committed, Git history must be considered the durable rollback source.

## Files removed by this cleanup

Unreachable Python decision-engine code:

```text
oneshot_ui/classifier.py
oneshot_ui/dedupe.py
oneshot_ui/lifecycle.py
oneshot_ui/scoring.py
oneshot_ui/source_policy.py
oneshot_ui/taxonomy.py
oneshot_ui/util.py
tests/test_decision_engine.py
```

Disconnected Discover assets:

```text
oneshot_ui/static/discover/app.js
oneshot_ui/static/discover/app.test.js
oneshot_ui/static/discover/nav.css
oneshot_ui/static/discover/styles.css
```

Files superseded by consolidation:

```text
run.py
oneshot_ui/static/layout.css
oneshot_ui/static/application/base.css
var/.gitignore
```

Generated local artifacts were also removed:

```text
.venv/
.pytest_cache/
oneshot_ui/__pycache__/
oneshot_ui/demo/__pycache__/
tests/__pycache__/
var/
```

The nested `.venv` was redundant; documented commands use the repository-root
environment at `../../.venv`.

## Backend and API changes

The backend was reduced to the API used by the current pages. The supported
routes are now:

```text
GET    /api/health
GET    /api/jobs
GET    /api/source-coverage
POST   /api/jobs/{job_id}/applications
POST   /api/applications/external
GET    /api/applications
GET    /api/applications/{application_id}
PATCH  /api/applications/{application_id}
DELETE /api/applications/{application_id}
```

Important compatibility removals:

- `GET /api/jobs/{job_id}` was removed. The Opportunities list response now
  contains every field needed by its detail panel.
- `POST /api/applications/{application_id}/events` was removed. The current UI
  changes stages through the application `PATCH` endpoint.
- The unused `compact` and `eligibility` job-list parameters were removed.
- Creating an application from a discovered job no longer accepts an initial
  stage, note, submission date, confirmation, or resume variant. The current
  action always creates a `considering` record.
- Job responses no longer contain duplicate compatibility aliases such as
  `company_name`, `location_text`, `description_html`, `posting_url`, or
  `published_at`.
- Application responses no longer include unused opportunity-type or resume
  variant fields.
- The job list excludes jobs already tracked as applications. This matches
  sample mode and prevents an already-added job from remaining selectable.
- `/api/health` is now a lightweight process-readiness response. It no longer
  performs count queries whose results were ignored by the UI.

If a regression affects an external API consumer rather than one of the four
pages above, these removals should be investigated first.

## Frontend changes

- Remote API transport is now the default. The unused stub transport,
  `connect()`, job-detail client, and event client were removed.
- Opportunities uses the server's filtering and sorting instead of repeating
  those operations in the browser.
- Deleted multi-view navigation state, compatibility field lookup, legacy date
  helpers, and other test-only utilities were removed from the Opportunities
  application.
- Old SQLite application-stage translation was removed. Applications now uses
  the PostgreSQL stage vocabulary directly.
- Sample data now follows the live API shape and schema enums. Its storage key
  changed from `oneshot.sample-data.state.v3` to `.v4`, intentionally avoiding
  reuse of stale browser-local records.
- Root and Applications CSS were consolidated to one stylesheet per page.
  Source Coverage CSS was reduced in place. Every remaining CSS class and
  custom property is referenced by the corresponding page.
- Unused Source Coverage SVG symbols were removed.
- `/discover/` intentionally loads only `discover/index.html` and `stub.css`.
  It contains no OneShot navigation, API client, script, or link back into the
  primary application.
- Opportunities now has one canonical layout at `/`. The temporary
  `opportunities-full/index.html` and `opportunities-split/index.html`
  presentation variants, their navigation links, and their conditional CSS
  were removed. They shared the same API and JavaScript, so this removed no
  backend or PostgreSQL behavior.
- The Opportunities navigation badge is populated on every primary page by
  `static/nav-count.js`. It reads the unfiltered `total` from the existing jobs
  endpoint with `limit=1`, so it uses the same account-specific exclusion of
  already-tracked jobs rather than maintaining separate counting logic.

## Packaging and startup changes

- Package version is now `0.2.0` everywhere it is declared.
- The supported launcher is:

  ```bash
  ../../.venv/bin/python -m oneshot_ui
  ```

- `run.py` and its repository-path mutation were removed.
- `pydantic` is declared directly because application code imports it.
- `httpx` moved to the development dependency group because it is used by
  tests, not the running server.
- `uv.lock` was regenerated after the dependency changes.

## Validation performed

The cleaned implementation passed:

```text
Python:     4 passed, 1 skipped
JavaScript: 19 passed
```

The skipped test is the opt-in PostgreSQL round trip. Run it with a disposable
database before deployment when one is available:

```bash
XJOBSCORE_TEST_DSN=postgresql://localhost/xjobscore_test \
  ../../.venv/bin/pytest tests/test_postgres_repository.py
```

Additional validation covered:

- every browser entrypoint and referenced static asset;
- CSS syntax balance, class reachability, and custom-property reachability;
- SVG use references against symbols defined by each page;
- `uv lock --check --offline`;
- `git diff --check`;
- absence of generated environments and caches inside `oneshot-ui`.

A live browser/database session was not performed because neither configured
server port was running and no PostgreSQL test DSN was provided.

## Rollback procedures

### Restore one file immediately from the local snapshot

Prefer a selective restore when a single removed or simplified component is
needed:

```bash
cp /private/tmp/oneshot-ui-precleanup-2026-08-09/oneshot_ui/FILE.py \
  /Users/ck/Documents/GitHub/xjobscore/xjobscore/oneshot-ui/oneshot_ui/FILE.py
```

After restoring code, also restore or update its imports, routes, tests, and
dependencies. A formerly reachable file should not be reintroduced in
isolation.

### Restore the complete pre-cleanup subtree immediately

Stop the server first. This command makes the working subtree exactly match
the snapshot and deletes cleanup-era files that were not present in it:

```bash
rsync -a --delete \
  /private/tmp/oneshot-ui-precleanup-2026-08-09/ \
  /Users/ck/Documents/GitHub/xjobscore/xjobscore/oneshot-ui/
```

This is intentionally destructive to the current `oneshot-ui` subtree. Do not
use it after new legitimate work has been added without first taking another
snapshot.

### Roll back after the cleanup is committed

Find the commit that introduced this document:

```bash
git log --follow --format='%H %s' -- \
  xjobscore/oneshot-ui/CLEANUP_AND_ROLLBACK.md
```

If the cleanup was committed independently, revert that commit:

```bash
git revert CLEANUP_COMMIT
```

If the commit contains unrelated work, restore only the affected paths from
its parent instead:

```bash
git restore --source=CLEANUP_COMMIT^ -- \
  xjobscore/oneshot-ui/path/to/file
```

Always inspect the resulting diff and rerun the Python and JavaScript suites
before committing a rollback.

The two temporary Opportunities variants were created after the local
pre-cleanup snapshot, so that snapshot cannot restore them. If their removal
has been committed and a presentation variant is later needed, recover its
HTML and the associated navigation/CSS from the parent of that removal commit
with `git restore --source=REMOVAL_COMMIT^ -- PATH`, then add focused layout
coverage. Do not restore either entrypoint without its layout rules.

## Recommended regression triage

When something is reported missing, determine which boundary failed before
restoring code:

1. For a browser-page failure, check the page's HTML asset references and
   JavaScript console first.
2. For an API client failure, compare its request or expected response fields
   with the compatibility removals above.
3. For sample-only behavior, clear the sample-data keys in local storage and
   allow the `.v4` fixture to initialize again.
4. For a visual regression, compare the current page stylesheet with the
   corresponding pre-cleanup stylesheet or stylesheets in the snapshot.
5. Restore the smallest coherent behavior, add a regression test for the
   newly discovered consumer, and avoid restoring an entire legacy subsystem
   unless it is genuinely required.
