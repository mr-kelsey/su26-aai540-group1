"""Build the festivals reference table for the Silver layer.

Setlist.fm and Ticketmaster don't cover large multi-act festivals well (the
per-act setlists individually show up, but the festival as a single event
with ~250K attendance does not). This script attaches a hand-curated
attendance number to each festival-county-year combination so the Gold
layer can include festival impact alongside concert / race signals.

Source: data/curated/festivals.csv — manually maintained with attendance
figures from Wikipedia, festival operator websites, and trade-press
reports. Re-run after adding entries.

Output:
- data/curated/festivals.parquet (intermediate, not committed)
- s3://jonno-lucas-steve-bucket/usd-aai540-group1/silver/festivals/festivals.parquet
- aai540_silver.festivals Glue table (created/updated separately)
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

CURATED = Path("data/curated")
CSV_PATH = CURATED / "festivals.csv"
PARQUET_PATH = CURATED / "festivals.parquet"
S3_TARGET = "s3://jonno-lucas-steve-bucket/usd-aai540-group1/silver/festivals/festivals.parquet"


def main() -> int:
    if not CSV_PATH.exists():
        raise SystemExit(f"missing input: {CSV_PATH}")

    # Force county_fips to string so leading zero on "06065" is preserved.
    df = pl.read_csv(CSV_PATH, schema_overrides={"county_fips": pl.Utf8})
    # Defensive: pad to 5 chars in case anyone edits the CSV in Excel and loses
    # the leading zero (Excel notoriously strips numeric-leading-zero strings).
    df = df.with_columns(pl.col("county_fips").str.zfill(5))
    print(f"loaded {df.height} festival rows from {CSV_PATH}")

    # Parse event_date, derive year + quarter + period_id for downstream joins
    df = df.with_columns(
        pl.col("event_date").str.to_date(strict=False),
    )
    df = df.with_columns(
        pl.col("event_date").dt.year().cast(pl.Int32).alias("year"),
        pl.col("event_date").dt.quarter().cast(pl.Int16).alias("quarter"),
    )
    df = df.with_columns(
        (pl.col("year").cast(pl.Utf8) + pl.lit("Q") + pl.col("quarter").cast(pl.Utf8)).alias("period_id"),
    )
    # Reformat event_date as string (consistent with events table)
    df = df.with_columns(pl.col("event_date").dt.strftime("%Y-%m-%d").alias("event_date"))

    # Final column order
    df = df.select([
        "festival_id",
        "festival_name",
        "event_date",
        "year",
        "quarter",
        "period_id",
        "venue_name",
        "county_fips",
        "state_code",
        "attendance",
        "category",
        "source_notes",
    ])

    df.write_parquet(PARQUET_PATH)
    print(f"wrote {PARQUET_PATH} ({df.height} rows)")

    # Summary
    print("\nfestivals by year:")
    summary = df.group_by("year").agg([
        pl.len().alias("n_festivals"),
        pl.col("attendance").sum().alias("total_attendance"),
    ]).sort("year")
    print(summary)

    print("\nfestivals by county (top 10):")
    by_county = df.group_by("county_fips").agg([
        pl.len().alias("n_festivals"),
        pl.col("attendance").sum().alias("total_attendance"),
    ]).sort("total_attendance", descending=True).head(10)
    print(by_county)

    print(f"\nUpload command:")
    print(f"  aws s3 cp {PARQUET_PATH} {S3_TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
