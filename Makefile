# Makefile — task runner for the EIA pipeline.
# Uses uv for Python env management.

.PHONY: help install install-dev install-postgres install-notebook \
        lint format typecheck test \
        pull-all pull-bls-qcew pull-bea-io pull-census-acs pull-tiger pull-hud \
        pull-ticketmaster pull-runsignup pull-setlistfm pull-cdtfa \
        pull-census-state-tax \
        warehouse-init warehouse-reset \
        phase0 validate-bea-multipliers enrich-events build-events \
        phase1-summary warehouse-health clean

# UVRUN: invocation prefix for all python/eia commands.
# - chflags nohidden: macOS+iCloud quirk under Desktop paths marks editable-install
#   .pth files hidden, which makes site.py skip them and break `import eia`.
# - --no-sync: avoid the uv resync that would re-hide them.
UVRUN = @chflags nohidden .venv/lib/python*/site-packages/*.pth 2>/dev/null || true; uv run --no-sync

help:  ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ----- Environment -----
install:  ## Install runtime dependencies.
	uv sync

install-dev:  ## Install dev dependencies.
	uv sync --extra dev

install-postgres:  ## Install Postgres extras.
	uv sync --extra postgres

install-notebook:  ## Install notebook extras.
	uv sync --extra notebook

# ----- Quality -----
lint:  ## Lint with ruff.
	$(UVRUN) ruff check src tests

format:  ## Format with ruff.
	$(UVRUN) ruff format src tests

typecheck:  ## Type-check with mypy.
	$(UVRUN) mypy src

test:  ## Run pytest with coverage.
	$(UVRUN) pytest

# ----- Warehouse -----
warehouse-init:  ## Initialize the DuckDB warehouse with schema.
	$(UVRUN) eia warehouse init

warehouse-reset:  ## Drop and re-create the warehouse (destructive).
	$(UVRUN) eia warehouse reset

# ----- Federal data pulls (no auth required) -----
pull-bls-qcew:  ## Pull BLS QCEW for the configured year/quarter.
	$(UVRUN) eia pull bls-qcew

pull-bea-io:  ## Pull BEA Input-Output Use & Make tables.
	$(UVRUN) eia pull bea-io

pull-census-acs:  ## Pull Census ACS 5-year for configured states.
	$(UVRUN) eia pull census-acs

pull-tiger:  ## Pull TIGER/Line county shapefiles.
	$(UVRUN) eia pull tiger

pull-hud:  ## Pull HUD ZIP-County crosswalk.
	$(UVRUN) eia pull hud-crosswalk

pull-cdtfa:  ## Pull California CDTFA quarterly taxable sales by county/business type.
	$(UVRUN) eia pull cdtfa-taxable-sales

pull-census-state-tax:  ## Pull Census STC: annual state tax collections all 50 + DC.
	$(UVRUN) eia pull census-state-tax

# ----- Event-side pulls (require API keys in .env) -----
pull-ticketmaster:  ## Pull Ticketmaster Discovery events.
	$(UVRUN) eia pull ticketmaster

pull-runsignup:  ## Pull RunSignUp races.
	$(UVRUN) eia pull runsignup

pull-setlistfm:  ## Pull Setlist.fm setlists for the configured partitions.
	$(UVRUN) eia pull setlistfm

# ----- Phase 0 -----
phase0: warehouse-init pull-bls-qcew pull-bea-io pull-census-acs pull-tiger pull-hud  ## Run all Phase 0 pulls.
	$(UVRUN) python pipelines/phase0_exit_query.py

# ----- Validation -----
validate-bea-multipliers:  ## Cross-check our B matrix against BEA's published CxI_DR.
	$(UVRUN) python pipelines/validate_bea_multipliers.py

# ----- Events enrichment -----
enrich-events:  ## Re-enrich the existing events table in place.
	$(UVRUN) python pipelines/enrich_events.py

build-events:  ## UNION all event-source staging tables -> events (canonical pipeline).
	$(UVRUN) python pipelines/build_events.py

phase1-summary:  ## Cross-source summary: events x dim_county x ACS x QCEW.
	$(UVRUN) python pipelines/phase1_summary.py

warehouse-health:  ## List all warehouse tables with row counts + freshness.
	$(UVRUN) python pipelines/warehouse_health.py

# ----- Cleanup -----
clean:  ## Remove caches.
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
