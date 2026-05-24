-- KubeForge migration 002 — Helm chart support columns
-- Adds Helm-specific columns to deployment_artifacts.
-- Safe to run on any schema (error tolerance handles duplicates).

ALTER TABLE deployment_artifacts ADD COLUMN values_content TEXT NOT NULL DEFAULT '';
ALTER TABLE deployment_artifacts ADD COLUMN namespace TEXT NOT NULL DEFAULT 'default';
ALTER TABLE deployment_artifacts ADD COLUMN chart_role TEXT NOT NULL DEFAULT 'app';
ALTER TABLE deployment_artifacts ADD COLUMN deploy_order INTEGER NOT NULL DEFAULT 0
