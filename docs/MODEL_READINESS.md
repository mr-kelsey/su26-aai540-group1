# Model-Layer Readiness Brief

**Date:** 2026-05-10
**Audience:** USD AAI-540 Group 1 — you and your partners, going into the model-design discussion

**Purpose:** Capture what's in the warehouse, what choices the data forces, and which questions are open. Not a design — a starting point.

## What we have on `main`

### Features (X)

| Source | Granularity | Coverage | Use as feature |
|---|---|---|---|
| `dim_county` | 3,235 US counties | All states, 2023 vintage | Geographic dummy, land area |
| `census_acs_county` | county | 50 states + DC, latest ACS 5-yr | Population, median income, education, age |
| `bls_qcew` | county × quarter × NAICS × ownership | 2015-2023 nationwide (104M rows) | Sector employment, wages, # establishments — the local economic baseline |
| `bea_io_use` / `bea_io_make` | national × NAICS | 1997-2023 | Leontief multiplier matrix (`compute_leontief_inverse` in `src/eia/multipliers/`) — converts event-driven sector spend into total sector spend |
| `events` | event-level | 49,610 events; Setlist.fm 2022 (14 states so far, drip-pull running) + Ticketmaster forward-only sample | Event count by category × period_id × county_fips, optionally rolled to attendance-weighted with venue capacity if/when added |

### Outcome (Y)

| Source | Granularity | Coverage | Notes |
|---|---|---|---|
| `cdtfa_taxable_sales` | CA county × quarter × business type (12 categories) | 2015 Q1 - 2025 Q4 (30,624 rows, ~5 month lag) | **California only.** Has both a `"Total All Outlets"` aggregate and per-sector breakdowns. Most directly event-relevant categories: `Food Services and Drinking Places`, `Clothing and Clothing Accessories Stores`, `Gasoline Stations`, `Other Retail Group`. |

**Implication:** initial model training runs on California events for now. National coverage requires more state DOR sources (each one is its own per-state scrape; Texas Comptroller, NY Taxation and Finance, etc. — separate from CDTFA's pattern).

## The cleanest join

```
events
  ⋈ dim_county        on county_fips       — geographic context
  ⋈ census_acs_county on county_fips       — demographic features
  ⋈ bls_qcew          on county_fips, period_id, NAICS  — local sector strength
  ⋈ cdtfa_taxable_sales on county_fips, period_id, business_type  — Y (CA only)
```

For CA county × 2022-Q3, we have ~3,710 LA-County concert-events (X), 36,754 accommodation jobs (X), $53B taxable sales (Y). All keyed cleanly.

## Open questions for partner discussion

These are choices that should come from the team, not from me unilaterally:

1. **Model family.** Bayesian regression family was implicit from "Bayesian impact model" in the project brief. Open: hierarchical (counties nested in states), Gaussian process (spatial smoothing), simple linear, or something else? Tradeoff is interpretability vs. flexibility vs. compute budget.

2. **Counterfactual structure.** Two clean framings:
   - **Predictive:** model Y = f(features including events), then ablate events for impact estimate
   - **Synthetic control:** match each county-quarter to a "no-event" twin via propensity scoring, compare
   - **Difference-in-differences:** before/after big events vs. control counties

   The class material likely points to one of these. Worth aligning early.

3. **Event-level vs. county-month panel.** Two ways to structure the training set:
   - Each row = (county, quarter), Y = taxable sales, X includes event-count feature
   - Each row = (event), Y = post-event Δ in county Y vs. predicted-no-event baseline
   
   First is simpler; second gives event-attributed impact directly. Both are defensible.

4. **Attendance proxy.** Setlist.fm gives only "show happened here." No attendance figures, no ticket price. Options:
   - Venue capacity from another source (manual research per top-N venues, ~tedious)
   - Treat all events as binary "present/not" — simplest, loses tour-vs-club distinction
   - Use Ticketmaster's `ticket_min_usd` / `ticket_max_usd` as a quality proxy (TM events only)
   - Wait for Wikidata `P1132` (number of participants) for major-event subset

5. **NAICS reconciliation.** CDTFA's `business_type` (12 categories) is NOT NAICS. The BLS QCEW data and BEA multipliers use NAICS. To regress against multiplier-weighted features, we'd need either:
   - Map CDTFA categories to BLS NAICS sectors (manual mapping table)
   - Or operate at the CDTFA-category level and ignore NAICS-keyed features
   
   The mapping isn't 1:1 (BLS has 1,000+ NAICS codes; CDTFA has 12). Coarse aggregation is probably fine.

6. **Disclosure suppression.** ~3-5% of CDTFA rows have `disclosure_flag = "D"` (taxable_sales suppressed for small-population counties). Two options: drop, or impute as average of neighbors.

7. **Train/val/holdout split.** Time-based (e.g., train 2015-2019, val 2020-2022, holdout 2023+) is the honest choice for forecasting. Random split would leak. COVID years (2020-2021) are anomalous and might warrant either inclusion (as a stressor) or exclusion (as out-of-distribution).

8. **PyMC vs. NumPyro vs. PyStan.** All work. PyMC has the friendliest Python API; NumPyro is fastest; PyStan is most established. Probably default to PyMC unless someone has a Stan preference.

## What's NOT in scope to discuss with partners (yet)

- **Inference-side pipeline.** How the trained model serves predictions for future events — that's Phase 3 in the data acquisition strategy doc.
- **MLOps / SageMaker deployment.** AAI-540 emphasizes production; the deployment story can wait until the model itself is settled.
- **Other states' DORs.** Coverage-expansion is a known TODO but separate from "what model to build."

## What we don't have yet but might affect choices

- **Setlist.fm:** drip-pull running through ~2026-05-15; will add ~37 more states (~150K more setlists). Doesn't change the model family choice, but may change "CA-only training" → "full-US training" timeline.
- **RunSignUp + HUD:** stubs awaiting free API keys. Marathons/races would expand event categories; HUD ZIP-county fallback would close geocoding gaps.

## Concrete next step proposal

Once the team agrees on (1) model family and (2) counterfactual structure, I can:

1. Scaffold the model module under `src/eia/model/` (training script, evaluation harness)
2. Build a `tests/test_model.py` with the deterministic data plumbing
3. Wire `make train-model` into the Makefile
4. Run an initial fit on CA-only data and report cross-validation metrics

Estimated effort: 1-2 days after the design discussion.

## References

- [docs/data_acquisition_strategy.md](data_acquisition_strategy.md) — §5 covers the temporal-lag problem central to model evaluation
- [src/eia/multipliers/leontief.py](../src/eia/multipliers/leontief.py) — the Leontief inverse implementation
- [pipelines/phase1_summary.py](../pipelines/phase1_summary.py) — current cross-source demo query
- [docs/STATUS.md](STATUS.md) — runtime data state
