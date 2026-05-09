# BEA Leontief Multipliers (Industry-by-Industry Total Requirements)

**Date:** 2026-05-09
**Phase:** 1 (model-side foundation)
**Status:** Design approved (autonomous run; user authorized "do as much as you possibly can"); awaiting implementation plan

## Background

The data acquisition strategy ([docs/data_acquisition_strategy.md](../../data_acquisition_strategy.md) §3.1) names BEA's Input-Output Use & Make tables as the foundation for "deriving Leontief inverse and our own multipliers — replaces $30k IMPLAN license." The BEA source ([src/eia/sources/bea_io.py](../../../src/eia/sources/bea_io.py)) already has the puller skeleton; the warehouse migrations ([migrations/002_economic_backbone.sql](../../../migrations/002_economic_backbone.sql)) define `bea_io_use` and `bea_io_make` tables.

What's missing is the multiplier *math*: given a Use matrix and a Make matrix, compute the industry-by-industry **Total Requirements** matrix `L = (I - A)^-1` (the "Leontief inverse"). That matrix is what turns a dollar of direct event spending into total economic output, including indirect (supply-chain) and the local intermediate-purchases ripple. It is the central object of an input-output model.

This spec covers the math, not the data pull. The function takes Use and Make as Polars DataFrames; whether they came from the BEA pull, a synthetic test fixture, or a future fixture-loaded reference dataset is the caller's concern.

## Goals

- One public function in a new `src/eia/multipliers/` package that takes a Use matrix and a Make matrix as long-format Polars DataFrames and returns the Total Requirements matrix as a long-format Polars DataFrame.
- Standard BEA Type I, industry-by-industry, industry-technology-assumption derivation: `A = D @ B`, then `L = (I - A)^-1`.
- Output is queryable via `WHERE output_industry = ?` / `WHERE demand_industry = ?` joins, suitable for downstream model code that multiplies a final-demand vector against L to estimate total output.
- Hand-computable 2x2 reference test case to lock the math invariants.

## Non-goals

- **Type II multipliers** (model closed with respect to households — endogenous consumption). Standard practice is to add Type II as a follow-up after Type I is validated; the additional rows/columns for the household sector require separate input data (Personal Consumption Expenditures by industry).
- **Commodity-by-commodity multipliers.** The natural query at the call site is "given a dollar of demand for industry X" (e.g. NAICS 721 = Accommodation), so industry-by-industry is the right granularity. Commodity-by-commodity would be useful for some ancillary analyses but is YAGNI here.
- **Regional Type I/II RIMS-style multipliers.** Those derive from regional purchase coefficients, which are a separate (and more complex) layer on top of the national Leontief inverse. Phase 1 uses the national multiplier; regionalization is a Phase 2 add.
- **Pulling BEA data.** This spec is the math; the BEA source already has a fetch skeleton.
- **Storing multipliers in the warehouse.** The function is stateless; downstream model code calls it (or memoizes the result) when needed. No new warehouse table or migration.
- **Aggregation-level conversion** (summary <-> sector <-> detail). The function works at whatever level the caller's matrices use. For Phase 1 we'll target summary (71-industry); the function doesn't care.

## Design

### Module placement

New top-level package `src/eia/multipliers/`. This complements `src/eia/transforms/` (clean-time enrichment) and is the natural home for future model-side foundation code. Files:

- `src/eia/multipliers/__init__.py` — re-exports `compute_leontief_inverse`
- `src/eia/multipliers/leontief.py` — the function
- `tests/multipliers/__init__.py` — empty package marker
- `tests/multipliers/test_leontief.py` — unit tests

### Public surface

```python
def compute_leontief_inverse(
    use_matrix: pl.DataFrame,
    make_matrix: pl.DataFrame,
    *,
    industry_col: str = "industry_code",
    commodity_col: str = "commodity_code",
    value_col: str = "value_millions",
) -> pl.DataFrame
```

**Inputs.** Both `use_matrix` and `make_matrix` are long-format Polars DataFrames. Each must contain at least the three columns named by `industry_col`, `commodity_col`, `value_col`. One row per (industry, commodity) cell. Defaults match the warehouse schema in [migrations/002_economic_backbone.sql:21](../../../migrations/002_economic_backbone.sql:21).

- `use_matrix`: `U[c, j]` = dollar value of commodity `c` used by industry `j` as input. The function reads `value_col` indexed by `(commodity_col, industry_col)`.
- `make_matrix`: `V[i, c]` = dollar value of commodity `c` produced by industry `i`. Indexed by `(industry_col, commodity_col)`.

**Output.** A long-format Polars DataFrame with three columns:

| Column | Dtype | Meaning |
|---|---|---|
| `output_industry` | Utf8 | row `i` of `L` — the industry whose output is required |
| `demand_industry` | Utf8 | column `j` of `L` — the industry receiving final demand |
| `total_requirement` | Float64 | `L[i, j]` — output of `i` per unit of final demand for `j` |

For BEA summary level (71 industries), output has 71 × 71 = 5041 rows. Sorted by `(demand_industry, output_industry)`.

### The math (industry technology assumption)

Following Miller & Blair, *Input-Output Analysis* (and BEA's published methodology):

1. **Industry total output** `q[i] = Σ_c V[i, c]` (each industry's total revenue).
2. **Commodity total output** `x[c] = Σ_i V[i, c]` (each commodity's total production).
3. **Market shares matrix** `D[i, c] = V[i, c] / x[c]` (industry × commodity; what fraction of commodity `c` is produced by industry `i`). Columns sum to 1.
4. **Direct requirements coefficients** `B[c, j] = U[c, j] / q[j]` (commodity × industry; commodity `c` needed per dollar of industry `j`'s output).
5. **Industry-by-industry direct requirements** `A = D @ B` (industry × industry). `A[i, j]` = output of industry `i` directly required per unit of industry `j`'s output.
6. **Leontief inverse** `L = (I - A)^-1`. `L[i, j]` = total (direct + all indirect rounds) output of industry `i` required per unit of final demand for industry `j`'s output. Diagonals are always ≥ 1 (an industry's own output is always at least the final demand on it).

### Implementation strategy

Math runs through NumPy (`numpy.linalg.inv`); long-format Polars DataFrames are pivoted to dense matrices, the inverse is computed, and the result is unpacked back into long format. NumPy is already a transitive dependency via pyarrow/polars; no `pyproject.toml` change is needed.

Step-by-step:

1. **Validate inputs.** Confirm the three required columns exist in both inputs. Raise `ValueError` if not.
2. **Determine industry and commodity index sets.** Union of the values in `industry_col` (from both Use and Make) and `commodity_col`. Sort alphabetically for deterministic output ordering.
3. **Pivot to dense NumPy matrices.** Build `V` (n_industry × n_commodity) and `U` (n_commodity × n_industry) by iterating the long form and assigning into pre-allocated zero matrices. Iter-rows is fine at Phase 1 scale (71 × 71).
4. **Compute `q`, `x`, `D`, `B`** per the formulas above. Use `numpy.where(denom > 0, num / denom, 0.0)` to handle divide-by-zero (industries or commodities with zero total output).
5. **Compute `A = D @ B` and `I - A`.**
6. **Invert.** `numpy.linalg.inv(I - A)`. If the matrix is singular, NumPy raises `LinAlgError`; the function re-raises as `ValueError("(I - A) is singular: ...")` so callers see a domain-specific error message.
7. **Unpack** `L` into long format: build the `(n_industry × n_industry)` row list with `(output_industry, demand_industry, total_requirement)`. Sort by `(demand_industry, output_industry)`.

### Edge cases

| Input | Output |
|---|---|
| Both matrices populated with consistent industry/commodity sets | Standard L matrix, `n_industry^2` rows |
| Industries appear in only one of Use or Make | Treated as zero in the missing matrix; rows/cols of zeros propagate through the math |
| Commodities appear in only one of Use or Make | Same: treated as zero in the missing matrix |
| An industry has zero total output (`q[i] = 0`) | That column of `B` is set to zero (no input requirements per zero output); does not crash |
| A commodity has zero total output (`x[c] = 0`) | That column of `D` is set to zero (no industry produces zero commodity); does not crash |
| `(I - A)` is singular | Raises `ValueError("(I - A) is singular: ...")` |
| Inputs missing required columns | Raises `ValueError` naming the missing column |

### Caller integration

After this lands, downstream model code (a separate spec) will:

```python
from eia.multipliers import compute_leontief_inverse
from eia.warehouse import get_warehouse

wh = get_warehouse()
use = wh.query("SELECT * FROM bea_io_use WHERE table_year = 2023")
make = wh.query("SELECT * FROM bea_io_make WHERE table_year = 2023")

L = compute_leontief_inverse(use, make)

# Apply to a final-demand vector y (industry -> dollars):
total_output = L.join(y, left_on="demand_industry", right_on="industry").with_columns(
    contribution=pl.col("total_requirement") * pl.col("dollars"),
).group_by("output_industry").agg(pl.sum("contribution"))
```

The downstream feature ETL / model is **out of scope for this spec**.

## Testing

Unit tests in `tests/multipliers/test_leontief.py`:

- **2-industry, 2-commodity hand-computed case.** A simple matrix where I can derive the expected `L` analytically. The exact numbers are part of the test:
  - Make: I1 makes \$10 of commodity A; I2 makes \$20 of commodity B (each industry makes only its own commodity, so D = I).
  - Use: I1 uses \$2 of A and \$1 of B; I2 uses \$6 of A and \$8 of B.
  - Expected `A = D @ B = [[0.2, 0.3], [0.1, 0.4]]`.
  - Expected `L = inv(I - A) ≈ [[1.333, 0.667], [0.222, 1.778]]` (computed: `det(I-A) = 0.45`).
  - Test asserts each element of L within 1e-9 of the analytical value.

- **Identity-like case.** Each industry produces exactly its own commodity AND uses no inputs from any industry → `A = 0`, `L = I`. Diagonals = 1.0, off-diagonals = 0.0.

- **Singular `I - A` raises ValueError.** Construct a degenerate case where `A` has eigenvalue 1 (e.g. `A = I`, so `I - A = 0`); confirm `ValueError` with substring `"singular"`.

- **Custom column names.** Caller passes `industry_col="naics_code"`, `value_col="usd"`; output schema and values are correct.

- **Output schema.** Output has exactly columns `["output_industry", "demand_industry", "total_requirement"]` with dtypes `[Utf8, Utf8, Float64]`. Output is sorted by `(demand_industry, output_industry)`.

- **Output row count.** For an `n × n` Leontief inverse, output has exactly `n^2` rows.

- **Industries union correctly across Use and Make.** Use has industries `{I1, I2, I3}`; Make has industries `{I1, I2}` (I3 doesn't make anything). The function treats the missing rows of Make as zero; output `L` has 3×3 rows including I3.

- **Diagonals are ≥ 1.** Every multiplier `L[i, i]` (output industry = demand industry) must be at least 1 by construction. Holds for any well-formed matrix.

- **Public import smoke test** — `from eia.multipliers import compute_leontief_inverse` resolves and produces a correct result for the 2-industry case.

## Operational notes

- **NumPy.** No new pyproject dependency. NumPy is transitively pulled in by pyarrow.
- **Performance.** BEA summary is 71 industries; the inverse of a 71×71 matrix is sub-millisecond on modern hardware. Sector level (405) is also fine. Detail level (~400+) is still well under 1 second.
- **Numerical stability.** For the BEA matrices we'll see, condition numbers are low (multipliers are well-defined, off-diagonal `A` values are small). `numpy.linalg.inv` is sufficient. If condition numbers become a concern in Phase 2 (regional matrices, custom aggregations), switch to `scipy.linalg.solve` per-column or LU decomposition. Out of scope here.
- **Floating-point determinism.** NumPy's `linalg.inv` uses LAPACK; results are bit-identical for a fixed input on a fixed BLAS build. Tests use absolute tolerances (1e-9) rather than exact equality.

## References

- [docs/data_acquisition_strategy.md](../../data_acquisition_strategy.md) §3.1 — BEA tables as the foundation for replacing IMPLAN.
- [src/eia/sources/bea_io.py](../../../src/eia/sources/bea_io.py) — the BEA Use/Make source skeleton.
- [migrations/002_economic_backbone.sql](../../../migrations/002_economic_backbone.sql) — `bea_io_use` and `bea_io_make` table schemas.
- Miller, Ronald E. & Peter D. Blair, *Input-Output Analysis: Foundations and Extensions*, Cambridge UP — standard reference for the formulation used here.
- BEA, "Concepts and Methods of the U.S. Input-Output Accounts" — methodology document for the Use/Make/Total-Requirements formulation.
