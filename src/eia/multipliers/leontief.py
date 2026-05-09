"""BEA Leontief inverse — industry-by-industry total requirements multiplier.

Implements the standard BEA Type I formulation under the industry technology
assumption: A = D @ B where D is the market-shares matrix and B is the direct-
requirements coefficients matrix. L = (I - A)^-1. Inputs are long-form Polars
DataFrames (Use and Make tables); output is a long-form Polars DataFrame
queryable by (output_industry, demand_industry).

See docs/superpowers/specs/2026-05-09-leontief-multipliers-design.md for the
full derivation.
"""

from __future__ import annotations

import itertools

import numpy as np
import polars as pl


def compute_leontief_inverse(
    use_matrix: pl.DataFrame,
    make_matrix: pl.DataFrame,
    *,
    industry_col: str = "industry_code",
    commodity_col: str = "commodity_code",
    value_col: str = "value_millions",
) -> pl.DataFrame:
    """Compute the industry-by-industry Total Requirements (Leontief inverse).

    Inputs are long-format Polars DataFrames each with the three named columns.
    `use_matrix[c, j]` is the value of commodity c used by industry j as input.
    `make_matrix[i, c]` is the value of commodity c produced by industry i.

    Returns a long-format Polars DataFrame with columns
    (output_industry, demand_industry, total_requirement), sorted by
    (demand_industry, output_industry). Row count is n_industry^2.

    Raises ValueError if a required column is missing or if (I - A) is singular.
    """
    for df_name, df in (("use_matrix", use_matrix), ("make_matrix", make_matrix)):
        for col in (industry_col, commodity_col, value_col):
            if col not in df.columns:
                raise ValueError(
                    f"{df_name} missing required column {col!r}"
                )

    all_industries = sorted(
        set(use_matrix[industry_col].unique().to_list())
        | set(make_matrix[industry_col].unique().to_list())
    )
    all_commodities = sorted(
        set(use_matrix[commodity_col].unique().to_list())
        | set(make_matrix[commodity_col].unique().to_list())
    )
    n_ind = len(all_industries)
    n_com = len(all_commodities)

    industry_idx = {code: i for i, code in enumerate(all_industries)}
    commodity_idx = {code: i for i, code in enumerate(all_commodities)}

    V = np.zeros((n_ind, n_com))
    for row in make_matrix.iter_rows(named=True):
        i = industry_idx[row[industry_col]]
        c = commodity_idx[row[commodity_col]]
        V[i, c] = float(row[value_col])

    U = np.zeros((n_com, n_ind))
    for row in use_matrix.iter_rows(named=True):
        c = commodity_idx[row[commodity_col]]
        j = industry_idx[row[industry_col]]
        U[c, j] = float(row[value_col])

    q = V.sum(axis=1)
    x = V.sum(axis=0)

    D = np.zeros_like(V)
    nonzero_x = x > 0
    D[:, nonzero_x] = V[:, nonzero_x] / x[nonzero_x]

    B = np.zeros_like(U)
    nonzero_q = q > 0
    B[:, nonzero_q] = U[:, nonzero_q] / q[nonzero_q]

    A = D @ B

    I_minus_A = np.eye(n_ind) - A
    try:
        L = np.linalg.inv(I_minus_A)
    except np.linalg.LinAlgError as exc:
        raise ValueError(f"(I - A) is singular: {exc}") from exc

    demand_codes = [d for d, _ in itertools.product(all_industries, all_industries)]
    output_codes = [o for _, o in itertools.product(all_industries, all_industries)]
    values = L.flatten(order="F").tolist()

    return pl.DataFrame(
        {
            "output_industry": output_codes,
            "demand_industry": demand_codes,
            "total_requirement": values,
        }
    )
