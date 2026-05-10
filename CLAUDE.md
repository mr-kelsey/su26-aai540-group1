# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

ML-based Economic Impact Assessment pipeline (USD AAI-540 Group 1). Aggregates federal economic data (BEA I-O, BLS QCEW, Census ACS, TIGER, HUD) and event-side sources (Ticketmaster, RunSignUp, Setlist.fm) into a county-FIPS-keyed warehouse to feed a Bayesian impact model. Python 3.11+, `uv`-managed.

## Commands

All tasks run via the Makefile, which shells out to `uv run`. Run `make help` for the full list.

```bash
make install-dev        # uv sync --extra dev
make lint               # ruff check src tests
make format             # ruff format src tests
make typecheck          # mypy src (strict mode)
make test               # pytest with coverage on src/eia
make warehouse-init     # apply SQL migrations to the configured warehouse
make warehouse-reset    # DESTRUCTIVE: drop & re-create
make phase0             # full Phase 0 lifecycle: warehouse-init → all federal pulls → exit query
make pull-<source>      # pull one source (bls-qcew, bea-io, census-acs, tiger, hud, ticketmaster, runsignup, setlistfm)
```

Direct CLI (the `eia` script is registered via `pyproject.toml`):

```bash
uv run eia warehouse init|reset|info
uv run eia pull <source-name>
```

Run a single test:

```bash
uv run pytest tests/test_warehouse.py::test_register_table_from_parquet -v
```

Skip coverage (the default `addopts` in `pyproject.toml` enables `--cov`):

```bash
uv run pytest --no-cov tests/test_warehouse.py
```

## Architecture

### Three-zone data flow

`data/raw/<source>/` (verbatim, immutable) → `data/cleaned/<source>/*.parquet` (FIPS-keyed, schema-conformed) → warehouse tables. All federal sources are joinable on `county_fips` (CHAR(5)) and `period_id` (e.g. `'2023Q3'`). Event rows carry both `venue_lat/lon` AND a derived `county_fips` so they join the same way.

### Source plugin contract

Every external data source is a subclass of `eia.sources.base.Source` (an ABC) with three lifecycle methods:

```
fetch() → Path        # download/API-pull, write verbatim under self.raw_dir
to_cleaned(raw) → Path # transform raw → cleaned Parquet with conformed schema
load(cleaned, wh)     # default impl replaces target_table from the Parquet
```

Each module **must call `register(name, cls)` at import time** (see [bls_qcew.py](src/eia/sources/bls_qcew.py) for the canonical example). The CLI's `_register_pull_commands` in [cli.py](src/eia/cli.py) imports each source module to trigger registration, then dynamically generates a `eia pull <name>` Typer command per registered class. **When you add a new source, you must also add its `importlib.import_module(...)` line to `_register_pull_commands` and a Makefile target.**

Source-level configuration (URLs, default years/quarters, FIPS lists) belongs in [configs/sources.yaml](configs/sources.yaml), loaded by each source's `_load_config()` classmethod. Don't hardcode tunable values in source modules.

### Warehouse abstraction

`Warehouse` is a runtime-checkable Protocol in [src/eia/warehouse/base.py](src/eia/warehouse/base.py). Source modules and the CLI depend on the Protocol only — never on `DuckDBWarehouse` or `PostgresWarehouse` directly. Use `eia.warehouse.get_warehouse()` to obtain the configured backend (`EIA_WAREHOUSE_BACKEND=duckdb|postgres`).

Migrations: numbered SQL files in `migrations/` are applied in lexicographic order; each backend tracks applied files in a `_migrations` table. Migrations must be ANSI-compatible across DuckDB and Postgres (CHAR/VARCHAR, no DuckDB-specific syntax).

DuckDB-specific note: parameterized queries use `$name` syntax with a dict, not `?`/positional. See `query()` in [duckdb_impl.py](src/eia/warehouse/duckdb_impl.py) and tests in [tests/test_warehouse.py](tests/test_warehouse.py).

### HTTP client

All API-backed sources go through `RateLimitedClient` ([src/eia/clients/base.py](src/eia/clients/base.py)) — it provides per-second throttling, tenacity retries on 429/5xx, and optional response caching. Don't instantiate `httpx.Client` directly in a source.

### Configuration

`Settings` ([src/eia/config.py](src/eia/config.py)) is a Pydantic v2 `BaseSettings` loaded from env + `.env`. Project-namespaced env vars use the `EIA_` prefix; bare API keys (`CENSUS_API_KEY`, `TICKETMASTER_API_KEY`, etc.) live alongside without prefix. Import the module-level `settings` singleton.

### Transforms layer (clean-time enrichment)

`src/eia/transforms/` holds pure-Polars functions called from event-source `to_cleaned()` methods. All take a `pl.DataFrame` and return a new one with derived columns added. Composable left-to-right; canonical chain at the call site:

```python
from eia.transforms import (
    attach_county_fips,         # lat/lon -> county_fips (TIGER spatial join)
    attach_county_fips_via_zip, # fill nulls from HUD ZIP-county crosswalk
    attach_period_id,           # event_date -> period_id ('YYYYQQ') + period_month ('YYYY-MM')
    build_county_month_panel,   # counties -> dense (county × month) panel for treatment-effect modeling
)

df = attach_county_fips(df)
df = attach_county_fips_via_zip(df, hud)  # caller pre-filters hud to one quarter
df = attach_period_id(df)
```

Format consistency across transforms is locked: `period_id` matches `bls_qcew.period_id` byte-for-byte, `period_month` matches what `build_county_month_panel` produces. Verified by `tests/transforms/test_composition.py`.

### Multipliers layer (model-side foundation)

`src/eia/multipliers/` holds the BEA Leontief multiplier engine. Standard Type I, industry-by-industry, industry-technology-assumption formulation: `A = D @ B`; `L = (I - A)^-1`. Long-form Polars in/out; NumPy under the hood (already a transitive dep).

```python
from eia.multipliers import compute_leontief_inverse, apply_multipliers

# Build the multiplier table from BEA Use/Make.
L = compute_leontief_inverse(use, make)
# Apply to a final-demand vector y -> total output per industry.
total = apply_multipliers(L, demand)
```

Math invariants are locked by a hand-computable 2-industry reference test case in [tests/multipliers/test_leontief.py](tests/multipliers/test_leontief.py).

### Phase 0 exit criterion

[pipelines/phase0_exit_query.py](pipelines/phase0_exit_query.py) is the canonical proof-of-data query: it joins `dim_county` + `census_acs_county` + `bls_qcew` for San Diego (FIPS `06073`), Q3 2023, NAICS 721 (Accommodation). If that cross-source join returns a row, Phase 0 is green. Treat this as the integration test for new federal-side schema changes.

### BEA multiplier validation

[pipelines/validate_bea_multipliers.py](pipelines/validate_bea_multipliers.py) (run via `make validate-bea-multipliers`) cross-checks our computed direct-requirements matrix `B = U / q` against BEA's published `CxI_DR_*_Summary.xlsx` for every year 1997-2023, and runs Leontief sanity checks (`L` diagonal >= 1, max diagonal < 10). Agreement is bounded at ~3.4e-5 across all years; the small systematic gap is the documented publication-date stagger between BEA's CxI_DR (2024-08-28) and the Use/Make tables (2024-09-06) inside `AllTablesIO.zip`. Re-run after any change to `compute_leontief_inverse` or the BEA parser.

### Events enrichment

Event-source `to_cleaned()` methods (Ticketmaster, RunSignUp, Setlist.fm) deliberately leave `county_fips`, `period_id`, and `period_month` null — those derive from `venue_lat`/`venue_lon` and `event_date` via transforms that live in `src/eia/transforms/`. [pipelines/enrich_events.py](pipelines/enrich_events.py) (run via `make enrich-events`) reads the events table, applies `attach_county_fips` (TIGER spatial join) and `attach_period_id`, writes `data/cleaned/events_enriched.parquet`, and re-registers the events table. Run AFTER any event pull. Idempotent — old derived columns are dropped and recomputed each time.

## Conventions

- DataFrames use **Polars**, not pandas. `pl.read_csv`, `pl.read_parquet`, and the warehouse's `query()` all return Polars.
- Geometric work uses **GeoPandas** (only inside source modules that need it, e.g. TIGER); the warehouse stores derived scalars (centroid lat/lon, area), not geometries.
- Ruff (line length 100, double quotes) and mypy strict are enforced — keep them green.
- `from __future__ import annotations` at the top of every new module.
