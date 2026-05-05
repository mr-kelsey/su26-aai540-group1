"""Phase 0 exit query.

Per the Data Acquisition Strategy: prove that QCEW + dim_county + ACS join
cleanly on FIPS, and that any concerts/marathons in the events table can be
attributed to the same county+quarter slice.

Canonical query (from the strategy doc):
    "What was the QCEW employment in Accommodation (NAICS 721) in San Diego
     County (06073) in Q3 2023, and which scheduled concerts and marathons
     fell in that same county and quarter?"
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from eia.warehouse import get_warehouse

console = Console()

SAN_DIEGO_FIPS = "06073"
TARGET_PERIOD = "2023Q3"
TARGET_NAICS = "721"  # Accommodation


def main() -> None:
    wh = get_warehouse()

    console.rule("[bold]Phase 0 Exit Query")
    console.print(f"County: San Diego ({SAN_DIEGO_FIPS}) — Period: {TARGET_PERIOD}\n")

    # 1. Verify dim_county is populated.
    dim = wh.query(
        "SELECT county_fips, county_name, state_fips, latitude, longitude, "
        "land_area_sqmi FROM dim_county WHERE county_fips = $fips",
        params={"fips": SAN_DIEGO_FIPS},
    )
    _print_table("dim_county", dim)

    # 2. QCEW: total + accommodation employment for the period.
    qcew = wh.query(
        """
        SELECT
            naics_code,
            SUM(avg_employment) AS employment,
            SUM(total_wages_usd) AS wages_usd
        FROM bls_qcew
        WHERE county_fips = $fips
          AND period_id = $period
          AND ownership_code = 5      -- private
          AND naics_code IN ('10', '721')
        GROUP BY naics_code
        ORDER BY naics_code
        """,
        params={"fips": SAN_DIEGO_FIPS, "period": TARGET_PERIOD},
    )
    _print_table("BLS QCEW (private)", qcew)

    # 3. ACS context.
    acs = wh.query(
        "SELECT county_fips, acs_5yr_end_year, population, median_household_income, "
        "median_age, bachelor_or_higher_pct FROM census_acs_county "
        "WHERE county_fips = $fips ORDER BY acs_5yr_end_year DESC LIMIT 1",
        params={"fips": SAN_DIEGO_FIPS},
    )
    _print_table("Census ACS (latest)", acs)

    # 4. HUD ZIP-county crosswalk: prove ZIP-level joinability.
    hud = wh.query(
        "SELECT zip, county_fips, quarter, res_ratio FROM hud_zip_county "
        "WHERE county_fips = $fips ORDER BY zip",
        params={"fips": SAN_DIEGO_FIPS},
    )
    _print_table("HUD ZIP→County (sample)", hud)

    # 5. Events: anything landed for the county+period (will be empty until API keys arrive).
    events = wh.query(
        """
        SELECT category, COUNT(*) AS n,
               MIN(event_date) AS first_date, MAX(event_date) AS last_date
        FROM events
        WHERE county_fips = $fips AND period_id = $period
        GROUP BY category
        ORDER BY n DESC
        """,
        params={"fips": SAN_DIEGO_FIPS, "period": TARGET_PERIOD},
    )
    _print_table("Events landed", events)

    # 6. Cross-source sanity: join QCEW + dim_county + ACS in one query.
    cross = wh.query(
        """
        SELECT
            d.county_name,
            d.land_area_sqmi,
            a.population,
            a.median_household_income,
            q.employment       AS private_accommodation_emp,
            q.wages_usd        AS private_accommodation_wages
        FROM dim_county d
        JOIN census_acs_county a USING (county_fips)
        JOIN (
            SELECT county_fips,
                   SUM(avg_employment)  AS employment,
                   SUM(total_wages_usd) AS wages_usd
            FROM bls_qcew
            WHERE period_id = $period
              AND ownership_code = 5
              AND naics_code = '721'
            GROUP BY county_fips
        ) q USING (county_fips)
        WHERE d.county_fips = $fips
        """,
        params={"fips": SAN_DIEGO_FIPS, "period": TARGET_PERIOD},
    )
    _print_table("Cross-source join (THE Phase 0 exit criterion)", cross)

    console.rule("[bold green]Phase 0 exit criterion met if the cross-source join returned a row")


def _print_table(name: str, df) -> None:
    table = Table(title=name, header_style="bold cyan")
    if df.is_empty():
        table.add_column("(no rows)")
        console.print(table)
        return
    for col in df.columns:
        table.add_column(col)
    for row in df.iter_rows():
        table.add_row(*[str(v) for v in row])
    console.print(table)


if __name__ == "__main__":
    main()
