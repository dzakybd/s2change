# s2cd developer tasks. `make help` lists them.
COMPOSE ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo docker-compose)
LOCAL_DATABASE_URL ?= postgresql+psycopg://s2cd:s2cd@localhost:5432/s2cd

.DEFAULT_GOAL := help
.PHONY: help setup run test test-all lint fmt db-up db-down run-local compose-run query viewer report-pdf figures publish

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install dependencies (uv) from the lockfile
	uv sync --frozen

run: ## Run the pipeline; writes the results to DATABASE_URL from .env (production), skips the database if unset
	uv run s2cd run

test: ## Fast test suite (no slow integration test)
	uv run pytest -q -m "not slow"

test-all: ## Test suite including the full-pipeline integration test
	uv run pytest -q --runslow

lint: ## ruff format check + lint
	uv run ruff format --check .
	uv run ruff check .

fmt: ## Auto-format and fix lint
	uv run ruff format .
	uv run ruff check --fix .

db-up: ## Start PostGIS (waits until healthy)
	$(COMPOSE) up -d --wait db

db-down: ## Stop PostGIS and the compose stack (keeps the pgdata volume)
	$(COMPOSE) down

run-local: db-up ## Run the pipeline into the local compose PostGIS instead of production
	DATABASE_URL=$(LOCAL_DATABASE_URL) uv run s2cd run

compose-run: ## Build the image and run the pipeline fully inside docker compose
	$(COMPOSE) run --rm --build pipeline

query: ## Run sql/queries_postgis.sql in the local compose db
	$(COMPOSE) exec -T db psql -U s2cd -d s2cd -f - < sql/queries_postgis.sql

viewer: ## Build the web viewer (viewer/dist)
	cd viewer && npm ci && npm run build

report-pdf: ## Print the Report tab to docs/web/report/s2change-report.pdf (starts its own dev server)
	uv run python -m s2cd.site
	uv run --with playwright --with pymupdf python scripts/report_pdf.py

FIG_PY = uv run --with playwright python
FIG_SCRIPTS = $(filter-out scripts/fig_common.py,$(wildcard scripts/fig_*.py))

figures: ## Render every report figure (icons, step frames, at-a-glance, scripts/fig_*.py) from the real outputs
	$(FIG_PY) scripts/fig_common.py
	$(FIG_PY) scripts/step_frames.py
	$(FIG_PY) scripts/at_a_glance.py
	@for f in $(FIG_SCRIPTS); do echo "$$f"; $(FIG_PY) $$f || exit 1; done

publish: ## Regenerate every static output (web export, figures, report PDF), then lint, test and build the viewer
	uv run python -m s2cd.site
	$(MAKE) figures
	uv run python -m s2cd.site
	uv run --with playwright --with pymupdf python scripts/report_pdf.py
	$(MAKE) lint
	uv run pytest -q
	cd viewer && npm run build
