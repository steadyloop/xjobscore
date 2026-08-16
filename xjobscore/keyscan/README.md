# keyscan

`keyscan` is a standalone, resumable ATS-board token scanner. It does not
write OneShot, catalog, or application data.

Run from the repository root:

```sh
PYTHONPATH=. uv run python -m xjobscore.keyscan selftest
PYTHONPATH=. uv run python -m xjobscore.keyscan run \
  --methods company,commoncrawl,wayback --crawls 6
PYTHONPATH=. uv run python -m xjobscore.keyscan run \
  --methods company,commoncrawl --sources adp,oracle,ukg
```

Working state is local to `xjobscore/keyscan/work/`:

- `keyscan.sqlite` holds runs, candidates, evidence, decisions, and checkpoints.
- `runs/<id>/run.jsonl` is the append-only log for that run.
- `runs/<id>/commoncrawl/` holds resumable parquet output.

Useful commands:

```sh
PYTHONPATH=. uv run python -m xjobscore.keyscan status
PYTHONPATH=. uv run python -m xjobscore.keyscan candidates --validation live --decision pending
PYTHONPATH=. uv run python -m xjobscore.keyscan accept 42
PYTHONPATH=. uv run python -m xjobscore.keyscan export accepted.jsonl --accepted
PYTHONPATH=. uv run python -m xjobscore.keyscan catalog
PYTHONPATH=. uv run python -m xjobscore.keyscan robots --sources greenhouse,lever
```

`catalog` updates the canonical TOML files in
`xjobscore/sources/registry/ats-catalog/`. Existing feeds and comments are
preserved; new candidates are appended only when their company ID already
exists in the registry. Candidates without a known registry company are
skipped rather than producing invalid feed entries. Feeds that need adapter
config Keyscan could not establish are written with `enabled = false` rather
than guessed. Bullhorn is the notable case: its token is discoverable, but its
employer-specific posting URL is not part of the public API response.

Ctrl-C records an interrupted run; resume it with `resume <run-id>`. The
package never imports or writes legacy `cc_ats`.

## Validation runs alongside discovery

Tokens are probed as they are found, by three workers draining a queue that
`_record_candidate` feeds, so a token is usable long before the crawl that
found it ends. This is also faster overall: `_request` serialises requests per
host, so validating only at the end leaves every vendor host idle for the whole
crawl and then queues every probe behind one lock.

A run still ends with a sweep, which is what backfills a resumed run and
retries anything a probe dropped. The sweep feeds the same worker pool, and
`--methods validate` runs it with no discovery at all — use that to drain a
backlog of already-discovered tokens:

```sh
PYTHONPATH=. uv run python -m xjobscore.keyscan run --methods validate
PYTHONPATH=. uv run python -m xjobscore.keyscan run --methods validate --sources greenhouse
```

A failed probe is logged and leaves its candidate unvalidated for the next run
rather than aborting the crawl.

## HTTP daemon

`serve` runs a FastAPI app for API-triggered runs and incremental polling. The
scan writes SQLite; the API only reads it.

```sh
PYTHONPATH=. uv run python -m xjobscore.keyscan serve --port 8787
```

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness plus whether a run is active |
| `POST /runs` | `{"methods": [...], "crawls": 6, "sources": [...]}`; 409 if a run is active, 400 on bad input |
| `GET /runs` `GET /runs/{id}` | latest or named run status |
| `POST /runs/{id}/cancel` `POST /runs/{id}/resume` | run control |
| `GET /runs/{id}/logs?offset=` | durable log, paged by byte offset |
| `GET /candidates?since_seq=` | tokens validated since the cursor |

Poll `GET /candidates` with the `next_seq` from the previous response:

```sh
curl 'http://127.0.0.1:8787/candidates?since_seq=0&validation=live&limit=500'
```

The cursor is `validated_seq`, a counter bumped on every validation — **not**
`id`. A candidate is inserted during discovery and validated minutes later, so
an id-ordered poller would step past a token before it ever went live. `more`
is true when the page filled, meaning poll again immediately rather than
waiting out the interval. Pass `validation=all` to see absent and inconclusive
candidates too.

The endpoint serves every `live` candidate; it does not wait on the
`accept`/`reject` gate, which remains available for catalog export. There is no
authentication and the default bind is `127.0.0.1`. Do not expose the port.

An existing `work/keyscan.sqlite` gains `validated_seq` and WAL mode on the
next command, and already-validated rows are backfilled in id order so a
poller starting at zero receives them too. Candidates that were never probed
keep a null cursor until `--methods validate` reaches them. A database written
before this subsystem was renamed from `tokscan` sits at `work/tokscan.sqlite`;
rename the file and the next command drops the stale `tokscan_*` indexes.

Comeet candidates are validated in two public requests: keyscan reads the
board page for Comeet's public API token, then validates the jobs API. A live
candidate is exported as `board-id/api-token`; split it once into the existing
Comeet adapter's `key` and `config.token` fields.

Keyscan has rules for every currently implemented token-scoped ATS. TalentReef
is intentionally absent: its implemented source is one platform-wide feed and
has no tenant token to discover.
