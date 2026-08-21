# keyscan

`keyscan` is a standalone, resumable ATS-board token scanner. It is fully
self-contained: it imports nothing from `xjobscore`, reads no environment
variables, and writes nothing outside `keyscan/work/`. Its SQLite file is its
only output.

Run from the repository root:

```sh
PYTHONPATH=. uv run python -m keyscan selftest
PYTHONPATH=. uv run python -m keyscan run --methods commoncrawl --crawls 6
PYTHONPATH=. uv run python -m keyscan run --methods commoncrawl --sources adp,oracle,ukg
PYTHONPATH=. uv run python -m keyscan run --methods board --archives
```

Working state is local to `keyscan/work/`:

- `keyscan.sqlite` holds runs, candidates, evidence, and checkpoints.
- `runs/<id>/run.jsonl` is the append-only log for that run.
- `runs/<id>/commoncrawl/` holds resumable parquet output.

Useful commands:

```sh
PYTHONPATH=. uv run python -m keyscan status
PYTHONPATH=. uv run python -m keyscan candidates --validation live
PYTHONPATH=. uv run python -m keyscan robots --sources greenhouse,lever
```

Results leave keyscan by reading the database directly:

```sh
sqlite3 keyscan/work/keyscan.sqlite \
  "SELECT source, token, job_count FROM candidates WHERE validation = 'live'"
```

Ctrl-C records an interrupted run; resume it with `resume <run-id>`.

## Discovery methods

There are two, and they are independent.

`--methods commoncrawl` scans Common Crawl's columnar index with DuckDB, one
vendor at a time, checkpointing each finished crawl so an interrupted run
resumes where it stopped. `robots` reports which shared ATS hosts block CCBot
— a vendor that blocks it will never appear in the index, which is the
difference between a broken rule and a genuine blind spot.

`--methods board` reads public GitHub-hosted internship boards (`BOARD_FEEDS`
in `core.py`). These boards publish no tokens directly -- each listing row
carries a per-posting ATS deep link, which `rules.extract` reduces to a
`(source, token)` pair. `company_id` is always null; a token lands with only
the board's `company_name` to identify it.

The boards' live listing files change a few times a week at most, so `board`
is cheap to run often. `--archives` additionally reads their frozen
`archived/*.json` history -- worth one seeding pass, pointless on a recurring
poll, so it is off by default.

## Validation runs alongside discovery

Tokens are probed as they are found, by three workers draining a queue that
`_record_candidate` feeds, so a token is usable long before the scan that
found it ends. This is also faster overall: `_request` serialises requests per
host, so validating only at the end leaves every vendor host idle for the whole
scan and then queues every probe behind one lock.

A run still ends with a sweep, which is what backfills a resumed run and
retries anything a probe dropped. The sweep feeds the same worker pool, and
`--methods validate` runs it with no discovery at all — use that to drain a
backlog of already-discovered tokens rather than re-fetching crawl manifests:

```sh
PYTHONPATH=. uv run python -m keyscan run --methods validate
PYTHONPATH=. uv run python -m keyscan run --methods validate --sources greenhouse
```

A failed probe is logged and leaves its candidate unvalidated for the next run
rather than aborting the scan.

A zero job count is only accepted as `live` when the response also identifies
the company. Several vendors return an empty-but-valid listing for a token that
does not exist, so a bare zero with no company name is recorded as
`inconclusive`, not as a live board.

Comeet candidates are validated in two public requests: keyscan reads the
board page for Comeet's public API token, then validates the jobs API. A live
candidate is stored as `board-id/api-token`.

SQLite runs in WAL mode, so you can query the file with any SQL client while a
scan is writing to it.

Keyscan has rules for every currently implemented token-scoped ATS. TalentReef
is intentionally absent: its implemented source is one platform-wide feed and
has no tenant token to discover.
