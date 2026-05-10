# BEA Use+Make XLSX Parsing

**Date:** 2026-05-09
**Phase:** 1 (model-side foundation — final federal piece)
**Status:** Design approved (autonomous run); awaiting implementation plan

## Background

The existing BEA source ([src/eia/sources/bea_io.py](../../../src/eia/sources/bea_io.py)) downloads BEA's `AllTablesIO.zip` (already on disk at `data/raw/bea-io/AllTablesIO.zip`, 16 MB) but its `to_cleaned()` is explicitly a stub — it only writes a manifest of filenames inside the zip. The `bea_io_use` and `bea_io_make` warehouse tables defined in [migrations/002_economic_backbone.sql](../../../migrations/002_economic_backbone.sql) are empty. Without real Use/Make data, the `compute_leontief_inverse` function in `src/eia/multipliers/leontief.py` has nothing to consume.

This spec covers completing the source: parse Use + Make at Summary aggregation level for all years 1997–2023, land them long-form into the existing warehouse tables.

## Goals

- Replace the manifest stub in `BEAIO.to_cleaned()` with real XLSX parsing for both the Use and Make Summary tables.
- Output two long-form Polars parquets matching the existing migration schemas: `(table_year, industry_code, commodity_code, value_millions, fetched_at)`.
- Cover all 27 years (1997–2023) in one source pull. Each XLSX file contains one sheet per year.
- Drop the placeholder `bea_io_manifest` table during load — it was a stub and is no longer needed.

## Non-goals

- **Final-demand columns** (Personal Consumption Expenditures, exports, government, imports). Per the brainstorm's locked scope (a) — industry × commodity matrix only. Final-demand can be added later in a separate cycle without changing the multiplier behavior.
- **Value-added footer rows** (compensation of employees, taxes on production, gross operating surplus). Same — scope (a).
- **Detail aggregation level** (~405 industries). Only available for 2017 (single year), low value for time-varying analysis.
- **Sector aggregation level** (~21 industries). Too coarse for industry-level multipliers.
- **Before-Redefinitions** variants. After-Redefinitions is BEA's preferred for analytical use.
- **Purchasers' price (PUR)** Use tables. Producers' price (PRO) is BEA's standard for I-O multiplier analysis; what `compute_leontief_inverse` expects.
- **CxI_DR pre-computed direct-requirements** matrix. BEA publishes `A = D @ B` already, but we compute it ourselves from U and V (more general; validates our math).
- **Detail-level XLSX** (`*_2017_Detail.xlsx`). Single-year, low time-series value.
- **Multi-region (RIMS II) regional multipliers.** BEA publishes those as a separate paid product; not in this zip.

## Design

### File selection (locked)

| Purpose | File |
|---|---|
| Use table | `IOUse_After_Redefinitions_PRO_1997-2023_Summary.xlsx` |
| Make table | `IOMake_After_Redefinitions_PRO_1997-2023_Summary.xlsx` |

Both inside `data/raw/bea-io/AllTablesIO.zip`. Each XLSX has 27 sheets named `'1997'` … `'2023'`.

### Sheet layout (verified by inspection of the 2023 Use sheet, 88 rows × 96 cols)

| Row | Content |
|---|---|
| 1 | Title, e.g. `"The Use of Commodities by Industries..."` (skip) |
| 2 | Units: `"(Millions of dollars)"` (skip) |
| 3 | Attribution: `"Bureau of Economic Analysis"` (skip) |
| 4 | Year: `"2023"` (skip; year comes from sheet name) |
| 5 | Column header: col 2 = `"Commodities/Industries"`, cols 3+ = industry IOCodes (`111CA`, `113FF`, `211`, …) |
| 6 | Column names: col 1 = `"IOCode"`, col 2 = `"Name"`, cols 3+ = industry names |
| 7+ | Data: col 1 = commodity IOCode, col 2 = commodity name, cols 3+ = `value_millions` |

Make is structurally identical except its rows are industries and its columns are commodities (no final-demand expansion, fewer columns).

### Industry/commodity identification — cross-reference against Make

Two viable strategies were considered (pattern-match on IOCode prefix vs cross-reference). The cross-reference approach was chosen for robustness:

1. **Parse Make first.** Extract the set of row IOCodes (industries) and column IOCodes (commodities). Make is structurally clean — no final-demand or value-added expansions.
2. **When parsing Use:** keep only columns whose code is in the industries set, only rows whose code is in the commodities set. Final-demand columns and value-added rows naturally fall out.

If a Use column code is NOT found in Make's industries set, log a WARNING with the code (it's an anomaly worth noting) and exclude it. Same for rows.

### Public surface (extending the existing source class)

`BEAIO.to_cleaned(raw_path: Path) -> Path` is reimplemented:

1. Open the zip; for each of the two target files, extract to a temp file (fastexcel reads from path, not bytes).
2. Call `_industries_and_commodities_from_make(make_path)` → `(industries: set[str], commodities: set[str])`.
3. For each year 1997–2023:
   - `_parse_make_year(make_path, year, industries, commodities)` → DataFrame rows.
   - `_parse_use_year(use_path, year, industries, commodities)` → DataFrame rows.
4. Concatenate per-year frames into one `use_master` and one `make_master` DataFrame.
5. Write `use_summary.parquet` and `make_summary.parquet` to `cleaned_dir`.
6. Return the use parquet path (canonical; make path is at the known sibling location).

`BEAIO.load(cleaned_path: Path, warehouse: Warehouse)` is reimplemented:

1. Register `bea_io_use` from `cleaned_path` (the use parquet), replace=True.
2. Compute the make path from `cleaned_path.parent / cleaned_path.name.replace("use_", "make_")`.
3. Register `bea_io_make` from the make path, replace=True.
4. Drop the legacy `bea_io_manifest` table if it exists (`DROP TABLE IF EXISTS bea_io_manifest`).

### Output schema (matches existing migration)

```
table_year       SMALLINT   NOT NULL  -- e.g. 2023
industry_code    VARCHAR    NOT NULL  -- BEA IOCode, e.g. "111CA"
commodity_code   VARCHAR    NOT NULL  -- BEA IOCode, e.g. "111CA" or "S00101"
value_millions   DOUBLE                -- BEA reports in millions; null where source is blank
fetched_at       TIMESTAMP  NOT NULL
PRIMARY KEY (table_year, industry_code, commodity_code)
```

For Make: same schema; `industry_code` and `commodity_code` interpretation is "industry i produces commodity c with this dollar value." For Use: "industry j consumes commodity c with this dollar value." Stored identically; the table name disambiguates semantics.

### Edge cases

| Input | Output |
|---|---|
| Cell is blank or null | `value_millions` = null in long-form |
| Cell is `"..."` (BEA suppression marker) | `value_millions` = null |
| Cell is a numeric string with whitespace | coerced to float; non-numeric strings raise |
| Year sheet missing in either file | logged as ERROR; that year skipped (defensive — shouldn't happen in 1997–2023 file) |
| Use column code not in Make industries set | logged as WARNING; column dropped from output |
| Use row code not in Make commodities set | logged as WARNING; row dropped from output |
| Negative values | preserved (BEA tables can have negative entries from valuation adjustments) |

### Logging

`logger.info` per major phase: "Parsed Make: 71 industries, 73 commodities", "Parsed Use 2023: 5183 cells", "Wrote use_summary.parquet (135K rows)". Total summary at end: "BEA Use: 27 years × 5183 cells = 139,941 rows; BEA Make: 27 years × ~5K cells = ~140K rows".

### Dependencies

- **`fastexcel`** — runtime dependency. Already added to `pyproject.toml` as `fastexcel>=0.20.2`. Polars uses it as the default engine for `pl.read_excel(...)`.
- **`openpyxl`** — dev-only dependency. Used by tests to generate synthetic XLSX fixtures. To be added to `[project.optional-dependencies] dev`.

## Testing

`tests/sources/test_bea_io.py`:

- **Fixture builder helper** that creates a synthetic 3-industry × 4-commodity Use+Make XLSX in-memory using `openpyxl`. Optionally with N year sheets, optionally with extra final-demand columns and value-added rows. Returns a Path to the constructed zip.
- **Test: 2-year, 3-industry Use+Make stacking.** Build a fixture with 2 year sheets in each file. Run `BEAIO().to_cleaned(zip_path)` (with monkeypatched `eia.config.settings.eia_data_root` so writes go to `tmp_path`). Assert the output `use_summary.parquet` has `2 × 3 × 4 = 24` rows; same for Make. `table_year` distribution is 12+12.
- **Test: final-demand columns are excluded from Use.** Fixture: Use sheet has 3 industry columns AND 1 final-demand column (e.g. `F010`). Make sheet has 3 industry rows. Output Use parquet has only 3 industries × 4 commodities = 12 rows per year (the F010 column is dropped because it's not in Make's industries set).
- **Test: value-added rows are excluded from Use.** Fixture: Use sheet has 4 commodity rows AND 1 value-added row (e.g. `V001`). Output Use parquet has only the 4 commodity rows per year.
- **Test: Make is structurally identical** — its rows are industries, its columns are commodities. Output schema and values match expected.
- **Test: blank cell → null.** Fixture has one cell explicitly blank; output `value_millions` is null in that row.
- **Test: load() drops bea_io_manifest.** Pre-create a `bea_io_manifest` table in a tmp warehouse; run `BEAIO().load(...)` after `to_cleaned`; assert `bea_io_manifest` no longer exists.
- **Test: load() populates bea_io_use and bea_io_make.** End-to-end through a tmp warehouse; assert both tables have the expected row counts.
- **Test: anomaly warning** — Use has a column code that isn't in Make's industries set. The output excludes that column AND `caplog` records a warning naming the code.

## Operational notes

- **Performance.** Use file is 1.07 MB, Make is 0.55 MB. Reading 27 sheets × 2 files = 54 sheet reads via fastexcel. Each sheet is small (88×96 max). Total parse time should be 5–15 seconds. Negligible.
- **Memory.** Largest intermediate frame is ~140K rows × 5 columns of small types. Trivial.
- **Idempotence.** The fetch is idempotent (existing zip skipped). The clean and load steps overwrite/replace, so re-running BEA pulls is safe.
- **Output validation.** After the real run lands the data, the multiplier engine can be sanity-checked by computing `compute_leontief_inverse(use, make)` for a single year and comparing diagonal multipliers (should all be ≥ 1) against BEA's published Total Requirements multipliers in the `CxI_DR_*` file. (Not part of this spec; just an obvious follow-up validation.)

## References

- [docs/superpowers/specs/2026-05-09-leontief-multipliers-design.md](2026-05-09-leontief-multipliers-design.md) — the multiplier engine that consumes this output.
- [src/eia/sources/bea_io.py](../../../src/eia/sources/bea_io.py) — the existing source class being extended.
- [migrations/002_economic_backbone.sql](../../../migrations/002_economic_backbone.sql) — `bea_io_use` and `bea_io_make` table schemas.
- BEA Methodology, "Concepts and Methods of the U.S. Input-Output Accounts" — describes the After-Redefinitions producer-price formulation used here.
