# County-Month Training Panel Transform

**Date:** 2026-05-09
**Phase:** 1 (MVP corpus)
**Status:** Design approved (autonomous run — user authorized "choose the recommended path"); awaiting implementation plan

## Background

The data acquisition strategy ([docs/data_acquisition_strategy.md](../../data_acquisition_strategy.md)) names the Phase 1 model exit criterion as: "produce a direct/indirect/induced estimate for any concert or marathon in the corpus, and we can score it against state DOR signals at the **county-month** level." Both training and evaluation need a panel-shape input — every (county, month) cell present, including months at a county where no events occurred. Without those zero-event cells, a regression has no untreated baseline to subtract from treated cells; the model can't isolate event-attributable impact.

Two transforms are already in place:
- [`attach_county_fips`](../../../src/eia/transforms/geo.py) attaches `county_fips` to event rows by spatial join.
- [`attach_period_id`](../../../src/eia/transforms/temporal.py) attaches `period_id` (`'YYYYQQ'`) and `period_month` (`'YYYY-MM'`) to event rows from `event_date`.

But the events table is event-keyed — one row per event. `GROUP BY (county_fips, period_month)` on `events` only returns months that had events, not the dense panel the model needs. This transform fills the gap.

## Goals

- One public function in `src/eia/transforms/panel.py` that produces the dense `(county_fips × period_month)` cartesian product as a Polars DataFrame, with derived `period_id`, `year`, and `month` columns.
- Output rows are keys only — no event aggregates, no federal covariates. Downstream feature ETL joins those in.
- Pure data transform — caller reads `dim_county` from the warehouse and passes it as a DataFrame; the function has no warehouse dependency.
- Phase 1 default year range 2015–2023 per the strategy doc's training-corpus window.

## Non-goals

- **Event aggregates** (`n_events`, `total_attendance`, etc.) inline in the panel. Downstream feature ETL joins `events` to the panel by `(county_fips, period_month)`. Keeping the panel orthogonal makes both the panel and the event aggregates evolve independently.
- **Federal covariates** (`qcew_employment`, `acs_population`, etc.) inline. Same reasoning — joined downstream.
- **Materialized warehouse table or SQL view.** A function is simpler — no migration to manage, no staleness lifecycle, easy to test with synthetic counties data. Performance is fine: 3,143 counties × 108 months ≈ 340k rows, sub-second in Polars.
- **Configurable month boundaries** (custom calendars, fiscal years, ISO weeks). Strict Gregorian Jan-Dec months only.
- **Backfill of existing data.** No event rows exist yet; the panel is generated on demand by downstream ETL when needed.
- **Quarter-only or year-only variants.** If a caller wants quarterly granularity, they can derive it from `period_id` post-hoc; the panel itself is monthly.

## Design

### Module placement

`src/eia/transforms/panel.py`. Re-exported from `src/eia/transforms/__init__.py` alongside `attach_county_fips` and `attach_period_id`:

```python
from eia.transforms.geo import attach_county_fips
from eia.transforms.panel import build_county_month_panel
from eia.transforms.temporal import attach_period_id

__all__ = ["attach_county_fips", "attach_period_id", "build_county_month_panel"]
```

### Public surface

```python
def build_county_month_panel(
    counties: pl.DataFrame,
    *,
    start_year: int = 2015,
    end_year: int = 2023,
    fips_col: str = "county_fips",
) -> pl.DataFrame
```

Returns a new Polars DataFrame with columns `[fips_col, "period_month", "period_id", "year", "month"]`, sorted by `(fips_col, year, month)`. Row count is `n_unique_counties × 12 × (end_year − start_year + 1)`. The input `counties` DataFrame must contain `fips_col` (Utf8); other columns are ignored. Duplicates in `counties[fips_col]` are silently deduped.

### Internals

Step-by-step Polars expression chain:

1. **Validate inputs.** Raise `ValueError` if `start_year > end_year` or if `counties[fips_col]` contains nulls.
2. **Build months frame.** A small `pl.DataFrame` with columns `year` (Int32) and `month` (Int32, 1-12) covering the year range. `n_years × 12` rows.
3. **Derive period strings on the months frame.** Use `pl.date(year, month, 1).dt.strftime("%Y-%m")` for `period_month` (matching what `attach_period_id` produces) and `pl.format("{}Q{}", year, ((month - 1) // 3 + 1))` for `period_id`.
4. **Dedupe counties.** `counties.select(fips_col).unique()` to handle duplicate FIPS inputs.
5. **Cross join.** `counties_unique.join(months, how="cross")`.
6. **Project + sort.** Select columns in canonical order `[fips_col, "period_month", "period_id", "year", "month"]`, sort by `(fips_col, year, month)`.

### Output formats (matching existing producers)

| Column | Dtype | Format | Producer |
|---|---|---|---|
| `<fips_col>` | Utf8 | 5-char FIPS, e.g. `"06073"` | from input |
| `period_month` | Utf8 | `'YYYY-MM'`, e.g. `"2023-08"` | matches `attach_period_id` |
| `period_id` | Utf8 | `'YYYYQQ'`, e.g. `"2023Q3"` | matches `attach_period_id` and `bls_qcew.period_id` |
| `year` | Int32 | 4-digit calendar year | derived |
| `month` | Int32 | 1-12 | derived |

The two period-key columns are byte-identical to what `attach_period_id` produces for the same date — joins like `panel JOIN events USING (county_fips, period_month)` work without format wrangling.

### Edge-case matrix

| Input | Output |
|---|---|
| Valid counties, 2015–2023 default range | full panel, sorted |
| Empty `counties` DataFrame | empty panel with the five expected columns |
| Single county × single year (start == end) | 12 rows |
| Duplicate FIPS in `counties[fips_col]` | silently deduped |
| Null in `counties[fips_col]` | raises `ValueError(f"{fips_col} contains null values")` |
| `start_year > end_year` | raises `ValueError(f"start_year ({start_year}) > end_year ({end_year})")` |
| `start_year == end_year` | n_counties × 12 rows |

### Caller integration

Downstream feature ETL pseudocode:

```python
from eia.transforms import build_county_month_panel
from eia.warehouse import get_warehouse

wh = get_warehouse()
counties = wh.query("SELECT county_fips FROM dim_county")
panel = build_county_month_panel(counties)

# Join event aggregates (LEFT join — months without events get null/0)
event_aggs = wh.query("""
    SELECT county_fips, period_month,
           COUNT(*) AS n_events,
           SUM(expected_attendance) AS total_attendance
    FROM events
    GROUP BY county_fips, period_month
""")
training_input = panel.join(event_aggs, on=["county_fips", "period_month"], how="left")

# Federal covariates layered next: QCEW (quarterly), ACS (annual), DOR (monthly)
# That's a separate transform.
```

The panel is the foundation; everything else joins on top of it.

## Testing

Unit tests in `tests/transforms/test_panel.py`:

- **Empty counties** → empty panel with the five expected columns and correct dtypes.
- **Single county × single year (start == end)** → 12 rows; assert all months 1-12 present, all `period_id` values are `2015Q1..2015Q4`.
- **Two counties × two years** → 48 rows (2 × 2 × 12); assert sort order is `(fips, year, month)`.
- **Quarter derivation correctness** — 2023-01 → '2023Q1', 2023-04 → '2023Q2', 2023-07 → '2023Q3', 2023-10 → '2023Q4'. Same dates check `period_month` is `'2023-MM'` form.
- **Format consistency with `attach_period_id`** — for a given `(year, month)`, the panel's `period_id` and `period_month` strings are byte-identical to what `attach_period_id(date(year, month, 1))` produces. This guards against silent format drift between the two transforms.
- **Duplicate FIPS deduped** — input with two rows of FIPS `"06073"` produces a panel with each `period_month` appearing exactly once for `"06073"`.
- **`start_year > end_year`** → `ValueError`.
- **Null FIPS in input** → `ValueError`.
- **Custom `fips_col`** — caller passes a column named `"my_fips"`; output respects it.
- **Public import smoke test** — `from eia.transforms import build_county_month_panel`.

No fixture-based monkeypatching needed (transform has no external dependencies — pure Polars).

## Operational notes

- **Performance.** 3,143 counties × 108 months = ~340k rows. Polars cross-join on this is well under a second on consumer hardware. No optimization concerns at Phase 1 scale.
- **Memory.** ~340k × 5 columns of small types ≈ a few MB. Trivial.
- **Determinism.** Output is fully deterministic for a given `(counties, start_year, end_year)`. Sort order is locked to `(fips, year, month)` so downstream tests don't break on Polars version bumps.
- **Year-range expansion.** When Phase 2 widens the corpus past 2023, callers pass `end_year=<latest>`. The function doesn't read the calendar — caller decides the window.

## References

- [docs/superpowers/specs/2026-05-09-spatial-join-transform-design.md](2026-05-09-spatial-join-transform-design.md) — `attach_county_fips` design (sibling).
- [docs/superpowers/specs/2026-05-09-period-id-transform-design.md](2026-05-09-period-id-transform-design.md) — `attach_period_id` design; this panel mirrors the format guarantees.
- [docs/data_acquisition_strategy.md](../../data_acquisition_strategy.md) §5 — Phase 1 sample-size targets and county-month evaluation requirement.
- [migrations/001_dimensions.sql](../../../migrations/001_dimensions.sql) — `dim_county` schema, the panel's primary input source.
