# System Architecture

## Three-zone warehouse

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   raw/      │ →  │  cleaned/   │ →  │  features/  │
│ (verbatim)  │    │ (FIPS-keyed)│    │ (model-ready)│
└─────────────┘    └─────────────┘    └─────────────┘
```

- **Raw zone** — every external pull is dumped verbatim with its `fetch_timestamp`. Never deleted; immutable. Format follows the source (CSV, JSON, GeoJSON, Parquet, shapefile). Lives at `data/raw/<source>/<partition>/`.
- **Cleaned zone** — schema-conformed Parquet, deduplicated, FIPS-joined. Versioned via filename (`v=YYYY-MM-DD`). Lives at `data/cleaned/<source>/`.
- **Feature zone** — model-ready tables; one row per event with the full feature vector. Materialized into the warehouse for fast querying.

## Source pipeline pattern

Every source implements the `Source` protocol in `src/eia/sources/base.py`:

```python
class Source(Protocol):
    name: str
    raw_format: str  # "csv" | "json" | "shapefile" | "parquet"

    def fetch(self) -> Path:
        """Download or API-pull; write verbatim to data/raw/."""

    def to_cleaned(self, raw_path: Path) -> Path:
        """Transform raw → cleaned Parquet with conformed schema."""

    def load(self, cleaned_path: Path, warehouse: Warehouse) -> None:
        """Insert/replace into warehouse."""
```

Pipeline orchestration lives in `pipelines/` — for now, plain Python scripts driven by `make` targets. We promote to Airflow / Prefect only if scheduling complexity demands it.

## Warehouse abstraction

`Warehouse` protocol with two implementations:

- `DuckDBWarehouse` — file-based, columnar, zero-ops. Default.
- `PostgresWarehouse` — server-based, multi-writer. Stub for future swap.

Both implement the same minimal interface: `execute_sql`, `register_table_from_parquet`, `read_table_polars`, `migrate`. Source modules never see the underlying engine.

## Geographic keys

Two keys per event:
- `venue_lat_lon` — point geometry, computed at fetch time from venue address or API field.
- `county_fips` — derived from lat/lon via TIGER county polygons; the join key for federal data.

## Configuration

`src/eia/config.py` exposes a Pydantic `Settings` model loaded from `.env`. Source-level config (URLs, partition keys, refresh cadence) lives in `configs/sources.yaml` so the team can tweak partition windows without code changes.

## Future shape (Phase 1+)

```
┌──────────────┐
│ Federal pulls│──┐
└──────────────┘  │
┌──────────────┐  │   ┌─────────────┐    ┌──────────────┐
│ Event APIs   │──┼──→│ Warehouse   │──→ │ Feature ETL  │──→ Bayesian regression
└──────────────┘  │   └─────────────┘    └──────────────┘
┌──────────────┐  │                            │
│ City portals │──┘                            ▼
└──────────────┘                       ┌──────────────┐
                                       │ Eval scoring │ ← state DOR + Census
                                       └──────────────┘
```
