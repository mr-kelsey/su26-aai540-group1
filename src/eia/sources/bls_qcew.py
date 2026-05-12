"""BLS Quarterly Census of Employment and Wages.

Two pull modes:

    `mode="by_area"` (Phase 0 default):
        Hits the per-area CSV endpoint
        https://data.bls.gov/cew/data/api/<year>/<q>/area/<fips>.csv
        once per configured county. Each response is ~450KB; great for fast,
        targeted pulls during development.

    `mode="singlefile"` (Phase 1):
        Downloads the yearly singlefile bundle (323MB) at
        https://data.bls.gov/cew/data/files/<year>/csv/<year>_qtrly_singlefile.zip
        and filters to the configured quarter. Use when scaling to all
        ~3,000 US counties.

Layout reference: https://www.bls.gov/cew/about-data/downloadable-file-layouts/quarterly/csv-quarterly-layout.htm
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any, ClassVar

import polars as pl
import yaml

from eia.clients import RateLimitedClient
from eia.sources.base import Source
from eia.sources.registry import register


class BLSQCEW(Source):
    name = "bls-qcew"
    target_table = "bls_qcew"
    raw_format = "csv"

    # County-level QCEW aggregation level codes.
    COUNTY_AGGLVL_CODES: ClassVar[set[int]] = {70, 71, 72, 73, 74, 75, 76, 77, 78}

    BY_AREA_BASE = "https://data.bls.gov/cew/data/api"
    SINGLEFILE_BASE = "https://data.bls.gov/cew/data/files"

    def __init__(
        self,
        year: int | None = None,
        quarter: int | None = None,
        mode: str = "by_area",
        county_fips: list[str] | None = None,
    ) -> None:
        cfg = self._load_config()
        self.year = year or cfg["default_year"]
        self.quarter = quarter or cfg["default_quarter"]
        self.mode = mode
        # Phase 0 default: a small set of counties anchoring the exit query.
        # Phase 1 widens to all counties via mode="singlefile".
        self.county_fips = county_fips or cfg.get(
            "phase0_counties",
            ["06073"],  # San Diego
        )

    @staticmethod
    def _load_config() -> dict[str, Any]:
        with open("configs/sources.yaml") as f:
            return yaml.safe_load(f)["bls_qcew"]  # type: ignore[no-any-return]

    # ---- fetch ----

    def fetch(self) -> Path:
        if self.mode == "by_area":
            return self._fetch_by_area()
        elif self.mode == "singlefile":
            return self._fetch_singlefile()
        else:
            raise ValueError(f"Unknown BLSQCEW mode: {self.mode}")

    def _fetch_by_area(self) -> Path:
        out_dir = self.raw_dir / f"by_area_{self.year}_q{self.quarter}"
        out_dir.mkdir(parents=True, exist_ok=True)
        with RateLimitedClient(self.BY_AREA_BASE, requests_per_second=2.0) as client:
            for fips in self.county_fips:
                target = out_dir / f"{fips}.csv"
                if target.exists() and target.stat().st_size > 0:
                    continue
                data = client.get_bytes(f"/{self.year}/{self.quarter}/area/{fips}.csv")
                target.write_bytes(data)
        return out_dir

    def _fetch_singlefile(self) -> Path:
        out = self.raw_dir / f"{self.year}_qtrly_singlefile.zip"
        if out.exists() and out.stat().st_size > 0:
            return out
        with RateLimitedClient(
            self.SINGLEFILE_BASE, requests_per_second=1.0, timeout_s=600.0
        ) as client:
            data = client.get_bytes(f"/{self.year}/csv/{self.year}_qtrly_singlefile.zip")
        out.write_bytes(data)
        return out

    # ---- clean ----

    def to_cleaned(self, raw_path: Path) -> Path:
        if self.mode == "by_area":
            df = pl.concat([self._read_csv(p) for p in sorted(raw_path.glob("*.csv"))])
        else:
            df = self._read_singlefile(raw_path)

        # Filter to county-level aggregations and exclude national/MSA rows.
        df = df.filter(pl.col("agglvl_code").is_in(list(self.COUNTY_AGGLVL_CODES)))
        df = df.filter(pl.col("area_fips").str.len_chars() == 5)
        df = df.filter(pl.col("area_fips").str.contains(r"^\d{5}$"))
        # Filter to the configured quarter (singlefile contains all 4).
        df = df.filter(pl.col("qtr") == self.quarter)

        # Average employment over the three months of the quarter.
        df = df.with_columns(
            avg_employment=(
                pl.col("month1_emplvl") + pl.col("month2_emplvl") + pl.col("month3_emplvl")
            )
            // 3,
            period_id=pl.format("{}Q{}", pl.col("year"), pl.col("qtr")),
            fetched_at=pl.lit(self.now_utc()),
        )

        cleaned = df.select(
            pl.col("area_fips").alias("county_fips"),
            pl.col("industry_code").alias("naics_code"),
            "period_id",
            "year",
            pl.col("qtr").alias("quarter"),
            pl.col("own_code").cast(pl.Int16).alias("ownership_code"),
            pl.col("qtrly_estabs").cast(pl.Int32).alias("establishment_count"),
            pl.col("avg_employment").cast(pl.Int32),
            pl.col("total_qtrly_wages").alias("total_wages_usd"),
            pl.col("avg_wkly_wage").cast(pl.Int32).alias("avg_weekly_wage_usd"),
            "fetched_at",
        )

        out = self.cleaned_dir / f"{self.year}_q{self.quarter}.parquet"
        cleaned.write_parquet(out)
        return out

    # ---- helpers ----

    @staticmethod
    def _csv_schema_overrides() -> dict[str, Any]:
        return {
            "area_fips": pl.Utf8,
            "industry_code": pl.Utf8,
            "agglvl_code": pl.Int32,
            "own_code": pl.Int32,
            "year": pl.Int32,
            "qtr": pl.Int32,
            "qtrly_estabs": pl.Int64,
            "month1_emplvl": pl.Int64,
            "month2_emplvl": pl.Int64,
            "month3_emplvl": pl.Int64,
            "total_qtrly_wages": pl.Int64,
            "avg_wkly_wage": pl.Int64,
        }

    @classmethod
    def _read_csv(cls, path: Path) -> pl.DataFrame:
        return pl.read_csv(path, schema_overrides=cls._csv_schema_overrides(), ignore_errors=True)

    @classmethod
    def _read_singlefile(cls, raw_path: Path) -> pl.DataFrame:
        with zipfile.ZipFile(raw_path) as z:
            csv_name = next(n for n in z.namelist() if n.endswith(".csv"))
            with z.open(csv_name) as f:
                return pl.read_csv(
                    io.BytesIO(f.read()),
                    schema_overrides=cls._csv_schema_overrides(),
                    ignore_errors=True,
                )


register(BLSQCEW.name, BLSQCEW)
