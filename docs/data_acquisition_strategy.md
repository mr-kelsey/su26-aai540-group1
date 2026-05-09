# Data Acquisition Strategy

**Project:** ML-based Economic Impact Assessment for Social Events
**Scope:** US, all event categories, free/public + open APIs preferred
**Owner:** Steve
**Status:** Draft v1 (May 2026)

---

## 1. Executive Summary

The model needs to estimate three outputs — direct, indirect, and induced economic impact — given an event's type, expected attendance, and host location. To do that well, we need data in four distinct layers:

1. **Economic backbone** — the input-output structure of the US economy at the regional level. This is what lets us translate a dollar of direct event spending into the indirect and induced ripples. Sourced entirely from BEA, BLS, and Census; all free or near-free.
2. **Event-side training corpus** — historical events with attributes (type, attendance, location, ticket revenue, vendor mix where available). This is the hard part. Public APIs cover concerts and races reasonably well; conferences, expos, and amateur sports are sparser and require multi-source assembly.
3. **Geographic and demographic context** — the local economy the event lands in. Population density, income distribution, industry mix, accommodation supply, transit access. Census ACS, TIGER/Line shapefiles, and OSM cover this.
4. **Ground-truth tax data for evaluation** — the long-cycle measurement against which model predictions are scored. This is multi-year-lagged and must be planned for from day one.

The strategy is **free-public-first**, with paid sources flagged but not depended upon. Several "obvious" event APIs (Eventbrite, Songkick, Bandsintown, Meetup) have been restricted or paywalled in recent years; the plan routes around them rather than relying on a future reversal. A phased roadmap (Phase 0 → 3) lets the team prove out the data pipeline on one event category before scaling.

---

## 2. Strategic Principles

- **Free and public by default.** The $500 EIA price point cannot survive a $30k IMPLAN license or a $12k/yr Pollstar Pro subscription. Where commercial data is genuinely required, isolate it to a single feature so it can be ablated later.
- **Two-tier sourcing.** Bulk historical pulls (one-off, multi-GB downloads) versus live refresh (small, frequent API calls). They have different storage, scheduling, and failure modes; treat them as different pipelines.
- **Build a local mirror, don't hot-call APIs at inference.** Every external source becomes a versioned local table. The model never reaches outside the warehouse.
- **Prefer geography keys that join cleanly.** County FIPS, CBSA codes, and ZIP-to-county crosswalks. Anything that can't be joined to FIPS becomes a pre-processing problem, not an in-model problem.
- **Plan for the temporal lag from day one.** Ground-truth tax data lags events by 6 months to 5 years depending on the source. The training corpus must be old enough that outcome data exists; the inference path must work without it.
- **Document every source's ToS.** Several event-side sources (Athlinks, sports-reference, Setlist.fm) have explicit scraping clauses. Track this in a register, not in tribal memory.

---

## 3. Data Domain Inventory

### 3.1 Economic Backbone (multipliers, industry structure)

| Source | What it gives us | Why we need it |
|---|---|---|
| **BEA Input-Output Use & Make tables** | National 71-, 405-, and detail-industry I-O matrices | Foundation for deriving Leontief inverse and our own multipliers — replaces $30k IMPLAN license |
| **BEA RIMS II multipliers** | Pre-calculated regional multipliers by industry | Validation benchmark; can selectively buy ($275/region) for spot checks |
| **BEA Regional Economic Accounts (GDP-by-county, personal income)** | Per-capita income, GDP composition by county | Localizes national I-O matrix to the host region |
| **BLS Quarterly Census of Employment and Wages (QCEW)** | County-level employment & wages by 6-digit NAICS, quarterly | Direct-effect anchoring; identifies industry mix in host city |
| **BEA Travel and Tourism Satellite Account** | Tourism-related industry shares | Calibrates the visitor-spending portion of direct impact |

### 3.2 Event-Side Training Corpus

| Source | Coverage | Mechanism | Notes |
|---|---|---|---|
| **Ticketmaster Discovery API** | Concerts, sports, family events sold through TM | REST, free, ~5k calls/day | Best single source; venue, date, capacity, price tier |
| **Setlist.fm API** | Concert setlists with venue + date | REST, free, key required | Gives us tour-level rollups Ticketmaster misses |
| **RunSignUp API** | Race events (5k → marathons), participant counts | REST, free, key required | Strong marathon coverage |
| **MarathonGuide.com / BAA / NYRR archives** | Historical marathon results, finisher counts | Targeted scrape (per ToS) | Fills gaps RunSignUp doesn't cover |
| **Wikidata SPARQL** | Notable events with attendance figures | SPARQL, free, rate-limited | Crowd-sourced but well-structured for high-profile events |
| **Wikipedia event pages** | Long-tail events with attendance citations | Targeted parse via wiki API | Good for one-time/major events (Olympics, World Cup, championships) |
| **NCAA `data.ncaa.com` JSON endpoints** | College sports schedules and venues | Undocumented JSON, treat as fragile | NCAA killed the official API; community-known unofficial endpoints |
| **City open-data portals (NYC, Chicago, LA, Austin, San Diego, etc.)** | Permits, special-event filings, attendance estimates | Socrata APIs, mostly free | Highest-value untapped source for non-Ticketmaster events |
| **State tourism / convention bureau reports** | Post-event impact reports (often by past consultants) | PDF parsing | Gives us human-labeled examples of "ground truth" we can backfit against |
| **PredictHQ** *(commercial fallback)* | Aggregated event intelligence | REST, paid (~$1k+/mo) | Free trial only — use to bootstrap, not depend on |

**Routed-around sources (do not plan on):**
- **Eventbrite API** — restricted to your-own-org events since Dec 2019. Not usable for harvesting third-party events.
- **Songkick API** — partner-only since 2017. No self-serve signup.
- **Bandsintown API** — partner-only.
- **Meetup API** — moved to paid Pro tier for useful queries.
- **ESPN public API** — officially deprecated 2014. Some undocumented endpoints survive but are not contractually safe.
- **Athlinks** — no public API; ToS discourages scraping.

### 3.3 Geographic and Demographic Context

| Source | What it gives us |
|---|---|
| **Census ACS 5-year API** | Population, income, age, education, housing at block-group, tract, ZIP, county |
| **Census TIGER/Line shapefiles** | All US geographic boundaries — county, place, CBSA, ZCTA |
| **HUD USPS ZIP-to-county crosswalk** | Quarterly-updated ZIP↔county weights — joins messy ZIP-coded sources to FIPS |
| **OpenStreetMap Overpass API** | Venue coordinates, hotel & restaurant density around event sites |
| **FCC API for CBSAs** | Metropolitan/Micropolitan Statistical Area definitions and updates |
| **FRED** | Convenience series — unemployment by metro, hotel-sector employment, etc. |

### 3.4 Ground-Truth Tax Data (Evaluation)

| Source | Cadence | Lag | Use |
|---|---|---|---|
| **Census Annual Survey of State and Local Government Finances** | Annual | ~2 years | Primary long-run evaluator — total tax receipts by state and large local govts |
| **Census Annual Survey of State Government Tax Collections** | Annual | ~1 year | Faster-moving state-level tax categories (sales, lodging, income) |
| **Census of Governments** | Every 5 years (2017, 2022, 2027) | ~2 years | Most comprehensive; benchmarks the annuals |
| **IRS SOI county-level data** | Annual | ~2 years | County-level wage and income aggregates |
| **State Department of Revenue dashboards (e.g., CA CDTFA, FL DOR, NY DTF)** | Monthly/quarterly | 2–6 months | Faster signal — sales tax + lodging tax by county. Critical for shorter-cycle eval. |
| **City open finance portals** | Varies | Varies | Best-case 1-month lag for major cities; supplements state data with hyper-local |

This domain is the project's biggest structural challenge. The fastest signal we get is monthly state DOR sales/lodging tax data with a 2–6 month lag. The comprehensive picture takes 2 years. The model evaluation pipeline must be designed for this — see §5.

---

## 4. Source-by-Source Acquisition Mechanism

For each source, the operational details that drive engineering work:

| Source | Mechanism | Auth | Refresh Cadence | Volume | Legal/ToS |
|---|---|---|---|---|---|
| BEA I-O tables | Bulk download (XLSX/CSV) | None | Annual (Dec release for prior year) | < 100 MB | Public domain |
| BEA RIMS II | Email/portal order | Account | Per order; updated annually | Small (CSV per region) | Licensed for internal use |
| BLS QCEW | Bulk download (CSV/Parquet) | None | Quarterly, ~6 mo lag | ~2 GB/yr nationwide | Public domain |
| BEA Travel & Tourism SA | Bulk download | None | Annual | < 50 MB | Public domain |
| Census Gov Finances | Bulk download | None | Annual + 5-yr Census | ~500 MB/yr | Public domain |
| Census ACS | API + bulk | API key (free) | Annual (5-yr rolling) | ~5 GB nationwide | Public domain |
| TIGER/Line | Bulk download | None | Annual | ~10 GB full set | Public domain |
| HUD ZIP crosswalk | Bulk download | None | Quarterly | < 100 MB | Public domain |
| Ticketmaster Discovery | REST API | API key (free) | Live | Rate-limited 5k/day | ToS prohibits republishing raw data; aggregates fine |
| Setlist.fm | REST API | API key (free) | Live | Polite rate (1/sec) | Attribution required |
| RunSignUp | REST API | API key (free) | Live | Generous limits | ToS allows research use |
| Wikidata | SPARQL | None | Live | Rate-limited | CC0 |
| Wikipedia | API + parse | None | Live | Polite use | CC BY-SA — track attribution |
| City open-data portals | Socrata APIs / bulk | Varies | Varies | Small per city, cumulative ~GB | Mostly open licenses |
| State DOR dashboards | Mix — APIs, CSV, scrape | Varies | Monthly/quarterly | Small | Mostly public domain |
| OSM Overpass | REST | None | Live | Rate-limited | ODbL — track attribution |
| IRS SOI | Bulk download | None | Annual | < 1 GB | Public domain |
| FRED | REST API | API key (free) | Daily | Small | Public + Series-specific terms |

**Engineering implication:** ~70% of sources are bulk downloads on annual or quarterly cadence — these get a scheduled batch job. ~30% are APIs called in low volume — these get a polite-rate fetcher with retry/backoff and a local cache.

---

## 5. Statistical Sufficiency

The headline number every stakeholder will ask: **how many events do we need?**

### 5.1 Per-category sample-size targets

For a Bayesian regression with ~15–25 features (event type one-hots, attendance, attendance², region one-hots or embeddings, income/density covariates) and a heavy-tailed continuous target, we want a posterior credible interval that's commercially useful — say, ±25% on the median estimate at the 80% credible level. Working backwards from typical posterior-shrinkage rates for hierarchical Bayesian regression on noisy targets, that points to:

| Event category | Minimum viable n | Comfortable n | Source feasibility |
|---|---|---|---|
| Major concerts/festivals | 800 | 3,000+ | Easy via Ticketmaster + Setlist.fm — 10 yr backcatalog gives ~50k US shows |
| Marathons & large running events | 400 | 1,200 | RunSignUp + scrape covers ~1,100 US marathons/yr |
| Pro & college sports games | 1,500 | 5,000 | NCAA endpoints + sports-reference (within ToS) |
| Conferences & trade shows | 300 | 1,000 | Hardest — city portals + tourism bureau reports |
| Local festivals / food events | 300 | 800 | City open-data portals; varies wildly by city |

These are pre-de-duplication and pre-quality-filter. Realistic post-cleaning yields are ~60% of raw counts. The pilot should not gate on hitting comfortable-n in every category; minimum-viable-n in the top 2–3 categories is enough to publish a v1 model.

### 5.2 The temporal-lag problem

The fundamental constraint: we cannot evaluate model predictions on an event until ground-truth tax data for the surrounding period is published. Lags by source:

- State DOR monthly data: 2–6 months → fast eval, narrow signal
- Annual Survey of State Gov Tax Collections: ~12 months → year-over-year city-level comparisons
- Annual Survey of State and Local Gov Finances: ~24 months → comprehensive but slow
- Census of Governments: ~24 months after a 5-year cycle close → benchmark only

**Implication for the corpus design:**
- Training set should draw from events at least **24 months old**, so all evaluation sources are populated.
- A "validation cohort" of events 6–24 months old can be scored against state DOR data only — useful as an early-warning system for model drift.
- A "live prediction" path serves new events with no ground truth; performance is monitored via the validation cohort.

### 5.3 Backtesting design

The training corpus should span **at least 8 years (2015–2023)** to capture:
- Pre-pandemic baseline (2015–2019)
- COVID anomaly window (2020–2021) — explicit covariate, not silently filtered
- Post-pandemic re-normalization (2022–2023)

Time-based holdout (not random) — train on 2015–2021, test on 2022–2023. Random holdout would leak temporal structure and over-state performance.

### 5.4 Selection bias risks to flag now

- **Events with published impact reports skew toward "good news" cases.** Tourism bureaus commission EIAs to justify subsidies. Be wary of using their reported impact figures as ground truth without skepticism.
- **Ticketmaster coverage skews toward larger, professionally-promoted events.** Folk festivals, regional fairs, and amateur sports are under-represented. Down-weight or stratify.
- **Marathons skew toward affluent metros.** A naïve location feature will conflate marathon presence with metro wealth.

---

## 6. Phased Acquisition Roadmap

### Phase 0 — Proof of Data (weeks 1–2)

**Goal:** prove every backbone source is actually accessible and joinable on FIPS.

- Pull one quarter of QCEW; one BEA Use table; ACS for one state; TIGER counties.
- Verify joins on FIPS work end-to-end across all four.
- Pull 50 concerts via Ticketmaster + 50 marathons via RunSignUp; confirm we can geocode each to a FIPS county.
- Stand up local Postgres or DuckDB with raw / cleaned schemas.

**Exit criterion:** can answer "what was the QCEW employment in 'Accommodation' (NAICS 721) in San Diego County in Q3 2023, and which scheduled concerts and marathons fell in that same county and quarter?" in one query.

### Phase 1 — MVP Corpus (weeks 3–8)

**Goal:** training-ready corpus for two event categories (concerts + marathons).

- Full Ticketmaster + Setlist.fm scrape for 2015–2023.
- Full RunSignUp + targeted marathon-archive scrapes for the same window.
- All BEA I-O tables; full QCEW history; ACS 5-yr for all years; TIGER all years.
- Build the Leontief inverse and a custom multiplier table from BEA Use/Make.
- Pull state DOR data for the 10 largest states for sales/lodging tax.
- First end-to-end model run (even a baseline OLS) to validate the pipeline.

**Exit criterion:** model can produce a direct/indirect/induced estimate for any concert or marathon in the corpus, and we can score it against state DOR signals at the county-month level.

### Phase 2 — Production-Grade Coverage (weeks 9–16)

**Goal:** add remaining event categories and ground-truth depth.

- Sports (NCAA + pro) via undocumented endpoints + sports-reference (within ToS).
- Conferences via top-25 metro tourism-bureau reports + city open-data portals.
- Census Gov Finances historic series.
- IRS SOI county series.
- Wikidata SPARQL pull for high-profile events.
- Validation cohort scoring infrastructure.

**Exit criterion:** model covers ≥4 event categories, scored against ≥3 distinct ground-truth sources.

### Phase 3 — Continuous Refresh (ongoing)

**Goal:** keep the warehouse fresh without manual intervention.

- Scheduled quarterly QCEW + BEA refreshes.
- Monthly state DOR pulls for the validation pipeline.
- Weekly Ticketmaster / RunSignUp polls for new events.
- Annual ACS, BEA T&T SA, Census Gov Finances refreshes with model retrain trigger.
- Drift alarms: if state DOR signal diverges from predictions by more than X%, flag for review.

---

## 7. Storage and Pipeline Architecture (sketch)

Three-zone warehouse:

- **Raw zone** — every external pull is dumped verbatim with its fetch timestamp. Never deleted.
- **Cleaned zone** — schema-conformed, deduplicated, FIPS-joined. Versioned.
- **Feature zone** — model-ready tables; one row per event with the full feature vector.

DuckDB or Postgres are both reasonable for the volumes involved (~100–500 GB cleaned). Avoid Spark/Snowflake — the data isn't big enough to justify the operational tax for an academic project.

A small Airflow or simple cron + Python setup handles scheduling. Pull jobs write to Raw; transformation jobs lift to Cleaned and Feature. All transformations idempotent and re-runnable from Raw.

---

## 8. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Ticketmaster restricts API tier | Medium | High | Maintain Setlist.fm + city-portal fallbacks; cache aggressively |
| State DOR portals change schema | High | Medium | Wrap each portal in its own adapter with snapshot tests |
| Tax-data lag invalidates short-window evaluation | Certain | Medium | Designed-around in §5; backtest window starts ≥24 mo old |
| Selection bias in event corpus | High | High | Stratified sampling; explicit category one-hots; sensitivity analysis on results |
| OSM/Wikidata schema drift | Medium | Low | Pin queries; snapshot quarterly |
| ToS violations from over-aggressive scraping | Medium | High (legal) | Maintain ToS register; respect robots.txt; rate-limit conservatively |
| BEA I-O table re-benchmark changes coefficients | Annual | Medium | Version multipliers; retrain on benchmark change |
| Pandemic anomaly distorts 2020–2021 training data | Certain | Medium | Explicit pandemic-period covariate; sensitivity check holding it out |

---

## 9. Open Decisions

1. **Custom multipliers vs. RIMS II buy-in.** Building our own from BEA Use/Make is free but requires a quarter of econometric work. Buying RIMS II for a few pilot regions ($275 × ~10 = ~$2.7k) gives us a defensible benchmark at low cost. *Recommendation: do both — build our own as the production path, buy RIMS II for the top 5–10 metros as validation.*
2. **Storage stack.** DuckDB (file-based, zero-ops) vs. Postgres (server-based, more familiar to most teams). *Recommendation: start in DuckDB; promote to Postgres only if multi-user concurrency becomes a constraint.*
3. **Geography grain.** County-FIPS is the natural join key for federal data, but events happen at venue scale. *Recommendation: keep two geography keys per event — `venue_lat_lon` and `county_fips` — and let downstream features choose.*
4. **Validation cohort size.** Trade-off between recency and confidence — narrower window → fewer events → noisier eval. *Recommendation: 6–24-month window, accepted that early eval is directional only.*
5. **Whether to budget for any commercial event data at all.** A short PredictHQ trial could bootstrap the corpus considerably. *Recommendation: defer until Phase 2; if Phase 1 corpus is sufficient, skip entirely.*

---

## 10. Next Steps

- Confirm phase scope and timeline with project team.
- Stand up the Phase 0 environment and execute the proof-of-data exit query.
- Open a separate doc for the **Data Schema** (entity model, FIPS keys, NAICS levels) — that work follows directly from this strategy.
- Open a separate doc for the **Model Evaluation Framework** — the tax-data-lag mechanics need their own dedicated design.
