# Architecture updates

## 1. Unify the field-mapping DSL across JSON and XML/RSS/JSON-LD

`json-api-configs.py` uses the declarative `_value` vocabulary (`@path`, `fallback`,
`join`, `template`, `date`, `lookup`, `map`, ...). `docxtract.py` instead dispatches to
hand-written Python parsers (`_rss`, `_schemaorg`, `_simplify`) selected by
`config["parser"]`. `_rss` (docxtract.py:261-305) already carries per-vendor flags
(`company_path`, `location_path`, `unescape_description`) added one at a time.

`doc/adapter-recon.md` engine gaps 1 and 2 (lines 39-46) — no Atom parser, no generic
XML field map — are both instances of the same missing capability: a declarative mapper
over XML the way `_value` already covers JSON.

Change: normalize XML/Atom/JSON-LD to a tree once, extend `@path` to address attributes
(e.g. `@link/@href`), and replace `_rss`/`_schemaorg` with the same `fields` config
vocabulary JSON sources use. Closes both current engine gaps; stops `_rss` from growing
a new flag per vendor.

## 2. Validate `CONFIGS` at load time

`json-api-configs.py` / `docxtract-configs.py` are executable adapter data and
need validation just as much as a database-backed feed configuration does.
— have no schema validation. A misspelled key in a `fields` mapping silently resolves
to `None` via `_dig`'s `.get()` chains, matching the exact silent-absence failure mode
documented as a verified defect in CLAUDE.md (Oracle drops 100% of postings).

Change: add a load-time schema check for `CONFIGS` (both `json-api-configs.py` and
`docxtract-configs.py`) with strict validation — fail loud
on an unrecognized or malformed key instead of resolving to a missing field.

## 3. Give community/aggregator boards a real third contract

`doc/adapter-recon.md` (lines 130-133): HN "Who is hiring" is blocked not by transport
(the Algolia API is clean paginated JSON) but because turning one freeform comment into
`title`/`company_raw` "is not expressible as field mapping." This currently has no home
and would fall into `bespoke` alongside genuinely one-off company APIs (e.g. Jane Street)
— same bucket, different problem shape. `Posting.fidelity` (`"origin"` vs
`"secondhand"`, used in `janestreet.py`/`simplify`) is the right seed but isn't a
systematic engine.

Change: add a third small engine for freeform/community sources — fetch/paginate
(reuse existing transport helpers), then a pluggable structuring step (regex splitter
and/or schema-constrained extraction) producing `Posting`s tagged with a
confidence/fidelity field, instead of a one-off `bespoke` module per board.

Resolution for HN (2026-08-09): implemented as the single focused bespoke module
`hn.py`. It discovers the latest monthly thread, paginates Algolia, keeps top-level
comments, and conservatively structures their pipe-delimited headers. A third engine
is deferred until a second community source demonstrates a reusable contract.

## 4. Remove duplicated tree-walk and dead aliasing

- `_dig` is independently reimplemented in `json-api.py:28-42` and
  `docxtract.py:135-142` — identical dict/list path walk, two copies.
- `json-api.py:21-23` — `SPECS = REGISTRY = ADAPTERS = CONFIGS`, three names for one
  dict; only `SPECS` is referenced elsewhere.
- `json-api.py:285-289` — `get` / `get_adapter`, same dead-alias pattern.

Change: one shared `_dig`; drop unused aliases, keep one canonical name each.

## 5. Gate fixture coverage per (engine, shape), not per source

`test_docxtract_rss.py` gives the RSS shape (successfactors, teamtailor) golden-file
coverage. No equivalent fixture test found for the other `docxtract` extraction shapes:
`jsonld`/`html_links` (jobvite) or `json_items` (simplify).

Change: require one golden fixture test per (engine, extraction shape, parser) triple
before a manifest entry may be marked `status = "implemented"`.

## 6. Confirm detail fan-out is batched, not serial

`DETAIL_A`/`DETAIL_B` configs (smartrecruiters, oracle, workday, bamboohr, rippling)
issue one request per item in `json-api.py`'s detail-fetch loop (lines 227-252), with
no visible batching/concurrency control in this file. For a board with thousands of
postings this is thousands of serial requests per poll.

Change: verify `net.py` applies concurrency/politeness limits to this path; if not, add
batching so a config author adding a new `DETAIL_A`-shaped source can't accidentally
hammer a vendor.
