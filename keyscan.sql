-- keyscan -- SQLite
-- NOT source of truth. keyscan/core.py `_initialize()` builds the real
-- database; this file mirrors it for reading, and adds the JSON payload shapes
-- SQLite itself cannot declare. Regenerate the DDL below with:
--   sqlite3 keyscan/work/keyscan.sqlite .schema
--
-- The file lives at keyscan/work/keyscan.sqlite and is keyscan's only output.
PRAGMA foreign_keys = ON;

-- RUNS --
-- One row per invocation of crawl/scan/validate/resume. `keyscan_one_active`
-- makes "running" exclusive, so a second CLI cannot start on top of a first.
CREATE TABLE runs (
    id              TEXT PRIMARY KEY,   -- 32 hex chars, minted at start
    methods_json    TEXT NOT NULL,      -- subset of ["commoncrawl","board","validate"]
    config_json     TEXT NOT NULL,      -- see JSON PAYLOADS
    status          TEXT NOT NULL
        CHECK (status IN ('running','interrupted','cancelled','completed','failed')),
    phase           TEXT NOT NULL,      -- crawl | scan | validate | board, then the terminal status
    checkpoint_json TEXT NOT NULL DEFAULT '{}',  -- resume position
    counts_json     TEXT NOT NULL DEFAULT '{}',  -- found/live/dead/unknown
    artifact_dir    TEXT NOT NULL,      -- keyscan/work/runs/<id>
    pid             INTEGER,            -- checked on open; a dead pid marks the run interrupted
    error           TEXT,
    blocked_json    TEXT,
    started_at      TEXT NOT NULL,      -- ISO-8601 UTC, as every timestamp here
    finished_at     TEXT
);
CREATE UNIQUE INDEX keyscan_one_active ON runs(status) WHERE status = 'running';

-- TENANTS --
-- The output of the whole tool: one row per (vendor, board token) ever seen,
-- with the liveness answer from its last probe. Read this table to get results
-- out of keyscan.
CREATE TABLE tenants (
    ats           TEXT NOT NULL CHECK (ats <> 'workday-list'),  -- vendor; matches rules.py `source`
    key           TEXT NOT NULL,        -- board token as the vendor spells it; workday keys lowercased
    name          TEXT,
    status        TEXT NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('unknown','live','dead')),  -- 'unknown' = never probed, or probe inconclusive
    canonical_url TEXT NOT NULL,        -- public board URL; the durable rediscovery key
    urls          TEXT NOT NULL DEFAULT '[]',  -- JSON array of source URLs the token was seen at
    method        TEXT,                 -- 'cc' | 'board:<feed_url>' -- how it was discovered
    run_id        TEXT REFERENCES runs(id) ON DELETE SET NULL,  -- the run that first recorded it
    details       TEXT NOT NULL DEFAULT '{}',  -- see JSON PAYLOADS
    discovered_at TEXT NOT NULL,
    checked_at    TEXT,                 -- last probe; NULL alongside status 'unknown'
    imported_at   TEXT,
    PRIMARY KEY (ats, key)
);
CREATE INDEX keyscan_tenants_review ON tenants(status, ats);
CREATE INDEX keyscan_tenants_run ON tenants(run_id);
CREATE UNIQUE INDEX keyscan_tenants_canonical ON tenants(ats, canonical_url);

-- Existing SQLite tables cannot gain a CHECK constraint in place. These give an
-- upgraded database the same invariant the CHECK on `ats` gives a fresh one.
CREATE TRIGGER keyscan_tenants_workday_insert
BEFORE INSERT ON tenants WHEN NEW.ats = 'workday-list'
BEGIN
    SELECT RAISE(ABORT, 'workday-list was renamed back to workday');
END;
CREATE TRIGGER keyscan_tenants_workday_update
BEFORE UPDATE OF ats ON tenants WHEN NEW.ats = 'workday-list'
BEGIN
    SELECT RAISE(ABORT, 'workday-list was renamed back to workday');
END;

-- COMMON CRAWL --
-- The last successful `collinfo.json` fetch, so the snapshot picker still has
-- something to show when Common Crawl's index is unreachable. Replaced
-- wholesale on every live fetch, never merged, so it can never mix rows from
-- two different fetches.
CREATE TABLE crawls_cache (
    id         TEXT PRIMARY KEY,        -- e.g. CC-MAIN-2026-08
    name       TEXT NOT NULL,
    from_date  TEXT NOT NULL,
    to_date    TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

-- One row per row group of Common Crawl's columnar index: the span of
-- `url_surtkey` it holds, and what reading it costs.
--
-- This is the index *of* the index. A vendor's domains become a surtkey range,
-- and a row group whose span misses that range cannot hold one of its URLs --
-- so the range alone says which files are worth opening. Without this table
-- that answer costs ~186MB of footer reads per crawl, paid again on every run;
-- with it the same answer is two string comparisons per row, ~0.5ms for a whole
-- crawl, offline.
--
-- Rows are immutable once written. A published snapshot never changes, so a
-- range recorded here stays true forever, and `harvested_at` is provenance
-- rather than an expiry.
--
-- Kept at row-group rather than file granularity because the footer read that
-- fills this table yields row groups anyway: storing the coarser thing would
-- discard `bytes` -- the only way to know a fetch's size before issuing it --
-- and buying it back later would mean re-reading every footer.
CREATE TABLE footers (
    crawl        TEXT    NOT NULL,
    -- Path as the crawl manifest gives it; the URL is CC_CDN + part, so a probe
    -- never re-fetches the manifest either.
    part         TEXT    NOT NULL,
    -- Ordinal position in the file's footer. Parquet stores no id of its own;
    -- position is stable because the file is.
    rg           INTEGER NOT NULL,
    lo           TEXT    NOT NULL,      -- lowest url_surtkey in the row group
    hi           TEXT    NOT NULL,      -- highest url_surtkey in the row group
    -- Compressed bytes of just the columns a scan reads, for this row group --
    -- not the row group, and not the file.
    bytes        INTEGER NOT NULL,
    harvested_at TEXT    NOT NULL,
    PRIMARY KEY (crawl, part, rg)
) WITHOUT ROWID;

-- One row per (vendor, crawl) already scanned. A published snapshot never
-- changes and extraction is deterministic, so the pair recorded here can only
-- reproduce tokens already in `tenants`; the row's existence is the whole fact.
--
-- Written after the vendor's scan of that crawl finishes, never before: a crawl
-- abandoned mid-way must leave no row, or the next run skips the part it never
-- read.
--
-- Keyed `(ats, crawl)` because "where has greenhouse been?" is asked more often
-- than "what is done for this crawl?".
CREATE TABLE scanned (
    ats        TEXT NOT NULL,
    crawl      TEXT NOT NULL,
    scanned_at TEXT NOT NULL,
    PRIMARY KEY (ats, crawl)
) WITHOUT ROWID;

-- JSON PAYLOADS --
-- Keys observed in the live database; every one is optional, and a writer adds
-- only what it knows. Query them with json_extract(col, '$.key').
--
-- tenants.details
--   crawl               TEXT   -- CC snapshot id the token came from     (cc discovery)
--   url_count           INT    -- distinct index URLs backing the token   (cc discovery)
--   repo                TEXT   -- GitHub board feed the row came from  (board discovery)
--   title, active              -- listing row's own fields              (board discovery)
--   verdict             TEXT   -- live | absent | inconclusive; drives tenants.status
--   reason              TEXT   -- why not live: zero_count, board_not_registered,
--                              --   exists_check_failed, invalid_json, http_<code>
--   job_count           INT    -- postings the probe counted; 0 gives reason zero_count
--   responses           ARRAY  -- [[url, status_code], ...] from the liveness probe
--   company_name        TEXT   -- promoted to tenants.name
--   company_name_source TEXT   -- metadata_json | probe_json
--   name_responses      ARRAY  -- [[url, status_code], ...] from the name lookup
--
-- runs.config_json      sources, archives, date_from, date_to, crawls, crawl_ids
-- runs.checkpoint_json  cc_done (crawls finished), board_index (feeds finished)
-- runs.counts_json      found, live, dead, unknown
-- runs.blocked_json     at, host, reason, status_code, retry_after, url
