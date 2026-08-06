-- OneShot's local, durable data model.  Timestamps are UTC ISO-8601 text.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    feed_key TEXT NOT NULL,
    company_id TEXT,
    company_name TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    state_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    complete INTEGER NOT NULL DEFAULT 0 CHECK (complete IN (0, 1)),
    closes INTEGER NOT NULL DEFAULT 0 CHECK (closes IN (0, 1)),
    credentialed INTEGER NOT NULL DEFAULT 0 CHECK (credentialed IN (0, 1)),
    parser_state TEXT NOT NULL DEFAULT 'unknown'
        CHECK (parser_state IN ('unknown', 'available', 'missing', 'error')),
    health TEXT NOT NULL DEFAULT 'never_fetched'
        CHECK (health IN ('never_fetched', 'healthy', 'partial', 'not_modified', 'error', 'missing_parser')),
    last_poll_id INTEGER,
    last_outcome TEXT,
    last_error TEXT,
    last_http_status INTEGER,
    last_fetched_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source, feed_key),
    FOREIGN KEY (last_poll_id) REFERENCES polls(id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    trigger TEXT NOT NULL DEFAULT 'manual' CHECK (trigger = 'manual'),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT NOT NULL DEFAULT 'running'
        CHECK (outcome IN ('running', 'ok', 'not_modified', 'partial', 'error')),
    http_status INTEGER,
    item_count INTEGER NOT NULL DEFAULT 0,
    parser_state TEXT NOT NULL DEFAULT 'unknown'
        CHECK (parser_state IN ('unknown', 'available', 'missing', 'error')),
    error TEXT,
    validators_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS polls_feed_started_idx ON polls(feed_id, started_at DESC);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('json', 'xml', 'html', 'mime')),
    payload_json TEXT,
    payload_text TEXT,
    envelope_json TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT NOT NULL,
    CHECK ((payload_json IS NULL) != (payload_text IS NULL)),
    UNIQUE (poll_id, external_id)
);
CREATE INDEX IF NOT EXISTS observations_feed_external_idx
    ON observations(feed_id, external_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    canonical_name TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    attributes_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entities_kind_idx ON entities(kind);

CREATE TABLE IF NOT EXISTS identifiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    namespace TEXT NOT NULL,
    value TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE (namespace, value)
);
CREATE INDEX IF NOT EXISTS identifiers_entity_idx ON identifiers(entity_id);

CREATE TABLE IF NOT EXISTS revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    observation_id INTEGER REFERENCES observations(id) ON DELETE SET NULL,
    revision_no INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    document_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (entity_id, revision_no),
    UNIQUE (entity_id, content_hash)
);

CREATE TABLE IF NOT EXISTS memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    child_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relationship TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (parent_entity_id, child_entity_id, relationship, valid_from)
);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    revision_id INTEGER REFERENCES revisions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    value_json TEXT NOT NULL,
    provenance TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    source_span_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_entity_kind_idx ON evidence(entity_id, kind);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL UNIQUE REFERENCES entities(id) ON DELETE CASCADE,
    stage TEXT NOT NULL DEFAULT 'new',
    status TEXT NOT NULL DEFAULT 'active',
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    decision TEXT NOT NULL,
    reason TEXT,
    actor TEXT NOT NULL DEFAULT 'human',
    decided_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_candidate_idx ON decisions(candidate_id, decided_at DESC);

CREATE TABLE IF NOT EXISTS requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER REFERENCES profile(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    operator TEXT NOT NULL,
    value_json TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    required INTEGER NOT NULL DEFAULT 0 CHECK (required IN (0, 1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    value_json TEXT NOT NULL,
    provenance TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    created_at TEXT NOT NULL,
    UNIQUE (candidate_id, label, provenance)
);

CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    total REAL NOT NULL,
    components_json TEXT NOT NULL DEFAULT '{}',
    scored_at TEXT NOT NULL,
    UNIQUE (candidate_id, model, scored_at)
);
CREATE INDEX IF NOT EXISTS scores_candidate_idx ON scores(candidate_id, scored_at DESC);

CREATE TABLE IF NOT EXISTS triage (
    candidate_id INTEGER PRIMARY KEY REFERENCES candidates(id) ON DELETE CASCADE,
    bucket TEXT NOT NULL,
    rank INTEGER,
    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    note TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    applied_at TEXT,
    external_url TEXT,
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS applications_candidate_idx ON applications(candidate_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_aggregate_idx
    ON events(aggregate_type, aggregate_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    document_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS profile_one_active_idx ON profile(active) WHERE active = 1;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Read compatibility for callers that use the singular domain name.
CREATE VIEW IF NOT EXISTS source_feed AS SELECT * FROM feeds;
