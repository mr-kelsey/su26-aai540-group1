"""Pull recent BLS QCEW quarters that haven't been integrated yet.

The existing `eia pull bls-qcew` command pulls one (year, quarter) at a time
via the BlsQcewSource class, and re-downloads the ~325 MB yearly singlefile
once per quarter — wasteful for filling in a whole year. This wrapper:

  1. Downloads each year's singlefile ZIP exactly once into /tmp/
  2. Extracts the single combined CSV (all quarters of that year in one file)
  3. Filters to county-level rows (agglvl_code in 70-78)
  4. Writes per-quarter parquets matching the existing layout at
     data/cleaned/bls-qcew/<year>_q<n>.parquet
  5. Rebuilds phase1_master.parquet as the concat of all per-quarter files

Use this when BLS publishes new quarters beyond what's in your local
data/cleaned/bls-qcew/. Resumable: if a year's ZIP is already in /tmp/, it's
not re-downloaded; if a per-quarter parquet already exists, it's left alone
unless you delete it first.
"""
from __future__ import annotations

import logging
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

BLS_URL = "https://data.bls.gov/cew/data/files/{year}/csv/{year}_qtrly_singlefile.zip"
CLEANED = Path("data/cleaned/bls-qcew")
CACHE = Path("/tmp/bls_qcew_singlefile")


def download_year(year: int, out_path: Path) -> bool:
    """Stream the yearly singlefile ZIP. Returns False if BLS hasn't
    published this year yet (404)."""
    if out_path.exists():
        log.info("%d: %s already cached, skipping download", year, out_path.name)
        return True
    url = BLS_URL.format(year=year)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading %s -> %s", url, out_path)
    try:
        with httpx.stream("GET", url, timeout=300.0, follow_redirects=True) as resp:
            resp.raise_for_status()
            with out_path.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):
                    f.write(chunk)
        log.info("  %.1f MB written", out_path.stat().st_size / 1e6)
        return True
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            log.warning("%d: BLS hasn't published this year yet (404)", year)
            return False
        raise


def clean_year(year: int) -> None:
    zip_path = CACHE / f"{year}_qtrly_singlefile.zip"
    if not zip_path.exists():
        log.warning("missing %s — skipping", zip_path)
        return

    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".singlefile.csv")]
        if not csv_names:
            log.error("no singlefile.csv in %s", zip_path)
            return
        csv_name = csv_names[0]
        csv_size_gb = zf.getinfo(csv_name).file_size / 1e9
        log.info("%d: reading %s (%.1f GB)", year, csv_name, csv_size_gb)
        # Extract to disk; in-memory full read would spike RSS to ~5 GB.
        extract_path = CACHE / csv_name
        if not extract_path.exists():
            with zf.open(csv_name) as src, extract_path.open("wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
            log.info("  extracted -> %s", extract_path.name)

    log.info("%d: scanning + filtering county-level rows (agglvl 70-78)...", year)
    df = (
        pl.scan_csv(
            extract_path,
            infer_schema_length=10_000,
            schema_overrides={"area_fips": pl.Utf8, "industry_code": pl.Utf8},
        )
        .filter(pl.col("agglvl_code").is_in([70, 71, 72, 73, 74, 75, 76, 77, 78]))
        .collect()
    )
    log.info("  filtered: %s rows", f"{df.height:,}")

    # Conform to canonical schema (matches the existing 2015-2023 parquets).
    df = df.select(
        pl.col("area_fips").str.zfill(5).alias("county_fips"),
        pl.col("industry_code").alias("naics_code"),
        (pl.col("year").cast(pl.Utf8) + pl.lit("Q") + pl.col("qtr").cast(pl.Utf8)).alias("period_id"),
        pl.col("year").cast(pl.Int32),
        pl.col("qtr").cast(pl.Int16).alias("quarter"),
        pl.col("own_code").cast(pl.Int16).alias("ownership_code"),
        pl.col("qtrly_estabs").cast(pl.Int64).alias("establishment_count"),
        pl.col("month3_emplvl").cast(pl.Int64).alias("avg_employment"),
        pl.col("total_qtrly_wages").cast(pl.Float64).alias("total_wages_usd"),
        pl.col("avg_wkly_wage").cast(pl.Float64).alias("avg_weekly_wage_usd"),
        pl.lit(datetime.now(UTC)).alias("fetched_at"),
    )

    for q in sorted(df["quarter"].unique().to_list()):
        sub = df.filter(pl.col("quarter") == q)
        out = CLEANED / f"{year}_q{q}.parquet"
        if out.exists():
            log.info("  %d Q%d: already cleaned — skipping", year, q)
            continue
        sub.write_parquet(out)
        log.info("  %d Q%d -> %s  (%s rows)", year, q, out.name, f"{sub.height:,}")


def main() -> int:
    CLEANED.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)

    # Target years to backfill. Adjust as new ones become available.
    target_years = [2024, 2025]

    for year in target_years:
        zip_path = CACHE / f"{year}_qtrly_singlefile.zip"
        if download_year(year, zip_path):
            clean_year(year)

    # Rebuild phase1_master.parquet (concat of all per-quarter files)
    pieces = sorted(CLEANED.glob("[0-9]*_q*.parquet"))
    log.info("concatenating %d per-quarter files -> phase1_master.parquet", len(pieces))
    master = pl.concat([pl.read_parquet(p) for p in pieces], how="vertical_relaxed")
    log.info(
        "phase1_master: %s rows  (years %d-%d)",
        f"{master.height:,}",
        master["year"].min(),
        master["year"].max(),
    )
    master.write_parquet(CLEANED / "phase1_master.parquet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
