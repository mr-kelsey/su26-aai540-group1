"""TIGER/Line county shapefiles.

Pulls the national US counties shapefile from the Census FTP and lands a
flat Parquet of FIPS, name, lat/lon (centroid), area in sq miles. The full
geometry is preserved alongside in case downstream needs it for spatial joins.

Reference: https://www2.census.gov/geo/tiger/TIGER<year>/COUNTY/
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import polars as pl
import yaml

from eia.clients import RateLimitedClient
from eia.sources.base import Source
from eia.sources.registry import register


class TIGERCounties(Source):
    name = "tiger"
    target_table = "dim_county"
    raw_format = "shapefile"

    def __init__(self, year: int | None = None) -> None:
        cfg = self._load_config()
        self.year = year or cfg["default_year"]

    @staticmethod
    def _load_config() -> dict:
        with open("configs/sources.yaml") as f:
            return yaml.safe_load(f)["tiger"]

    def fetch(self) -> Path:
        out = self.raw_dir / f"tl_{self.year}_us_county.zip"
        if out.exists() and out.stat().st_size > 0:
            return out
        base = "https://www2.census.gov"
        path = f"/geo/tiger/TIGER{self.year}/COUNTY/tl_{self.year}_us_county.zip"
        with RateLimitedClient(base, requests_per_second=1.0, timeout_s=60.0) as client:
            data = client.get_bytes(path)
        out.write_bytes(data)
        return out

    def to_cleaned(self, raw_path: Path) -> Path:
        """Read the shapefile via geopandas, derive centroid + sq miles, write Parquet.

        Note: shapefile centroids in geographic CRS are inaccurate; we project
        to NAD83 / Conus Albers (EPSG:5070) for area + centroid math.
        """
        import geopandas as gpd

        # geopandas reads zipped shapefiles directly via zip:// scheme.
        gdf = gpd.read_file(f"zip://{raw_path}")

        # Project to equal-area for sane area calculations.
        gdf_aea = gdf.to_crs(epsg=5070)
        gdf["land_area_sqmi"] = gdf_aea.geometry.area / 2_589_988.11  # m^2 → mi^2
        centroids = gdf_aea.geometry.centroid.to_crs(epsg=4326)
        gdf["latitude"] = centroids.y
        gdf["longitude"] = centroids.x

        # TIGER county fields: STATEFP, COUNTYFP, GEOID, NAME, NAMELSAD, CBSAFP
        cleaned = pl.DataFrame(
            {
                "county_fips": gdf["GEOID"].astype(str).tolist(),
                "state_fips": gdf["STATEFP"].astype(str).tolist(),
                "state_abbr": [None] * len(gdf),  # joined in later
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
        return out


register(TIGERCounties.name, TIGERCounties)
