"""Tests for multipliers.apply."""

# Note: 'L' below is BEA notation for the Leontief inverse; ruff N806 silenced.

from __future__ import annotations

import polars as pl
import pytest


def _two_industry_L() -> pl.DataFrame:  # noqa: N802
    """The 2-industry reference Leontief matrix from leontief.py's hand-computed case.

    L[I1, I1] = 0.6/0.45,  L[I1, I2] = 0.3/0.45,
    L[I2, I1] = 0.1/0.45,  L[I2, I2] = 0.8/0.45.

    Long-form output_industry, demand_industry, total_requirement.
    Sorted by (demand_industry, output_industry) per compute_leontief_inverse contract.
    """
    return pl.DataFrame(
        {
            "output_industry": ["I1", "I2", "I1", "I2"],
            "demand_industry": ["I1", "I1", "I2", "I2"],
            "total_requirement": [0.6 / 0.45, 0.1 / 0.45, 0.3 / 0.45, 0.8 / 0.45],
        }
    )


def test_unit_demand_first_industry() -> None:
    """y = [1, 0] -> output = first column of L."""
    from eia.multipliers.apply import apply_multipliers

    L = _two_industry_L()  # noqa: N806
    demand = pl.DataFrame({"industry": ["I1"], "value": [1.0]})

    out = apply_multipliers(L, demand)

    by_industry = {r["industry"]: r["total_output"] for r in out.to_dicts()}
    assert abs(by_industry["I1"] - 0.6 / 0.45) < 1e-9
    assert abs(by_industry["I2"] - 0.1 / 0.45) < 1e-9


def test_unit_demand_second_industry() -> None:
    """y = [0, 1] -> output = second column of L."""
    from eia.multipliers.apply import apply_multipliers

    L = _two_industry_L()  # noqa: N806
    demand = pl.DataFrame({"industry": ["I2"], "value": [1.0]})

    out = apply_multipliers(L, demand)

    by_industry = {r["industry"]: r["total_output"] for r in out.to_dicts()}
    assert abs(by_industry["I1"] - 0.3 / 0.45) < 1e-9
    assert abs(by_industry["I2"] - 0.8 / 0.45) < 1e-9


def test_mixed_demand() -> None:
    """y = [10, 5] -> output = L @ y."""
    from eia.multipliers.apply import apply_multipliers

    L = _two_industry_L()  # noqa: N806
    demand = pl.DataFrame({"industry": ["I1", "I2"], "value": [10.0, 5.0]})

    out = apply_multipliers(L, demand)

    by_industry = {r["industry"]: r["total_output"] for r in out.to_dicts()}
    expected_i1 = 10 * (0.6 / 0.45) + 5 * (0.3 / 0.45)
    expected_i2 = 10 * (0.1 / 0.45) + 5 * (0.8 / 0.45)
    assert abs(by_industry["I1"] - expected_i1) < 1e-9
    assert abs(by_industry["I2"] - expected_i2) < 1e-9


def test_demand_industry_not_in_L_is_ignored() -> None:  # noqa: N802
    """Demand for an industry that isn't in L contributes zero (no multiplier defined)."""
    from eia.multipliers.apply import apply_multipliers

    L = _two_industry_L()  # noqa: N806
    demand = pl.DataFrame(
        {"industry": ["I1", "I3"], "value": [1.0, 999.0]}
    )

    out = apply_multipliers(L, demand)

    by_industry = {r["industry"]: r["total_output"] for r in out.to_dicts()}
    assert abs(by_industry["I1"] - 0.6 / 0.45) < 1e-9
    assert abs(by_industry["I2"] - 0.1 / 0.45) < 1e-9
    assert "I3" not in by_industry


def test_industry_in_L_not_in_demand_appears_in_output() -> None:  # noqa: N802
    """An L-industry with no demand in y is still in the output."""
    from eia.multipliers.apply import apply_multipliers

    L = _two_industry_L()  # noqa: N806
    demand = pl.DataFrame({"industry": ["I1"], "value": [1.0]})

    out = apply_multipliers(L, demand)

    by_industry = {r["industry"]: r["total_output"] for r in out.to_dicts()}
    assert "I2" in by_industry
    assert abs(by_industry["I2"] - 0.1 / 0.45) < 1e-9


def test_empty_demand_yields_all_zero_output() -> None:
    """Empty final_demand -> every L industry has zero total_output."""
    from eia.multipliers.apply import apply_multipliers

    L = _two_industry_L()  # noqa: N806
    demand = pl.DataFrame(
        {"industry": [], "value": []},
        schema={"industry": pl.Utf8, "value": pl.Float64},
    )

    out = apply_multipliers(L, demand)

    assert out.height == 2
    for v in out["total_output"].to_list():
        assert abs(v) < 1e-12


def test_null_value_treated_as_zero() -> None:
    """A null value in final_demand is coalesced to zero, not an error."""
    from eia.multipliers.apply import apply_multipliers

    L = _two_industry_L()  # noqa: N806
    demand = pl.DataFrame(
        {"industry": ["I1", "I2"], "value": [1.0, None]},
        schema={"industry": pl.Utf8, "value": pl.Float64},
    )

    out = apply_multipliers(L, demand)

    by_industry = {r["industry"]: r["total_output"] for r in out.to_dicts()}
    assert abs(by_industry["I1"] - 0.6 / 0.45) < 1e-9
    assert abs(by_industry["I2"] - 0.1 / 0.45) < 1e-9


def test_missing_column_in_L_raises() -> None:  # noqa: N802
    """If L is missing a required column, raise ValueError naming it."""
    from eia.multipliers.apply import apply_multipliers

    L_bad = pl.DataFrame(  # noqa: N806
        {"output_industry": ["I1"], "demand_industry": ["I1"]}
    )
    demand = pl.DataFrame({"industry": ["I1"], "value": [1.0]})

    with pytest.raises(ValueError, match=r"total_requirement"):
        apply_multipliers(L_bad, demand)


def test_missing_column_in_demand_raises() -> None:
    """If final_demand is missing a required column, raise ValueError naming it."""
    from eia.multipliers.apply import apply_multipliers

    L = _two_industry_L()  # noqa: N806
    demand_bad = pl.DataFrame({"industry": ["I1"]})

    with pytest.raises(ValueError, match=r"value"):
        apply_multipliers(L, demand_bad)


def test_custom_column_names() -> None:
    """Caller can override industry_col and value_col on final_demand."""
    from eia.multipliers.apply import apply_multipliers

    L = _two_industry_L()  # noqa: N806
    demand = pl.DataFrame({"naics": ["I1"], "dollars": [1.0]})

    out = apply_multipliers(L, demand, industry_col="naics", value_col="dollars")

    by_industry = {r["industry"]: r["total_output"] for r in out.to_dicts()}
    assert abs(by_industry["I1"] - 0.6 / 0.45) < 1e-9


def test_public_import_smoke() -> None:
    """from eia.multipliers import apply_multipliers."""
    from eia.multipliers import apply_multipliers as imported

    L = _two_industry_L()  # noqa: N806
    demand = pl.DataFrame({"industry": ["I1"], "value": [1.0]})
    out = imported(L, demand)
    assert out.height == 2
    assert out.columns == ["industry", "total_output"]
