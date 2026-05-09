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
