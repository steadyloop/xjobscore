# raw_inspection

Harvests one live feed per source adapter and writes what came back.

- `harvested/<source>.json` — `Record.envelope` + `Record.items[0]`, as the adapter returned them.
- `parsed/<source>.json` — that same item run through the source's `parse()`, as a `Posting` (title/company_raw un-normalized — that normalization happens later, in `silver.replay`, not here).

Parse always runs right after harvest, in the same call, on the item still in memory. There is no way to parse without harvesting first.

Feeds come from `xjobscore/sources/catalogs/*.toml`, not the database — no Postgres needed.

## Commands

Run from the repo root, via `python -m` (not `python <file>.py`). `xjobscore` has no
`__init__.py`; `-m` implicitly resolves it as a namespace package as long as cwd is the
repo root, so `PYTHONPATH=.` is not actually needed here — only set it if you're
invoking from elsewhere.

**1. Run all (harvest + parse) on all sources**
```bash
.venv/bin/python -m xjobscore.raw_inspection
```

**2. Harvest only, for all sources** (skip parse)
```bash
.venv/bin/python -m xjobscore.raw_inspection --harvest-only
```

**2. Parse only, for all sources** — not supported; parse cannot run without a fresh harvest.

**3. Run all (harvest + parse) for one source**
```bash
.venv/bin/python -m xjobscore.raw_inspection <source>
```

**3. Harvest only, for one source**
```bash
.venv/bin/python -m xjobscore.raw_inspection <source> --harvest-only
```

**3. Parse only, for one source** — not supported, same as above.

## Other flags

- `--list` — print resolved targets, no network call.
- `--timeout <seconds>` — per-source cap (default 300).
- `--concurrency <n>` — sources fetched in parallel (default 4).
