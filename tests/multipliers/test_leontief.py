"""Tests for multipliers.leontief."""

from __future__ import annotations

import polars as pl
import pytest

# Note: matrix variable name 'L' below is BEA notation; ruff N806 silenced.


def _two_industry_inputs() -> tuple[pl.DataFrame, pl.DataFrame]:
    """The canonical 2-industry / 2-commodity hand-computed reference case.

    Make:
        I1 makes $10 of A, $0 of B
        I2 makes $0 of A, $20 of B
    Use:
        I1 uses $2 of A, $1 of B
        I2 uses $6 of A, $8 of B

    With these inputs:
        q = [10, 20]              (industry totals)
        x = [10, 20]              (commodity totals)
        D = [[1, 0], [0, 1]]      (each industry produces only its own commodity)
        B = [[0.2, 0.3], [0.1, 0.4]]
        A = D @ B = [[0.2, 0.3], [0.1, 0.4]]
        I - A = [[0.8, -0.3], [-0.1, 0.6]]
        det(I - A) = 0.45
        L = (1/0.45) * [[0.6, 0.3], [0.1, 0.8]]

    So L[I1, I1] = 0.6/0.45,  L[I1, I2] = 0.3/0.45,
       L[I2, I1] = 0.1/0.45,  L[I2, I2] = 0.8/0.45.
    """
    use = pl.DataFrame(
        {
            "industry_code": ["I1", "I1", "I2", "I2"],
            "commodity_code": ["A", "B", "A", "B"],
            "value_millions": [2.0, 1.0, 6.0, 8.0],
        }
    )
    make = pl.DataFrame(
        {
            "industry_code": ["I1", "I1", "I2", "I2"],
            "commodity_code": ["A", "B", "A", "B"],
            "value_millions": [10.0, 0.0, 0.0, 20.0],
        }
    )
    return use, make


def test_two_industry_hand_computed() -> None:
    """Match the analytical L for the canonical 2-industry case to 1e-9."""
    from eia.multipliers.leontief import compute_leontief_inverse

    use, make = _two_industry_inputs()
    L = compute_leontief_inverse(use, make)  # noqa: N806

    # Sorted by (demand_industry asc, output_industry asc).
    expected = [
        ("I1", "I1", 0.6 / 0.45),
        ("I2", "I1", 0.1 / 0.45),
        ("I1", "I2", 0.3 / 0.45),
        ("I2", "I2", 0.8 / 0.45),
    ]
    actual = L.to_dicts()
    assert len(actual) == len(expected)
    for row, (out_code, demand_code, val) in zip(actual, expected, strict=True):
        assert row["output_industry"] == out_code
        assert row["demand_industry"] == demand_code
        assert abs(row["total_requirement"] - val) < 1e-9


def test_output_schema_and_sort_order() -> None:
    """Output columns and dtypes match spec; rows sorted by (demand, output)."""
    from eia.multipliers.leontief import compute_leontief_inverse

    use, make = _two_industry_inputs()
    L = compute_leontief_inverse(use, make)  # noqa: N806

    assert L.columns == ["output_industry", "demand_industry", "total_requirement"]
    assert L.schema["output_industry"] == pl.Utf8
    assert L.schema["demand_industry"] == pl.Utf8
    assert L.schema["total_requirement"] == pl.Float64
    assert L.height == 4
    assert L["demand_industry"].to_list() == ["I1", "I1", "I2", "I2"]
    assert L["output_industry"].to_list() == ["I1", "I2", "I1", "I2"]


def test_identity_case() -> None:
    """Each industry produces only itself with NO inter-industry inputs -> L = I."""
    from eia.multipliers.leontief import compute_leontief_inverse

    make = pl.DataFrame(
        {
            "industry_code": ["I1", "I1", "I2", "I2"],
            "commodity_code": ["A", "B", "A", "B"],
            "value_millions": [10.0, 0.0, 0.0, 20.0],
        }
    )
    use = pl.DataFrame(
        {
            "industry_code": ["I1", "I1", "I2", "I2"],
            "commodity_code": ["A", "B", "A", "B"],
            "value_millions": [0.0, 0.0, 0.0, 0.0],
        }
    )

    L = compute_leontief_inverse(use, make)  # noqa: N806

    by_pair = {
        (r["demand_industry"], r["output_industry"]): r["total_requirement"] for r in L.to_dicts()
    }
    assert abs(by_pair[("I1", "I1")] - 1.0) < 1e-9
    assert abs(by_pair[("I2", "I2")] - 1.0) < 1e-9
    assert abs(by_pair[("I1", "I2")] - 0.0) < 1e-9
    assert abs(by_pair[("I2", "I1")] - 0.0) < 1e-9


def test_diagonals_are_at_least_one() -> None:
    """For any well-formed matrix, L[i, i] >= 1 by construction."""
    from eia.multipliers.leontief import compute_leontief_inverse

    use, make = _two_industry_inputs()
    L = compute_leontief_inverse(use, make)  # noqa: N806

    diagonal = L.filter(pl.col("output_industry") == pl.col("demand_industry"))[
        "total_requirement"
    ].to_list()

    for v in diagonal:
        assert v >= 1.0 - 1e-12


def test_singular_matrix_raises() -> None:
    """Constructing A = I makes (I - A) = 0; the function must raise ValueError."""
    from eia.multipliers.leontief import compute_leontief_inverse

    # Each industry's full output is its own commodity; each industry consumes
    # a full dollar of its own commodity per dollar of output. That makes
    # B = [[1, 0], [0, 1]], D = I, A = D @ B = I, and (I - A) = 0.
    make = pl.DataFrame(
        {
            "industry_code": ["I1", "I2"],
            "commodity_code": ["A", "B"],
            "value_millions": [10.0, 10.0],
        }
    )
    use = pl.DataFrame(
        {
            "industry_code": ["I1", "I2"],
            "commodity_code": ["A", "B"],
            "value_millions": [10.0, 10.0],
        }
    )

    with pytest.raises(ValueError, match=r"singular"):
        compute_leontief_inverse(use, make)


def test_missing_required_column_raises() -> None:
    """If an input is missing a required column, raise ValueError naming it."""
    from eia.multipliers.leontief import compute_leontief_inverse

    use_bad = pl.DataFrame({"industry_code": ["I1"], "commodity_code": ["A"]})
    make_ok = pl.DataFrame(
        {"industry_code": ["I1"], "commodity_code": ["A"], "value_millions": [10.0]}
    )

    with pytest.raises(ValueError, match=r"value_millions"):
        compute_leontief_inverse(use_bad, make_ok)


def test_custom_column_names() -> None:
    """Caller can override industry_col, commodity_col, value_col names."""
    from eia.multipliers.leontief import compute_leontief_inverse

    use = pl.DataFrame(
        {
            "naics": ["I1", "I1", "I2", "I2"],
            "comm": ["A", "B", "A", "B"],
            "usd": [2.0, 1.0, 6.0, 8.0],
        }
    )
    make = pl.DataFrame(
        {
            "naics": ["I1", "I1", "I2", "I2"],
            "comm": ["A", "B", "A", "B"],
            "usd": [10.0, 0.0, 0.0, 20.0],
        }
    )

    L = compute_leontief_inverse(  # noqa: N806
        use,
        make,
        industry_col="naics",
        commodity_col="comm",
        value_col="usd",
    )

    diag = L.filter(pl.col("output_industry") == pl.col("demand_industry"))
    assert diag.height == 2
    by_demand = {r["demand_industry"]: r["total_requirement"] for r in diag.to_dicts()}
    assert abs(by_demand["I1"] - 0.6 / 0.45) < 1e-9
    assert abs(by_demand["I2"] - 0.8 / 0.45) < 1e-9


def test_public_import_smoke() -> None:
    """The canonical caller form — `from eia.multipliers import compute_leontief_inverse`."""
    from eia.multipliers import compute_leontief_inverse as imported

    use, make = _two_industry_inputs()
    L = imported(use, make)  # noqa: N806
    assert L.height == 4
    assert L.columns == ["output_industry", "demand_industry", "total_requirement"]


def test_industries_union_when_one_input_missing_some() -> None:
    """If Use has industry I3 but Make doesn't (I3 makes nothing), output still includes I3 rows.

    Make matrix has industries {I1, I2}. Use matrix has industries {I1, I2, I3} —
    I3 buys $5 of A from somewhere. Since I3 doesn't appear in Make, V[I3, :] = 0,
    q[I3] = 0, so B[:, I3] = 0 (B's I3 column is the zero vector by the divide-by-zero
    guard). The function should still produce a 3x3 output L with I3 as one of the
    output_industry / demand_industry values.
    """
    from eia.multipliers.leontief import compute_leontief_inverse

    make = pl.DataFrame(
        {
            "industry_code": ["I1", "I1", "I2", "I2"],
            "commodity_code": ["A", "B", "A", "B"],
            "value_millions": [10.0, 0.0, 0.0, 20.0],
        }
    )
    use = pl.DataFrame(
        {
            "industry_code": ["I1", "I1", "I2", "I2", "I3"],
            "commodity_code": ["A", "B", "A", "B", "A"],
            "value_millions": [2.0, 1.0, 6.0, 8.0, 5.0],
        }
    )

    result = compute_leontief_inverse(use, make)

    # 3 industries -> 3x3 = 9 rows.
    assert result.height == 9
    industries_in_output = set(result["output_industry"].unique().to_list())
    assert industries_in_output == {"I1", "I2", "I3"}
    industries_in_demand = set(result["demand_industry"].unique().to_list())
    assert industries_in_demand == {"I1", "I2", "I3"}

    # Diagonal of I3 must be exactly 1.0: I3 has zero output -> column j=I3 of A is zero
    # -> (I - A)'s I3 column is the unit vector e_I3 -> L's I3 column has 1 on the diagonal
    # and zeros elsewhere.
    by_pair = {
        (r["demand_industry"], r["output_industry"]): r["total_requirement"]
        for r in result.to_dicts()
    }
    assert abs(by_pair[("I3", "I3")] - 1.0) < 1e-9
    assert abs(by_pair[("I3", "I1")] - 0.0) < 1e-9
    assert abs(by_pair[("I3", "I2")] - 0.0) < 1e-9
