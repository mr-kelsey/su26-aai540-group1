# BEA Leontief Multipliers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `compute_leontief_inverse(use_matrix, make_matrix) -> pl.DataFrame` — the industry-by-industry Type I Total Requirements multiplier from BEA Use/Make matrices.

**Architecture:** New `src/eia/multipliers/` package separate from `transforms/` (which is for clean-time enrichment; this is model-side foundation). Polars long-form in/out at the public surface; NumPy under the hood for the matrix math. Standard BEA formulation `A = D @ B; L = (I - A)^-1`. Hand-computable 2-industry test case locks the math.

**Tech Stack:** Polars 1.x, NumPy (already a transitive dep — no `pyproject.toml` change), pytest 8.x.

---

## File Structure

**Create:**
- `src/eia/multipliers/__init__.py` — re-exports `compute_leontief_inverse`
- `src/eia/multipliers/leontief.py` — the function plus its private helpers (none needed in v1)
- `tests/multipliers/__init__.py` — empty package marker
- `tests/multipliers/test_leontief.py` — unit tests (8 covering math + edge cases + public import)

**Modify:** none

---

## Task 1: Package + `compute_leontief_inverse` function (TDD)

Write all 8 tests first, confirm they fail, create the package files and function, confirm they pass.

**Files:**
- Create: `src/eia/multipliers/__init__.py`
- Create: `src/eia/multipliers/leontief.py`
- Create: `tests/multipliers/__init__.py`
- Create: `tests/multipliers/test_leontief.py`

### Step 1: Create the empty test-package marker

```bash
: > tests/multipliers/__init__.py
```

### Step 2: Write the failing tests in `tests/multipliers/test_leontief.py`

```python
"""Tests for multipliers.leontief."""

from __future__ import annotations

import polars as pl
import pytest


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
    L = compute_leontief_inverse(use, make)

    # Sorted by (demand_industry asc, output_industry asc).
    expected = [
        ("I1", "I1", 0.6 / 0.45),
        ("I2", "I1", 0.1 / 0.45),
        ("I1", "I2", 0.3 / 0.45),
        ("I2", "I2", 0.8 / 0.45),
    ]
    actual = L.to_dicts()
    assert len(actual) == len(expected)
    for row, (out_code, demand_code, val) in zip(actual, expected):
        assert row["output_industry"] == out_code
        assert row["demand_industry"] == demand_code
        assert abs(row["total_requirement"] - val) < 1e-9


def test_output_schema_and_sort_order() -> None:
    """Output columns and dtypes match spec; rows sorted by (demand, output)."""
    from eia.multipliers.leontief import compute_leontief_inverse

    use, make = _two_industry_inputs()
    L = compute_leontief_inverse(use, make)

    assert L.columns == ["output_industry", "demand_industry", "total_requirement"]
    assert L.schema["output_industry"] == pl.Utf8
    assert L.schema["demand_industry"] == pl.Utf8
    assert L.schema["total_requirement"] == pl.Float64
    assert L.height == 4  # 2 industries -> 2x2 = 4 rows
    # First two rows have demand_industry == "I1", next two have "I2".
    assert L["demand_industry"].to_list() == ["I1", "I1", "I2", "I2"]
    # Within each demand block, output_industry is sorted ascending.
    assert L["output_industry"].to_list() == ["I1", "I2", "I1", "I2"]


def test_identity_case() -> None:
    """Each industry produces only itself with NO inter-industry inputs -> L = I."""
    from eia.multipliers.leontief import compute_leontief_inverse

    # Two industries, two commodities; each industry makes only its own
    # commodity AND uses NO inputs (use values all zero).
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

    L = compute_leontief_inverse(use, make)

    # A = 0, so L = I. Diagonals = 1, off-diagonals = 0.
    by_pair = {(r["demand_industry"], r["output_industry"]): r["total_requirement"] for r in L.to_dicts()}
    assert abs(by_pair[("I1", "I1")] - 1.0) < 1e-9
    assert abs(by_pair[("I2", "I2")] - 1.0) < 1e-9
    assert abs(by_pair[("I1", "I2")] - 0.0) < 1e-9
    assert abs(by_pair[("I2", "I1")] - 0.0) < 1e-9


def test_diagonals_are_at_least_one() -> None:
    """For any well-formed matrix, L[i, i] >= 1 by construction."""
    from eia.multipliers.leontief import compute_leontief_inverse

    use, make = _two_industry_inputs()
    L = compute_leontief_inverse(use, make)

    diagonal = L.filter(
        pl.col("output_industry") == pl.col("demand_industry")
    )["total_requirement"].to_list()

    for v in diagonal:
        assert v >= 1.0 - 1e-12


def test_singular_matrix_raises() -> None:
    """Constructing A = I makes (I - A) = 0; the function must raise ValueError."""
    from eia.multipliers.leontief import compute_leontief_inverse

    # If each industry's full output is consumed BY ITSELF (1.0 share, 1.0 input
    # per dollar of output), A becomes the identity matrix and (I - A) is zero.
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
            "value_millions": [10.0, 10.0],  # I1 uses $10 of A; q[I1]=10 -> B[A,I1]=1
        }
    )

    with pytest.raises(ValueError, match=r"singular"):
        compute_leontief_inverse(use, make)


def test_missing_required_column_raises() -> None:
    """If an input is missing a required column, raise ValueError naming it."""
    from eia.multipliers.leontief import compute_leontief_inverse

    use_bad = pl.DataFrame(
        {"industry_code": ["I1"], "commodity_code": ["A"]}  # no value_millions
    )
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

    L = compute_leontief_inverse(
        use, make,
        industry_col="naics",
        commodity_col="comm",
        value_col="usd",
    )

    # Same expected values as the hand-computed case.
    diag = L.filter(pl.col("output_industry") == pl.col("demand_industry"))
    assert diag.height == 2
    by_demand = {r["demand_industry"]: r["total_requirement"] for r in diag.to_dicts()}
    assert abs(by_demand["I1"] - 0.6 / 0.45) < 1e-9
    assert abs(by_demand["I2"] - 0.8 / 0.45) < 1e-9


def test_public_import_smoke() -> None:
    """The canonical caller form — `from eia.multipliers import compute_leontief_inverse`."""
    from eia.multipliers import compute_leontief_inverse as imported

    use, make = _two_industry_inputs()
    L = imported(use, make)
    assert L.height == 4
    assert L.columns == ["output_industry", "demand_industry", "total_requirement"]
```

### Step 3: Run the tests to confirm they all fail

```bash
uv run pytest tests/multipliers/test_leontief.py -v --no-cov
```

Expected: 8 errors / failures with `ModuleNotFoundError: No module named 'eia.multipliers'` (the package and module don't exist yet).

### Step 4: Create the package init at `src/eia/multipliers/__init__.py`

```python
"""Economic-multiplier computations: Leontief inverse and related model-side foundation code."""

from eia.multipliers.leontief import compute_leontief_inverse

__all__ = ["compute_leontief_inverse"]
```

### Step 5: Create the function at `src/eia/multipliers/leontief.py`

```python
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
    # Validate columns
    for df_name, df in (("use_matrix", use_matrix), ("make_matrix", make_matrix)):
        for col in (industry_col, commodity_col, value_col):
            if col not in df.columns:
                raise ValueError(
                    f"{df_name} missing required column {col!r}"
                )

    # Determine the union of industries and commodities for deterministic indexing
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

    # Build dense V (n_industry x n_commodity) from make_matrix
    V = np.zeros((n_ind, n_com))
    for row in make_matrix.iter_rows(named=True):
        i = industry_idx[row[industry_col]]
        c = commodity_idx[row[commodity_col]]
        V[i, c] = float(row[value_col])

    # Build dense U (n_commodity x n_industry) from use_matrix
    U = np.zeros((n_com, n_ind))
    for row in use_matrix.iter_rows(named=True):
        c = commodity_idx[row[commodity_col]]
        j = industry_idx[row[industry_col]]
        U[c, j] = float(row[value_col])

    # Industry total output q[i] = sum over commodities
    q = V.sum(axis=1)
    # Commodity total output x[c] = sum over industries
    x = V.sum(axis=0)

    # D[i, c] = V[i, c] / x[c] -- normalize Make by commodity total; zero for x=0
    D = np.zeros_like(V)
    nonzero_x = x > 0
    D[:, nonzero_x] = V[:, nonzero_x] / x[nonzero_x]

    # B[c, j] = U[c, j] / q[j] -- normalize Use by industry total; zero for q=0
    B = np.zeros_like(U)
    nonzero_q = q > 0
    B[:, nonzero_q] = U[:, nonzero_q] / q[nonzero_q]

    # A = D @ B (industry-by-industry direct requirements)
    A = D @ B

    # L = (I - A)^-1
    I_minus_A = np.eye(n_ind) - A
    try:
        L = np.linalg.inv(I_minus_A)
    except np.linalg.LinAlgError as exc:
        raise ValueError(f"(I - A) is singular: {exc}") from exc

    # Convert to long-form Polars sorted by (demand_industry, output_industry).
    # itertools.product(industries, industries) yields (j, i) pairs with j varying slowest.
    demand_codes = [d for d, _ in itertools.product(all_industries, all_industries)]
    output_codes = [o for _, o in itertools.product(all_industries, all_industries)]
    # L.flatten(order='F') visits column-major: column j read top-to-bottom, then next column.
    # That matches our (j-slow, i-fast) tuple ordering.
    values = L.flatten(order="F").tolist()

    return pl.DataFrame(
        {
            "output_industry": output_codes,
            "demand_industry": demand_codes,
            "total_requirement": values,
        }
    )
```

### Step 6: Run the tests to confirm they pass

```bash
uv run pytest tests/multipliers/test_leontief.py -v --no-cov
```

Expected: 8 passed.

### Step 7: Run the full suite for regression

```bash
make test
```

Expected: 51 passed (5 warehouse + 2 tiger + 19 geo + 7 temporal + 9 panel + 1 composition + 8 multipliers).

### Step 8: Commit

```bash
git add tests/multipliers/__init__.py tests/multipliers/test_leontief.py src/eia/multipliers/__init__.py src/eia/multipliers/leontief.py
git commit -m "feat(multipliers): compute_leontief_inverse with BEA Type I formulation"
```

The Bash tool will append the standard `Co-Authored-By` footer automatically.

## Constraints

- Touch ONLY the four files listed above. Do NOT modify `pyproject.toml`, `Makefile`, or any existing source/test file.
- Use `numpy.linalg.inv`; do NOT pull in scipy.
- Do NOT memoize / cache the result; the function is stateless.
- Run the failing tests (Step 3) FIRST and confirm the failure mode is the predicted ModuleNotFoundError.

## Self-Review

- All 8 tests pass?
- The hand-computed test asserts within 1e-9 of analytical values?
- The singular-matrix test raises with substring "singular"?
- Output is sorted by (demand_industry, output_industry)?
- Output column types are (Utf8, Utf8, Float64)?
- `from __future__ import annotations` at the top of the new modules?
- No `# type: ignore` without reason; no emojis?

## Report Format

- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- What you implemented
- Pytest output for Step 3 (failing) and Step 7 (full sweep) — actual lines
- Files changed
- Commit SHA
- Any concerns about the math (especially the singular-matrix construction in `test_singular_matrix_raises`; verify it actually triggers a singular condition)

---

## Task 2: Quality sweep — ruff, mypy, full pytest

Run gates scoped to in-scope files; fix anything found.

### Step 1: Lint

```bash
uv run ruff check src/eia/multipliers/ tests/multipliers/test_leontief.py
```

Expected: clean. If RUF002/RUF003 flags Unicode (`×`, `→`) in docstrings, replace with ASCII (`x`, `->`) — known sensitivity in this codebase from prior cycles.

### Step 2: Type-check

```bash
uv run mypy src/eia/multipliers/
```

Expected: `Success: no issues found in 2 source files`. If mypy reports an issue, fix the code (not the test). For unavoidable third-party gaps, use targeted `# type: ignore[<error-code>]` with brief justification. NumPy has good stubs in modern versions; should not need ignores.

### Step 3: Run the full test suite

```bash
make test
```

Expected: 51 passed. Coverage on `src/eia/multipliers/leontief.py` should be near 100%.

### Step 4: Commit fixes if any

```bash
git add -p
git commit -m "chore: lint and type fixes for Leontief multipliers"
```

If Steps 1-3 are clean, no commit. Just report DONE.

## Constraints

- Touch ONLY `src/eia/multipliers/` and `tests/multipliers/test_leontief.py`.
- Out-of-scope pre-existing errors in other source files are NOT this task's concern.

---

## Done

After Task 2, `compute_leontief_inverse` is the third-leg of the Phase 1 model-side foundation:

- `eia.transforms.attach_county_fips` / `attach_county_fips_via_zip` / `attach_period_id` / `build_county_month_panel` (clean-time enrichment + panel)
- **`eia.multipliers.compute_leontief_inverse`** (multiplier math) ← this work
- (future) feature ETL that joins panel × event-aggregates × multiplier-applied covariates → regression input

The downstream feature-ETL transform that turns "events + panel + multipliers" into a model-ready DataFrame is the next natural piece of work; spec'd separately.
