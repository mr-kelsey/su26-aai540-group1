"""Apply a Leontief multiplier matrix L to a final-demand vector.

L @ y = total output per industry. L comes from compute_leontief_inverse
(long-form output_industry, demand_industry, total_requirement). y comes
from the caller (long-form industry, value).

The function is pure-Polars: a left-join + group-by + sum. No NumPy at
the public surface.
"""

from __future__ import annotations

import polars as pl


def apply_multipliers(
    L: pl.DataFrame,  # noqa: N803
    final_demand: pl.DataFrame,
    *,
    industry_col: str = "industry",
    value_col: str = "value",
) -> pl.DataFrame:
    """Apply Leontief multipliers L to a final-demand vector y.

    Returns a long-form DataFrame with columns (industry, total_output) where
    total_output[i] = sum_j L[i, j] * y[j]. Output is sorted by industry.

    L must be the long-form output of compute_leontief_inverse (columns
    output_industry, demand_industry, total_requirement). final_demand has
    industry_col and value_col columns; null values are treated as zero.

    Industries in y not in L's demand_industry set are silently dropped (no
    multiplier defined). Industries in L's output_industry set but not in y
    still appear in the output.
    """
    L_required = ("output_industry", "demand_industry", "total_requirement")  # noqa: N806
    for col in L_required:
        if col not in L.columns:
            raise ValueError(f"L missing required column {col!r}")
    for col in (industry_col, value_col):
        if col not in final_demand.columns:
            raise ValueError(f"final_demand missing required column {col!r}")

    weighted = L.join(
        final_demand.select(
            pl.col(industry_col).alias("_demand_industry_key"),
            pl.col(value_col).alias("_demand_value"),
        ),
        left_on="demand_industry",
        right_on="_demand_industry_key",
        how="left",
    ).with_columns(
        contribution=pl.col("total_requirement") * pl.col("_demand_value").fill_null(0.0),
    )

    return (
        weighted.group_by("output_industry")
        .agg(total_output=pl.col("contribution").sum())
        .rename({"output_industry": "industry"})
        .sort("industry")
    )
