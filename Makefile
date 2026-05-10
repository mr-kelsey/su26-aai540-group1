# Makefile — task runner for the EIA pipeline.
# Uses uv for Python env management.

.PHONY: help install install-dev install-postgres install-notebook \
        lint format typecheck test \
        pull-all pull-bls-qcew pull-bea-io pull-census-acs pull-tiger pull-hud \
        pull-ticketmaster pull-runsignup pull-setlistfm \
        warehouse-init warehouse-reset \
        phase0 validate-bea-multipliers clean

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
	uv run ruff check src tests

format:  ## Format with ruff.
	uv run ruff format src tests

typecheck:  ## Type-check with mypy.
	uv run mypy src

test:  ## Run pytest with coverage.
	uv run pytest

# ----- Warehouse -----
warehouse-init:  ## Initialize the DuckDB warehouse with schema.
	uv run eia warehouse init

warehouse-reset:  ## Drop and re-create the warehouse (destructive).
	uv run eia warehouse reset

# ----- Federal data pulls (no auth required) -----
pull-bls-qcew:  ## Pull BLS QCEW for the configured year/quarter.
	uv run eia pull bls-qcew

pull-bea-io:  ## Pull BEA Input-Output Use & Make tables.
	uv run eia pull bea-io

pull-census-acs:  ## Pull Census ACS 5-year for configured states.
	uv run eia pull census-acs

pull-tiger:  ## Pull TIGER/Line county shapefiles.
	uv run eia pull tiger

pull-hud:  ## Pull HUD ZIP-County crosswalk.
	uv run eia pull hud-crosswalk

# ----- Event-side pulls (require API keys in .env) -----
pull-ticketmaster:  ## Pull Ticketmaster Discovery events.
	uv run eia pull ticketmaster

pull-runsignup:  ## Pull RunSignUp races.
	uv run eia pull runsignup

pull-setlistfm:  ## Pull Setlist.fm setlists for sampled venues.
	uv run eia pull setlistfm

# ----- Phase 0 -----
phase0: warehouse-init pull-bls-qcew pull-bea-io pull-census-acs pull-tiger pull-hud  ## Run all Phase 0 pulls.
	uv run python pipelines/phase0_exit_query.py

# ----- Validation -----
validate-bea-multipliers:  ## Cross-check our B matrix against BEA's published CxI_DR.
	@# Workaround for macOS marking .pth files hidden under iCloud/Desktop paths,
	@# which causes site.py to skip the editable-install pth and break `import eia`.
	@chflags nohidden .venv/lib/python*/site-packages/*.pth 2>/dev/null || true
	uv run --no-sync python pipelines/validate_bea_multipliers.py

# ----- Cleanup -----
clean:  ## Remove caches.
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
