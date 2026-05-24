-- KubeForge migration 004 — Fix deployments table schema
-- Aligns with current code expectations (package_id, target, log, updated_at).

DROP TABLE IF EXISTS deployments;

CREATE TABLE IF NOT EXISTS deployments (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    package_id      TEXT NOT NULL DEFAULT '',
    target          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    log             TEXT NOT NULL DEFAULT '',
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_deployments_project ON deployments(project_id);
