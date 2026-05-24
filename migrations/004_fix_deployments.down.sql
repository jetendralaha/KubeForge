-- Revert to original schema
DROP TABLE IF EXISTS deployments;

CREATE TABLE IF NOT EXISTS deployments (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    namespace         TEXT NOT NULL DEFAULT 'default',
    strategy          TEXT NOT NULL DEFAULT 'k3s_direct',
    status            TEXT NOT NULL DEFAULT 'pending',
    manifest_snapshot TEXT NOT NULL DEFAULT '',
    deployed_at       DATETIME,
    created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_deployments_project ON deployments(project_id);
