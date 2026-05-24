-- Revert to original schema
DROP TABLE IF EXISTS generated_manifests;

CREATE TABLE IF NOT EXISTS generated_manifests (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    name            TEXT NOT NULL,
    namespace       TEXT NOT NULL DEFAULT 'default',
    content_yaml    TEXT NOT NULL,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_manifests_project ON generated_manifests(project_id);
