-- KubeForge database schema v001
-- Supports both SQLite and PostgreSQL with minor dialect differences

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'created',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS deployment_artifacts (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    content         TEXT NOT NULL,
    artifact_type   TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'uploaded',
    parsed_json     TEXT NOT NULL DEFAULT '',
    values_content  TEXT NOT NULL DEFAULT '',
    namespace       TEXT NOT NULL DEFAULT 'default',
    chart_role      TEXT NOT NULL DEFAULT 'app',
    deploy_order    INTEGER NOT NULL DEFAULT 0,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS images (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    tag             TEXT NOT NULL DEFAULT '',
    digest          TEXT NOT NULL DEFAULT '',
    registry        TEXT NOT NULL DEFAULT 'docker.io',
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    platform        TEXT NOT NULL DEFAULT 'linux/amd64',
    is_cached       BOOLEAN NOT NULL DEFAULT FALSE,
    cached_path     TEXT NOT NULL DEFAULT '',
    resolved_at     DATETIME
);

CREATE TABLE IF NOT EXISTS artifact_images (
    artifact_id TEXT NOT NULL REFERENCES deployment_artifacts(id) ON DELETE CASCADE,
    image_id    TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    PRIMARY KEY (artifact_id, image_id)
);

CREATE TABLE IF NOT EXISTS ai_analyses (
    id              TEXT PRIMARY KEY,
    artifact_id     TEXT NOT NULL REFERENCES deployment_artifacts(id) ON DELETE CASCADE,
    analysis_type   TEXT NOT NULL,
    result_json     TEXT NOT NULL,
    model_used      TEXT NOT NULL DEFAULT '',
    confidence      REAL NOT NULL DEFAULT 0,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_risks (
    id              TEXT PRIMARY KEY,
    analysis_id     TEXT NOT NULL REFERENCES ai_analyses(id) ON DELETE CASCADE,
    severity        TEXT NOT NULL,
    category        TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    remediation     TEXT NOT NULL DEFAULT '',
    resource_ref    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS generated_manifests (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    name            TEXT NOT NULL,
    namespace       TEXT NOT NULL DEFAULT 'default',
    content_yaml    TEXT NOT NULL,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS packaging_jobs (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    format          TEXT NOT NULL DEFAULT 'tar',
    status          TEXT NOT NULL DEFAULT 'queued',
    progress        INTEGER NOT NULL DEFAULT 0,
    output_path     TEXT NOT NULL DEFAULT '',
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    include_k3s     BOOLEAN NOT NULL DEFAULT TRUE,
    error_message   TEXT NOT NULL DEFAULT '',
    started_at      DATETIME,
    completed_at    DATETIME,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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

CREATE TABLE IF NOT EXISTS audit_logs (
    id          TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    action      TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'system',
    details     TEXT NOT NULL DEFAULT '',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_artifacts_project ON deployment_artifacts(project_id);
CREATE INDEX IF NOT EXISTS idx_analyses_artifact ON ai_analyses(artifact_id);
CREATE INDEX IF NOT EXISTS idx_risks_analysis ON ai_risks(analysis_id);
CREATE INDEX IF NOT EXISTS idx_manifests_project ON generated_manifests(project_id);
CREATE INDEX IF NOT EXISTS idx_packages_project ON packaging_jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_deployments_project ON deployments(project_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
