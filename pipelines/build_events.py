"""Build the canonical `events` table from per-source staging tables.

Each event-side source (Ticketmaster, Setlist.fm, RunSignUp, ...) writes to
its own staging table with a source-shaped schema. This pipeline maps each
staging schema to the unified `events` schema, UNIONs them, applies the
geographic + temporal enrichments (`attach_county_fips`, `attach_period_id`),
and re-registers the result as the `events` table.

Run after any event-source pull. Idempotent.

Currently consumes:
    ticketmaster_events  -- already events-schema (1:1 passthrough)
    setlistfm_setlists   -- mapped via _setlistfm_to_events
"""

from __future__ import annotations

import sys

import polars as pl
from rich.console import Console
from rich.table import Table

from eia.config import settings
from eia.transforms import attach_county_fips, attach_period_id
from eia.warehouse import get_warehouse
from eia.warehouse.base import Warehouse

console = Console()

EVENTS_COLUMNS = [
    "event_id",
    "source",
    "category",
    "event_name",
    "event_date",
    "venue_name",
    "venue_address",
    "venue_city",
    "venue_state",
    "venue_zip",
    "venue_lat",
    "venue_lon",
    "county_fips",
    "period_id",
    "period_month",
    "expected_attendance",
    "ticket_min_usd",
    "ticket_max_usd",
    "raw_payload",
    "fetched_at",
]


def _table_exists(wh: Warehouse, name: str) -> bool:
    df = wh.query(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name = $name",
        params={"name": name},
    )
    return not df.is_empty()


def _normalize_fetched_at(df: pl.DataFrame) -> pl.DataFrame:
    """Coerce `fetched_at` to a tz-naive Datetime so concat across sources works."""
    if "fetched_at" not in df.columns:
        return df
    dtype = df.schema["fetched_at"]
    if isinstance(dtype, pl.Datetime) and dtype.time_zone is not None:
        df = df.with_columns(
            pl.col("fetched_at").dt.convert_time_zone("UTC").dt.replace_time_zone(None)
        )
    return df


def _read_ticketmaster(wh: Warehouse) -> pl.DataFrame:
    """Pull Ticketmaster's staging rows; already events-shaped."""
    if not _table_exists(wh, "ticketmaster_events"):
        return pl.DataFrame(schema={c: pl.Utf8 for c in EVENTS_COLUMNS})
    df = wh.query("SELECT * FROM ticketmaster_events")
    # Ensure all events-schema columns exist (older Ticketmaster pulls may not
    # have had county_fips / period_id / period_month yet).
    for col in EVENTS_COLUMNS:
        if col not in df.columns:
            df = df.with_columns(pl.lit(None).alias(col))
    df = _normalize_fetched_at(df)
    return df.select(EVENTS_COLUMNS)


def _read_setlistfm(wh: Warehouse) -> pl.DataFrame:
    """Pull Setlist.fm staging rows and map onto the events schema."""
    if not _table_exists(wh, "setlistfm_setlists"):
        return pl.DataFrame(schema={c: pl.Utf8 for c in EVENTS_COLUMNS})
    raw = wh.query("SELECT * FROM setlistfm_setlists")
    if raw.is_empty():
        return pl.DataFrame(schema={c: pl.Utf8 for c in EVENTS_COLUMNS})
    raw = _normalize_fetched_at(raw)
    return raw.select(
        pl.format("sfm_{}", pl.col("setlist_id")).alias("event_id"),
        pl.lit("setlistfm").alias("source"),
        pl.lit("concert").alias("category"),
        pl.col("artist_name").alias("event_name"),
        pl.col("event_date").dt.strftime("%Y-%m-%d").alias("event_date"),
        pl.col("venue_name"),
        pl.lit(None).cast(pl.Utf8).alias("venue_address"),
        pl.col("city_name").alias("venue_city"),
        pl.col("state_code").alias("venue_state"),
        pl.lit(None).cast(pl.Utf8).alias("venue_zip"),
        pl.col("venue_lat"),
        pl.col("venue_lon"),
        pl.lit(None).cast(pl.Utf8).alias("county_fips"),
        pl.lit(None).cast(pl.Utf8).alias("period_id"),
        pl.lit(None).cast(pl.Utf8).alias("period_month"),
        pl.lit(None).cast(pl.Int32).alias("expected_attendance"),
        pl.lit(None).cast(pl.Float64).alias("ticket_min_usd"),
        pl.lit(None).cast(pl.Float64).alias("ticket_max_usd"),
        pl.col("raw_payload"),
        pl.col("fetched_at"),
    )


def _enrich(df: pl.DataFrame) -> pl.DataFrame:
    """Apply county_fips and period_id transforms across the UNIONed frame."""
    if df.is_empty():
        return df
    drop_cols = [c for c in ("county_fips", "period_id", "period_month") if c in df.columns]
    if drop_cols:
        df = df.drop(drop_cols)
    if df["event_date"].dtype == pl.Utf8:
        df = df.with_columns(pl.col("event_date").str.to_date(strict=False))
    df = attach_county_fips(df)
    df = attach_period_id(df)
    df = df.with_columns(pl.col("event_date").dt.strftime("%Y-%m-%d").alias("event_date"))
    return df.select(EVENTS_COLUMNS)


def main() -> int:
    wh = get_warehouse()
    console.rule("[bold]Building events table from staging")

    tm = _read_ticketmaster(wh)
    sfm = _read_setlistfm(wh)
    console.print(f"Ticketmaster staging: {tm.height} rows")
    console.print(f"Setlist.fm staging:   {sfm.height} rows")
    if tm.is_empty() and sfm.is_empty():
        console.print("[yellow]Both staging tables empty. Nothing to build.[/yellow]")
        return 0

    combined = (
        pl.concat([tm, sfm], how="vertical_relaxed")
        if (not tm.is_empty() and not sfm.is_empty())
        else (tm if not tm.is_empty() else sfm)
    )
    # Deduplicate on event_id (sources prefix their ids to keep namespaces disjoint).
    if combined.height > 0:
        combined = combined.unique(subset=["event_id"], keep="first")

    enriched = _enrich(combined)
    n_fips = enriched.filter(pl.col("county_fips").is_not_null()).height
    n_period = enriched.filter(pl.col("period_id").is_not_null()).height
    console.print(
        f"After enrichment: {enriched.height} events, "
        f"{n_fips} with county_fips, {n_period} with period_id"
    )

    out = settings.cleaned_dir / "events_unified.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    enriched.write_parquet(out)
    wh.register_table_from_parquet("events", out, replace=True)
    console.print(f"Wrote {out} and re-registered events table.")

    # Breakdown by source + category + quarter (FIPS-mapped only)
    summary = wh.query(
        """
        SELECT source, category, period_id, COUNT(*) AS n
        FROM events
        WHERE county_fips IS NOT NULL
        GROUP BY source, category, period_id
        ORDER BY source, period_id, category
        """
    )
    if not summary.is_empty():
        t = Table(title="Events by source x category x quarter (FIPS-mapped)")
        for col in summary.columns:
            t.add_column(col)
        for row in summary.iter_rows():
            t.add_row(*[str(v) for v in row])
        console.print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
