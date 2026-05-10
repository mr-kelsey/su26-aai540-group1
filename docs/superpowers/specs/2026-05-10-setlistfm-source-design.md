# Setlist.fm Source Implementation (Phase 1)

**Date:** 2026-05-10
**Phase:** 1 (event-side data acquisition)
**Status:** Design approved (autonomous run; user pre-authorized decisions)

## Background

The existing Setlist.fm source ([src/eia/sources/setlistfm.py](../../../src/eia/sources/setlistfm.py)) is a Phase 0 stub — `fetch()` writes a sentinel `.phase0_stub` file and `to_cleaned()` writes a one-row "stub" parquet. No real data is pulled. The class declares `target_table = "setlistfm_setlists"`, but no migration creates that table.

Per [docs/data_acquisition_strategy.md](../../data_acquisition_strategy.md), Setlist.fm is the project's primary **historical** concert data source: *"10 yr backcatalog gives ~50k US shows"* and *"Gives us tour-level rollups Ticketmaster misses"*. The Ticketmaster Discovery API (already integrated) is forward-only — empirical test confirmed zero events returned for any year before 2025. To build a training corpus of events from ≥24 months ago (paired with published state DOR tax outcomes), Setlist.fm is the realistic option.

## Empirical API findings (probed 2026-05-10)

- Endpoint: `GET https://api.setlist.fm/rest/1.0/search/setlists`
- Auth: `x-api-key` header
- Rate limit: 1 request/second (documented "polite rate"; not currently throttled by their server, but we will respect it)
- Pagination: `p` query parameter, fixed 20 results/page
- **Hard cap of 10,000 total results per query.** Pages beyond 500 are unreachable
- Filters: `countryCode` (2-letter), `stateCode` (2-letter), `cityName`, `year` (4-digit), `date` (DD-MM-YYYY), `artistName`, `venueId`
- Response shape: `{ total, page, itemsPerPage, setlist: [...] }`
- US 2022 by state probe:
  - CA/NY/TX: 10,000 (capped — true count is larger)
  - FL: 9,369; IL: 9,120; PA: 8,518; OH: 5,804; GA: 4,587; NC: 5,188; MA: 5,422 (real counts)

## Goals

- Replace the stub `SetlistFM.fetch()` / `to_cleaned()` with real implementations.
- Pull setlists from Setlist.fm by `(countryCode, stateCode, year)` partition.
- Detect 10K-cap saturation and subdivide saturated partitions by month.
- Land all retrieved setlists into a `setlistfm_setlists` staging table.
- Validate the implementation by pulling California 2022 end-to-end (one state, one year — fast enough to run interactively).

## Non-goals (deferred)

- **Full multi-year pull.** Phase 1 sub-option D is "test on one state-year first." Full 2015-2023 pull happens in a separate cycle once we've seen the data shape.
- **Venue geocoding.** Setlist.fm gives `venue.city.coords.{lat,long}` only sometimes. Rows without coords land in staging but won't propagate to `events` until a geocoder source exists.
- **Artist or venue lookups.** The `/search/artists`, `/search/venues`, and per-artist/per-venue endpoints are out of scope. Phase 1 uses `/search/setlists` only.
- **Tour-level aggregation.** Setlist.fm provides `info.tour.name`; we preserve it as a column but don't roll up.
- **Setlist song-list parsing.** The `sets.set[].song[]` array goes to `raw_payload`; we extract `n_songs` (a useful proxy for show length) but don't decompose individual songs.
- **Ticketmaster `target_table` migration.** Ticketmaster currently overwrites `events` directly. This spec adds staging for Setlist.fm; a separate small change retrofits Ticketmaster to also use staging. Out of scope for this design but listed as the next step.

## Architecture

### Three-stage flow

```
fetch()      paginate state-year (subdivide if capped) → write raw JSON pages
to_cleaned() flatten JSON → long-form Polars parquet w/ staging schema
load()       register parquet as setlistfm_setlists table
```

### Partition strategy

Inputs from `configs/sources.yaml`:
```yaml
setlistfm:
  country_code: "US"
  default_years: [2022]          # Phase 1 D scope
  default_states: ["CA"]         # Phase 1 D scope; full list is 50 + DC
```

Pull algorithm:

```
for year in years:
  for state in states:
    fetch_partition(country, state, year)

def fetch_partition(country, state, year):
    page1 = GET search/setlists(country, state, year, p=1)
    total = page1.total
    if total < 10_000:                       # safe — exhaustive pagination
        for p in 1..ceil(total/20):
            write page
    else:                                    # capped — subdivide by month
        for month in 1..12:
            for p in 1..(ceil(month_total/20) or 500):
                date_range_query(country, state, year, month, p)
```

Setlist.fm doesn't support a `month` filter directly, but supports `date=DD-MM-YYYY` (single day). To cover a month, we'd need 28-31 daily queries. **Cleaner alternative:** use `cityName` subdivision for capped states — top cities by population. We have dim_county; pick top-5 cities per saturated state, query each, and fall back to "rest of state" by repeating the year query and filtering out already-pulled city setlists in `to_cleaned`. This is uglier.

**Decision:** Phase 1 D test only needs CA 2022 (capped, total truncated to 10,000). We will:
1. Pull the 10,000-result page-1..500 sweep for the partition.
2. Log a WARNING if `total >= 10_000` recording how many setlists are unreachable in that partition.
3. Defer the cap-busting subdivision to a follow-on cycle — it's an optimization on top of a working pipeline.

This is a documented partial-coverage compromise. For CA 2022, we'll get the most recent 10,000 setlists chronologically (the API sorts by date DESC). That's still a substantial training sample.

### Output paths

- Raw: `data/raw/setlistfm/<country>_<state>_<year>/page_<NNNN>.json` (one file per API response page)
- Cleaned: `data/cleaned/setlistfm/setlists.parquet`

### Cleaned schema (`setlistfm_setlists` table)

```
setlist_id      VARCHAR PRIMARY KEY    -- Setlist.fm setlist id (slug-style)
artist_name     VARCHAR NOT NULL
artist_mbid     VARCHAR                -- MusicBrainz ID (optional, useful for joining other sources)
event_date      DATE NOT NULL          -- parsed from DD-MM-YYYY -> ISO
venue_name      VARCHAR
venue_id        VARCHAR                -- Setlist.fm venue id
city_name       VARCHAR
state_code      CHAR(2)
country_code    CHAR(2)
venue_lat       DOUBLE                 -- nullable; from venue.city.coords.lat
venue_lon       DOUBLE                 -- nullable; from venue.city.coords.long
tour_name       VARCHAR                -- info.tour.name, nullable
info_text       VARCHAR                -- info string from Setlist.fm, nullable
n_songs         INTEGER                -- count of songs across all sets, 0 if no setlist
raw_payload     VARCHAR                -- JSON-stringified original payload
fetched_at      TIMESTAMP NOT NULL
```

Stored separately from `events`. A future `build_events.py` pipeline will UNION `ticketmaster_events`, `setlistfm_setlists`, etc. into `events` after enrichment.

### Rate limiting + retries

Reuse `RateLimitedClient` (`src/eia/clients/base.py`) with:
- `requests_per_second=1.0` (Setlist.fm polite rate)
- `timeout_s=30.0`
- Default tenacity retry behavior on 429/5xx

## Public surface

`SetlistFM` source class implementing the existing ABC:

```python
class SetlistFM(Source):
    name = "setlistfm"
    target_table = "setlistfm_setlists"
    raw_format = "json"

    def __init__(
        self,
        country_code: str | None = None,
        years: list[int] | None = None,
        state_codes: list[str] | None = None,
    ) -> None: ...

    def fetch(self) -> Path: ...
    def to_cleaned(self, raw_path: Path) -> Path: ...
    # load() uses the base-class default
```

`__init__` reads config defaults from `configs/sources.yaml > setlistfm`.

CLI: `eia pull setlistfm` (already registered via the existing module-level `register(...)` call).

## Migration

New file `migrations/005_setlistfm.sql`:

```sql
CREATE TABLE IF NOT EXISTS setlistfm_setlists (
    setlist_id      VARCHAR PRIMARY KEY,
    artist_name     VARCHAR NOT NULL,
    artist_mbid     VARCHAR,
    event_date      DATE NOT NULL,
    venue_name      VARCHAR,
    venue_id        VARCHAR,
    city_name       VARCHAR,
    state_code      CHAR(2),
    country_code    CHAR(2),
    venue_lat       DOUBLE,
    venue_lon       DOUBLE,
    tour_name       VARCHAR,
    info_text       VARCHAR,
    n_songs         INTEGER,
    raw_payload     VARCHAR,
    fetched_at      TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_setlistfm_date     ON setlistfm_setlists (event_date);
CREATE INDEX IF NOT EXISTS ix_setlistfm_state    ON setlistfm_setlists (state_code);
CREATE INDEX IF NOT EXISTS ix_setlistfm_country  ON setlistfm_setlists (country_code);
CREATE INDEX IF NOT EXISTS ix_setlistfm_venue    ON setlistfm_setlists (venue_id);
```

## Edge cases

| Input | Output |
|---|---|
| Setlist with no songs (empty `sets`) | `n_songs = 0`, row preserved |
| Setlist with no venue | row preserved; venue_* fields null |
| Venue with no coords | `venue_lat=null`, `venue_lon=null` |
| Malformed `eventDate` | row skipped; logged as WARNING |
| API returns 429 | tenacity retries with backoff (RateLimitedClient) |
| API returns 5xx | tenacity retries |
| Partition saturated (total >= 10,000) | log WARNING with state, year, total; pull only first 500 pages |
| Empty partition (total == 0) | skip silently; no JSON files written |
| Duplicate `setlist_id` within a partition | last write wins (Polars dedup keeps latest, sort by `fetched_at` DESC) |
| Cross-partition duplicates | dedup on `setlist_id` after concat in `to_cleaned` |

## Logging

`logger.info` for:
- Each partition start: `"Setlist.fm CA 2022: 10,000 total, 500 pages"`
- Page failures (retry exhaustion): `"Setlist.fm CA 2022 page 47 failed: ..."`
- Cap-warning: `"Setlist.fm CA 2022: total 10,000 reached cap; some setlists unreachable"`
- Final summary: `"Setlist.fm pull complete: 9,873 setlists across 1 partitions"`

## Testing

`tests/sources/test_setlistfm.py`:

- **Test: `_parse_setlist` normalizes a single API response.** Synthetic JSON with all fields filled → expected schema with correct types.
- **Test: `_parse_setlist` handles missing optional fields.** Venue with no coords, no tour, no info → nulls in the corresponding columns.
- **Test: `_parse_setlist` parses DD-MM-YYYY date correctly.** `"15-03-2022"` → `date(2022, 3, 15)`.
- **Test: `_parse_setlist` counts songs across multiple sets.** Setlist with 2 sets of 5 + 3 songs → `n_songs == 8`.
- **Test: `_parse_setlist` skips rows with malformed eventDate.** Returns None; caller filters.
- **Test: `fetch` pagination respects 500-page cap.** Mock httpx returns `total=12000`; assert exactly 500 page files written.
- **Test: `fetch` respects exhaustive pagination under cap.** Mock returns `total=437`; assert `ceil(437/20)=22` page files.
- **Test: `to_cleaned` deduplicates by `setlist_id`.** Two pages with overlapping IDs → output has unique IDs.
- **Test: `to_cleaned` writes parquet with expected schema.** Parquet read-back has all 16 columns and correct dtypes.
- **Test: source registered.** `from eia.sources.registry import SOURCES; assert "setlistfm" in SOURCES`.

Mocking strategy: use `respx` or `pytest-httpx` to intercept httpx requests with canned responses. (If neither already in dev deps, fall back to monkeypatching `RateLimitedClient.get_json`.)

## Operational notes

- **Performance.** California 2022, capped at 10,000 setlists / 20 per page = 500 pages at 1 req/sec = ~8 minutes per partition.
- **Storage.** 10,000 setlists at ~2KB raw each = ~20MB per partition raw. Cleaned parquet ~3MB per partition.
- **Idempotence.** Raw pages with the same `(country, state, year, page)` overwrite. `to_cleaned` is pure; rerunning replaces the cleaned parquet. `load` uses `register_table_from_parquet(..., replace=True)`.
- **Test sub-pull for development.** Add a `max_pages` constructor parameter (default = None = no limit). Tests and quick smoke runs pass `max_pages=2` to fetch only the first 40 setlists.

## References

- [docs/data_acquisition_strategy.md](../../data_acquisition_strategy.md) — strategic role of Setlist.fm
- [src/eia/sources/setlistfm.py](../../../src/eia/sources/setlistfm.py) — existing stub being replaced
- [src/eia/sources/bls_qcew.py](../../../src/eia/sources/bls_qcew.py) — closest existing source pattern to follow
- [migrations/003_events.sql](../../../migrations/003_events.sql) — events table schema (the eventual join target via future `build_events.py`)
- Setlist.fm API docs: https://api.setlist.fm/docs/1.0/index.html
