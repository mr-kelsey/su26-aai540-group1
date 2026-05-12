"""Phase 1 cross-source summary.

The model-side Phase 1 milestone: events from every source, joined to
federal economic context. This pipeline demonstrates the full join chain
and prints a series of summary tables intended for a human-readable
status check (or for a Slack milestone notification).

Joins:
    events
      JOIN dim_county          ON county_fips
      JOIN census_acs_county   ON county_fips
      JOIN bls_qcew            ON county_fips, period_id (NAICS 721 = accommodation)

Outputs (printed):
    1. Counts by source x category
    2. Top 10 counties by event density, with economic context
    3. State-level event counts vs accommodation employment
    4. Coverage gaps: events without county_fips, without QCEW match
"""

from __future__ import annotations

import sys

import polars as pl
from rich.console import Console
from rich.table import Table

from eia.warehouse import get_warehouse

console = Console()


def _print(name: str, df: pl.DataFrame) -> None:
    t = Table(title=name, header_style="bold cyan")
    if df.is_empty():
        t.add_column("(no rows)")
        console.print(t)
        return
    for col in df.columns:
        t.add_column(col)
    for row in df.iter_rows():
        t.add_row(*[str(v) if v is not None else "—" for v in row])
    console.print(t)


def main() -> int:
    wh = get_warehouse()
    console.rule("[bold]Phase 1 cross-source summary")

    # 1. Counts by source x category
    counts = wh.query(
        """
        SELECT source, category, COUNT(*) AS n,
               COUNT(county_fips) AS with_fips,
               COUNT(period_id) AS with_period
        FROM events
        GROUP BY source, category
        ORDER BY n DESC
        """
    )
    _print("Events by source x category", counts)

    # 2. Top 10 counties by event count, with population + median income
    top = wh.query(
        """
        SELECT
            e.county_fips,
            d.county_name,
            d.state_fips,
            COUNT(*) AS events,
            MAX(a.population) AS population,
            MAX(ROUND(a.median_household_income, 0)) AS median_hh_income
        FROM events e
        LEFT JOIN dim_county d USING (county_fips)
        LEFT JOIN census_acs_county a USING (county_fips)
        WHERE e.county_fips IS NOT NULL
        GROUP BY e.county_fips, d.county_name, d.state_fips
        ORDER BY events DESC
        LIMIT 10
        """
    )
    _print("Top 10 counties by event count", top)

    # 3. State-level: total events vs accommodation employment (2023Q3 baseline)
    state_summary = wh.query(
        """
        SELECT
            d.state_fips,
            COUNT(e.event_id) AS events,
            COUNT(DISTINCT e.county_fips) AS counties_with_events,
            SUM(q.avg_employment) AS accommodation_employment_2023q3
        FROM events e
        LEFT JOIN dim_county d USING (county_fips)
        LEFT JOIN (
            SELECT county_fips, SUM(avg_employment) AS avg_employment
            FROM bls_qcew
            WHERE period_id = '2023Q3' AND naics_code = '721' AND ownership_code = 5
            GROUP BY county_fips
        ) q USING (county_fips)
        WHERE e.county_fips IS NOT NULL
        GROUP BY d.state_fips
        ORDER BY events DESC
        LIMIT 10
        """
    )
    _print("Top 10 states by event count (with accommodation jobs context)", state_summary)

    # 4. Setlist.fm-specific: most active artists
    sfm_artists = wh.query(
        """
        SELECT
            event_name AS artist,
            COUNT(*) AS shows,
            COUNT(DISTINCT county_fips) AS distinct_counties
        FROM events
        WHERE source = 'setlistfm' AND county_fips IS NOT NULL
        GROUP BY event_name
        ORDER BY shows DESC
        LIMIT 10
        """
    )
    _print("Top 10 Setlist.fm artists by show count", sfm_artists)

    # 5. Coverage gaps
    gaps = wh.query(
        """
        SELECT
            source,
            SUM(CASE WHEN county_fips IS NULL THEN 1 ELSE 0 END) AS missing_fips,
            SUM(CASE WHEN venue_lat IS NULL THEN 1 ELSE 0 END) AS missing_latlon,
            COUNT(*) AS total
        FROM events
        GROUP BY source
        """
    )
    _print("Coverage gaps by source", gaps)

    console.rule("[bold green]Phase 1 cross-source summary complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
