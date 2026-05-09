"""Tests for transforms.geo."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import polars as pl
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


def test_loader_raises_clear_error_when_geo_parquet_missing(
    fake_counties_geo: Path,
) -> None:
    """If the file is missing, the error message must point the user at make pull-tiger."""
    from eia.transforms import geo

    fake_counties_geo.unlink()  # delete the planted fixture
    geo._COUNTIES_CACHE.clear()

    with pytest.raises(FileNotFoundError, match=r"make pull-tiger"):
        geo._load_counties_gdf(2023)


def test_attach_county_fips_full_matrix(fake_counties_geo: Path) -> None:
    """One test, six rows, one assertion per edge-case-matrix row."""
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
