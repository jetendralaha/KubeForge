-- Rollback migration 002 — Helm chart support
-- SQLite does not support DROP COLUMN directly in older versions.
-- For SQLite 3.35.0+ (2021-03-12):
ALTER TABLE deployment_artifacts DROP COLUMN values_content;
ALTER TABLE deployment_artifacts DROP COLUMN namespace;
ALTER TABLE deployment_artifacts DROP COLUMN chart_role;
ALTER TABLE deployment_artifacts DROP COLUMN deploy_order;
