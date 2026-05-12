"""Enrich the events table with derived geographic and temporal columns.

Run AFTER any event source pull (ticketmaster, runsignup, setlistfm) to
populate the derived columns from the raw venue lat/lon and event_date that
each source's `to_cleaned()` leaves null:

    county_fips    via attach_county_fips (TIGER spatial join)
    period_id      via attach_period_id   ('YYYYQQ')
    period_month   via attach_period_id   ('YYYY-MM')

This script reads the current events table, applies the two transforms, and
re-registers the enriched output as the events table. It is idempotent — old
derived values are dropped and recomputed every run.

Typical workflow:
    make pull-ticketmaster   # (and any other event pulls)
    make enrich-events
"""

from __future__ import annotations

import sys

import polars as pl
from rich.console import Console
from rich.table import Table

from eia.config import settings
from eia.transforms import attach_county_fips, attach_period_id
from eia.warehouse import get_warehouse

console = Console()


def main() -> int:
    wh = get_warehouse()
    events = wh.query("SELECT * FROM events")
    if events.is_empty():
        console.print("[yellow]events table is empty — run a source pull first.[/yellow]")
        return 0

    n = events.height
    before_fips = events.filter(pl.col("county_fips").is_not_null()).height
    before_period = events.filter(pl.col("period_id").is_not_null()).height

    console.rule("[bold]Enriching events")
    console.print(f"Read {n} events from warehouse")
    console.print(f"  starting with county_fips: {before_fips}")
    console.print(f"  starting with period_id:   {before_period}")

    # Drop the columns we're about to recompute (idempotency + avoid join collisions).
    drop_cols = [c for c in ("county_fips", "period_id", "period_month") if c in events.columns]
    if drop_cols:
        events = events.drop(drop_cols)

    # Cast event_date string -> Date for the temporal transform (warehouse stores VARCHAR).
    if events["event_date"].dtype == pl.Utf8:
        events = events.with_columns(pl.col("event_date").str.to_date(strict=False))

    enriched = attach_county_fips(events)
    enriched = attach_period_id(enriched)

    # Recast event_date back to ISO string for warehouse round-trip.
    enriched = enriched.with_columns(
        pl.col("event_date").dt.strftime("%Y-%m-%d").alias("event_date")
    )

    after_fips = enriched.filter(pl.col("county_fips").is_not_null()).height
    after_period = enriched.filter(pl.col("period_id").is_not_null()).height
    console.print("After enrichment:")
    console.print(f"  with county_fips: {after_fips}  (+{after_fips - before_fips})")
    console.print(f"  with period_id:   {after_period}  (+{after_period - before_period})")

    out = settings.cleaned_dir / "events_enriched.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    enriched.write_parquet(out)
    wh.register_table_from_parquet("events", out, replace=True)
    console.print(f"Wrote {out} and re-registered events table")

    # Show breakdown by category x quarter as a sanity-check
    summary = wh.query(
        """
        SELECT category, period_id, COUNT(*) AS n
        FROM events
        WHERE county_fips IS NOT NULL
        GROUP BY category, period_id
        ORDER BY period_id, category
        """
    )
    if not summary.is_empty():
        t = Table(title="Enriched events by category x quarter (FIPS-mapped only)")
        for col in summary.columns:
            t.add_column(col)
        for row in summary.iter_rows():
            t.add_row(*[str(v) for v in row])
        console.print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
