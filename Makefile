# s2cd developer tasks. `make help` lists them.
COMPOSE ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo docker-compose)
DATABASE_URL ?= postgresql+psycopg://s2cd:s2cd@localhost:5432/s2cd
SQLITE ?= data/change_detection.sqlite

.DEFAULT_GOAL := help
.PHONY: help setup run test test-all lint fmt db-up db-down run-postgis compose-run query query-postgis viewer report-pdf figures publish

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install dependencies (uv) from the lockfile
	uv sync --frozen

run: ## Run the pipeline into SpatiaLite (data/change_detection.sqlite)
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

run-postgis: db-up ## Run the pipeline locally (uv) and write to the compose PostGIS
	DATABASE_URL=$(DATABASE_URL) uv run s2cd run --postgis

compose-run: ## Build the image and run the pipeline fully inside docker compose
	$(COMPOSE) run --rm --build pipeline

define QUERY_PY
import sys, pyogrio
text = open("sql/queries_spatialite.sql").read()
body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))
for i, q in enumerate((s.strip() for s in body.split(";\n")), 1):
    if q.rstrip(";"):
        print(f"-- query {i}")
        print(pyogrio.read_dataframe(sys.argv[1], sql=q.rstrip(";"), sql_dialect="SQLITE").to_string(max_colwidth=70))
endef
export QUERY_PY

query: ## Run sql/queries_spatialite.sql against $(SQLITE)
	uv run python -c "$$QUERY_PY" $(SQLITE)

query-postgis: ## Run sql/queries_postgis.sql in the compose db
	$(COMPOSE) exec -T db psql -U s2cd -d s2cd -f - < sql/queries_postgis.sql

viewer: ## Build the web viewer (viewer/dist)
	cd viewer && npm ci && npm run build

report-pdf: ## Print the Report tab to docs/web/report/s2change-report.pdf (starts its own dev server)
	uv run python -m s2cd.site
	uv run --with playwright --with pymupdf python scripts/report_pdf.py

FIG_PY = uv run --with playwright python
FIG_SCRIPTS = $(filter-out scripts/fig_common.py,$(wildcard scripts/fig_*.py))

figures: ## Render every report figure (icons, step frames, flow, at-a-glance, scripts/fig_*.py) from the real outputs
	$(FIG_PY) scripts/fig_common.py
	$(FIG_PY) scripts/step_frames.py
	$(FIG_PY) scripts/flow_figure.py
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
