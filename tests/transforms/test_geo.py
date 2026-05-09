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
