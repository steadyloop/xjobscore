-- Product-facing read model. Raw manual fetches remain in the 001 tables;
-- these tables preserve canonical observations, reversible entity resolution,
-- explainable classifications, and application history.

CREATE TABLE IF NOT EXISTS companies (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    domain TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS companies_normalized_idx ON companies(normalized_name);

CREATE TABLE IF NOT EXISTS company_facts (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    value_json TEXT NOT NULL,
    provenance TEXT NOT NULL CHECK (provenance IN ('source', 'model', 'human')),
    expires_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(company_id, name, provenance)
);

CREATE TABLE IF NOT EXISTS job_entities (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id),
    company_name TEXT NOT NULL,
    title TEXT NOT NULL,
    title_normalized TEXT NOT NULL,
    opportunity_type TEXT NOT NULL DEFAULT 'unclassified'
        CHECK (opportunity_type IN (
            'spring_internship', 'spring_coop', 'student_researcher',
            'summer_internship', 'fall_coop', 'fall_internship',
            'unclassified', 'review'
        )),
    opportunity_confidence REAL NOT NULL DEFAULT 0 CHECK (opportunity_confidence BETWEEN 0 AND 1),
    opportunity_evidence_json TEXT NOT NULL DEFAULT '[]',
    location TEXT,
    workplace_type TEXT,
    employment_type TEXT,
    description_html TEXT,
    posting_url TEXT,
    apply_url TEXT,
    published_at TEXT,
    deadline_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'unlisted', 'closed', 'conflicting', 'merged')),
    eligibility TEXT NOT NULL DEFAULT 'review'
        CHECK (eligibility IN ('eligible', 'review', 'ineligible')),
    eligibility_reasons_json TEXT NOT NULL DEFAULT '[]',
    stack_json TEXT NOT NULL DEFAULT '[]',
    score INTEGER NOT NULL DEFAULT 0 CHECK (score BETWEEN 0 AND 100),
    score_band TEXT NOT NULL DEFAULT 'log_only'
        CHECK (score_band IN ('one_shot', 'tailored_fast', 'volume', 'log_only')),
    score_components_json TEXT NOT NULL DEFAULT '[]',
    resume_variant TEXT NOT NULL DEFAULT 'backend-infra',
    source_count INTEGER NOT NULL DEFAULT 1,
    authoritative_open INTEGER NOT NULL DEFAULT 1 CHECK (authoritative_open IN (0, 1)),
    triage_state TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK (triage_state IN ('unreviewed', 'saved', 'queued', 'dismissed')),
    merged_into_id TEXT REFERENCES job_entities(id),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS job_entities_board_idx
    ON job_entities(status, triage_state, opportunity_type, score DESC, first_seen_at DESC);
CREATE INDEX IF NOT EXISTS job_entities_company_title_idx
    ON job_entities(company_id, title_normalized);

CREATE TABLE IF NOT EXISTS posting_observations (
    id TEXT PRIMARY KEY,
    feed_id INTEGER REFERENCES feeds(id) ON DELETE SET NULL,
    source TEXT NOT NULL,
    feed_key TEXT NOT NULL,
    external_id TEXT NOT NULL,
    job_entity_id TEXT NOT NULL REFERENCES job_entities(id),
    company_raw TEXT,
    title TEXT NOT NULL,
    location TEXT,
    employment_type TEXT,
    workplace_type TEXT,
    description_html TEXT,
    posting_url TEXT NOT NULL,
    apply_url TEXT,
    requisition_id TEXT,
    published_at TEXT,
    deadline_at TEXT,
    source_active INTEGER CHECK (source_active IN (0, 1)),
    authority TEXT NOT NULL CHECK (authority IN ('origin', 'secondhand', 'manual')),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'unlisted', 'closed', 'conflicting')),
    absence_count INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL,
    revision_no INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_poll_id INTEGER REFERENCES polls(id) ON DELETE SET NULL,
    source_extra_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source, feed_key, external_id)
);
CREATE INDEX IF NOT EXISTS posting_observations_entity_idx ON posting_observations(job_entity_id);
CREATE INDEX IF NOT EXISTS posting_observations_feed_seen_idx
    ON posting_observations(feed_id, last_poll_id, status);

CREATE TABLE IF NOT EXISTS posting_revisions (
    id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES posting_observations(id) ON DELETE CASCADE,
    revision_no INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(observation_id, revision_no),
    UNIQUE(observation_id, content_hash)
);

CREATE TABLE IF NOT EXISTS posting_identifiers (
    observation_id TEXT NOT NULL REFERENCES posting_observations(id) ON DELETE CASCADE,
    namespace TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL,
    provenance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(observation_id, namespace, scope, value)
);
CREATE INDEX IF NOT EXISTS posting_identifiers_lookup_idx
    ON posting_identifiers(namespace, scope, value);

CREATE TABLE IF NOT EXISTS link_evidence (
    id TEXT PRIMARY KEY,
    observation_a_id TEXT NOT NULL REFERENCES posting_observations(id) ON DELETE CASCADE,
    observation_b_id TEXT NOT NULL REFERENCES posting_observations(id) ON DELETE CASCADE,
    method TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    details_json TEXT NOT NULL DEFAULT '{}',
    algorithm_version TEXT NOT NULL,
    review_state TEXT NOT NULL DEFAULT 'automatic'
        CHECK (review_state IN ('automatic', 'pending', 'accepted', 'rejected')),
    created_at TEXT NOT NULL,
    UNIQUE(observation_a_id, observation_b_id, method)
);

CREATE TABLE IF NOT EXISTS dedupe_candidates (
    id TEXT PRIMARY KEY,
    entity_a_id TEXT NOT NULL REFERENCES job_entities(id),
    entity_b_id TEXT NOT NULL REFERENCES job_entities(id),
    outcome TEXT NOT NULL CHECK (outcome IN ('exact', 'review', 'new', 'related')),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    reasons_json TEXT NOT NULL DEFAULT '[]',
    differences_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'merged', 'kept_separate', 'deferred', 'related', 'reversed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(entity_a_id, entity_b_id)
);

CREATE TABLE IF NOT EXISTS dedupe_decisions (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES dedupe_candidates(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('merge', 'keep_separate', 'defer', 'reverse')),
    rationale TEXT,
    actor TEXT NOT NULL DEFAULT 'human',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS merge_operations (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES dedupe_candidates(id),
    survivor_id TEXT NOT NULL REFERENCES job_entities(id),
    absorbed_id TEXT NOT NULL REFERENCES job_entities(id),
    moved_observation_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reversed_at TEXT
);

CREATE TABLE IF NOT EXISTS job_requirements (
    id TEXT PRIMARY KEY,
    job_entity_id TEXT NOT NULL REFERENCES job_entities(id) ON DELETE CASCADE,
    technology TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('required', 'preferred', 'mentioned')),
    evidence TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(job_entity_id, technology)
);

CREATE TABLE IF NOT EXISTS classification_snapshots (
    id TEXT PRIMARY KEY,
    job_entity_id TEXT NOT NULL REFERENCES job_entities(id) ON DELETE CASCADE,
    opportunity_type TEXT NOT NULL,
    eligibility TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS score_snapshots (
    id TEXT PRIMARY KEY,
    job_entity_id TEXT NOT NULL REFERENCES job_entities(id) ON DELETE CASCADE,
    score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
    band TEXT NOT NULL,
    components_json TEXT NOT NULL,
    resume_variant TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_applications (
    id TEXT PRIMARY KEY,
    job_entity_id TEXT NOT NULL REFERENCES job_entities(id),
    source_observation_id TEXT REFERENCES posting_observations(id) ON DELETE SET NULL,
    stage TEXT NOT NULL CHECK (stage IN (
        'considering', 'preparing', 'submitted', 'assessment', 'screen',
        'interview', 'offer', 'rejected', 'withdrawn', 'no_reply'
    )),
    resume_variant TEXT NOT NULL,
    apply_url_snapshot TEXT,
    company_snapshot TEXT NOT NULL,
    title_snapshot TEXT NOT NULL,
    description_snapshot TEXT,
    submitted_at TEXT,
    next_action TEXT,
    next_due_at TEXT,
    note TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS job_applications_stage_due_idx
    ON job_applications(stage, next_due_at, updated_at DESC);

CREATE TABLE IF NOT EXISTS application_events (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES job_applications(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    from_stage TEXT,
    to_stage TEXT,
    occurred_at TEXT NOT NULL,
    due_at TEXT,
    note TEXT,
    data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS application_events_timeline_idx
    ON application_events(application_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS lifecycle_events (
    id TEXT PRIMARY KEY,
    observation_id TEXT REFERENCES posting_observations(id) ON DELETE SET NULL,
    job_entity_id TEXT NOT NULL REFERENCES job_entities(id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT NOT NULL,
    poll_id INTEGER REFERENCES polls(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

