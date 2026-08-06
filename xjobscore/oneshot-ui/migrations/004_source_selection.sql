-- User-owned source enablement. Catalog `feeds.enabled` remains the upstream
-- availability flag; absence from this table deliberately means disabled in
-- OneShot so new catalog feeds never become active without an explicit choice.
CREATE TABLE IF NOT EXISTS source_selections (
    feed_id INTEGER PRIMARY KEY REFERENCES feeds(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS source_selections_enabled_idx
    ON source_selections(enabled, feed_id);
