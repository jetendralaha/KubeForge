-- Rollback migration 001
DROP INDEX IF EXISTS idx_audit_created;
DROP INDEX IF EXISTS idx_audit_entity;
DROP INDEX IF EXISTS idx_deployments_project;
DROP INDEX IF EXISTS idx_packages_project;
DROP INDEX IF EXISTS idx_manifests_project;
DROP INDEX IF EXISTS idx_risks_analysis;
DROP INDEX IF EXISTS idx_analyses_artifact;
DROP INDEX IF EXISTS idx_artifacts_project;

DROP TABLE IF EXISTS audit_logs;
DROP TABLE IF EXISTS deployments;
DROP TABLE IF EXISTS packaging_jobs;
DROP TABLE IF EXISTS generated_manifests;
DROP TABLE IF EXISTS ai_risks;
DROP TABLE IF EXISTS ai_analyses;
DROP TABLE IF EXISTS artifact_images;
DROP TABLE IF EXISTS images;
DROP TABLE IF EXISTS deployment_artifacts;
DROP TABLE IF EXISTS projects;
