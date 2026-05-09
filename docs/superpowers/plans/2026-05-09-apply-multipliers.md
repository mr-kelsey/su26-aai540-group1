# `apply_multipliers` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `apply_multipliers(L, final_demand) -> pl.DataFrame` — applies a Leontief multiplier matrix to a final-demand vector to produce total output per industry.

**Architecture:** Pure-Polars left-join + group-by + sum implementation in a new `src/eia/multipliers/apply.py`. Re-exported from `eia.multipliers`.

**Tech Stack:** Polars 1.x, pytest 8.x. No new dependencies.

---

## File Structure

**Create:**
- `src/eia/multipliers/apply.py` — the function
- `tests/multipliers/test_apply.py` — 10 unit tests

**Modify:**
- `src/eia/multipliers/__init__.py` — extend `__all__` and add the new import

---

## Task 1: `apply_multipliers` function (TDD) + public export

Single task — write tests, fail, implement function + export, pass, commit.

### Step 1: Write the failing tests in `tests/multipliers/test_apply.py`

```python
"""Tests for multipliers.apply."""

# Note: 'L' below is BEA notation for the Leontief inverse; ruff N806 silenced.

from __future__ import annotations

import polars as pl
import pytest


def _two_industry_L() -> pl.DataFrame:
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


def test_demand_industry_not_in_L_is_ignored() -> None:
    """Demand for an industry that isn't in L contributes zero (no multiplier defined)."""
    from eia.multipliers.apply import apply_multipliers

    L = _two_industry_L()  # noqa: N806
    # I3 isn't in L; its demand should be silently dropped.
    demand = pl.DataFrame(
        {"industry": ["I1", "I3"], "value": [1.0, 999.0]}
    )

    out = apply_multipliers(L, demand)

    # Same result as if y = [I1: 1.0] alone.
    by_industry = {r["industry"]: r["total_output"] for r in out.to_dicts()}
    assert abs(by_industry["I1"] - 0.6 / 0.45) < 1e-9
    assert abs(by_industry["I2"] - 0.1 / 0.45) < 1e-9
    # I3 should NOT appear in output (it's not in L's output_industry set).
    assert "I3" not in by_industry


def test_industry_in_L_not_in_demand_appears_in_output() -> None:
    """An L-industry with no demand in y is still in the output (with whatever it picks up)."""
    from eia.multipliers.apply import apply_multipliers

    L = _two_industry_L()  # noqa: N806
    # Only demand for I1; I2 has no direct demand but still appears.
    demand = pl.DataFrame({"industry": ["I1"], "value": [1.0]})

    out = apply_multipliers(L, demand)

    # I2's total_output = L[I2,I1] * 1 + L[I2,I2] * 0 = 0.1/0.45
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

    assert out.height == 2  # both L industries present
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

    # Same as if I2's value were 0 -> equivalent to y = [I1: 1].
    by_industry = {r["industry"]: r["total_output"] for r in out.to_dicts()}
    assert abs(by_industry["I1"] - 0.6 / 0.45) < 1e-9
    assert abs(by_industry["I2"] - 0.1 / 0.45) < 1e-9


def test_missing_column_in_L_raises() -> None:
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
    demand_bad = pl.DataFrame({"industry": ["I1"]})  # no value column

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
```

### Step 2: Run, confirm 11 tests fail

```bash
uv run pytest tests/multipliers/test_apply.py -v --no-cov
```

Expected: 11 errors / failures with `ModuleNotFoundError: No module named 'eia.multipliers.apply'`.

### Step 3: Create `src/eia/multipliers/apply.py`

```python
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
    L: pl.DataFrame,  # noqa: N803  (BEA notation; conventional uppercase)
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
    L_required = ("output_industry", "demand_industry", "total_requirement")
    for col in L_required:
        if col not in L.columns:
            raise ValueError(f"L missing required column {col!r}")
    for col in (industry_col, value_col):
        if col not in final_demand.columns:
            raise ValueError(f"final_demand missing required column {col!r}")

    # Left-join L's demand_industry against final_demand, treating missing
    # values as zero so unmatched industries contribute nothing.
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
```

### Step 4: Update `src/eia/multipliers/__init__.py`

Replace the file contents with:

```python
"""Economic-multiplier computations: Leontief inverse and related model-side foundation code."""

from eia.multipliers.apply import apply_multipliers
from eia.multipliers.leontief import compute_leontief_inverse

__all__ = ["apply_multipliers", "compute_leontief_inverse"]
```

### Step 5: Run tests, confirm pass

```bash
uv run pytest tests/multipliers/ -v --no-cov
```

Expected: 20 passed (9 leontief + 11 apply).

```bash
make test
```

Expected: 63 passed (5 warehouse + 2 tiger + 19 geo + 7 temporal + 9 panel + 1 composition + 9 leontief + 11 apply).

### Step 6: Lint and typecheck

```bash
uv run ruff check src/eia/multipliers/ tests/multipliers/
uv run mypy src/eia/multipliers/
```

Both should be clean. `noqa: N803` on the `L` parameter is intentional (BEA convention); test file uses `# noqa: N806` on local `L` assignments per the prior multipliers cycle.

If ruff flags anything else (Unicode, etc.), fix in place.

### Step 7: Commit

```bash
git add src/eia/multipliers/apply.py src/eia/multipliers/__init__.py tests/multipliers/test_apply.py
git commit -m "feat(multipliers): apply_multipliers — total output from final demand"
```

The Bash tool will append the standard `Co-Authored-By` footer automatically.

## Constraints

- Touch ONLY `src/eia/multipliers/apply.py`, `src/eia/multipliers/__init__.py`, `tests/multipliers/test_apply.py`.
- Do NOT modify `leontief.py` or any test file outside `tests/multipliers/`.
- Use `# noqa: N803` on the `L:` parameter and `# noqa: N806` on local `L` assignments in tests; these match the established multiplier-package pattern.

## Self-Review

- All 11 tests pass?
- `__all__` is alphabetical (apply_multipliers, compute_leontief_inverse)?
- Hand-verified mixed-demand test arithmetic: `10*1.333 + 5*0.667 = 13.33 + 3.33 = 16.67` and `10*0.222 + 5*1.778 = 2.22 + 8.89 = 11.11` (both within 1e-9)?
- Empty-demand test produces zero output for every L industry?
- ValueError messages name the missing column?

## Report

- Status (DONE/etc.)
- Pytest output for Step 2 (failing) and Step 5 (full suite)
- Files changed
- Commit SHA
