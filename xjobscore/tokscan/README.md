# tokscan

`tokscan` is a standalone, resumable ATS-board token scanner. It does not
write OneShot, catalog, or application data.

Run from the repository root:

```sh
PYTHONPATH=. uv run python -m xjobscore.tokscan selftest
PYTHONPATH=. uv run python -m xjobscore.tokscan run \
  --methods company,commoncrawl,wayback --crawls 6
```

Working state is local to `xjobscore/tokscan/work/`:

- `tokscan.sqlite` holds runs, candidates, evidence, decisions, and checkpoints.
- `runs/<id>/run.jsonl` is the append-only log for that run.
- `runs/<id>/commoncrawl/` holds resumable parquet output.

Useful commands:

```sh
PYTHONPATH=. uv run python -m xjobscore.tokscan status
PYTHONPATH=. uv run python -m xjobscore.tokscan candidates --validation live --decision pending
PYTHONPATH=. uv run python -m xjobscore.tokscan accept 42
PYTHONPATH=. uv run python -m xjobscore.tokscan export accepted.jsonl --accepted
PYTHONPATH=. uv run python -m xjobscore.tokscan catalog
```

`catalog` writes one TOML per ATS to `xjobscore/tokscan/catalogs/`, using the
same schema as `xjobscore/sources/catalogs/`. It exports live candidates by
default; add `--accepted` after review. SQLite remains the working store.

Runs are explicit and blocking. Ctrl-C records an interrupted run; resume it
with `resume <run-id>`. The package never imports or writes legacy `cc_ats`.

Comeet candidates are validated in two public requests: tokscan reads the
board page for Comeet's public API token, then validates the jobs API. A live
candidate is exported as `board-id/api-token`; split it once into the existing
Comeet adapter's `key` and `config.token` fields.
