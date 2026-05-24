# KubeForge — AI-Powered K3s Deployment Platform

.PHONY: all install dev run test lint fmt clean docker-build

# ── Install ─────────────────────────────────────────────────

install:
	@echo "==> Installing KubeForge..."
	pip install -e .

dev:
	@echo "==> Installing KubeForge (dev)..."
	pip install -e ".[dev]"

# ── Run ─────────────────────────────────────────────────────

run:
	@echo "==> Starting KubeForge server..."
	kubeforge serve

run-reload:
	@echo "==> Starting KubeForge server (auto-reload)..."
	kubeforge serve --reload

# ── Development Services ────────────────────────────────────

services-up:
	@echo "==> Starting Ollama + Qdrant..."
	docker compose -f docker-compose.dev.yml up -d
	@echo "==> Ollama:  http://localhost:11434"
	@echo "==> Qdrant:  http://localhost:6333"

services-down:
	docker compose -f docker-compose.dev.yml down

# ── Code Quality ────────────────────────────────────────────

test:
	@echo "==> Running tests..."
	pytest --cov=kubeforge --cov-report=term-missing

test-verbose:
	pytest -v --cov=kubeforge

lint:
	@echo "==> Linting..."
	ruff check src/ tests/

fmt:
	@echo "==> Formatting..."
	ruff format src/ tests/
	ruff check --fix src/ tests/

typecheck:
	@echo "==> Type checking..."
	mypy src/kubeforge/

# ── Docker ──────────────────────────────────────────────────

docker-build:
	docker build -t kubeforge:latest .

# ── Clean ───────────────────────────────────────────────────

clean:
	rm -rf dist/ build/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -f coverage.xml .coverage
