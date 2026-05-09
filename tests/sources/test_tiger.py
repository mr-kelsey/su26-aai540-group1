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
