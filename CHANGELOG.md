# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [Unreleased]

### Added
- Initial open-source documentation set (`README.md`, `CONTRIBUTING.md`, `docs/ARCHITECTURE.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`)

### Changed
- Improved project positioning and onboarding content for open-source release
- Updated architecture documentation to reflect currently implemented parser support

## [0.1.0] - 2026-05-24

### Added
- FastAPI application and REST API surface under `/api/v1`
- Typer CLI (`kubeforge`, `kfctl`)
- Artifact parsing for Docker Compose, Kubernetes manifests, and Helm chart archives
- Heuristic + LLM-assisted artifact detection and risk analysis
- Hardened manifest generation for K3s targets
- Offline packaging workflow and ISO build path
- SQLite-backed persistence with migration scripts
- Initial test suite
