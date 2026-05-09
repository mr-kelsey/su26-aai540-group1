# Spatial-Join Transform: lat/lon → county_fips

**Date:** 2026-05-09
**Phase:** 1 (MVP corpus)
**Status:** Design approved, awaiting implementation plan

## Background

The events table ([migrations/003_events.sql](../../../migrations/003_events.sql)) carries `venue_lat`, `venue_lon`, and a nullable `county_fips`. The DDL comment marks `county_fips` as "derived (computed at clean-time from TIGER county polygons)". No transform exists yet to do that derivation, so every event-side source pipeline (Ticketmaster, RunSignUp, Setlist.fm) is blocked from emitting rows that join to the federal-side tables on FIPS.

Phase 1's exit criterion is producing direct/indirect/induced estimates for any concert or marathon in the corpus. That requires events landing in the warehouse with `county_fips` populated. This transform unblocks that path before any API key arrives.

## Goals

- One public function in `src/eia/transforms/geo.py` that callers in event-source `to_cleaned()` methods invoke to attach `county_fips` to a Polars DataFrame of event rows.
- Spatial-join correctness for the contiguous US plus Hawaii (everything in TIGER's national counties shapefile).
- Soft-fail behavior on bad/missing coordinates so a single bad geocode never aborts an ETL run.
- Zero modification to the warehouse Protocol or migrations.

## Non-goals

- `period_id` derivation from event date — separate transform, separate PR.
- HUD ZIP→county fallback for rows missing lat/lon — separate transform; uses different (probabilistic) semantics.
- Single-point convenience helper (`fips_for_point(lat, lon)`) — not needed by any caller; add when something needs it.
- Multi-year polygon swap per event date — Phase 1 backcatalog spans 2015–2023; county boundaries are stable across that window for our purposes. Pin to TIGER 2023.
- Warehouse write helpers — this is a pure-Polars in/out transform; warehouse loading stays in each source's `load()`.

## Design

### TIGER source change

[src/eia/sources/tiger.py](../../../src/eia/sources/tiger.py)'s `to_cleaned()` already builds a `GeoDataFrame` to compute centroids and area, then drops the geometry. The change:

- Keep the existing flat-Parquet output unchanged. The Source ABC's `to_cleaned() -> Path` contract still returns the flat file the warehouse loader uses to populate `dim_county`.
- After the existing centroid math, also write a sibling artifact at `data/cleaned/tiger/counties_geo_<year>.parquet` via `gdf[["GEOID", "geometry"]].to_crs(4326).to_parquet(...)`. GeoParquet preserves CRS metadata. Two columns: `GEOID` (5-char FIPS string) and `geometry` (polygon, WGS84 / EPSG:4326).
- Idempotent: skip the write if the geo file already exists and is non-empty (mirrors the `fetch()` pattern already used in this source).

This is an explicit side-effect output, named distinctly from the warehouse-bound flat Parquet so nothing is ambiguous. The Source ABC is unchanged.

### `transforms/geo.py` public surface

```python
def attach_county_fips(
    df: pl.DataFrame,
    *,
    lat_col: str = "venue_lat",
    lon_col: str = "venue_lon",
    out_col: str = "county_fips",
    tiger_year: int = 2023,
) -> pl.DataFrame
```

Returns the input frame with `out_col` appended (Utf8, nullable). Row order preserved. No other columns touched.

### Internals

1. **Index loader.** Module-level dict keyed by `tiger_year` caches the counties `GeoDataFrame`. On first call for a given year, read `data/cleaned/tiger/counties_geo_<year>.parquet` via `geopandas.read_parquet`. The STRtree is built lazily by `geopandas.sjoin` on the cached frame; we don't manage it ourselves. Subsequent calls — including across multiple sources in one process — skip the disk read.
2. **File-not-found.** If the geo Parquet is missing, raise `FileNotFoundError` with a message naming `make pull-tiger` as the fix.
3. **Sanity gate.** A row is "joinable" iff:
    - `lat` and `lon` are both non-null, AND
    - `lat ∈ [-90, 90]` and `lon ∈ [-180, 180]`, AND
    - `(lat, lon) != (0, 0)` (the most common bad-API sentinel).
   Non-joinable rows skip the spatial join entirely and receive `null`. No coordinate-swap detection — too much magic, risk of corrupting legitimate (-90W, 30N)-style points.
4. **Spatial join.** Build a points GeoDataFrame from the joinable subset, call `geopandas.sjoin(points_gdf, counties_gdf, predicate="within", how="left")`, take the resulting `GEOID` column. Geopandas uses shapely's STRtree under the hood; performance is fine for ~50k points × 3,143 counties (Phase 1 scale).
5. **Reassembly.** Add a stable row-position column to the input frame (e.g. `pl.int_range`), keep that column on the joinable subset that goes into geopandas, then left-join the resulting `(row_position → GEOID)` table back onto the input. Non-joinable rows have no match in the join and naturally receive `null`. Drop the row-position column before returning. The output frame has the same row count and order as the input.
6. **Logging.** One INFO-level summary line per call:

   ```
   attach_county_fips: <miss>/<total> unmapped (<bad_coords> bad coords, <off_county> off-county) [tiger_year=<year>]
   ```

   Uses Python's `logging` module — quiet by default, visible when callers configure logging.

### Edge-case matrix

| Input | Output | Counted as |
|---|---|---|
| Valid lat/lon inside a US county | `county_fips` set | hit |
| Valid lat/lon offshore or off-CONUS with no matching polygon | `null` | off-county |
| `lat` or `lon` is null | `null` | bad coords |
| `(0, 0)` | `null` | bad coords |
| `lat ∉ [-90, 90]` or `lon ∉ [-180, 180]` | `null` | bad coords |
| Point exactly on a county boundary | Whichever polygon the `within` predicate matches first | hit (deterministic-but-arbitrary) |

The boundary case is documented in the function docstring. It is deterministic for a given TIGER year (geopandas/shapely is deterministic for a fixed input order), and economically irrelevant — events on a literal county boundary are a measure-zero set, and either neighboring county is a defensible attribution.

### Caller integration

Each event source's `to_cleaned()` will, in its eventual implementation, end with:

```python
from eia.transforms.geo import attach_county_fips
df = attach_county_fips(df)        # uses default lat/lon col names
```

That is the entire integration surface. No source-specific configuration is required.

## Testing

Unit tests in `tests/transforms/test_geo.py`:

- A pytest fixture builds a tiny synthetic GeoParquet with 2-3 fake county polygons in a known projected location and monkeypatches the module's loader to point at it. No TIGER download in tests; no warehouse touched.
- One test per row in the edge-case matrix above.
- Empty DataFrame: returns empty frame with `county_fips` column added.
- Cache hit: a second call within the same process does not re-read the GeoParquet (assert via a load-counter spy on the loader).
- Realistic shape: a small DataFrame matching the events table column set, asserting the rest of the columns pass through unchanged.

The TIGER source change ([src/eia/sources/tiger.py](../../../src/eia/sources/tiger.py)) gets a separate test that, given a tiny in-memory zipped shapefile fixture, asserts both the existing flat Parquet AND the new geo Parquet are written, and that the geo Parquet round-trips through `geopandas.read_parquet` with `EPSG:4326`.

## Operational notes

- **TIGER year pinning.** Default `tiger_year=2023` matches the configured year in [configs/sources.yaml](../../../configs/sources.yaml). If/when the configured year advances, callers can either accept the default change or pass an explicit year. The corpus-wide year choice is a separate decision documented in the data acquisition strategy.
- **CRS.** Input lat/lon is assumed WGS84 (EPSG:4326). The counties GeoParquet is written in 4326 specifically so no per-point reprojection is needed at join time.
- **Performance.** Phase 1 max scale is ~50k events. STRtree-backed `sjoin` over 3,143 counties is well under one second at that volume; we don't need to optimize further. If Phase 2 sport-event volumes push this past tolerance, the path is to switch to direct `shapely.STRtree.query_nearest` with manual point-in-polygon — but YAGNI.

## References

- [docs/data_acquisition_strategy.md](../../data_acquisition_strategy.md) — Phase 1 goals and event-source plan.
- [docs/architecture.md](../../architecture.md) — three-zone warehouse layout, source-plugin contract.
- [migrations/003_events.sql](../../../migrations/003_events.sql) — events table DDL with derived `county_fips`.
- [src/eia/sources/base.py](../../../src/eia/sources/base.py) — Source ABC and `to_cleaned()` contract.
- [src/eia/sources/tiger.py](../../../src/eia/sources/tiger.py) — current TIGER source; the file modified here.
