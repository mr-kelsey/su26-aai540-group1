"""Repartition cleaned parquets into Hive-style folders for the Silver layer.

Writes to `data/silver_staging/`. Each table gets its own directory under
that path, with partition subdirectories named `key=value` (e.g.
`year=2022/quarter=3/`). Athena + the Glue Crawler recognize this layout
and auto-detect partition keys from the path.

Run from project root:
    python pipelines/repartition_for_silver.py

Then upload:
    aws s3 sync data/silver_staging/ s3://jonno-lucas-steve-bucket/usd-aai540-group1/silver/
"""

from __future__ import annotations

import logging
import re
import shutil
import sys
from pathlib import Path

import polars as pl

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

CLEANED = Path("data/cleaned")
STAGING = Path("data/silver_staging")


def reset_staging() -> None:
    """Delete the staging directory so we start fresh."""
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)


def write_unpartitioned(df: pl.DataFrame, table: str, filename: str) -> None:
    """Tables small enough that a partition would add no value."""
    out = STAGING / table
    out.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out / filename)
    log.info("  %s: %d rows (no partition)", table, df.height)


def write_partitioned_by(df: pl.DataFrame, table: str, by: list[str], filename: str) -> None:
    """Hive-style partition: writes one file per unique combination of `by` columns.

    Drops null partition values. Keeps the partition columns in the data
    (slight duplication, but easier for non-Athena readers).
    """
    df = df.drop_nulls(subset=by)
    n_groups = 0
    for (key_tuple, sub) in df.group_by(by):
        parts = [f"{col}={val}" for col, val in zip(by, key_tuple, strict=True)]
        out = STAGING / table / "/".join(parts)
        out.mkdir(parents=True, exist_ok=True)
        sub.write_parquet(out / filename)
        n_groups += 1
    log.info("  %s: %d rows across %d partitions (by %s)", table, df.height, n_groups, by)


def repartition_dim_county() -> None:
    df = pl.read_parquet(CLEANED / "tiger" / "counties_2023.parquet")
    write_unpartitioned(df, "dim_county", "dim_county.parquet")


def repartition_census_acs() -> None:
    df = pl.read_parquet(CLEANED / "census-acs" / "end_2023.parquet")
    write_unpartitioned(df, "census_acs_county", "census_acs_county.parquet")


def repartition_bls_qcew() -> None:
    """36 per-quarter files already exist; copy them into year=Y/quarter=Q dirs."""
    pattern = re.compile(r"^(\d{4})_q(\d)\.parquet$")
    out_root = STAGING / "bls_qcew"
    n = 0
    for p in sorted((CLEANED / "bls-qcew").glob("*.parquet")):
        m = pattern.match(p.name)
        if not m:
            continue  # skip phase1_master.parquet etc.
        year, quarter = m.group(1), m.group(2)
        out = out_root / f"year={year}" / f"quarter={quarter}"
        out.mkdir(parents=True, exist_ok=True)
        shutil.copy(p, out / "qcew.parquet")
        n += 1
    log.info("  bls_qcew: %d per-quarter parquets copied into year=Y/quarter=Q layout", n)


def repartition_bea() -> None:
    for kind in ("use", "make"):
        df = pl.read_parquet(CLEANED / "bea-io" / f"{kind}_summary.parquet")
        write_partitioned_by(df, f"bea_io_{kind}", ["table_year"], f"{kind}.parquet")


def repartition_hud() -> None:
    df = pl.read_parquet(CLEANED / "hud-crosswalk" / "crosswalk_2024Q1.parquet")
    write_unpartitioned(df, "hud_zip_county", "hud_zip_county.parquet")


def repartition_ticketmaster() -> None:
    tm_files = list((CLEANED / "ticketmaster").glob("*.parquet"))
    if not tm_files:
        log.info("  ticketmaster_events: no parquets found, skipping")
        return
    dfs = [pl.read_parquet(p) for p in tm_files]
    df = pl.concat(dfs, how="vertical_relaxed")
    write_unpartitioned(df, "ticketmaster_events", "ticketmaster_events.parquet")


def repartition_setlistfm() -> None:
    df = pl.read_parquet(CLEANED / "setlistfm" / "setlists.parquet")
    df = df.with_columns(pl.col("event_date").dt.year().alias("year"))
    write_partitioned_by(
        df, "setlistfm_setlists", ["year", "state_code"], "setlists.parquet"
    )


def repartition_events() -> None:
    """The unified events table. event_date is stored as a string (YYYY-MM-DD)."""
    df = pl.read_parquet(CLEANED / "events_unified.parquet")
    df = df.with_columns(
        pl.col("event_date").str.to_date(strict=False).dt.year().alias("year")
    )
    write_partitioned_by(df, "events", ["year", "source"], "events.parquet")


def repartition_cdtfa() -> None:
    df = pl.read_parquet(CLEANED / "cdtfa-taxable-sales" / "taxable_sales_counties.parquet")
    write_partitioned_by(df, "cdtfa_taxable_sales", ["table_year"], "cdtfa.parquet")


def repartition_census_stc() -> None:
    df = pl.read_parquet(CLEANED / "census-state-tax" / "state_tax_collections.parquet")
    write_partitioned_by(df, "census_state_tax_collections", ["table_year"], "stc.parquet")


def repartition_tx() -> None:
    df = pl.read_parquet(CLEANED / "tx-comptroller" / "tx_county_allocations.parquet")
    # Pad month to 2 digits for stable alphabetical sort in the partition dir.
    df = df.with_columns(pl.col("month").cast(pl.Utf8).str.zfill(2).alias("month_str"))
    df = df.drop("month").rename({"month_str": "month"})
    write_partitioned_by(
        df, "tx_comptroller_county_allocations", ["table_year", "month"], "tx.parquet"
    )


def main() -> int:
    log.info("Resetting staging dir at %s", STAGING)
    reset_staging()

    log.info("--- Dimension tables (unpartitioned) ---")
    repartition_dim_county()
    repartition_census_acs()
    repartition_hud()

    log.info("--- Federal feature tables ---")
    repartition_bls_qcew()
    repartition_bea()

    log.info("--- Event tables ---")
    repartition_ticketmaster()
    repartition_setlistfm()
    repartition_events()

    log.info("--- Y-target tables ---")
    repartition_cdtfa()
    repartition_census_stc()
    repartition_tx()

    log.info("\nStaging output at %s", STAGING.resolve())
    log.info("Ready to upload: aws s3 sync data/silver_staging/ s3://jonno-lucas-steve-bucket/usd-aai540-group1/silver/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
