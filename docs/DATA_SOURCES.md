# Data Sources — Project Summary

Audience: AAI-540 Group 1 partners. This is the "what's in the warehouse and what does it mean" doc.

The Bayesian regression we're building needs three categories of data:

- **Features (X)** — geographic + economic + temporal context for each county
- **Events** — what concerts, sports, marathons happened where and when
- **Outcomes (Y)** — taxable sales activity we'll regress against the events

Everything is keyed on `county_fips` (CHAR(5), e.g. `'06073'` for San Diego) and `period_id` (e.g. `'2023Q3'`), so all joins are clean.

---

## 1. Federal feature data (X)

These are the **inputs** describing each county's baseline economy. The model uses them as features alongside event counts.

### 1.1 `dim_county` — 3,235 rows
- **Source:** US Census Bureau TIGER/Line county shapefiles, 2023 vintage. https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html
- **What it is:** Every US county with its centroid lat/lon, land area, state FIPS, and human-readable name.
- **Granularity:** one row per county.
- **Why we have it:** the universal join key. Every event, every Y-target row joins back here. Also lets us spatially attach events from lat/lon to a county FIPS.
- **Coverage:** all 50 states + DC + territories.

### 1.2 `census_acs_county` — 3,144 rows
- **Source:** US Census Bureau, American Community Survey 5-year estimates (latest). https://api.census.gov/data.html
- **What it is:** Demographic and economic baseline per county — population, median household income, median age, % bachelor's-or-higher educated.
- **Granularity:** one row per county.
- **Why we have it:** features that distinguish counties (e.g., income elasticity of event impact).
- **Coverage:** all 50 states + DC (Puerto Rico's 91 counties + small territories miss from the 3,235 total).

### 1.3 `bls_qcew` — 104,075,464 rows
- **Source:** US Bureau of Labor Statistics, Quarterly Census of Employment and Wages (QCEW). https://www.bls.gov/cew/
- **What it is:** Employment counts, total wages, average weekly wages, and establishment counts for every (county, quarter, industry NAICS code, ownership type) tuple. Industry NAICS goes from 2-digit (sectors like "72 - Accommodation and Food Services") down to 6-digit (specific industry codes).
- **Granularity:** county × quarter × NAICS × ownership_code (private/federal/state/local).
- **Why we have it:** the **local economic baseline**. For each event location, we know how big the accommodation industry already is in that county — the model adjusts impact predictions by sector size.
- **Coverage:** 2015 Q1 → 2023 Q4 (9 years × 4 quarters), 3,277 distinct counties (more than dim_county because QCEW includes some non-standard reporting areas).

### 1.4 `bea_io_use` + `bea_io_make` — 139,941 rows each
- **Source:** US Bureau of Economic Analysis, Input-Output Accounts (After-Redefinitions, Producer's Price, Summary level). https://www.bea.gov/industry/input-output-accounts-data
- **What it is:** The "Use" table records dollars of commodity *c* used by industry *j*; the "Make" table records dollars of commodity *c* produced by industry *i*. Together they form the foundation of the **Leontief inverse multiplier matrix** L = (I - A)^-1 where A = D @ B (D is the market-shares matrix from Make, B is the direct-requirements matrix from Use).
- **Granularity:** annual × industry × commodity (71 industries × 73 commodities × 27 years).
- **Why we have it:** **Multipliers.** When a concert drives $1 of direct spending in food service, the Leontief matrix tells us how much *total* downstream economic activity that creates (suppliers, distributors, labor income, etc.). This is what justifies calling our output an "economic impact" rather than just "direct spending."
- **Validation:** our computed direct-requirements matrix B has been cross-checked against BEA's published `CxI_DR_*_Summary.xlsx` for every year 1997-2023; max coefficient difference is ~3.4e-5 (rounding-precision agreement).
- **Coverage:** 1997-2023.

### 1.5 `hud_zip_county` — **6 rows (placeholder)**
- **Source:** Department of Housing and Urban Development, USPS ZIP-County Crosswalk. https://www.huduser.gov/portal/datasets/usps_crosswalk.html
- **What it is:** Maps each ZIP code to its primary county FIPS (and the share of residential addresses in that county).
- **Granularity:** ZIP × quarter × county (a single ZIP can split across counties).
- **Why we have it:** **Fallback geocoder.** If an event arrives with only a ZIP code (no lat/lon), we use this to attach it to a county. Today: not yet populated for real (requires a free HUD signup) — only 6 hand-curated San Diego ZIPs as placeholder.
- **Coverage:** placeholder only. Real pull is ~5 min of signup at huduser.gov; the source class is ready.

---

## 2. Event data (X — variable input)

These are the **events** whose impact we're trying to model.

### 2.1 `events` — 78,226 rows (unified)
- **Source:** UNION of `ticketmaster_events` + `setlistfm_setlists`, mapped onto a shared schema and joined to `county_fips` via `dim_county` lat/lon spatial join.
- **What it is:** Each row is one event with `event_date`, `venue_*`, `county_fips`, `period_id` (quarterly), `period_month`, `source`, `category` (`concert` / `sport` / `marathon` / etc.), optional ticket price range.
- **Built by:** `make build-events`, which reads the staging tables and applies `attach_county_fips` + `attach_period_id` transforms.

### 2.2 `ticketmaster_events` — 200 rows
- **Source:** Ticketmaster Discovery API v2. https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/
- **What it is:** Concerts, sports, family events, theater. Each has venue lat/lon, dates, ticket price range (min/max), category classification.
- **Granularity:** per-event.
- **Why we have it:** Real-time event source — captures upcoming events with structured metadata.
- **Critical caveat:** The Discovery API is **forward-only**. Querying historical years returns 0 events. Empirically tested: 2022/2023/2024 returned 0; 2026 returned 49,000+. We can use Ticketmaster as a *forward-looking inference source* (predict impact of upcoming events) but **NOT for training data**, which needs events ≥24 months old paired with realized tax outcomes.
- **Coverage:** the most recent 30-day window only (200 events in current snapshot).

### 2.3 `setlistfm_setlists` — 78,027 rows (and growing)
- **Source:** Setlist.fm Search API. https://api.setlist.fm/docs/1.0/index.html
- **What it is:** Crowd-sourced confirmed concerts — artist, venue, city, date, songs played. The historical backbone of the events corpus.
- **Granularity:** per-setlist (one row = one concert at one venue).
- **Why we have it:** **Setlist.fm is our historical event source.** It has 10+ years of confirmed concerts, which is what Ticketmaster can't give us. This is what the model trains on.
- **Pull status:** 26 of 51 states complete (the alphabet's first half, AK→MS). Daily-quota-constrained pull continues drip-style over the next few days; full coverage estimated ~2 days out.
- **Coverage:** 2022 (full year, the test slice). Goal: 2015-2023 once we widen the year range after Phase 1.
- **Caveat:** Setlist.fm provides *no attendance data* — only "show happened here." Attendance proxy must come from venue capacity or another source.

---

## 3. Outcome data (Y target)

Tax-revenue data the model regresses event activity against. **All three layers are stacked** — the model can use whichever level of detail is available for a given county-period.

### 3.1 `census_state_tax_collections` — 14,217 rows (the universal floor)
- **Source:** US Census Bureau, Annual Survey of State Government Tax Collections (STC). https://www.census.gov/programs-surveys/stc.html
- **What it is:** Tax revenue by state × year × tax category. Of particular relevance: `item_code = 'T09'` = General Sales and Gross Receipts Tax (the primary Y); `T15` = Amusements Sales Tax (event-specific); plus alcohol, motor fuels, tobacco, insurance sales taxes.
- **Granularity:** state × year × tax category.
- **Why we have it:** **Coverage for every state.** This is the universal Y floor — every state, every year (2016-2024). When state DOR data isn't directly accessible (most states), this is the fallback Y.
- **Coverage:** all 50 states + DC for 2016-2024.
- **Caveat:** state-level annual only. Within-state, within-year variation requires deeper sources.

### 3.2 `cdtfa_taxable_sales` — 30,624 rows (deepest source we have)
- **Source:** California Department of Tax and Fee Administration, Taxable Sales by County by Type of Business (Taxable Table 3). OData JSON API at https://cdtfa.ca.gov/dataportal/api/odata/Taxable_Sales_Counties
- **What it is:** Taxable sales (dollars) by California county × quarter × business type (12 categories: Motor Vehicle Dealers, Food Services and Drinking Places, Clothing Stores, Gasoline Stations, etc.).
- **Granularity:** county × quarter × business_type.
- **Why we have it:** **Best Y granularity in the warehouse, for California.** Quarterly data with sector breakdown means we can attribute event impact to specific sectors (e.g., concerts → restaurants).
- **Coverage:** 2015 Q1 → 2025 Q4 (11 years × 4 quarters), all 58 CA counties, 100% FIPS-mapped.
- **Why CA only:** CDTFA happens to publish a clean OData API. Most other states' DOR data is PDFs or buried — see [`docs/state_dor_inventory.md`](state_dor_inventory.md).

### 3.3 `tx_comptroller_county_allocations` — 19,944 rows
- **Source:** Texas Comptroller, "Sales Tax Allocation: County, MTA, SPD" via the Texas Open Data Portal. https://data.texas.gov/resource/qsh8-tby8.json
- **What it is:** Monthly local sales tax dollars allocated back to each Texas county (the county's share of the 2% local sales tax).
- **Granularity:** county × month.
- **Why we have it:** Best Y proxy for Texas at sub-state granularity. Not raw taxable sales, but proportional to sales activity (each county's allocation = its taxable sales × the county's local rate).
- **Coverage:** 2013-01 through 2026-05, 100% FIPS-mapped.
- **Major caveat:** **Only counties that adopted a county-level sales tax appear.** Harris (Houston), Dallas, and Travis (Austin) counties don't have a county sales tax — they fund through cities/MTAs instead. So our TX data misses the largest urban centers in their county form. State-level totals via Census STC fill that gap.

---

## 3.5 `venue_capacities` (Silver, reference) — 2,673 rows

- **Source:** Hand-curated SEED dict + WikiData SPARQL scrape + name-keyword heuristic. Every distinct CA venue appearing in `setlistfm_setlists` gets a capacity estimate, attributed to one of four tiers (in priority order: SEED, WikiData, heuristic, flat default).
- **What it is:** A row per CA venue (keyed by Setlist.fm `venue_id`) with an estimated attendance capacity and the source of that estimate.
- **Granularity:** one row per (`venue_id`, `venue_name`, `city_name`) — venues with the same name in different cities (e.g., "Goldfield Trading Post" in Roseville vs. Sacramento) are separate rows.
- **Why we have it:** **The whole reason event magnitude is observable.** Setlist.fm doesn't publish attendance — it's a post-concert listing service — so without this table we'd have no signal for event *size*. The Gold layer multiplies `capacity * 0.80` (the sell-through assumption) to derive `total_est_attendance` per county-quarter.
- **Coverage by source (~39K CA events across 2018-2022):**
  - **50.5%** seeded — hand-curated `SEED` dict in `pipelines/build_venue_capacities.py` (Wikipedia, operator websites, festival records)
  - **2.5%** WikiData — auto-discovered via SPARQL scrape against `wdt:P1083` (max capacity)
  - **13.5%** heuristic — name-keyword fallback (`Stadium` → 30K, `Arena` → 12K, `Theatre` → 1.2K, `Bar` → 200, etc.)
  - **33.5%** default — flat 500 (median small-CA-club capacity)
- **`capacity_source` values:** `wikipedia`, `operator`, `festival_*` (festival-specific lookups), `wikidata_sparql`, `heuristic_<keyword>` (e.g. `heuristic_stadium`), `cruise_show_theater`, `tv_studio_audience`, `default_small_club`. Filter the table on `capacity_source` to see which classification a row got.
- **Reproducibility:**
  - [`pipelines/scrape_wikidata_venues.py`](../pipelines/scrape_wikidata_venues.py) — re-run to refresh WikiData cache
  - [`pipelines/build_venue_capacities.py`](../pipelines/build_venue_capacities.py) — builds the canonical table from SEED + WikiData + heuristic
- **Caveats:**
  - Capacity is an *upper bound* on attendance. The 0.80 sell-through factor is a fixed prior; PyMC can learn it as a latent later.
  - Default-500 venues add noise to the long tail. The Bayesian model handles this via a measurement-error term.
  - Currently CA-only. Extend to other states by running the WikiData scrape per state and extending the SEED dict for state-specific venues.

---

## 3.6 `festivals` (Silver, reference) — ~92 rows

- **Source:** Hand-curated, from Wikipedia infoboxes / festival operator websites / trade-press reports. Maintained in [`data/curated/festivals.csv`](../data/curated/festivals.csv).
- **What it is:** Major CA festivals 2018-2023 (Coachella, Stagecoach, Outside Lands, BottleRock, Aftershock, KAABOO, HARD Summer, Camp Flog Gnaw, Cruel World, SDCC, Anime Expo, WonderCon, etc.) with total attendance per instance.
- **Granularity:** one row per festival per year. Multi-weekend festivals (e.g., Coachella has 2 weekends) are summed into a single row.
- **Why we have it:** **Setlist.fm only captures per-act setlists**, so a single festival like Coachella shows up as ~50 individual concerts of 5,000-attendance each, dramatically understating the actual ~125K/weekend headcount. The Gold layer joins this table separately to expose `total_festival_attendance` per county-quarter alongside `total_est_attendance` from concerts.
- **Categories:** `music_festival` (78 rows) and `conference_festival` (14 rows — SDCC, Anime Expo, WonderCon, E3).
- **2020 is intentionally sparse** — most major festivals were canceled due to COVID. This is captured correctly as a real-world event-magnitude shock the model can learn from.
- **Reproducibility:** [`pipelines/build_festivals_reference.py`](../pipelines/build_festivals_reference.py) — read CSV → write parquet to `s3://.../silver/festivals/`.
- **Caveats:**
  - CA-only for now. Phase C work expands to nationwide top 200.
  - Attendance figures are public estimates; some are organizer-reported (likely overstated) vs. trade-press estimates (more conservative).
  - Doesn't capture small local festivals (community events, art walks, etc.). The model's "baseline" county economy from QCEW + ACS should absorb that signal.

---

## 4. How the three Y layers fit together

For any (state, period) the model needs Y, it picks the finest-grained source available:

| State, granularity | Source | Notes |
|---|---|---|
| CA, county × quarter × sector | `cdtfa_taxable_sales` | Best |
| TX, county × month | `tx_comptroller_county_allocations` | Best for TX counties that adopted local sales tax |
| Everywhere else (50+DC), state × year | `census_state_tax_collections` | Universal floor |

The model layer will need to decide how to handle the heterogeneous granularity (probably hierarchical Bayesian: state-level effects with county-level random effects where data exists).

---

## 5. Quick orientation queries

```sql
-- Row counts at a glance
SELECT 'events' AS t, COUNT(*) FROM events
UNION ALL SELECT 'cdtfa_taxable_sales', COUNT(*) FROM cdtfa_taxable_sales
UNION ALL SELECT 'census_state_tax_collections', COUNT(*) FROM census_state_tax_collections
UNION ALL SELECT 'tx_comptroller_county_allocations', COUNT(*) FROM tx_comptroller_county_allocations
UNION ALL SELECT 'bls_qcew', COUNT(*) FROM bls_qcew;

-- The X→Y join for one state (California, 2022)
SELECT
    d.county_name,
    a.population,
    COUNT(DISTINCT e.event_id) AS events_2022,
    ROUND(SUM(c.taxable_sales_usd) FILTER (WHERE c.business_type = 'Total All Outlets') / 1e9, 2) AS total_taxable_billions,
    ROUND(SUM(c.taxable_sales_usd) FILTER (WHERE c.business_type = 'Food Services and Drinking Places') / 1e9, 2) AS food_services_billions
FROM dim_county d
LEFT JOIN events e ON e.county_fips = d.county_fips
LEFT JOIN cdtfa_taxable_sales c ON c.county_fips = d.county_fips AND c.table_year = 2022
JOIN census_acs_county a USING (county_fips)
WHERE d.state_fips = '06'
GROUP BY d.county_name, a.population
ORDER BY total_taxable_billions DESC NULLS LAST
LIMIT 10;
```

---

## 6. Totals at a glance

| Category | Tables | Total rows |
|---|---|---:|
| Geographic / dimension | `dim_county` | 3,235 |
| Demographic | `census_acs_county` | 3,144 |
| Local economy | `bls_qcew` | **104,075,464** |
| Multipliers | `bea_io_use`, `bea_io_make` | 279,882 |
| Geocoding fallback | `hud_zip_county` | 6 (placeholder) |
| Events (X) | `events` (unified), `ticketmaster_events`, `setlistfm_setlists` | 164,399 unified |
| Reference | `venue_capacities` (CA venues w/ capacity), `festivals` (curated major CA festivals) | 2,673 + 92 |
| Y target | `census_state_tax_collections`, `cdtfa_taxable_sales`, `tx_comptroller_county_allocations` | 64,785 |
| **Warehouse total** | 13 main tables | **~104.6M rows** |

The Gold layer (`aai540_gold.model_training_matrix`) is a derived table with
2,552 rows: one per CA county × quarter × year (2015-2025). See
[`sql/gold/model_training_matrix.sql`](../sql/gold/model_training_matrix.sql)
and [`notebooks/aws_starter.ipynb`](../notebooks/aws_starter.ipynb).

---

## 7. References for deeper reading

- [`docs/STATUS.md`](STATUS.md) — runtime state, what's currently running, gotchas
- [`docs/MODEL_READINESS.md`](MODEL_READINESS.md) — the design questions to decide before we build the model
- [`docs/state_dor_inventory.md`](state_dor_inventory.md) — survey of which states publish what, why most are inaccessible without PDF parsing
- [`docs/data_acquisition_strategy.md`](data_acquisition_strategy.md) — the original strategy doc that scoped all this
- [`docs/superpowers/specs/`](superpowers/specs/) — per-source design specs (CDTFA, Setlist.fm, BEA parser, transforms, multipliers)
- [`notebooks/explore.ipynb`](../notebooks/explore.ipynb) — pre-executed walk-through of every table with sample queries
