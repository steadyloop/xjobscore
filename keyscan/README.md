# keyscan

`keyscan` is a standalone, resumable ATS-board token scanner. It is fully
self-contained: it imports nothing from `xjobscore`, reads no environment
variables, and writes nothing outside `keyscan/work/`. Its SQLite file is its
only output.

Run from the repository root:

```sh
PYTHONPATH=. uv run python -m keyscan selftest
PYTHONPATH=. uv run python -m keyscan crawl --from 2026-01-01 --to 2026-08-21
PYTHONPATH=. uv run python -m keyscan crawl --from 2026-06-01 --to 2026-08-21 --vendor adp,oracle,ukg
PYTHONPATH=. uv run python -m keyscan scan --archives
```

`crawl`, `scan`, `validate`, and `resume` print a compact live progress stream to stderr while keeping
the final JSON result alone on stdout. Common Crawl reports approximately every
100 files (104/200/304/400/504/600 with the default eight-file batches), plus
retries, snapshot completion, validation totals, and final status. Pass
`--quiet` to suppress the live stream for automation; the complete durable log
is written regardless.

Working state is local to `keyscan/work/`:

- `keyscan.sqlite` holds runs, tenants, evidence, and checkpoints.
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
  "SELECT ats, key, json_extract(details, '\$.job_count') AS job_count
     FROM tenants WHERE status = 'live'"
```

Ctrl-C records an interrupted run; resume it with `resume <run-id>`.

## Discovery methods

There are two, and they are independent. A third, `enum`, covers the vendor
neither can reach.

`crawl` scans Common Crawl's columnar index with DuckDB, one
vendor at a time, checkpointing each finished crawl so an interrupted run
resumes where it stopped. `robots` reports which shared ATS hosts block CCBot
— a vendor that blocks it will never appear in the index, which is the
difference between a broken rule and a genuine blind spot.

`footers` collects Parquet metadata so later scans can skip unrelated files:

```sh
PYTHONPATH=. uv run python -m keyscan footers
```

The 2012, 2009–2010, and 2008–2009 collections predate the Parquet index.
The UI snapshot picker and `keyscan list` exclude them, including when using
an older cached collection list. The default `footers` selection uses this
same supported list.
`footers` and `crawl` skip these with an explanation, without requesting a
missing manifest or marking them scanned. A `crawl` selection containing only
these collections reports that no supported index was selected. For `footers`,
the JSON `skipped` map explains each omission; `no_index` is retained for
compatibility. Neither means the archived webpages contain no website footers.
An HTTP 404 for another crawl is reported as a missing manifest, rather than
classified as an unsupported legacy collection.

`scan` reads public GitHub-hosted internship boards (`BOARD_FEEDS`
in `core.py`). These boards publish no tokens directly -- each listing row
carries a per-posting ATS deep link, which `rules.extract` reduces to a
`(source, token)` pair. `company_id` is always null; a token lands with only
the board's `company_name` to identify it.

The boards' live listing files change a few times a week at most, so `board`
is cheap to run often. `--archives` additionally reads their frozen
`archived/*.json` history -- worth one seeding pass, pointless on a recurring
poll, so it is off by default.

`enum` is dictionary enumeration for a vendor with no domain to scan: iCIMS
serves each board on the customer's own `careers.{x}.com`, so the only way in
is to guess `x`. It takes a wordlist (one word or domain per line; a domain
contributes its first label), probes each guess with the vendor's own rule,
and writes only the boards that answered `live`, as `method = 'enum'`. Misses
are counted, not stored. A company list is the natural dictionary:

```sh
psql -At -c "SELECT domain FROM company WHERE domain ~ '\.com$'" xjobscore \
  | PYTHONPATH=. uv run python -m keyscan enum --vendor icims
```

`--dry-run` probes and reports without writing; `--concurrency` bounds probes
in flight (default 16).

`wayback` asks the Internet Archive's CDX index for every capture under each
vendor domain, one capture year at a time, and runs the URLs through the same
rule patterns the crawl uses. It reaches what Common Crawl cannot: hosts that
refuse CCBot (jobs.lever.co, absent from every snapshot since CC-MAIN-2025-47)
and pages people saved by hand. Defaults to tier1 + tier2, every year from
2013; each finished (vendor, year) is recorded in `scanned` as `wayback:YYYY`
and skipped by later runs unless `--rescan`. Requests are spaced 4s apart (the
archive's ~15/min ceiling) and back off on 429/503:

```sh
uv run python -m keyscan wayback --vendor lever --years 2025,2026
uv run python -m keyscan wayback --from-year 2020
```

The archive rate-limits by network as well as by request rate: from a
datacenter or VPN address (its 429 carries `x-nid: <network>`) it can refuse
every request regardless of pacing, so run it from a residential connection.

## Validation runs alongside discovery

Tokens are probed as they are found, by three workers draining a queue that
`_record_candidate` feeds, so a token is usable long before the scan that
found it ends. This is also faster overall: `_request` serialises requests per
host, so validating only at the end leaves every vendor host idle for the whole
scan and then queues every probe behind one lock.

A run still ends with a sweep, which is what backfills a resumed run and
retries anything a probe dropped. The sweep feeds the same worker pool, and
`validate` runs it with no discovery at all — use that to drain a
backlog of already-discovered tokens rather than re-fetching crawl manifests:

```sh
PYTHONPATH=. uv run python -m keyscan validate
PYTHONPATH=. uv run python -m keyscan validate --vendor greenhouse
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
