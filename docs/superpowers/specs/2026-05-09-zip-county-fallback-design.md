# `attach_county_fips_via_zip` — ZIP→county fallback

**Date:** 2026-05-09
**Phase:** 1 (transition — defensive coverage for events without lat/lon)
**Status:** Design approved (autonomous run; user authorized "choose the recommended path"); awaiting implementation plan

## Background

`attach_county_fips` ([src/eia/transforms/geo.py](../../../src/eia/transforms/geo.py)) attaches `county_fips` to event rows via TIGER polygon spatial join on `(venue_lat, venue_lon)`. Rows whose lat/lon is null, sentinel `(0, 0)`, out-of-range, or off-CONUS receive null `county_fips` per the soft-fail design.

The events DDL ([migrations/003_events.sql](../../../migrations/003_events.sql)) carries both `venue_lat`/`venue_lon` AND `venue_zip`. Some events arrive with a ZIP but no usable coordinates — particularly Phase 2 sources like city open-data portals, sports scrapers, or APIs that occasionally omit coordinates. Without a ZIP-based fallback, those rows silently drop out of every panel-joined query.

The HUD USPS ZIP-County crosswalk ([migrations/002_economic_backbone.sql:53](../../../migrations/002_economic_backbone.sql:53)) provides the lookup: each ZIP maps to one or more counties weighted by `res_ratio` (residential), `bus_ratio` (business), `oth_ratio` (other), and `tot_ratio` (total). For events, the residential weight is the right semantic — events impact the residents of the ZIP.

## Goals

- One public function in `src/eia/transforms/geo.py` (alongside `attach_county_fips`) that fills null `county_fips` values from a ZIP lookup, leaving non-null `county_fips` untouched.
- Caller-supplied HUD DataFrame parameter — same orthogonality as `build_county_month_panel`'s counties param. The transform doesn't read the warehouse.
- Pick `county_fips` with max `res_ratio` per ZIP; deterministic alphabetical tie-break on `county_fips`. ZIPs not in the HUD table leave the row's `county_fips` null.
- One INFO log line per call summarizing rows filled / not-filled, mirroring `attach_county_fips`'s observability pattern.

## Non-goals

- **Replacing `attach_county_fips`.** The two transforms compose: spatial join first (more accurate), ZIP fallback for the residual. This transform never overwrites a non-null `county_fips`.
- **Returning weighted shares (one row per (event, county_fips, weight)).** The regression input wants a single attribution per event row, not row explosion. Weighted attribution is a different downstream pattern.
- **Quarter-aware HUD selection.** Caller filters `hud` to a single quarter before calling. Selecting which HUD vintage to use against which event period is a separate concern.
- **Sentinel-ZIP handling** beyond null. ZIP `"00000"` or other obviously-invalid sentinels are looked up like any other ZIP — they simply won't be in HUD, so the row's `county_fips` stays null.
- **Bidirectional fallback** (i.e., looking up ZIP from county). Not a real use case.

## Design

### Module placement

Add to existing `src/eia/transforms/geo.py`. Both `attach_county_fips` and `attach_county_fips_via_zip` produce `county_fips` from event rows; co-locating them in `geo.py` keeps the geo-enrichment story discoverable. Re-exported from `src/eia/transforms/__init__.py`.

After this lands, `__all__` becomes:

```python
__all__ = [
    "attach_county_fips",
    "attach_county_fips_via_zip",
    "attach_period_id",
    "build_county_month_panel",
]
```

### Public surface

```python
def attach_county_fips_via_zip(
    df: pl.DataFrame,
    hud: pl.DataFrame,
    *,
    zip_col: str = "venue_zip",
    fips_col: str = "county_fips",
) -> pl.DataFrame
```

Returns a new Polars DataFrame, same shape as `df`, with the `fips_col` column populated where it was previously null, using `hud`-based ZIP lookups. Non-null `fips_col` values are unchanged. Rows whose `zip_col` is null, or whose ZIP is not in `hud`, keep their existing (null) `fips_col`.

`fips_col` MUST exist in `df` (typically populated by a prior `attach_county_fips` call). Calling without it raises `ValueError` — this is a contract guard, not silent behavior.

`hud` must contain columns `["zip", "county_fips", "res_ratio"]`. Caller is responsible for filtering `hud` to a single quarter before calling.

### Internals

Step-by-step Polars expression chain:

1. **Validate** that `fips_col` exists in `df.columns`. Raise `ValueError(f"{fips_col} not in df.columns; run attach_county_fips first")` if not.
2. **Build deterministic `zip → county_fips` mapping** from `hud`:
   - Sort by `(res_ratio DESC, county_fips ASC)`.
   - `group_by("zip", maintain_order=True).first()` — keeps the first row per ZIP after sorting, which is max-res-ratio with alphabetical tiebreak.
   - Project to `(zip, _zip_lookup_fips)` (rename to avoid join-column collision).
3. **Left-join** `df` with the lookup on `df[zip_col] == hud_lookup["zip"]`.
4. **Coalesce** `fips_col` with `_zip_lookup_fips`: `pl.coalesce(pl.col(fips_col), pl.col("_zip_lookup_fips"))`. Null `fips_col` gets the ZIP lookup; non-null is preserved.
5. **Drop** the `_zip_lookup_fips` column. Output frame has the original columns (with `fips_col` updated).
6. **Log** one INFO line: `"attach_county_fips_via_zip: filled {n_filled}/{n_originally_null} originally-null rows ({n_zip_missing} no ZIP, {n_zip_not_in_hud} ZIP not in crosswalk) [hud_quarter=<inferred or 'unknown'>]"`. The quarter is read from `hud["quarter"][0]` if present, else "unknown".

### Edge-case matrix

| Input row | Output |
|---|---|
| `fips_col` already set | unchanged (whatever it was) |
| `fips_col` null, ZIP in HUD with single county | filled |
| `fips_col` null, ZIP in HUD with multiple counties | filled with max-res-ratio county |
| `fips_col` null, ZIP in HUD with tie on res_ratio | filled deterministically with alphabetically-lowest `county_fips` |
| `fips_col` null, ZIP null | stays null |
| `fips_col` null, ZIP not in HUD | stays null |

The "deterministic tiebreak" guarantee matters because Polars' `group_by(...).first()` picks the first row in the group, and we explicitly sort to make "first" mean "highest res_ratio, then lowest county_fips." Without the secondary sort, ties would resolve arbitrarily based on input row order.

### Caller integration

The canonical chain for an event-source `to_cleaned()` becomes:

```python
from eia.transforms import (
    attach_county_fips,
    attach_county_fips_via_zip,
    attach_period_id,
)
from eia.warehouse import get_warehouse

wh = get_warehouse()
hud = wh.query("SELECT zip, county_fips, res_ratio, quarter FROM hud_zip_county WHERE quarter = '2024Q1'")

df = attach_county_fips(df)                  # spatial join, may leave nulls
df = attach_county_fips_via_zip(df, hud)     # fill remaining nulls from ZIP
df = attach_period_id(df)                    # period keys
```

Either order of `attach_county_fips_via_zip` and `attach_period_id` works; the convention is geo-enrichment then time-enrichment.

## Testing

Unit tests appended to `tests/transforms/test_geo.py` (the existing test file for the module, since the new function lives in `geo.py`):

- **Already-set `county_fips` is preserved** — input has non-null `county_fips`; the function does not consult HUD or change the value.
- **Null `county_fips` + ZIP in single-county HUD** → filled correctly.
- **Null `county_fips` + ZIP in multi-county HUD** → filled with the max-`res_ratio` county.
- **Tie on res_ratio** → deterministic fill with alphabetically-lowest `county_fips`.
- **Null `county_fips` + null ZIP** → stays null.
- **Null `county_fips` + ZIP not in HUD** → stays null.
- **Empty input frame** → returns empty frame with same columns.
- **Missing `fips_col` in input** → raises `ValueError` with a message naming `attach_county_fips`.
- **Custom `zip_col` / `fips_col`** → respected.
- **INFO log summary** — verified via `caplog`; counts match expected.
- **Public import smoke test** — `from eia.transforms import attach_county_fips_via_zip`.

## Operational notes

- **HUD fixture in tests.** Tests build a small synthetic `hud` DataFrame in-line (no warehouse, no fixture file). 4-5 ZIPs covering the test scenarios.
- **No new dependencies.** All operations are pure Polars.
- **Performance.** O(n_events + n_hud) for the join. Phase 1 corpus ~50k events, HUD has ~40k US ZIPs — both small. Sub-second.
- **Deterministic output.** The sort + group_by(maintain_order=True).first() chain produces byte-identical output for byte-identical inputs across Polars versions.

## References

- [docs/superpowers/specs/2026-05-09-spatial-join-transform-design.md](2026-05-09-spatial-join-transform-design.md) — `attach_county_fips` design; this transform fills the gap that one's soft-fail leaves.
- [migrations/002_economic_backbone.sql](../../../migrations/002_economic_backbone.sql) — `hud_zip_county` schema.
- [src/eia/sources/hud_crosswalk.py](../../../src/eia/sources/hud_crosswalk.py) — HUD source that populates the table.
- [docs/data_acquisition_strategy.md](../../data_acquisition_strategy.md) §3.3 — strategy doc on HUD as the residential-weight ZIP↔county source.
