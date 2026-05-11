# Project Status — 2026-05-10

A snapshot of what's in the warehouse and what pipelines exist, as of the
end of the Setlist.fm scaleup session.

## Data layer

Run `make warehouse-health` to get the current numbers. Latest snapshot:

| Table | Rows | What it is |
|---|---:|---|
| `dim_county` | 3,235 | TIGER county shapefile centroids + areas |
| `census_acs_county` | 3,144 | ACS 5-year (population, income, education) for all states |
| `bls_qcew` | 104,075,464 | BLS QCEW employment/wages by county/quarter/NAICS, 2015-2023 |
| `bea_io_use` | 139,941 | BEA Use table (commodity x industry $) 1997-2023 |
| `bea_io_make` | 139,941 | BEA Make table (industry x commodity $) 1997-2023 |
| `hud_zip_county` | 6 | ZIP-county crosswalk — **placeholder only**, needs `HUD_API_TOKEN` for real pull |
| `ticketmaster_events` | 200 | Last 30 days of Music + Sports events (forward-looking only) |
| `setlistfm_setlists` | 49,411 | 2022 US concerts across 14 states; **pull crashed on HTTP 429** at IL — resumable, see below |
| `events` | 239 | Unified events (`make build-events` to rebuild) |

## Pipelines (run via `make <target>`)

| Target | Purpose |
|---|---|
| `phase0` | Apply migrations, pull all federal sources, run exit query |
| `pull-<source>` | Pull a single source (`bls-qcew`, `bea-io`, `census-acs`, `tiger`, `hud`, `ticketmaster`, `setlistfm`, `runsignup`) |
| `build-events` | UNION every event-source staging table into the canonical `events` table |
| `enrich-events` | Re-apply county_fips + period_id to existing events (cheaper than build) |
| `phase1-summary` | Cross-source demo: events x dim_county x ACS x QCEW |
| `validate-bea-multipliers` | Confirm our Leontief B-matrix matches BEA's published CxI_DR |
| `warehouse-health` | One-screen overview of all warehouse tables |
| `test` / `lint` / `typecheck` | Quality gates (92 tests, 27 source files clean) |

## What was built in this session

- **Setlist.fm source** ([src/eia/sources/setlistfm.py](../src/eia/sources/setlistfm.py)) — was a Phase 0 stub, now pulls real `/rest/1.0/search/setlists` data by (country, state, year) partition with 13 unit tests
- **Event staging architecture** — every event source writes to its own staging table (`ticketmaster_events`, `setlistfm_setlists`, ...) instead of overwriting `events` directly
- **`build_events` pipeline** ([pipelines/build_events.py](../pipelines/build_events.py)) — UNIONs staging tables, applies enrichments, re-registers `events`; 8 unit tests
- **`phase1_summary` pipeline** — cross-source demo (events x dim_county x ACS x QCEW with state-level rollups)
- **`warehouse_health` pipeline** — quick ops introspection
- **Lint/mypy cleanup** — `make lint`, `make typecheck`, `make test` all green (92 tests)
- **Background pull** — 50-state Setlist.fm pull running; expected to finish in 1-2 hours from session end

## Known gaps (autonomous work didn't tackle)

- **Setlist.fm cap-busting is implemented but off by default.** The currently-running pull captures the first 10K setlists of saturated state-years (CA/NY/TX/FL/IL/PA). To get the full data, re-run with `cap_bust=True` (or flip `cap_bust: true` in [configs/sources.yaml](../configs/sources.yaml)). Cost: ~6x more API calls per saturated state-year (~43 min each at 1 req/sec) but 3-5x more setlists.
- **Venue geocoding fallback** — Setlist.fm doesn't always return lat/lon. Empirically the CA 2022 sample had 100% coverage, but other states may not. A city → county_fips lookup using Census Places data would close this gap.
- **Ticketmaster historical data** — Discovery API only surfaces the last ~14 months. For training data we need events ≥24 months old; Setlist.fm is the answer for concerts, but RunSignUp / Wikidata for races and major events still need attention.
- **Wikidata events source** — explored briefly; the API works but data is sparse and inconsistently typed. Would need substantial query design to be productive.
- **HUD real pull** — needs `HUD_API_TOKEN` (5-min free signup at huduser.gov).
- **RunSignUp** — needs `RUNSIGNUP_API_KEY` + `RUNSIGNUP_API_SECRET`.

## Recommended next moves when you return

1. **Verify the Setlist.fm pull completed cleanly:**
   ```bash
   make warehouse-health        # check setlistfm_setlists row count
   make build-events            # merge into events
   make phase1-summary          # see the cross-source demo with much more data
   ```

2. **Decide on Phase 2:**
   - **A. Model layer brainstorm** — design the Bayesian regression now that we have data
   - **B. Cap-busting subdivision** — get the rest of CA/NY/TX/FL/IL/PA
   - **C. Wikidata properly** — sub-project: identify the right entity types, build a curated set of high-impact events with attendance figures
   - **D. Get HUD or RunSignUp keys** — unblock the remaining stubbed sources

My recommendation: **A** if partners are available to discuss the model; **B or D** if you want more data first.

## Background pull state at session end

The 50-state pull crashed partway through with HTTP 429 (Too Many Requests). Setlist.fm has stricter rate limits than their documented 1 req/sec polite rate — likely hourly/burst quotas not surfaced in their docs.

**State at crash:**
- Completed states fully loaded: AK, AL, AR, AZ, CA (capped 10K), CO, CT, DC, DE, FL (capped 9369), GA, HI, ID
- Partially completed: IL (got 8540 of ~10K)
- 49,411 setlists in `setlistfm_setlists` table
- Raw page files preserved at `data/raw/setlistfm/`

**To resume the pull** (after waiting ~1 hour for rate limit to recover):

```bash
make pull-setlistfm     # resumable: skips partitions with existing page files
make build-events       # merge into unified events table
make phase1-summary     # see the result
```

The fetch() now skips any `US_<STATE>_<YEAR>` partition that already has page files (added in commit `1f3cf4e`), so a retry doesn't waste API quota on already-pulled states. The remaining ~37 states will probably take ~90 min to pull on a clean rate-limit slate.

If 429s persist, options:
- Wait longer (2-4 hours, or overnight)
- Reduce `requests_per_second` in `configs/sources.yaml` from `1.0` to `0.5`
- Pull smaller batches (edit `default_states` to a subset of remaining states, run, repeat)

**Confirmed quota behavior (probed 60 min after first block, still 429):**

```
x-amzn-errortype: LimitExceededException
{"message":"Limit Exceeded"}
```

Setlist.fm runs on AWS API Gateway with a daily quota that appears to be a rolling 24-hour window (not a calendar-day reset — UTC midnight passed during the block and we were still capped). Estimated recovery time: **~22 hours from when the 429s first started**. Free-tier daily quota is likely around 1,500-2,000 requests; our pull made ~2,477 before being cut off.

Practical recommendation: wait until the next calendar day, then run `make pull-setlistfm` (the resumable fetch picks up where we left off). Or split the pull across multiple days by editing `default_states` to a smaller subset each run.

**This is now automated.** A scheduled task `setlistfm-drip-pull` (in `~/.claude/scheduled-tasks/`) runs daily at 18:47 local time and invokes pull → build → status → Slack. The page-level resumability (commit `c3575b9`) means each run picks up from the exact page where the prior run stopped. Expected completion: ~5 days. The task auto-disables when all 51 partitions are done. See [docs/superpowers/plans/2026-05-10-setlistfm-multiday-drip.md](superpowers/plans/2026-05-10-setlistfm-multiday-drip.md).

**Lost data from IL:** the partial IL partition has 8,540 of an expected ~10,000 setlists. To force a clean refetch, delete `data/raw/setlistfm/US_IL_2022/` before re-running pull-setlistfm.
