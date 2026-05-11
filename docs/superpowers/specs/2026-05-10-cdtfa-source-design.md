# CDTFA California Taxable Sales Source

**Date:** 2026-05-10
**Phase:** 1 (Y-target acquisition — first state DOR)
**Status:** Design approved (autonomous; user pre-authorized decisions)

## Background

The economic-impact model needs an **outcome variable** (Y): county × month/quarter taxable economic activity that can be regressed against event presence + economic-context features. Per [docs/data_acquisition_strategy.md](../../data_acquisition_strategy.md) §5, state DOR (Department of Revenue) sales-tax data is the canonical Y target — *"the project's biggest structural challenge"* and *"the fastest signal we get is monthly state DOR sales/lodging tax data with a 2–6 month lag."*

This spec covers the **first state DOR scraper**: California's CDTFA (Department of Tax and Fee Administration), Taxable Table 3 ("Taxable Sales by County, by Type of Business"). California gives us national-significant geographic coverage in one source, and CDTFA happens to publish via a clean OData API — no PDF parsing, no auth, no rate limit.

## Empirical recon findings

Probed 2026-05-10 against `https://cdtfa.ca.gov/dataportal/api/odata/`.

- **Endpoint:** `GET /Taxable_Sales_Counties?$top=100000`
- **Auth:** none
- **Format:** OData JSON (`{ "@odata.context": "...", "value": [ {...row...}, ... ] }`)
- **Volume:** 30,624 rows, ~3 MB, fits in a single GET (the server respects arbitrary `$top`)
- **Coverage:** 58 California counties × ~12 business types × ~44 quarters (2015 Q1 through 2025 Q4)
- **Lag:** ~5 months (latest data is 2025 Q4, we're in May 2026)
- **Row shape (sample):**

```json
{
  "CalendarYear": 2015, "Quarter": "Q1",
  "QuarterMonthfrom": 1, "QuarterMonthto": 3,
  "County": "ALAMEDA", "CountyCode": 1,
  "BusinessGroupCode": "C01",
  "BusinessType": "Motor Vehicle and Parts Dealers",
  "PermitDate": "2015-03-31",
  "NumberOfPermits": 1347,
  "TaxableTransactions": 931405774,
  "DisclosureFlag": null
}
```

## Goals

- Pull all 30,624 rows of CDTFA Taxable Table 3 into a `cdtfa_taxable_sales` warehouse table.
- Map CDTFA's `County` (e.g. `"ALAMEDA"`) to standard 5-digit FIPS (`"06001"`) by joining against `dim_county` on normalized name + `state_fips='06'`.
- Derive `period_id` in the project-standard `'YYYYQQ'` format (e.g. `'2025Q4'`) so joins to `bls_qcew` and `events` work natively.
- Add a `pull-cdtfa` Makefile target and CLI registration.

## Non-goals (deferred)

- **Other states.** Texas, NY, FL, etc. each need their own source class (no standard format across states). This spec only covers CA. A future generic "DOR" coordination layer can come later if patterns emerge after we have 3+ states.
- **Unified `dor_outcomes` aggregation table.** YAGNI — one state's data lives under one source name. If we add 5+ states, we'll revisit.
- **Sub-quarterly granularity.** CDTFA publishes quarterly; monthly state-level totals exist in a different table (`SUTCashReceiptsByIndustry`) but lack county breakdown, so they don't fit our model.
- **Pre-2015 data.** CDTFA's OData API only goes back to 2015 Q1. Older historical data would need different scraping.
- **NAICS reconciliation.** CDTFA's `BusinessGroupCode` (C01–C12) is *not* NAICS. Mapping CDTFA business types to NAICS sectors (so we can join to BLS QCEW industry-level data) is interesting model-side work but doesn't belong in the ingestion source — we preserve both code and human-readable description and let the model layer decide.
- **City-level data.** CDTFA also publishes `Taxable_Sales_by_City` etc.; out of scope (we key on county FIPS).

## Architecture

Same Source ABC pattern as the federal sources (`bls_qcew.py`, `census_acs.py`):

```
fetch()      -> single GET, write JSON to raw_dir/taxable_sales_counties.json
to_cleaned() -> parse JSON, normalize, county-FIPS lookup, write parquet
load()       -> base class default (register_table_from_parquet)
```

### Output schema (`cdtfa_taxable_sales`)

```sql
CREATE TABLE IF NOT EXISTS cdtfa_taxable_sales (
    table_year          SMALLINT    NOT NULL,         -- e.g. 2025
    quarter             SMALLINT    NOT NULL,         -- 1-4
    period_id           VARCHAR     NOT NULL,         -- 'YYYYQQ', e.g. '2025Q4'
    county_fips         CHAR(5),                      -- nullable if name lookup fails
    cdtfa_county_code   SMALLINT    NOT NULL,         -- preserved for traceability
    cdtfa_county_name   VARCHAR     NOT NULL,         -- 'ALAMEDA' raw from CDTFA
    business_group_code VARCHAR,                      -- 'C01', 'C02', ...
    business_type       VARCHAR,                      -- human-readable description
    permit_count        INTEGER,
    taxable_sales_usd   BIGINT,                       -- raw dollars (not millions)
    disclosure_flag     VARCHAR,                      -- NULL or 'D' for suppressed cells
    fetched_at          TIMESTAMP   NOT NULL,
    PRIMARY KEY (period_id, cdtfa_county_code, business_group_code)
);
CREATE INDEX IF NOT EXISTS ix_cdtfa_county ON cdtfa_taxable_sales (county_fips);
CREATE INDEX IF NOT EXISTS ix_cdtfa_period ON cdtfa_taxable_sales (period_id);
```

`taxable_sales_usd` uses raw dollars (e.g. 931_405_774). BIGINT because values reach low billions for big counties.

### County-FIPS mapping

CDTFA's `CountyCode` is its own 1-58 sequential scheme, NOT FIPS. To get standard `county_fips` for joins, normalize the `County` name and look up in `dim_county`:

```python
# Pseudocode
normalized = pl.col("County").str.to_lowercase().str.replace(r" county$", "")
df = df.join(
    dim_county.filter(pl.col("state_fips") == "06").select(
        pl.col("county_fips"),
        pl.col("county_name").str.to_lowercase().str.replace(r" county$", "").alias("_norm_name"),
    ),
    left_on=normalized,
    right_on="_norm_name",
    how="left",
)
```

CDTFA's spellings to watch for:
- `"SAN FRANCISCO"` → `"San Francisco County"` in dim_county
- `"LOS ANGELES"` → `"Los Angeles County"`
- `"SAN LUIS OBISPO"` (multi-word) → `"San Luis Obispo County"`

Multi-word names work fine after `.lower() + strip " county"`. Log a WARNING if any `cdtfa_county_name` ends up with `county_fips IS NULL`.

### CLI / Makefile integration

- CLI: `eia pull cdtfa-taxable-sales` (auto-registered via `register(...)` at module-import time; corresponding line added to `_register_pull_commands` in `src/eia/cli.py`).
- Makefile: `pull-cdtfa` invoking that command.

## Edge cases

| Input | Output |
|---|---|
| `DisclosureFlag = "D"` (suppressed) | Row preserved; `taxable_sales_usd` is whatever CDTFA returns (typically 0 or null) — `disclosure_flag` column carries the marker |
| `TaxableTransactions = null` | `taxable_sales_usd = null` |
| `County` name not found in dim_county | `county_fips = null`, logged as WARNING |
| Re-running `pull-cdtfa` | Default `replace=True` register replaces the table — full refresh each time (cheap: 30K rows) |
| API endpoint 5xx | `RateLimitedClient` retries with tenacity backoff |

## Testing (`tests/sources/test_cdtfa.py`)

- **`_parse_row` happy path** — synthetic OData JSON row → expected schema dict
- **`_parse_row` null disclosure** — `DisclosureFlag = null` → `disclosure_flag = None`
- **`_parse_row` disclosure flag** — `DisclosureFlag = "D"` → `disclosure_flag = "D"`
- **`_parse_row` malformed quarter** — `Quarter = "Q5"` → row skipped, logged as warning
- **`_period_id` formatting** — (2024, "Q3") → "2024Q3"
- **county-FIPS lookup happy path** — fixture `dim_county` with "Alameda County", "Los Angeles County" → "ALAMEDA" maps to 06001
- **county-FIPS lookup missing** — "MARS" → null FIPS, no exception
- **`to_cleaned` end-to-end** — synthetic JSON with 5 rows × 3 counties → expected parquet schema and row count
- **`fetch` (monkeypatched httpx)** — assert single GET to `/Taxable_Sales_Counties` with `$top=100000`

No "live" API integration test — the real source pulls 30K rows on first use and that itself is the validation.

## Operational notes

- **Pull cost:** single GET, ~3 MB JSON, ~5 seconds. Negligible.
- **Memory:** 30K rows × small types → trivial. Polars DataFrame fits in <50 MB.
- **Idempotence:** safe to re-pull anytime. We get the latest published quarter (currently 2025 Q4).
- **Refresh cadence:** quarterly, ~5 month lag. After the model is built, a monthly cron checking for new quarters is enough.

## References

- CDTFA OData catalog (75 datasets): `https://cdtfa.ca.gov/dataportal/api/odata/Catalog`
- Source we're using: `Taxable_Sales_Counties` (Taxable Table 3)
- Sister source if we ever need state-level monthly: `SUT_Cash_Receipts_By_Industry`
- [docs/data_acquisition_strategy.md](../../data_acquisition_strategy.md) §5 — temporal-lag mechanics
- [migrations/001_dimensions.sql](../../../migrations/001_dimensions.sql) — `dim_county` schema (FIPS lookup target)
- [src/eia/sources/census_acs.py](../../../src/eia/sources/census_acs.py) — closest existing source pattern (JSON API + dimension join)
