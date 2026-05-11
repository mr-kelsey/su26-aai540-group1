# State DOR Inventory

**Date:** 2026-05-10
**Source:** Parallel survey of state Department of Revenue (or equivalent) data publication patterns.

## Bottom-line reality

Most US states do NOT publish quarterly county-level taxable-sales data via a clean API. CDTFA (CA) is exceptional. For Y-target coverage across all 50 states, the practical strategy is **federal aggregator (Census STC) + deep DOR sources where they exist**.

## Tier 1 — Deep, county-level, accessible (rare)

| State | Source | Format | Granularity | History | Status |
|---|---|---|---|---|---|
| California | CDTFA OData API | JSON, no auth | County × quarter × business type | 2015 Q1 - 2025 Q4 | ✅ implemented |
| Nebraska | revenue.nebraska.gov/research/statistics/sales-tax-data | Excel | County × business type, annual | 2002-2025 | candidate |

## Tier 2 — State-level downloadable, no auth (good for Census-fallback hybrid)

| State | Source | Format | Granularity | History |
|---|---|---|---|---|
| Nevada | tax.nv.gov/news-publications/statistics/taxable-sales-statistics | Excel + PDF | State, monthly | 2023+ |
| Utah | tax.utah.gov/commission/econstats | PDF/Excel | State, quarterly | available |
| Hawaii | tax.hawaii.gov/data-dashboard | Dashboard CSV/JSON | State, monthly/quarterly | 2023+ |
| New Mexico | tax.newmexico.gov RP-80 Reports | PDF/HTML | Quarterly by geography + NAICS | unknown |
| Idaho | tax.idaho.gov reports | PDF | County, annual | unknown |

## Tier 3 — Socrata/CKAN portal exists, dataset not yet located

These have open-data infrastructure but the specific taxable-sales dataset wasn't surfaced by a high-level survey — deeper search of the portal catalog might find them.

- Missouri (data.mo.gov)
- Colorado (data.colorado.gov)
- Virginia (data.virginia.gov + Weldon Cooper Center)
- Washington (data.wa.gov)
- Vermont (data.vermont.gov)
- Texas (data.texas.gov, comptroller.texas.gov/transparency)
- Maryland (opendata.maryland.gov)
- Massachusetts (data.mass.gov)
- DC (opendata.dc.gov)

## Tier 4 — Hard / PDF-only / no programmatic access found

These publish via PDF reports on agency websites. Programmatic extraction would require PDF parsing or direct agency contact. Roughly 25 states fall here:

NY, FL, IL, PA, OH, GA, NC, MI, NJ, IN, TN, AZ, WI, MN, SC, AL, LA, KY, OK, CT, IA, KS, AR, MS, WV, ND, SD, ME, RI, WY.

## Tier 5 — No state sales tax (exclude)

These five have no state sales tax at all. There is no DOR taxable-sales dataset to pull:

- Oregon
- Alaska (some local sales taxes exist; no state-level data)
- Montana (some local resort taxes; no state-level data)
- New Hampshire (has Business Profits Tax instead)
- Delaware

## Implementation strategy

Given the above, "all 50 states going back to 2015" is achievable as follows:

1. **Universal floor:** [Census Bureau State Tax Collections (STC)](https://www.census.gov/programs-surveys/stc.html) publishes annual state-level tax collections by category for all 50 states + DC, going back decades. This is the universal Y baseline. Granularity: state × year × tax category (sales tax is one column).

2. **Deep CA** (already done): `cdtfa_taxable_sales` — county × quarter × 12 business types, 2015-2025.

3. **Add deep where accessible:** Nebraska (county × business type × year), Nevada (state × month), Utah, Hawaii. ~3-5 sources, each ~2-4 hours of work.

4. **Unified table:** `state_dor_taxable_sales` with rows at varying granularity. Schema:
   ```
   state_fips, state_code, period_id, period_granularity,
   county_fips (nullable), geo_granularity,
   raw_category (nullable), normalized_category,
   taxable_sales_usd, source_name, fetched_at
   ```
   Model layer can filter by `(geo_granularity, period_granularity)` to pick the right tier.

## What's NOT viable in autonomous mode

Tiers 3 and 4 require either deep human investigation per portal or PDF parsing infrastructure. Both are reasonable follow-up work for the team (each Tier 3 state is probably 1-2 hours of focused search; each Tier 4 state is 3-6 hours including PDF extraction). Not blocking the model — Census STC covers them at the state-annual level.

## Open avenues for the future

- **Census Bureau Quarterly Tax Survey (QTAX)** — state-level quarterly tax collections, all 50 states + DC. Possibly a better fit than STC for the model's quarterly horizon.
- **State open-data portal catalog scraping** — given how many states use Socrata, a script that queries each portal's `/api/views.json` and grep'd for "sales" or "tax" could surface the Tier-3 hidden datasets.
- **PDF extraction pipeline** — `tabula-py` + per-state config for the ~25 PDF-only states. Big lift but predictable.
