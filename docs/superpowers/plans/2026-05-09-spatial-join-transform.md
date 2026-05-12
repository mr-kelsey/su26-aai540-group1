# Spatial-Join Transform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the lat/lon → county_fips spatial-join transform that unblocks event-source `to_cleaned()` methods, plus the TIGER source change that produces the geometry artifact it consumes.

**Architecture:** TIGER source extended to write a sibling `counties_geo_<year>.parquet` (GeoParquet, WGS84) alongside its existing flat Parquet. New `src/eia/transforms/geo.py` exposes a single `attach_county_fips(df, …) -> pl.DataFrame` function with a module-level GeoDataFrame cache, a coordinate sanity gate that maps null/sentinel/out-of-range rows to null `county_fips`, a `geopandas.sjoin(predicate="within", how="left")` against the cached counties, and a single INFO log line per call summarising hit/miss counts.

**Tech Stack:** Polars 1.x, GeoPandas 1.x + shapely 2.x + pyarrow 17.x (all in `pyproject.toml`), pytest 8.x, ruff, mypy strict.

---

## File Structure

**Create:**
- `src/eia/transforms/geo.py` — public function + module cache + private helpers
- `tests/transforms/__init__.py` — empty package marker
- `tests/transforms/conftest.py` — autouse fixture planting a fake counties GeoParquet under tmp_path and clearing the module cache
- `tests/transforms/test_geo.py` — unit tests for `attach_county_fips`
- `tests/sources/__init__.py` — empty package marker
- `tests/sources/test_tiger.py` — unit test for the TIGER `to_cleaned()` extension

**Modify:**
- `src/eia/sources/tiger.py` — extend `to_cleaned()` to also write `counties_geo_<year>.parquet`
- `src/eia/transforms/__init__.py` — re-export `attach_county_fips`

---

## Task 1: TIGER source — write counties_geo sibling Parquet

Extend [`tiger.py`](../../../src/eia/sources/tiger.py) so its `to_cleaned()` writes a second artifact next to the existing flat Parquet: `counties_geo_<year>.parquet`, two columns (`GEOID`, `geometry`), CRS `EPSG:4326`. Idempotent: skip if the file already exists and is non-empty. Drives this with a unit test that monkeypatches `geopandas.read_file` to return a fake counties GeoDataFrame (no real shapefile in tests).

**Files:**
- Create: `tests/sources/__init__.py`
- Create: `tests/sources/test_tiger.py`
- Modify: `src/eia/sources/tiger.py` (the `to_cleaned()` method)

- [ ] **Step 1: Create the empty package marker for `tests/sources/`**

```bash
: > tests/sources/__init__.py
```

- [ ] **Step 2: Write the failing test in `tests/sources/test_tiger.py`**

```python
"""Tests for the TIGER counties source's cleaned outputs."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

from eia.sources.tiger import TIGERCounties


@pytest.fixture()
def fake_tiger_gdf() -> gpd.GeoDataFrame:
    """One-county fake matching the TIGER county-shapefile schema."""
    return gpd.GeoDataFrame(
        {
            "STATEFP": ["06"],
            "COUNTYFP": ["073"],
            "GEOID": ["06073"],
            "NAMELSAD": ["San Diego County"],
            "CBSAFP": ["41740"],
        },
        geometry=[box(-117.5, 32.5, -116.0, 33.5)],
        crs="EPSG:4269",  # TIGER ships as NAD83
    )


def test_to_cleaned_writes_flat_and_geo_parquets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_tiger_gdf: gpd.GeoDataFrame,
) -> None:
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    monkeypatch.setattr("geopandas.read_file", lambda *a, **kw: fake_tiger_gdf)

    src = TIGERCounties(year=2023)
    fake_raw = tmp_path / "raw" / "tiger" / "tl_2023_us_county.zip"
    fake_raw.parent.mkdir(parents=True, exist_ok=True)
    fake_raw.write_bytes(b"")  # path only — read_file is patched out

    flat_path = src.to_cleaned(fake_raw)
    geo_path = flat_path.parent / "counties_geo_2023.parquet"

    assert flat_path.exists(), "existing flat parquet must still be written"
    assert geo_path.exists(), "new counties_geo parquet must be written"

    geo_back = gpd.read_parquet(geo_path)
    assert list(geo_back.columns) == ["GEOID", "geometry"]
    assert geo_back.crs is not None and geo_back.crs.to_epsg() == 4326
    assert geo_back["GEOID"].tolist() == ["06073"]


def test_to_cleaned_geo_parquet_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_tiger_gdf: gpd.GeoDataFrame,
) -> None:
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    monkeypatch.setattr("geopandas.read_file", lambda *a, **kw: fake_tiger_gdf)

    src = TIGERCounties(year=2023)
    fake_raw = tmp_path / "raw" / "tiger" / "tl_2023_us_county.zip"
    fake_raw.parent.mkdir(parents=True, exist_ok=True)
    fake_raw.write_bytes(b"")

    flat_path = src.to_cleaned(fake_raw)
    geo_path = flat_path.parent / "counties_geo_2023.parquet"
    first_mtime = geo_path.stat().st_mtime_ns

    # Second call must not rewrite the geo file.
    src.to_cleaned(fake_raw)
    assert geo_path.stat().st_mtime_ns == first_mtime
```

- [ ] **Step 3: Run the test to confirm it fails**

```bash
uv run pytest tests/sources/test_tiger.py -v --no-cov
```

Expected: both tests FAIL — the geo parquet does not yet exist after `to_cleaned`.

- [ ] **Step 4: Modify `src/eia/sources/tiger.py` to also write the geo parquet**

In `TIGERCounties.to_cleaned`, after the existing block that writes the flat parquet (`cleaned.write_parquet(out)`), add the geo-parquet write. The full new method body is:

```python
def to_cleaned(self, raw_path: Path) -> Path:
    """Read the shapefile via geopandas, derive centroid + sq miles, write Parquet.

    Also writes a sibling counties_geo_<year>.parquet (GEOID + polygon, WGS84)
    for downstream spatial-join transforms. The flat Parquet is the warehouse
    loader's input; the geo Parquet is consumed only by transforms/geo.py.

    Note: shapefile centroids in geographic CRS are inaccurate; we project
    to NAD83 / Conus Albers (EPSG:5070) for area + centroid math.
    """
    import geopandas as gpd

    gdf = gpd.read_file(f"zip://{raw_path}")

    gdf_aea = gdf.to_crs(epsg=5070)
    gdf["land_area_sqmi"] = gdf_aea.geometry.area / 2_589_988.11  # m^2 -> mi^2
    centroids = gdf_aea.geometry.centroid.to_crs(epsg=4326)
    gdf["latitude"] = centroids.y
    gdf["longitude"] = centroids.x

    cleaned = pl.DataFrame(
        {
            "county_fips": gdf["GEOID"].astype(str).tolist(),
            "state_fips": gdf["STATEFP"].astype(str).tolist(),
            "state_abbr": [None] * len(gdf),
            "county_name": gdf["NAMELSAD"].astype(str).tolist(),
            "cbsa_code": [
                str(c) if str(c) not in ("None", "nan", "") else None
                for c in gdf.get("CBSAFP", [None] * len(gdf))
            ],
            "cbsa_name": [None] * len(gdf),
            "latitude": gdf["latitude"].astype(float).tolist(),
            "longitude": gdf["longitude"].astype(float).tolist(),
            "population": [None] * len(gdf),
            "land_area_sqmi": gdf["land_area_sqmi"].astype(float).tolist(),
            "source": [f"tiger_{self.year}"] * len(gdf),
            "fetched_at": [self.now_utc()] * len(gdf),
        }
    )
    out = self.cleaned_dir / f"counties_{self.year}.parquet"
    cleaned.write_parquet(out)

    geo_out = self.cleaned_dir / f"counties_geo_{self.year}.parquet"
    if not (geo_out.exists() and geo_out.stat().st_size > 0):
        gdf.to_crs(epsg=4326)[["GEOID", "geometry"]].to_parquet(geo_out)

    return out
```

- [ ] **Step 5: Run the test to confirm it passes**

```bash
uv run pytest tests/sources/test_tiger.py -v --no-cov
```

Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/sources/__init__.py tests/sources/test_tiger.py src/eia/sources/tiger.py
git commit -m "feat(tiger): write counties_geo sibling Parquet for spatial joins"
```

---

## Task 2: Index loader with module-level cache

Create `src/eia/transforms/geo.py` with a private `_load_counties_gdf(year)` that reads the counties GeoParquet from `settings.cleaned_dir / "tiger" / f"counties_geo_{year}.parquet"` and caches it in a module-level dict keyed by year. Driven by a test that calls the loader twice and asserts only one disk read.

**Files:**
- Create: `tests/transforms/__init__.py`
- Create: `tests/transforms/conftest.py`
- Create: `tests/transforms/test_geo.py`
- Create: `src/eia/transforms/geo.py`

- [ ] **Step 1: Create the empty package marker**

```bash
: > tests/transforms/__init__.py
```

- [ ] **Step 2: Write the conftest fixture in `tests/transforms/conftest.py`**

```python
"""Shared fixtures for transforms tests.

Plants a tiny counties_geo Parquet at the path geo.py expects (under
tmp_path), so each test runs against the same compact fixture without ever
touching real TIGER data. Also clears the module-level cache between tests.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box


@pytest.fixture(autouse=True)
def fake_counties_geo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two fake counties:
        GEOID 00001  bbox lon=[0,2],   lat=[0,2]
        GEOID 00002  bbox lon=[10,12], lat=[10,12]
    """
    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)

    geo_dir = tmp_path / "cleaned" / "tiger"
    geo_dir.mkdir(parents=True, exist_ok=True)
    geo_path = geo_dir / "counties_geo_2023.parquet"

    gdf = gpd.GeoDataFrame(
        {"GEOID": ["00001", "00002"]},
        geometry=[box(0.0, 0.0, 2.0, 2.0), box(10.0, 10.0, 12.0, 12.0)],
        crs="EPSG:4326",
    )
    gdf.to_parquet(geo_path)

    from eia.transforms import geo

    geo._COUNTIES_CACHE.clear()
    yield geo_path
    geo._COUNTIES_CACHE.clear()
```

- [ ] **Step 3: Write the failing loader test in `tests/transforms/test_geo.py`**

```python
"""Tests for transforms.geo.attach_county_fips and its loader."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest


def test_loader_caches_after_first_read(
    monkeypatch: pytest.MonkeyPatch, fake_counties_geo: Path
) -> None:
    """Second call must not re-read the GeoParquet from disk."""
    from eia.transforms import geo

    real_read = gpd.read_parquet
    calls = {"n": 0}

    def counting_read(*args: object, **kwargs: object) -> gpd.GeoDataFrame:
        calls["n"] += 1
        return real_read(*args, **kwargs)

    monkeypatch.setattr("geopandas.read_parquet", counting_read)

    first = geo._load_counties_gdf(2023)
    second = geo._load_counties_gdf(2023)

    assert first is second
    assert calls["n"] == 1
```

- [ ] **Step 4: Run the test to confirm it fails**

```bash
uv run pytest tests/transforms/test_geo.py::test_loader_caches_after_first_read -v --no-cov
```

Expected: FAIL — `eia.transforms.geo` module does not exist.

- [ ] **Step 5: Create `src/eia/transforms/geo.py` with just enough to pass**

```python
"""lat/lon -> county_fips spatial-join transform.

Consumed by event sources' to_cleaned() methods to attach county_fips to
event rows. Reads the counties_geo Parquet produced by the TIGER source.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd

from eia.config import settings

logger = logging.getLogger(__name__)

_COUNTIES_CACHE: dict[int, gpd.GeoDataFrame] = {}


def _counties_path(year: int) -> Path:
    return settings.cleaned_dir / "tiger" / f"counties_geo_{year}.parquet"


def _load_counties_gdf(year: int) -> gpd.GeoDataFrame:
    cached = _COUNTIES_CACHE.get(year)
    if cached is not None:
        return cached
    path = _counties_path(year)
    gdf = gpd.read_parquet(path)
    _COUNTIES_CACHE[year] = gdf
    return gdf
```

- [ ] **Step 6: Run the test to confirm it passes**

```bash
uv run pytest tests/transforms/test_geo.py::test_loader_caches_after_first_read -v --no-cov
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/transforms/__init__.py tests/transforms/conftest.py tests/transforms/test_geo.py src/eia/transforms/geo.py
git commit -m "feat(transforms): geo.py module with cached counties loader"
```

---

## Task 3: File-not-found error path

When the counties GeoParquet does not exist on disk, `_load_counties_gdf` must raise `FileNotFoundError` with a message that names `make pull-tiger` as the fix.

**Files:**
- Modify: `src/eia/transforms/geo.py`
- Modify: `tests/transforms/test_geo.py`

- [ ] **Step 1: Add the failing test to `tests/transforms/test_geo.py`**

Append to the file:

```python
def test_loader_raises_clear_error_when_geo_parquet_missing(
    fake_counties_geo: Path,
) -> None:
    """If the file is missing, the error message must point the user at make pull-tiger."""
    from eia.transforms import geo

    fake_counties_geo.unlink()  # delete the planted fixture
    geo._COUNTIES_CACHE.clear()

    with pytest.raises(FileNotFoundError, match=r"make pull-tiger"):
        geo._load_counties_gdf(2023)
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
uv run pytest tests/transforms/test_geo.py::test_loader_raises_clear_error_when_geo_parquet_missing -v --no-cov
```

Expected: FAIL — `gpd.read_parquet` raises a generic file-not-found from pyarrow without our hint.

- [ ] **Step 3: Add the existence check in `src/eia/transforms/geo.py`**

Replace the existing `_load_counties_gdf` body with:

```python
def _load_counties_gdf(year: int) -> gpd.GeoDataFrame:
    cached = _COUNTIES_CACHE.get(year)
    if cached is not None:
        return cached
    path = _counties_path(year)
    if not path.exists():
        raise FileNotFoundError(
            f"Counties GeoParquet not found at {path}. "
            f"Run `make pull-tiger` to generate it."
        )
    gdf = gpd.read_parquet(path)
    _COUNTIES_CACHE[year] = gdf
    return gdf
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
uv run pytest tests/transforms/test_geo.py::test_loader_raises_clear_error_when_geo_parquet_missing -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/transforms/test_geo.py src/eia/transforms/geo.py
git commit -m "feat(transforms): clear FileNotFoundError when counties geo Parquet is missing"
```

---

## Task 4: `attach_county_fips` — sanity gate + spatial join + reassembly

The full public function. One test exercises four cases on the fixture: in-county, off-county, null-coord, sentinel-coord. Implementation: build a stable row-position column, mask joinable rows by the sanity gate, run `gpd.sjoin(predicate="within", how="left")`, drop duplicate matches (boundary determinism), left-join the (position → GEOID) table back onto the original frame.

**Files:**
- Modify: `src/eia/transforms/geo.py`
- Modify: `tests/transforms/test_geo.py`

- [ ] **Step 1: Add the failing test to `tests/transforms/test_geo.py`**

Append to the file:

```python
import polars as pl


def test_attach_county_fips_full_matrix(fake_counties_geo: Path) -> None:
    """One test, four rows, one assertion per edge-case-matrix row."""
    from eia.transforms.geo import attach_county_fips

    df = pl.DataFrame(
        {
            "venue_name": ["inside-A", "inside-B", "off-county", "null-lat", "sentinel-zero", "out-of-range"],
            "venue_lat": [1.0, 11.0, 5.0, None, 0.0, 91.0],
            "venue_lon": [1.0, 11.0, 5.0, 1.0, 0.0, 1.0],
        }
    )

    out = attach_county_fips(df)

    # Same shape + new column.
    assert out.height == df.height
    assert out["venue_name"].to_list() == df["venue_name"].to_list()
    assert out.columns == [*df.columns, "county_fips"]

    fips = out["county_fips"].to_list()
    assert fips[0] == "00001"   # inside county A
    assert fips[1] == "00002"   # inside county B
    assert fips[2] is None      # off-county
    assert fips[3] is None      # null lat
    assert fips[4] is None      # (0, 0) sentinel
    assert fips[5] is None      # lat out of range
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
uv run pytest tests/transforms/test_geo.py::test_attach_county_fips_full_matrix -v --no-cov
```

Expected: FAIL — `attach_county_fips` is not defined.

- [ ] **Step 3: Add `attach_county_fips` to `src/eia/transforms/geo.py`**

Append to the file (also add the `polars` import at the top of the module):

```python
import polars as pl
```

```python
def attach_county_fips(
    df: pl.DataFrame,
    *,
    lat_col: str = "venue_lat",
    lon_col: str = "venue_lon",
    out_col: str = "county_fips",
    tiger_year: int = 2023,
) -> pl.DataFrame:
    """Attach a `county_fips` column derived from `lat_col`/`lon_col`.

    Rows with null, out-of-range, or (0, 0) sentinel coordinates skip the
    spatial join entirely and receive a null `county_fips`. Rows whose point
    falls inside no US county polygon also receive null. Boundary rows match
    whichever polygon `gpd.sjoin(predicate="within")` returns first
    (deterministic for a fixed TIGER year).
    """
    counties = _load_counties_gdf(tiger_year)

    pos = "__pos__"
    work = df.with_row_index(pos)

    is_valid = (
        pl.col(lat_col).is_not_null()
        & pl.col(lon_col).is_not_null()
        & pl.col(lat_col).is_between(-90.0, 90.0)
        & pl.col(lon_col).is_between(-180.0, 180.0)
        & ~((pl.col(lat_col) == 0.0) & (pl.col(lon_col) == 0.0))
    )
    joinable = work.filter(is_valid).select(pos, lat_col, lon_col)

    if joinable.height == 0:
        return work.with_columns(pl.lit(None).cast(pl.Utf8).alias(out_col)).drop(pos)

    joinable_pd = joinable.to_pandas()
    points = gpd.GeoDataFrame(
        joinable_pd[[pos]],
        geometry=gpd.points_from_xy(joinable_pd[lon_col], joinable_pd[lat_col]),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(
        points,
        counties[["GEOID", "geometry"]],
        how="left",
        predicate="within",
    ).drop_duplicates(subset=[pos], keep="first")

    fips_df = pl.from_pandas(joined[[pos, "GEOID"]]).select(
        pl.col(pos).cast(pl.UInt32),
        pl.col("GEOID").cast(pl.Utf8).alias(out_col),
    )

    return work.join(fips_df, on=pos, how="left").drop(pos)
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
uv run pytest tests/transforms/test_geo.py::test_attach_county_fips_full_matrix -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Run the whole test_geo.py file to confirm nothing regressed**

```bash
uv run pytest tests/transforms/test_geo.py -v --no-cov
```

Expected: all three tests so far PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/transforms/test_geo.py src/eia/transforms/geo.py
git commit -m "feat(transforms): attach_county_fips end-to-end with sanity gate and sjoin"
```

---

## Task 5: INFO log summary

Emit one INFO-level log line per call: `"attach_county_fips: <miss>/<total> unmapped (<bad_coords> bad coords, <off_county> off-county) [tiger_year=<year>]"`. Verified with pytest's `caplog`.

**Files:**
- Modify: `src/eia/transforms/geo.py`
- Modify: `tests/transforms/test_geo.py`

- [ ] **Step 1: Add the failing test to `tests/transforms/test_geo.py`**

Append:

```python
import logging


def test_attach_county_fips_logs_miss_summary(
    fake_counties_geo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from eia.transforms.geo import attach_county_fips

    df = pl.DataFrame(
        {
            "venue_lat": [1.0, 11.0, 5.0, None, 91.0],
            "venue_lon": [1.0, 11.0, 5.0, 1.0, 1.0],
        }
    )

    with caplog.at_level(logging.INFO, logger="eia.transforms.geo"):
        attach_county_fips(df)

    matched = [r for r in caplog.records if "attach_county_fips" in r.getMessage()]
    assert len(matched) == 1
    msg = matched[0].getMessage()
    # 5 rows total, 2 hits, 1 off-county, 2 bad coords -> 3 unmapped
    assert "3/5 unmapped" in msg
    assert "2 bad coords" in msg
    assert "1 off-county" in msg
    assert "tiger_year=2023" in msg
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
uv run pytest tests/transforms/test_geo.py::test_attach_county_fips_logs_miss_summary -v --no-cov
```

Expected: FAIL — no log line is emitted.

- [ ] **Step 3: Add the logging block to `attach_county_fips`**

Replace the body of `attach_county_fips` in `src/eia/transforms/geo.py` with the version below. Two changes from Task 4: count `bad_coords` from the gate, count `off_county` from the join result, then emit one INFO line just before returning.

```python
def attach_county_fips(
    df: pl.DataFrame,
    *,
    lat_col: str = "venue_lat",
    lon_col: str = "venue_lon",
    out_col: str = "county_fips",
    tiger_year: int = 2023,
) -> pl.DataFrame:
    """Attach a `county_fips` column derived from `lat_col`/`lon_col`.

    Rows with null, out-of-range, or (0, 0) sentinel coordinates skip the
    spatial join entirely and receive a null `county_fips`. Rows whose point
    falls inside no US county polygon also receive null. Boundary rows match
    whichever polygon `gpd.sjoin(predicate="within")` returns first
    (deterministic for a fixed TIGER year).

    Logs one INFO line per call summarising hit/miss counts.
    """
    counties = _load_counties_gdf(tiger_year)

    pos = "__pos__"
    work = df.with_row_index(pos)
    n = work.height

    is_valid = (
        pl.col(lat_col).is_not_null()
        & pl.col(lon_col).is_not_null()
        & pl.col(lat_col).is_between(-90.0, 90.0)
        & pl.col(lon_col).is_between(-180.0, 180.0)
        & ~((pl.col(lat_col) == 0.0) & (pl.col(lon_col) == 0.0))
    )
    joinable = work.filter(is_valid).select(pos, lat_col, lon_col)
    bad_coords = n - joinable.height

    if joinable.height == 0:
        _log_summary(n, bad_coords=bad_coords, off_county=0, tiger_year=tiger_year)
        return work.with_columns(pl.lit(None).cast(pl.Utf8).alias(out_col)).drop(pos)

    joinable_pd = joinable.to_pandas()
    points = gpd.GeoDataFrame(
        joinable_pd[[pos]],
        geometry=gpd.points_from_xy(joinable_pd[lon_col], joinable_pd[lat_col]),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(
        points,
        counties[["GEOID", "geometry"]],
        how="left",
        predicate="within",
    ).drop_duplicates(subset=[pos], keep="first")

    fips_df = pl.from_pandas(joined[[pos, "GEOID"]]).select(
        pl.col(pos).cast(pl.UInt32),
        pl.col("GEOID").cast(pl.Utf8).alias(out_col),
    )
    off_county = fips_df.filter(pl.col(out_col).is_null()).height

    _log_summary(n, bad_coords=bad_coords, off_county=off_county, tiger_year=tiger_year)
    return work.join(fips_df, on=pos, how="left").drop(pos)


def _log_summary(total: int, *, bad_coords: int, off_county: int, tiger_year: int) -> None:
    miss = bad_coords + off_county
    logger.info(
        "attach_county_fips: %d/%d unmapped (%d bad coords, %d off-county) [tiger_year=%d]",
        miss,
        total,
        bad_coords,
        off_county,
        tiger_year,
    )
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
uv run pytest tests/transforms/test_geo.py::test_attach_county_fips_logs_miss_summary -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Run the whole test_geo.py file to confirm nothing regressed**

```bash
uv run pytest tests/transforms/test_geo.py -v --no-cov
```

Expected: all four tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/transforms/test_geo.py src/eia/transforms/geo.py
git commit -m "feat(transforms): INFO log summary per attach_county_fips call"
```

---

## Task 6: Regression coverage — empty DF, realistic events shape

Two regression tests that should pass against the current implementation: empty input returns an empty frame with the new column added, and a realistic events-shape input passes the other columns through untouched.

**Files:**
- Modify: `tests/transforms/test_geo.py`

- [ ] **Step 1: Add the regression tests**

Append:

```python
def test_attach_county_fips_empty_dataframe(fake_counties_geo: Path) -> None:
    from eia.transforms.geo import attach_county_fips

    df = pl.DataFrame(
        schema={"venue_lat": pl.Float64, "venue_lon": pl.Float64},
    )
    out = attach_county_fips(df)
    assert out.height == 0
    assert out.columns == ["venue_lat", "venue_lon", "county_fips"]
    assert out.schema["county_fips"] == pl.Utf8


def test_attach_county_fips_passes_other_columns_through(
    fake_counties_geo: Path,
) -> None:
    from eia.transforms.geo import attach_county_fips

    df = pl.DataFrame(
        {
            "event_id": ["tm_1", "tm_2"],
            "event_name": ["Show A", "Show B"],
            "event_date": ["2023-08-01", "2023-08-02"],
            "venue_lat": [1.0, 11.0],
            "venue_lon": [1.0, 11.0],
            "expected_attendance": [500, 1200],
        }
    )
    out = attach_county_fips(df)

    # All input columns preserved, in original order, plus county_fips.
    assert out.columns == [*df.columns, "county_fips"]
    for col in df.columns:
        assert out[col].to_list() == df[col].to_list()
    assert out["county_fips"].to_list() == ["00001", "00002"]
```

- [ ] **Step 2: Run the new tests**

```bash
uv run pytest tests/transforms/test_geo.py -v --no-cov
```

Expected: all six tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/transforms/test_geo.py
git commit -m "test(transforms): regression coverage for empty df and column passthrough"
```

---

## Task 7: Public export from `transforms/__init__.py`

Make `from eia.transforms import attach_county_fips` work — that's the form callers in event-source modules will use.

**Files:**
- Modify: `src/eia/transforms/__init__.py`
- Modify: `tests/transforms/test_geo.py`

- [ ] **Step 1: Add a smoke test for the public import path**

Append to `tests/transforms/test_geo.py`:

```python
def test_public_import(fake_counties_geo: Path) -> None:
    """The canonical caller form — `from eia.transforms import attach_county_fips`."""
    from eia.transforms import attach_county_fips as imported

    df = pl.DataFrame({"venue_lat": [1.0], "venue_lon": [1.0]})
    out = imported(df)
    assert out["county_fips"].to_list() == ["00001"]
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
uv run pytest tests/transforms/test_geo.py::test_public_import -v --no-cov
```

Expected: FAIL — `attach_county_fips` is not exported from `eia.transforms`.

- [ ] **Step 3: Update `src/eia/transforms/__init__.py`**

Replace the file contents with:

```python
"""Cleaning and feature-engineering transforms applied at to_cleaned() time."""

from eia.transforms.geo import attach_county_fips

__all__ = ["attach_county_fips"]
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
uv run pytest tests/transforms/test_geo.py::test_public_import -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/eia/transforms/__init__.py tests/transforms/test_geo.py
git commit -m "feat(transforms): export attach_county_fips at package level"
```

---

## Task 8: Quality sweep — ruff, mypy, full pytest

Run the full quality gates the project uses (per [CLAUDE.md](../../../CLAUDE.md)) and fix any issue they surface.

**Files:**
- Whatever ruff / mypy flags

- [ ] **Step 1: Lint**

```bash
make lint
```

Expected: no errors. If ruff complains, fix in place — common fixes are unused imports, missing `from __future__ import annotations`, or import ordering.

- [ ] **Step 2: Type-check**

```bash
make typecheck
```

Expected: no errors. The two surfaces likely to need attention:

- `gpd.read_parquet` and `gpd.sjoin` may lack stubs — `mypy.ini_options` already sets `ignore_missing_imports = true` in `pyproject.toml`, so missing stubs are silent. If a real type error surfaces, fix it; do not add `# type: ignore` without a reason comment.
- `pl.from_pandas(...)` returns `pl.DataFrame`; chain `.select(...)` rather than passing through a typed local that mypy can't narrow.

- [ ] **Step 3: Run the full test suite with coverage**

```bash
make test
```

Expected: all tests pass (existing warehouse tests + the new transforms and tiger tests). Coverage on `src/eia/transforms/geo.py` should be near 100% — if anything significant is uncovered, add a regression test for it.

- [ ] **Step 4: Commit any fixes**

```bash
git add -p   # review hunks before committing
git commit -m "chore: lint and type fixes for spatial-join transform"
```

(If steps 1–3 are clean, no commit is needed — skip step 4.)

---

## Done

The transform is now ready to be wired into event-source `to_cleaned()` methods. That wire-up is **out of scope for this plan** — it gets done per source as Phase 1 ingestion plans (Ticketmaster, RunSignUp, Setlist.fm) come online, each of which gets its own design + plan. The single integration line each will add is:

```python
from eia.transforms import attach_county_fips
df = attach_county_fips(df)   # uses default venue_lat / venue_lon column names
```
