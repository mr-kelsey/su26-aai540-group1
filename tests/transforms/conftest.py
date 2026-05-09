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
