-- KubeForge migration 003 — Fix generated_manifests schema
-- Aligns the table with the current code expectations.

-- Drop and recreate since ALTER TABLE in SQLite cannot rename/drop columns reliably.
DROP TABLE IF EXISTS generated_manifests;

CREATE TABLE IF NOT EXISTS generated_manifests (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    artifact_id     TEXT NOT NULL DEFAULT '',
    filename        TEXT NOT NULL,
    content         TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT '',
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_manifests_project ON generated_manifests(project_id);
CREATE INDEX IF NOT EXISTS idx_manifests_artifact ON generated_manifests(artifact_id);
