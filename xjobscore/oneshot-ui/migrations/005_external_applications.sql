-- Application tracker entries may come from Discover or be entered directly.
-- External entries deliberately have no job entity, so they never enter the
-- discovery, scoring, lifecycle, or dedupe models.

CREATE TABLE job_applications_new (
    id TEXT PRIMARY KEY,
    job_entity_id TEXT REFERENCES job_entities(id),
    source_observation_id TEXT REFERENCES posting_observations(id) ON DELETE SET NULL,
    external INTEGER NOT NULL DEFAULT 0 CHECK (external IN (0, 1)),
    stage TEXT NOT NULL CHECK (stage IN (
        'considering', 'preparing', 'submitted', 'assessment', 'screen',
        'interview', 'offer', 'rejected', 'withdrawn', 'no_reply'
    )),
    resume_variant TEXT NOT NULL,
    apply_url_snapshot TEXT,
    company_snapshot TEXT NOT NULL,
    title_snapshot TEXT NOT NULL,
    description_snapshot TEXT,
    location_snapshot TEXT NOT NULL DEFAULT '',
    submitted_at TEXT,
    next_action TEXT,
    next_due_at TEXT,
    note TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (external = 0 AND job_entity_id IS NOT NULL)
        OR (external = 1 AND job_entity_id IS NULL)
    )
);

INSERT INTO job_applications_new(
    id, job_entity_id, source_observation_id, external, stage, resume_variant,
    apply_url_snapshot, company_snapshot, title_snapshot, description_snapshot,
    location_snapshot, submitted_at, next_action, next_due_at, note, version,
    created_at, updated_at
)
SELECT
    a.id, a.job_entity_id, a.source_observation_id, 0, a.stage, a.resume_variant,
    a.apply_url_snapshot, a.company_snapshot, a.title_snapshot,
    a.description_snapshot, COALESCE(j.location, ''), a.submitted_at, a.next_action,
    a.next_due_at, a.note, a.version, a.created_at, a.updated_at
FROM job_applications a
JOIN job_entities j ON j.id = a.job_entity_id;

CREATE TABLE application_events_new (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES job_applications_new(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    from_stage TEXT,
    to_stage TEXT,
    occurred_at TEXT NOT NULL,
    due_at TEXT,
    note TEXT,
    data_json TEXT NOT NULL DEFAULT '{}'
);

INSERT INTO application_events_new(
    id, application_id, event_type, from_stage, to_stage,
    occurred_at, due_at, note, data_json
)
SELECT
    id, application_id, event_type, from_stage, to_stage,
    occurred_at, due_at, note, data_json
FROM application_events;

DROP TABLE application_events;
DROP TABLE job_applications;
ALTER TABLE job_applications_new RENAME TO job_applications;
ALTER TABLE application_events_new RENAME TO application_events;

CREATE INDEX job_applications_stage_due_idx
    ON job_applications(stage, next_due_at, updated_at DESC);
CREATE INDEX application_events_timeline_idx
    ON application_events(application_id, occurred_at DESC);
