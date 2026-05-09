"""(county_fips x period_month) cartesian-product panel for treatment-effect modeling.

Produces the dense (county, month) cell grid the regression input needs:
every county-month must exist as a row, including months at a county where
no events occurred. Without this baseline, the model has no untreated cells
to subtract from treated cells.

Event aggregates and federal covariates are NOT included in the panel —
they are joined downstream by feature ETL. The panel is keys-only so it
stays orthogonal to the upstream sources.
"""

from __future__ import annotations

import polars as pl


def build_county_month_panel(
    counties: pl.DataFrame,
    *,
    start_year: int = 2015,
    end_year: int = 2023,
    fips_col: str = "county_fips",
) -> pl.DataFrame:
    """Produce the (county_fips x period_month) cartesian product as a Polars DataFrame.

    Returns columns `[fips_col, "period_month", "period_id", "year", "month"]`,
    sorted by `(fips_col, year, month)`. Output formats:

        period_month  'YYYY-MM'   (matches attach_period_id)
        period_id     'YYYYQQ'    (matches attach_period_id and bls_qcew.period_id)

    Raises ValueError if start_year > end_year, or if counties[fips_col]
    contains null values. Duplicate FIPS values in the input are silently
    deduped.
    """
    if start_year > end_year:
        raise ValueError(
            f"start_year ({start_year}) > end_year ({end_year})"
        )
    if counties[fips_col].null_count() > 0:
        raise ValueError(f"{fips_col} contains null values")

    n_years = end_year - start_year + 1
    months = pl.DataFrame(
        {
            "year": pl.Series(
                [y for y in range(start_year, end_year + 1) for _ in range(12)],
                dtype=pl.Int32,
            ),
            "month": pl.Series(
                list(range(1, 13)) * n_years,
                dtype=pl.Int32,
            ),
        }
    )
    months = months.with_columns(
        pl.date(pl.col("year"), pl.col("month"), 1)
        .dt.strftime("%Y-%m")
        .alias("period_month"),
        pl.format(
            "{}Q{}",
            pl.col("year"),
            ((pl.col("month") - 1) // 3 + 1),
        ).alias("period_id"),
    )

    counties_unique = counties.select(fips_col).unique()

    return (
        counties_unique.join(months, how="cross")
        .select(fips_col, "period_month", "period_id", "year", "month")
        .sort(fips_col, "year", "month")
    )
