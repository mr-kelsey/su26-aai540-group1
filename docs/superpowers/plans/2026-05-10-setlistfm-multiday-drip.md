# Setlist.fm Multi-Day Drip-Pull Plan

**Date:** 2026-05-10
**Goal:** Complete the remaining ~37 states' 2022 Setlist.fm data over ~5 days under the API's daily quota.

## Why this plan

The single-session pull on 2026-05-10 finished 14 of 51 partitions before Setlist.fm's AWS API Gateway returned `LimitExceededException` (HTTP 429). Empirically the daily quota is around 2,000-2,500 requests in a rolling 24h window — well below the ~6,000-9,000 requests the full remaining pull needs.

The fix has two parts:

1. **Page-level resumability** in `_fetch_partition` (committed in `c3575b9`). Each daily run picks up where the previous one stopped — even mid-state. Completed partitions cost zero API calls.

2. **A scheduled daily task** (this plan) that runs `make pull-setlistfm` once per day. It burns through ~1,500 requests of fresh quota, exits when 429s start, and lets the next day's run continue.

## Inventory

| Status | States | Count |
|---|---|---|
| ✅ Loaded | AK AL AR AZ CA CO CT DC DE FL GA HI ID | 13 |
| ⚠️ Partial | IL (427 of 456 pages = 8,540 of ~9,120 setlists) | 1 |
| 🟡 Remaining | IA IN KS KY LA MA MD ME MI MN MO MS MT NC ND NE NH NJ NM NV NY OH OK OR PA RI SC SD TN TX UT VA VT WA WI WV WY | 37 |

**Estimated remaining work** (based on partial probes + reasonable defaults):
- Big states (likely cap at ~500 pages): NY, TX, MI ≈ 3 × 500 = 1,500
- Medium-large (250-450 pages): PA, MA, NJ, NC, OH, VA, WA, MN ≈ 8 × 350 = 2,800
- Medium (100-250 pages): IN, KY, LA, MO, MS, NV, OR, SC, TN, WI ≈ 10 × 175 = 1,750
- Small (<100 pages): IA, KS, MD, ME, MT, ND, NE, NH, NM, OK, RI, SD, UT, VT, WV, WY ≈ 16 × 60 = 960
- IL completion: 29 pages
- **Total: ~7,000 requests across ~37 state-years**

At ~1,500 requests per daily quota: **5 days to finish**.

## Schedule

A single scheduled task fires daily and runs the same pipeline; the resumable fetch handles state internally. No state-batch list to keep in sync.

- **Schedule:** Mon 2026-05-11 through Fri 2026-05-15, each day at **18:47 local time (MST)**
  - 18:47 is after the previous day's 429s would have rolled out of any 24h window
  - Off-the-zero minute to avoid coordinated load with other API users
  - 5 days × ~1,500 = ~7,500 total requests, matches the estimate

- **What each run does:**
  1. `make pull-setlistfm` (resumable; exits cleanly on 429)
  2. `make build-events` (merge staging tables into `events`)
  3. `make warehouse-health` and `make phase1-summary` (status snapshot)
  4. Send a one-line Slack update to the user's preferred DM channel `D0B2Q08DHEH` summarizing: partitions complete vs remaining, total setlists loaded, and any 429 stoppage.

- **Tooling:** `mcp__scheduled-tasks__create_scheduled_task` (skill-file backed; persists across Claude sessions).

## Stop conditions

- All 51 partitions show "already complete" in the fetch log → done (script will surface this).
- 5 days elapsed without completion → user reviews and decides whether to extend the schedule or pull manually.

## Out of scope

- Cap-busting (per-day fan-out for saturated big-state-years like NY/TX/CA) — costs 6x more requests; defer to a follow-on plan once Phase 1 unblocks.
- Earlier years (2015-2021, 2023) — separate cycle.
- Other event sources (RunSignUp / Wikidata) — separate cycle.

## Manual fallback

If the scheduled task misbehaves or the user wants to drive it themselves:

```bash
make pull-setlistfm   # resumable; safe to invoke repeatedly
make build-events
make phase1-summary
```
