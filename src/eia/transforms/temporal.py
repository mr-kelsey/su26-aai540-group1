"""event_date -> (period_id, period_month) temporal-key transform.

Consumed by event sources' to_cleaned() methods after attach_county_fips to
attach quarter and month period keys to event rows. Output formats match
the federal-side aggregators already in use:

    period_id     'YYYYQQ', e.g. '2023Q3'   -- joins bls_qcew, dim_time
    period_month  'YYYY-MM', e.g. '2023-08' -- joins state DOR, county-month panel
"""

from __future__ import annotations

import polars as pl


def attach_period_id(
    df: pl.DataFrame,
    *,
    date_col: str = "event_date",
    quarter_col: str = "period_id",
    month_col: str = "period_month",
) -> pl.DataFrame:
    """Attach quarterly and monthly period keys derived from `date_col`.

    Returns the input frame with two columns appended (Utf8, both nullable):
    `quarter_col` in 'YYYYQQ' format, `month_col` in 'YYYY-MM' format. A null
    `date_col` value produces null in both output columns. Original columns
    and their order are preserved.
    """
    return df.with_columns(
        pl.format(
            "{}Q{}",
            pl.col(date_col).dt.year(),
            pl.col(date_col).dt.quarter(),
        ).alias(quarter_col),
        pl.col(date_col).dt.strftime("%Y-%m").alias(month_col),
    )
