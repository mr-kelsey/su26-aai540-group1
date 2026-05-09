"""lat/lon -> county_fips spatial-join transform.

Consumed by event sources' to_cleaned() methods to attach county_fips to
event rows. Reads the counties_geo Parquet produced by the TIGER source.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import polars as pl

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
    if not path.exists():
        raise FileNotFoundError(
            f"Counties GeoParquet not found at {path}. "
            f"Run `make pull-tiger` to generate it."
        )
    gdf = gpd.read_parquet(path)
    _COUNTIES_CACHE[year] = gdf
    return gdf


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
