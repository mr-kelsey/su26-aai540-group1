# su26-aai540-group1

**Production ML system for estimating the economic impact of social events on host cities.**
USD AAI-540 (Production ML), Summer 2026 — Group 1 Final Project.

## What this is

Traditional Economic Impact Assessments (EIAs) cost $20–30k per event because they lean on economist SMEs walking through input-output models by hand. This project replaces most of that expert labor with a Bayesian regression front-end over publicly available federal data, with the goal of producing defensible direct/indirect/induced impact estimates at ~$500/event.

The system covers four data layers:

1. **Economic backbone** — BEA Input-Output tables, BLS QCEW, Census Government Finances. Provides the multipliers and industry structure that translate direct spending into indirect and induced ripples.
2. **Event-side training corpus** — Ticketmaster, Setlist.fm, RunSignUp, city open-data portals, Wikidata. Historical events with type, attendance, location.
3. **Geographic & demographic context** — Census ACS, TIGER/Line shapefiles, HUD ZIP-county crosswalk, OSM. Localizes national multipliers to the host region.
4. **Ground-truth tax data** — Census Annual Survey of State and Local Government Finances, IRS SOI, state DOR dashboards. The long-cycle evaluation target.

See `docs/data_acquisition_strategy.md` for the full strategy.

## Quick start

```bash
# Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install project dependencies
make install-dev

# Copy env template and fill in any API keys you have
cp .env.example .env

# Initialize the warehouse and run all Phase 0 federal pulls
make phase0
```

## Project structure

```
.
├── src/eia/              # Main package
│   ├── warehouse/        # DB abstraction (DuckDB now, Postgres-ready)
│   ├── sources/          # One module per external data source
│   ├── transforms/       # Cleaning + feature engineering (Polars)
│   ├── clients/          # Rate-limited HTTP clients for APIs
│   ├── config.py         # Pydantic settings
│   └── cli.py            # `eia` CLI entrypoint
├── pipelines/            # Orchestration scripts
├── migrations/           # SQL DDL
├── configs/              # YAML config (sources, schedules)
├── notebooks/            # Exploration + reports
├── tests/                # Pytest
├── docs/                 # Strategy + architecture
├── data/                 # Gitignored: raw/, cleaned/, warehouse.duckdb
├── pyproject.toml
└── Makefile              # Task runner — `make help`
```

## Data warehouse

DuckDB by default — file-based, zero-ops, columnar OLAP. The `Warehouse` protocol in `src/eia/warehouse/base.py` abstracts the backend so we can swap to Postgres later without touching the source pipelines. Set `EIA_WAREHOUSE_BACKEND=postgres` in `.env` and provide `EIA_POSTGRES_DSN`.

## Phases

- **Phase 0** (current): proof-of-data — federal pulls work, warehouse joins clean on FIPS.
- **Phase 1**: MVP corpus on concerts + marathons.
- **Phase 2**: production-grade coverage (sports, conferences, deeper ground-truth).
- **Phase 3**: continuous refresh + drift monitoring.

## License

See LICENSE.
