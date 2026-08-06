-- Integration hardening: manual snapshot membership, versioned decisions,
-- explicit routing, and enough merge state to make reversal lossless.

ALTER TABLE posting_observations ADD COLUMN absent_since TEXT;
ALTER TABLE posting_observations ADD COLUMN explicit_closed INTEGER NOT NULL DEFAULT 0
    CHECK (explicit_closed IN (0, 1));
ALTER TABLE posting_observations ADD COLUMN closed_evidence TEXT;
ALTER TABLE feeds ADD COLUMN snapshot_state TEXT NOT NULL DEFAULT 'none'
    CHECK (snapshot_state IN ('none', 'baseline', 'applied', 'quarantined', 'ineligible'));
ALTER TABLE feeds ADD COLUMN snapshot_reason TEXT;

ALTER TABLE lifecycle_events ADD COLUMN algorithm_version TEXT NOT NULL
    DEFAULT 'posting-lifecycle/1.0';
ALTER TABLE lifecycle_events ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE dedupe_candidates ADD COLUMN algorithm_version TEXT NOT NULL
    DEFAULT 'posting-resolution/1.0';
ALTER TABLE dedupe_candidates ADD COLUMN shared_identifiers_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE merge_operations ADD COLUMN moved_application_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE merge_operations ADD COLUMN before_state_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE classification_snapshots ADD COLUMN versions_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE score_snapshots ADD COLUMN route_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE job_entities ADD COLUMN route_instruction TEXT NOT NULL
    DEFAULT 'Log for discovery; do not apply or spend a slot.';
ALTER TABLE job_entities ADD COLUMN submit_within_hours INTEGER;
ALTER TABLE job_entities ADD COLUMN auto_submit INTEGER NOT NULL DEFAULT 0
    CHECK (auto_submit = 0);
ALTER TABLE job_entities ADD COLUMN manual_opportunity_type TEXT
    CHECK (manual_opportunity_type IN (
        'spring_internship', 'spring_coop', 'student_researcher',
        'summer_internship', 'fall_coop', 'fall_internship'
    ));
ALTER TABLE job_entities ADD COLUMN manual_classification_evidence TEXT;

CREATE TABLE IF NOT EXISTS poll_memberships (
    poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    observation_id TEXT NOT NULL REFERENCES posting_observations(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    PRIMARY KEY (poll_id, observation_id)
);
CREATE INDEX IF NOT EXISTS poll_memberships_external_idx
    ON poll_memberships(poll_id, external_id);

CREATE TABLE IF NOT EXISTS poll_snapshots (
    poll_id INTEGER PRIMARY KEY REFERENCES polls(id) ON DELETE CASCADE,
    feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('baseline', 'applied', 'quarantined', 'ineligible')),
    member_count INTEGER NOT NULL,
    previous_poll_id INTEGER REFERENCES poll_snapshots(poll_id) ON DELETE SET NULL,
    reason TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS poll_snapshots_feed_idx
    ON poll_snapshots(feed_id, poll_id DESC);
