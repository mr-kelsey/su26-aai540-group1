# `period_id` / `period_month` Transform: event_date → temporal join keys

**Date:** 2026-05-09
**Phase:** 1 (MVP corpus)
**Status:** Design approved, awaiting implementation plan

## Background

The events table ([migrations/003_events.sql](../../../migrations/003_events.sql)) carries `event_date DATE` and a derived `period_id VARCHAR` (comment-marked "year/quarter/month"). Phase 1's exit criterion in the data acquisition strategy requires the model to "produce a direct/indirect/induced estimate for any concert or marathon in the corpus, and we can score it against state DOR signals at the **county-month** level." Two distinct downstream joins on event temporality are therefore in scope right away:

1. **Quarterly** — events to `bls_qcew` ([migrations/002_economic_backbone.sql:8](../../../migrations/002_economic_backbone.sql:8)) and other quarterly federal aggregates. The `bls_qcew.period_id` format is already `'YYYYQQ'` (e.g. `'2023Q3'`), produced in [bls_qcew.py:119](../../../src/eia/sources/bls_qcew.py:119).
2. **Monthly** — events to a future state DOR sales/lodging-tax table for evaluation, AND to a county-month panel for training-time treatment-effect contrasts (events vs no-events months at the same county). Both want `'YYYY-MM'` keys.

No transform exists yet to derive either column from `event_date`. This unblocks both joins as soon as event sources begin landing rows.

## Goals

- One public function in `src/eia/transforms/temporal.py` that callers in event-source `to_cleaned()` methods invoke after `attach_county_fips` to attach quarterly + monthly period keys to a Polars DataFrame of event rows.
- Output formats matching what already exists elsewhere in the warehouse (`'YYYYQQ'` for quarter, `'YYYY-MM'` for month) so events join cleanly with no format wrangling at query time.
- Schema migration to add `events.period_month` (additive only; existing `events.period_id` reused for quarterly).
- Null `event_date` propagates to null in both output columns; no dedicated logging or error path.

## Non-goals

- Building the **county-month training panel** (cartesian product `dim_county × dim_time(monthly)` with event aggregates left-joined on). That is the natural next transform after this one and gets its own spec.
- Wiring this transform into Ticketmaster / RunSignUp / Setlist.fm `to_cleaned()` methods — each event source gets its own design + plan + integration cycle.
- Backfilling existing rows in the `events` table — there are no event rows yet (no event source is wired). The `events.period_month` column starts null after the migration; rows added after the transform lands will populate both columns at clean-time.
- Single-date convenience helper (`period_id_for_date(date) -> tuple[str, str]`). No caller needs it; add when something does.
- Custom calendar systems (fiscal year, ISO weeks, etc.). Strict Gregorian Q1-Q4.

## Design

### Module placement

`src/eia/transforms/temporal.py`. **Not** `time.py` — that name shadows Python's stdlib `time` module and creates confusing import errors when a contributor later writes `import time` inside the file.

Re-exported from `src/eia/transforms/__init__.py` alongside `attach_county_fips`:

```python
from eia.transforms.geo import attach_county_fips
from eia.transforms.temporal import attach_period_id

__all__ = ["attach_county_fips", "attach_period_id"]
```

### Public surface

```python
def attach_period_id(
    df: pl.DataFrame,
    *,
    date_col: str = "event_date",
    quarter_col: str = "period_id",
    month_col: str = "period_month",
) -> pl.DataFrame
```

Returns the input frame with two columns appended (both Utf8, both nullable). Original columns and order preserved; the two new columns are appended last. No row reordering.

### Internals

The implementation is a single Polars expression chain — no out-of-process roundtrip, no row-position tracking, no cache. Polars' `.dt.year()`, `.dt.quarter()`, and `.dt.strftime()` all propagate null naturally, so a null `event_date` produces null in both outputs without explicit gating:

```python
df.with_columns(
    pl.format(
        "{}Q{}",
        pl.col(date_col).dt.year(),
        pl.col(date_col).dt.quarter(),
    ).alias(quarter_col),
    pl.col(date_col).dt.strftime("%Y-%m").alias(month_col),
)
```

### Output formats

| Column | Format | Example | Joins to |
|---|---|---|---|
| `period_id` | `'YYYYQQ'` | `'2023Q3'` | `bls_qcew.period_id`, `dim_time` (where `is_quarter`) |
| `period_month` | `'YYYY-MM'` | `'2023-08'` | future state-DOR table, county-month panel, `dim_time` (where `is_month`) |

These formats are dictated by existing producers and consumers in the warehouse, not chosen here.

### Schema migration

New file `migrations/004_events_period_month.sql`:

```sql
ALTER TABLE events ADD COLUMN period_month VARCHAR;
CREATE INDEX IF NOT EXISTS ix_events_period_month ON events (period_month);
```

A new migration file (rather than editing 003 in place) is required because the warehouse migration system ([duckdb_impl.py:76](../../../src/eia/warehouse/duckdb_impl.py:76)) tracks applied migrations in a `_migrations` table and won't re-apply 003 in any environment that has already run it. The ALTER is additive — works whether or not the table currently has rows.

### Edge-case matrix

| Input | Output |
|---|---|
| Valid `event_date` | both columns set, formats per the table above |
| `event_date` is null | both columns null |
| `event_date` not parseable to a `Date` | unreachable — events DDL declares `event_date DATE NOT NULL`, and event sources' `to_cleaned()` is responsible for parsing before calling this transform; no defensive guard added here |

No INFO log summary. The spatial-join transform's logging exists because spatial misses are interesting and frequently signal data-quality problems upstream; date derivation has no analogous miss — null in produces null out, deterministically.

### Caller integration

Each event source's `to_cleaned()` will end with two composable transform calls:

```python
from eia.transforms import attach_county_fips, attach_period_id

df = attach_county_fips(df)
df = attach_period_id(df)
```

That's the entire integration surface. Either order works for correctness; the convention is spatial first, then temporal, matching how the events DDL declares the columns. No source-specific configuration is required for either transform.

## Testing

Unit tests in `tests/transforms/test_temporal.py`:

- **Quarter boundaries** — `2023-01-01` → `'2023Q1'`, `2023-04-01` → `'2023Q2'`, `2023-07-01` → `'2023Q3'`, `2023-10-01` → `'2023Q4'`. Same dates check `period_month` is the corresponding `'2023-MM'`.
- **Mid-quarter sanity** — `2023-08-15` → `('2023Q3', '2023-08')`; `2023-12-31` → `('2023Q4', '2023-12')`.
- **Year boundary** — `2024-01-01` → `('2024Q1', '2024-01')` (proves no off-by-one on year computation).
- **Null date** — null in → both columns null.
- **Custom column names** — caller passes `quarter_col="quarter"`, `month_col="month"`; output columns named accordingly, defaults still work in their default positions.
- **Realistic events-shape passthrough** — multi-column input frame; assert original columns unchanged, two new columns appended last.
- **Public import smoke test** — `from eia.transforms import attach_period_id` resolves and works.

No fixture-based monkeypatching needed (the transform has no external dependencies — pure Polars expressions on the input frame).

The migration itself doesn't need its own test beyond what `make warehouse-init` already exercises. The Phase 0 warehouse smoke tests in [tests/test_warehouse.py](../../../tests/test_warehouse.py) cover migration application generically; this migration is a one-line ALTER and adds no new behavior worth a dedicated test.

## Operational notes

- **Polars datetime API.** `pl.col(...).dt.year()`, `.dt.quarter()`, and `.dt.strftime("%Y-%m")` all return null when the input is null. No explicit gating required.
- **Format compatibility.** The quarter format `'YYYYQQ'` is already produced by [bls_qcew.py:119](../../../src/eia/sources/bls_qcew.py:119) using the same `pl.format("{}Q{}", year, quarter)` pattern. Using the same expression here guarantees byte-identical strings, so `JOIN ... USING (county_fips, period_id)` works without a CAST.
- **Performance.** Expression-only; no Python-level iteration, no out-of-process roundtrips. Phase 1 corpus (~50k events) processes in well under a second.

## References

- [docs/superpowers/specs/2026-05-09-spatial-join-transform-design.md](2026-05-09-spatial-join-transform-design.md) — sibling transform whose pattern this mirrors at the call site.
- [docs/data_acquisition_strategy.md](../../data_acquisition_strategy.md) — Phase 1 county-month evaluation requirement that drives the monthly column.
- [migrations/001_dimensions.sql](../../../migrations/001_dimensions.sql) — `dim_time` schema with both quarter (`'YYYYQQ'`) and month (`'YYYY-MM'`) entries.
- [migrations/003_events.sql](../../../migrations/003_events.sql) — events table with `event_date` and `period_id`.
- [src/eia/sources/bls_qcew.py](../../../src/eia/sources/bls_qcew.py) — reference producer of `'YYYYQQ'` period strings.
