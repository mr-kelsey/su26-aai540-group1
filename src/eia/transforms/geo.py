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
    falls inside no US county polygon also receive null. Points exactly on
    polygon boundaries are not matched by `within` (strict interior only) and
    receive `null`.

    Logs one INFO line per call summarising hit/miss counts.
    """
    counties = _load_counties_gdf(tiger_year)

    pos = "__pos__"
    if pos in df.columns:
        raise ValueError(
            f"Input DataFrame must not contain reserved column '{pos}'."
        )
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


def attach_county_fips_via_zip(
    df: pl.DataFrame,
    hud: pl.DataFrame,
    *,
    zip_col: str = "venue_zip",
    fips_col: str = "county_fips",
) -> pl.DataFrame:
    """Fill null `fips_col` values from a HUD ZIP-county crosswalk lookup.

    For each row whose `fips_col` is null, look up `zip_col` in `hud` and fill
    `fips_col` with the county_fips having max `res_ratio` (ties broken by
    alphabetically-lowest county_fips). Non-null `fips_col` values are
    preserved; null ZIP and ZIPs not in `hud` stay null.

    Caller must pre-filter `hud` to a single quarter. `df` must contain
    `fips_col` (typically populated by attach_county_fips first); raises
    ValueError otherwise.

    Logs one INFO line per call with fill counts.
    """
    if fips_col not in df.columns:
        raise ValueError(
            f"{fips_col!r} not in df.columns; run attach_county_fips first"
        )

    zip_to_county = (
        hud.sort(["res_ratio", "county_fips"], descending=[True, False])
        .group_by("zip", maintain_order=True)
        .first()
        .select(
            pl.col("zip"),
            pl.col("county_fips").alias("_zip_lookup_fips"),
        )
    )

    enriched = df.join(
        zip_to_county,
        left_on=zip_col,
        right_on="zip",
        how="left",
    )

    n_originally_null = enriched.filter(pl.col(fips_col).is_null()).height
    n_zip_missing = enriched.filter(
        pl.col(fips_col).is_null() & pl.col(zip_col).is_null()
    ).height
    n_zip_not_in_hud = enriched.filter(
        pl.col(fips_col).is_null()
        & pl.col(zip_col).is_not_null()
        & pl.col("_zip_lookup_fips").is_null()
    ).height
    n_filled = enriched.filter(
        pl.col(fips_col).is_null() & pl.col("_zip_lookup_fips").is_not_null()
    ).height

    result = enriched.with_columns(
        pl.coalesce(pl.col(fips_col), pl.col("_zip_lookup_fips")).alias(fips_col)
    ).drop("_zip_lookup_fips")

    hud_quarter = (
        hud["quarter"][0]
        if "quarter" in hud.columns and hud.height > 0
        else "unknown"
    )

    logger.info(
        "attach_county_fips_via_zip: filled %d/%d originally-null rows "
        "(%d no ZIP, %d ZIP not in crosswalk) [hud_quarter=%s]",
        n_filled,
        n_originally_null,
        n_zip_missing,
        n_zip_not_in_hud,
        hud_quarter,
    )

    return result
