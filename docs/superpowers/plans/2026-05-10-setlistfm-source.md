# Setlist.fm Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Phase 0 Setlist.fm stub with a real source that pulls setlists from `/rest/1.0/search/setlists` and lands them into a new `setlistfm_setlists` staging table, validated by a California-2022 smoke pull.

**Architecture:** Source plugin following the existing `Source` ABC pattern (fetch → to_cleaned → load). HTTP via `RateLimitedClient` at 1 req/sec. Partitioning by `(country_code, state_code, year)`; pagination capped at 500 pages per partition (10K results). Setlists land in a staging table (NOT directly into `events`); a future `build_events.py` will UNION staging tables.

**Tech Stack:** Python 3.11, Polars (DataFrames), httpx (via RateLimitedClient), pytest, DuckDB.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `migrations/005_setlistfm.sql` | CREATE | DDL for `setlistfm_setlists` staging table |
| `configs/sources.yaml` | MODIFY | Add `setlistfm:` section with default country/years/states |
| `src/eia/sources/setlistfm.py` | REPLACE | Full implementation: fetch/to_cleaned, partition + page logic, parser |
| `tests/sources/test_setlistfm.py` | CREATE | Unit tests for parser + pagination logic (no real HTTP) |
| `Makefile` | MODIFY | Add chflags workaround to `pull-setlistfm` target |

The implementation has three internal seams:
- `_parse_setlist(payload: dict) -> dict | None` — pure, deterministic JSON → cleaned-schema row
- `_fetch_page(client, country, state, year, page) -> dict` — single API call
- `_fetch_partition(country, state, year) -> int` — pagination loop calling `_fetch_page`

These are tested independently. `fetch()` orchestrates partitions; `to_cleaned()` reads raw JSON files and applies `_parse_setlist`.

---

## Task 1: Migration + Config

**Files:**
- Create: `migrations/005_setlistfm.sql`
- Modify: `configs/sources.yaml`

- [ ] **Step 1: Create migration file**

Create `migrations/005_setlistfm.sql` with:

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

- [ ] **Step 2: Add setlistfm section to configs/sources.yaml**

Append to `configs/sources.yaml`:

```yaml
setlistfm:
  # Phase 1 D-scope: validate end-to-end on a single state-year before scaling.
  country_code: "US"
  default_years: [2022]
  default_states: ["CA"]
  requests_per_second: 1.0
  page_size: 20  # Setlist.fm fixes this; informational only.
  max_pages_per_partition: 500  # 500 * 20 = 10,000 -- the API's hard cap.
```

- [ ] **Step 3: Apply migration**

Run:
```bash
make warehouse-init
```

Expected: applies `005_setlistfm.sql` and creates the table.

- [ ] **Step 4: Verify table exists**

Run:
```bash
chflags nohidden .venv/lib/python3.11/site-packages/*.pth 2>/dev/null || true
uv run --no-sync python -c "
from eia.warehouse import get_warehouse
print(get_warehouse().query('DESCRIBE setlistfm_setlists').to_pandas())
"
```

Expected: prints 16 columns matching the migration.

- [ ] **Step 5: Commit**

```bash
git add migrations/005_setlistfm.sql configs/sources.yaml
git commit -m "feat(setlistfm): add setlistfm_setlists staging table + config"
```

---

## Task 2: `_parse_setlist` Helper (TDD)

**Files:**
- Create: `tests/sources/test_setlistfm.py` (tests first)
- Replace: `src/eia/sources/setlistfm.py` (helper only; full source in Tasks 3-5)

- [ ] **Step 1: Write failing tests for `_parse_setlist`**

Create `tests/sources/test_setlistfm.py`:

```python
"""Tests for the Setlist.fm source."""

from __future__ import annotations

from datetime import date

import pytest

from eia.sources.setlistfm import SetlistFM


def _full_payload() -> dict:
    return {
        "id": "73d6a40b",
        "eventDate": "15-03-2022",
        "artist": {
            "name": "Radiohead",
            "mbid": "a74b1b7f-71a5-4011-9441-d0b5e4122711",
        },
        "venue": {
            "id": "73d6a380",
            "name": "The Greek Theatre",
            "city": {
                "name": "Berkeley",
                "stateCode": "CA",
                "country": {"code": "US"},
                "coords": {"lat": 37.8732, "long": -122.2547},
            },
        },
        "info": "Sold out show. Cover of 'Karma Police' was a surprise.",
        "tour": {"name": "OK Computer Anniversary Tour"},
        "sets": {
            "set": [
                {"song": [{"name": "Bones"}, {"name": "Airbag"}, {"name": "Lucky"}]},
                {"song": [{"name": "Karma Police"}, {"name": "Paranoid Android"}]},
            ]
        },
    }


def test_parse_setlist_full_fields():
    row = SetlistFM._parse_setlist(_full_payload())
    assert row is not None
    assert row["setlist_id"] == "73d6a40b"
    assert row["artist_name"] == "Radiohead"
    assert row["artist_mbid"] == "a74b1b7f-71a5-4011-9441-d0b5e4122711"
    assert row["event_date"] == date(2022, 3, 15)
    assert row["venue_name"] == "The Greek Theatre"
    assert row["venue_id"] == "73d6a380"
    assert row["city_name"] == "Berkeley"
    assert row["state_code"] == "CA"
    assert row["country_code"] == "US"
    assert row["venue_lat"] == pytest.approx(37.8732)
    assert row["venue_lon"] == pytest.approx(-122.2547)
    assert row["tour_name"] == "OK Computer Anniversary Tour"
    assert "Sold out" in (row["info_text"] or "")
    assert row["n_songs"] == 5


def test_parse_setlist_no_coords():
    payload = _full_payload()
    del payload["venue"]["city"]["coords"]
    row = SetlistFM._parse_setlist(payload)
    assert row is not None
    assert row["venue_lat"] is None
    assert row["venue_lon"] is None


def test_parse_setlist_no_venue():
    payload = _full_payload()
    del payload["venue"]
    row = SetlistFM._parse_setlist(payload)
    assert row is not None
    assert row["venue_name"] is None
    assert row["venue_id"] is None
    assert row["city_name"] is None


def test_parse_setlist_no_tour_no_info():
    payload = _full_payload()
    del payload["tour"]
    del payload["info"]
    row = SetlistFM._parse_setlist(payload)
    assert row is not None
    assert row["tour_name"] is None
    assert row["info_text"] is None


def test_parse_setlist_empty_sets():
    payload = _full_payload()
    payload["sets"] = {"set": []}
    row = SetlistFM._parse_setlist(payload)
    assert row is not None
    assert row["n_songs"] == 0


def test_parse_setlist_malformed_date_returns_none():
    payload = _full_payload()
    payload["eventDate"] = "not a date"
    assert SetlistFM._parse_setlist(payload) is None


def test_parse_setlist_missing_required_returns_none():
    payload = _full_payload()
    del payload["eventDate"]
    assert SetlistFM._parse_setlist(payload) is None
```

- [ ] **Step 2: Run tests; verify they fail**

```bash
chflags nohidden .venv/lib/python3.11/site-packages/*.pth 2>/dev/null || true
uv run --no-sync pytest tests/sources/test_setlistfm.py -v
```

Expected: All 7 tests FAIL (either AttributeError on `_parse_setlist` or import-time error because the source still has the stub).

- [ ] **Step 3: Implement `_parse_setlist` (replacing the stub for now; full class in next tasks)**

Replace `src/eia/sources/setlistfm.py` body with:

```python
"""Setlist.fm API source.

Pulls historical concert setlists by (country, state, year) partition into the
`setlistfm_setlists` staging table. A future `build_events.py` will UNION
this with the Ticketmaster staging table into the `events` table.

See docs/superpowers/specs/2026-05-10-setlistfm-source-design.md for design.

Free key: https://api.setlist.fm/docs/1.0/index.html
Polite rate: 1 req/sec.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml  # type: ignore[import-untyped]

from eia.clients import RateLimitedClient
from eia.config import settings
from eia.sources.base import Source
from eia.sources.registry import register

logger = logging.getLogger(__name__)


class SetlistFM(Source):
    name = "setlistfm"
    target_table = "setlistfm_setlists"
    raw_format = "json"

    BASE_URL = "https://api.setlist.fm"
    PAGE_SIZE = 20  # fixed by the API

    def __init__(
        self,
        country_code: str | None = None,
        years: list[int] | None = None,
        state_codes: list[str] | None = None,
        max_pages: int | None = None,
    ) -> None:
        cfg = self._load_config()
        self.api_key = settings.setlistfm_api_key
        self.country_code = country_code or cfg["country_code"]
        self.years = years or cfg["default_years"]
        self.state_codes = state_codes or cfg["default_states"]
        self.requests_per_second = cfg["requests_per_second"]
        self.max_pages_per_partition = (
            max_pages if max_pages is not None else cfg["max_pages_per_partition"]
        )

    @staticmethod
    def _load_config() -> dict[str, Any]:
        with open("configs/sources.yaml") as f:
            return yaml.safe_load(f)["setlistfm"]  # type: ignore[no-any-return]

    def _check_key(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "SETLISTFM_API_KEY not set. Get a free key at "
                "https://api.setlist.fm/docs/1.0/index.html"
            )

    def fetch(self) -> Path:
        raise NotImplementedError("Implemented in Task 4")

    def to_cleaned(self, raw_path: Path) -> Path:
        raise NotImplementedError("Implemented in Task 5")

    @staticmethod
    def _parse_setlist(payload: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize a single setlist response into the staging-schema row.

        Returns None if a required field is missing or unparseable.
        """
        event_date_raw = payload.get("eventDate")
        if not event_date_raw or not isinstance(event_date_raw, str):
            return None
        try:
            event_date = datetime.strptime(event_date_raw, "%d-%m-%Y").date()
        except ValueError:
            return None

        artist = payload.get("artist") or {}
        venue = payload.get("venue") or {}
        city = venue.get("city") or {}
        coords = city.get("coords") or {}
        tour = payload.get("tour") or {}
        sets = payload.get("sets", {}).get("set") or []
        n_songs = sum(len(s.get("song", []) or []) for s in sets)

        return {
            "setlist_id": payload.get("id"),
            "artist_name": artist.get("name"),
            "artist_mbid": artist.get("mbid"),
            "event_date": event_date,
            "venue_name": venue.get("name"),
            "venue_id": venue.get("id"),
            "city_name": city.get("name"),
            "state_code": city.get("stateCode"),
            "country_code": (city.get("country") or {}).get("code"),
            "venue_lat": coords.get("lat"),
            "venue_lon": coords.get("long"),
            "tour_name": tour.get("name"),
            "info_text": payload.get("info"),
            "n_songs": n_songs,
            "raw_payload": json.dumps(payload),
            "fetched_at": datetime.utcnow(),
        }


register(SetlistFM.name, SetlistFM)
```

- [ ] **Step 4: Run tests; verify all 7 pass**

```bash
chflags nohidden .venv/lib/python3.11/site-packages/*.pth 2>/dev/null || true
uv run --no-sync pytest tests/sources/test_setlistfm.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/sources/test_setlistfm.py src/eia/sources/setlistfm.py
git commit -m "feat(setlistfm): _parse_setlist helper + tests"
```

---

## Task 3: `_fetch_partition` (Pagination Logic, TDD)

**Files:**
- Modify: `src/eia/sources/setlistfm.py` (add `_fetch_partition`)
- Modify: `tests/sources/test_setlistfm.py` (add pagination tests)

- [ ] **Step 1: Add pagination tests**

Append to `tests/sources/test_setlistfm.py`:

```python
import math
from unittest.mock import patch


def _fake_response(total: int, page: int, items_per_page: int = 20) -> dict:
    """Build a Setlist.fm-shaped page response with `total` overall and a
    page of synthetic setlists (or empty if past the data)."""
    start = (page - 1) * items_per_page
    n_on_page = max(0, min(items_per_page, total - start))
    setlists = [
        {
            "id": f"sl_{start + i}",
            "eventDate": "01-06-2022",
            "artist": {"name": f"Artist {start + i}"},
            "venue": {
                "name": "Venue",
                "city": {
                    "name": "Berkeley",
                    "stateCode": "CA",
                    "country": {"code": "US"},
                },
            },
            "sets": {"set": []},
        }
        for i in range(n_on_page)
    ]
    return {
        "total": total,
        "page": page,
        "itemsPerPage": items_per_page,
        "setlist": setlists,
    }


def test_fetch_partition_exhaustive_under_cap(tmp_path, monkeypatch):
    """When total < 10000, fetch every page up to ceil(total/20)."""
    monkeypatch.setattr(
        "eia.config.settings.setlistfm_api_key", "test-key", raising=False
    )
    monkeypatch.setattr("eia.config.settings.eia_data_root", str(tmp_path))
    src = SetlistFM(country_code="US", years=[2022], state_codes=["CA"])

    total = 437  # arbitrary under-cap number
    calls: list[int] = []

    def fake_fetch_page(client, country, state, year, page):
        calls.append(page)
        return _fake_response(total, page)

    out_dir = tmp_path / "raw" / "setlistfm" / "US_CA_2022"
    with patch.object(SetlistFM, "_fetch_page", side_effect=fake_fetch_page):
        n_pages = src._fetch_partition(None, "US", "CA", 2022, out_dir)  # type: ignore[arg-type]

    expected_pages = math.ceil(total / 20)
    assert n_pages == expected_pages
    assert calls == list(range(1, expected_pages + 1))
    written = sorted(out_dir.glob("page_*.json"))
    assert len(written) == expected_pages


def test_fetch_partition_caps_at_max_pages(tmp_path, monkeypatch):
    """When total >= 10000, stop at max_pages_per_partition (500 by default)."""
    monkeypatch.setattr(
        "eia.config.settings.setlistfm_api_key", "test-key", raising=False
    )
    monkeypatch.setattr("eia.config.settings.eia_data_root", str(tmp_path))
    src = SetlistFM(
        country_code="US", years=[2022], state_codes=["CA"], max_pages=3
    )  # max_pages=3 for a fast test

    calls: list[int] = []

    def fake_fetch_page(client, country, state, year, page):
        calls.append(page)
        return _fake_response(50_000, page)  # 50K total → would be 2500 pages

    out_dir = tmp_path / "raw" / "setlistfm" / "US_CA_2022"
    with patch.object(SetlistFM, "_fetch_page", side_effect=fake_fetch_page):
        n_pages = src._fetch_partition(None, "US", "CA", 2022, out_dir)  # type: ignore[arg-type]

    assert n_pages == 3
    assert calls == [1, 2, 3]


def test_fetch_partition_empty_partition(tmp_path, monkeypatch):
    """When total == 0, write zero files and return 0."""
    monkeypatch.setattr(
        "eia.config.settings.setlistfm_api_key", "test-key", raising=False
    )
    monkeypatch.setattr("eia.config.settings.eia_data_root", str(tmp_path))
    src = SetlistFM(country_code="US", years=[2022], state_codes=["WY"])

    def fake_fetch_page(client, country, state, year, page):
        return _fake_response(0, page)

    out_dir = tmp_path / "raw" / "setlistfm" / "US_WY_2022"
    out_dir.mkdir(parents=True)
    with patch.object(SetlistFM, "_fetch_page", side_effect=fake_fetch_page):
        n_pages = src._fetch_partition(None, "US", "WY", 2022, out_dir)  # type: ignore[arg-type]

    assert n_pages == 0
    assert list(out_dir.glob("page_*.json")) == []
```

- [ ] **Step 2: Run tests; verify they fail**

```bash
chflags nohidden .venv/lib/python3.11/site-packages/*.pth 2>/dev/null || true
uv run --no-sync pytest tests/sources/test_setlistfm.py::test_fetch_partition_exhaustive_under_cap -v
```

Expected: FAIL (AttributeError: SetlistFM has no `_fetch_partition`).

- [ ] **Step 3: Implement `_fetch_partition` and `_fetch_page`**

Replace the `fetch` placeholder in `src/eia/sources/setlistfm.py` with these methods (insert before the `to_cleaned` placeholder):

```python
    def _fetch_page(
        self,
        client: RateLimitedClient,
        country: str,
        state: str,
        year: int,
        page: int,
    ) -> dict[str, Any]:
        """Single API call. Caller is responsible for rate limiting via client."""
        params = {
            "countryCode": country,
            "stateCode": state,
            "year": year,
            "p": page,
        }
        headers = {"Accept": "application/json", "x-api-key": self.api_key}
        return client.get_json(  # type: ignore[no-any-return]
            "/rest/1.0/search/setlists",
            params=params,
            headers=headers,
        )

    def _fetch_partition(
        self,
        client: RateLimitedClient | None,
        country: str,
        state: str,
        year: int,
        out_dir: Path,
    ) -> int:
        """Paginate one (country, state, year) partition; write page files.

        Returns the number of pages written. Logs cap-warnings when the
        partition's `total` exceeds `max_pages_per_partition * PAGE_SIZE`.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        page = 1
        first = self._fetch_page(client, country, state, year, page)
        total = int(first.get("total", 0))
        if total == 0:
            logger.info(
                "Setlist.fm %s %s %d: empty partition", country, state, year
            )
            return 0

        max_pages = self.max_pages_per_partition
        n_to_pull = min(
            max_pages,
            (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE,
        )
        if total > max_pages * self.PAGE_SIZE:
            logger.warning(
                "Setlist.fm %s %s %d: total %d exceeds cap (%d pages); "
                "some setlists unreachable in this partition",
                country, state, year, total, max_pages,
            )

        (out_dir / f"page_{page:04d}.json").write_text(json.dumps(first))
        for page in range(2, n_to_pull + 1):
            data = self._fetch_page(client, country, state, year, page)
            (out_dir / f"page_{page:04d}.json").write_text(json.dumps(data))

        logger.info(
            "Setlist.fm %s %s %d: pulled %d pages (total=%d)",
            country, state, year, n_to_pull, total,
        )
        return n_to_pull
```

- [ ] **Step 4: Run tests; verify all 3 partition tests pass**

```bash
chflags nohidden .venv/lib/python3.11/site-packages/*.pth 2>/dev/null || true
uv run --no-sync pytest tests/sources/test_setlistfm.py -v
```

Expected: 10 passed (7 parse + 3 partition).

- [ ] **Step 5: Commit**

```bash
git add tests/sources/test_setlistfm.py src/eia/sources/setlistfm.py
git commit -m "feat(setlistfm): _fetch_partition pagination logic with cap detection"
```

---

## Task 4: `fetch()` Entry Point

**Files:**
- Modify: `src/eia/sources/setlistfm.py` (replace fetch placeholder)

- [ ] **Step 1: Implement `fetch`**

Replace the `fetch` placeholder in `src/eia/sources/setlistfm.py`:

```python
    def fetch(self) -> Path:
        """Pull all configured (country, state, year) partitions into raw_dir.

        Returns the raw_dir path; per-partition output lives in subdirs named
        `<country>_<state>_<year>/page_NNNN.json`.
        """
        self._check_key()
        out_root = self.raw_dir
        out_root.mkdir(parents=True, exist_ok=True)
        total_pages = 0
        with RateLimitedClient(
            self.BASE_URL,
            requests_per_second=self.requests_per_second,
            timeout_s=30.0,
        ) as client:
            for year in self.years:
                for state in self.state_codes:
                    partition_dir = out_root / f"{self.country_code}_{state}_{year}"
                    pages = self._fetch_partition(
                        client, self.country_code, state, year, partition_dir
                    )
                    total_pages += pages
        logger.info("Setlist.fm fetch complete: %d pages total", total_pages)
        return out_root
```

- [ ] **Step 2: Confirm tests still pass**

```bash
chflags nohidden .venv/lib/python3.11/site-packages/*.pth 2>/dev/null || true
uv run --no-sync pytest tests/sources/test_setlistfm.py -v
```

Expected: 10 passed (no new tests; just regression-check).

- [ ] **Step 3: Commit**

```bash
git add src/eia/sources/setlistfm.py
git commit -m "feat(setlistfm): fetch() entry point over all configured partitions"
```

---

## Task 5: `to_cleaned()`

**Files:**
- Modify: `src/eia/sources/setlistfm.py` (replace to_cleaned placeholder)
- Modify: `tests/sources/test_setlistfm.py` (add to_cleaned tests)

- [ ] **Step 1: Add to_cleaned tests**

Append to `tests/sources/test_setlistfm.py`:

```python
def test_to_cleaned_writes_parquet_with_expected_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "eia.config.settings.setlistfm_api_key", "test-key", raising=False
    )
    monkeypatch.setattr("eia.config.settings.eia_data_root", str(tmp_path))
    src = SetlistFM()
    raw_root = tmp_path / "raw" / "setlistfm"
    partition = raw_root / "US_CA_2022"
    partition.mkdir(parents=True)
    page1 = _fake_response(40, 1)
    page2 = _fake_response(40, 2)
    (partition / "page_0001.json").write_text(json.dumps(page1))
    (partition / "page_0002.json").write_text(json.dumps(page2))

    out_path = src.to_cleaned(raw_root)

    assert out_path.exists() and out_path.suffix == ".parquet"
    df = pl.read_parquet(out_path)
    assert df.height == 40
    expected = {
        "setlist_id", "artist_name", "artist_mbid", "event_date",
        "venue_name", "venue_id", "city_name", "state_code", "country_code",
        "venue_lat", "venue_lon", "tour_name", "info_text", "n_songs",
        "raw_payload", "fetched_at",
    }
    assert expected.issubset(set(df.columns))


def test_to_cleaned_deduplicates_setlist_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "eia.config.settings.setlistfm_api_key", "test-key", raising=False
    )
    monkeypatch.setattr("eia.config.settings.eia_data_root", str(tmp_path))
    src = SetlistFM()
    raw_root = tmp_path / "raw" / "setlistfm"
    partition = raw_root / "US_CA_2022"
    partition.mkdir(parents=True)

    page = _fake_response(20, 1)
    # Force a duplicate setlist_id between two pages
    page2 = _fake_response(20, 2)
    page2["setlist"][0]["id"] = page["setlist"][0]["id"]
    (partition / "page_0001.json").write_text(json.dumps(page))
    (partition / "page_0002.json").write_text(json.dumps(page2))

    out_path = src.to_cleaned(raw_root)
    df = pl.read_parquet(out_path)
    # Duplicate id should be deduped; 40 total - 1 dup = 39
    assert df["setlist_id"].n_unique() == df.height
    assert df.height == 39


def test_to_cleaned_skips_unparseable_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "eia.config.settings.setlistfm_api_key", "test-key", raising=False
    )
    monkeypatch.setattr("eia.config.settings.eia_data_root", str(tmp_path))
    src = SetlistFM()
    raw_root = tmp_path / "raw" / "setlistfm"
    partition = raw_root / "US_CA_2022"
    partition.mkdir(parents=True)

    page = _fake_response(2, 1)
    page["setlist"][1]["eventDate"] = "garbage"  # unparseable
    (partition / "page_0001.json").write_text(json.dumps(page))

    out_path = src.to_cleaned(raw_root)
    df = pl.read_parquet(out_path)
    assert df.height == 1  # only the valid row survives
```

- [ ] **Step 2: Run tests; verify they fail**

```bash
chflags nohidden .venv/lib/python3.11/site-packages/*.pth 2>/dev/null || true
uv run --no-sync pytest tests/sources/test_setlistfm.py -v
```

Expected: 10 passed, 3 failed (the new to_cleaned tests).

- [ ] **Step 3: Implement `to_cleaned`**

Replace the `to_cleaned` placeholder in `src/eia/sources/setlistfm.py`:

```python
    def to_cleaned(self, raw_path: Path) -> Path:
        """Read all page JSONs under raw_path, flatten, dedup, write parquet."""
        rows: list[dict[str, Any]] = []
        for partition_dir in sorted(raw_path.iterdir()):
            if not partition_dir.is_dir():
                continue
            for page_file in sorted(partition_dir.glob("page_*.json")):
                data = json.loads(page_file.read_text())
                for sl in data.get("setlist", []):
                    row = self._parse_setlist(sl)
                    if row is not None:
                        rows.append(row)

        df = pl.DataFrame(
            rows,
            schema={
                "setlist_id": pl.Utf8,
                "artist_name": pl.Utf8,
                "artist_mbid": pl.Utf8,
                "event_date": pl.Date,
                "venue_name": pl.Utf8,
                "venue_id": pl.Utf8,
                "city_name": pl.Utf8,
                "state_code": pl.Utf8,
                "country_code": pl.Utf8,
                "venue_lat": pl.Float64,
                "venue_lon": pl.Float64,
                "tour_name": pl.Utf8,
                "info_text": pl.Utf8,
                "n_songs": pl.Int32,
                "raw_payload": pl.Utf8,
                "fetched_at": pl.Datetime,
            },
        )
        # Dedup on setlist_id, keep first occurrence
        if df.height > 0:
            df = df.unique(subset=["setlist_id"], keep="first")

        out = self.cleaned_dir / "setlists.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out)
        logger.info("Setlist.fm to_cleaned: %d setlists written to %s", df.height, out)
        return out
```

- [ ] **Step 4: Run tests; verify all 13 pass**

```bash
chflags nohidden .venv/lib/python3.11/site-packages/*.pth 2>/dev/null || true
uv run --no-sync pytest tests/sources/test_setlistfm.py -v
```

Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/sources/test_setlistfm.py src/eia/sources/setlistfm.py
git commit -m "feat(setlistfm): to_cleaned() flattens + dedups page JSONs to parquet"
```

---

## Task 6: Smoke Pull (California 2022, max_pages=2)

**Files:**
- Modify: `Makefile` (add chflags workaround to pull-setlistfm)

- [ ] **Step 1: Update Makefile target**

Find the existing `pull-setlistfm` target in `Makefile`:

```makefile
pull-setlistfm:  ## Pull Setlist.fm setlists for sampled venues.
	uv run eia pull setlistfm
```

Replace with:

```makefile
pull-setlistfm:  ## Pull Setlist.fm setlists for the configured partitions.
	@chflags nohidden .venv/lib/python*/site-packages/*.pth 2>/dev/null || true
	uv run --no-sync eia pull setlistfm
```

- [ ] **Step 2: Run a quick smoke pull with max_pages limit**

Run a small Python invocation that limits to 2 pages (40 setlists) for fast verification:

```bash
chflags nohidden .venv/lib/python3.11/site-packages/*.pth 2>/dev/null || true
uv run --no-sync python -c "
from eia.sources.setlistfm import SetlistFM
from eia.warehouse import get_warehouse

src = SetlistFM(max_pages=2)  # only 2 pages = 40 setlists for fast test
raw = src.fetch()
cleaned = src.to_cleaned(raw)
src.load(cleaned, get_warehouse())
print(f'fetched={raw}, cleaned={cleaned}')

# Quick verification
wh = get_warehouse()
print(wh.query('SELECT COUNT(*) AS n FROM setlistfm_setlists'))
print(wh.query('SELECT artist_name, event_date, venue_name, city_name, state_code FROM setlistfm_setlists ORDER BY event_date DESC LIMIT 5'))
"
```

Expected output:
- ~40 setlists in `setlistfm_setlists`
- Sample shows CA-2022 setlists with artist + venue names

- [ ] **Step 3: If smoke pull works, commit Makefile change**

```bash
git add Makefile
git commit -m "chore(setlistfm): pull-setlistfm Makefile target with chflags workaround"
```

---

## Task 7: Lint, Format, Mypy, Full Test Suite, Merge

**Files:** No new files; verifies the whole change.

- [ ] **Step 1: Run lint**

```bash
chflags nohidden .venv/lib/python3.11/site-packages/*.pth 2>/dev/null || true
uv run --no-sync ruff check src tests
```

Expected: All checks passed.

- [ ] **Step 2: Run formatter**

```bash
uv run --no-sync ruff format src tests
```

If anything was reformatted, commit:

```bash
git add -u
git commit -m "chore: ruff format"
```

- [ ] **Step 3: Run mypy**

```bash
uv run --no-sync mypy src
```

Expected: Success: no issues found.

If mypy complains about untyped yaml import in setlistfm.py, ensure `import yaml  # type: ignore[import-untyped]` is on the yaml import line.

- [ ] **Step 4: Run full pytest suite**

```bash
uv run --no-sync pytest --no-cov -q
```

Expected: All tests pass (previously 71 + 13 new = 84 tests).

- [ ] **Step 5: Merge feature branch to main**

```bash
git checkout main
git merge --no-ff feature/setlistfm-source -m "Merge branch 'feature/setlistfm-source': real Setlist.fm source + CA 2022 smoke pull"
git branch -d feature/setlistfm-source
git log --oneline -5
```

---

## Self-Review Notes

- **Spec coverage:**
  - Migration → Task 1 ✓
  - Config → Task 1 ✓
  - `_parse_setlist` + 7 unit tests → Task 2 ✓
  - `_fetch_partition` + 3 unit tests → Task 3 ✓
  - `fetch()` entry → Task 4 ✓
  - `to_cleaned()` + 3 unit tests → Task 5 ✓
  - Smoke pull (CA 2022) → Task 6 ✓
  - Lint/format/mypy/tests → Task 7 ✓
  - Cap-busting subdivision: explicitly deferred per spec (logged WARNING only, no implementation)
  - Venue geocoding gap: documented in spec, no task needed (out of scope)
  - `build_events.py`: out of scope per spec; future cycle

- **Type consistency:** `_parse_setlist` returns `dict | None`. `to_cleaned` filters out Nones. Pagination methods return `int` (page count). `fetch` returns `Path` (matches ABC).

- **No placeholders.** All steps have concrete code or commands.

- **Frequent commits:** Each task commits its own slice. 7 tasks, ~7 commits before merge.
