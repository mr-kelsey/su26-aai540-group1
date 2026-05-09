# `apply_multipliers` — Total Output from Final Demand

**Date:** 2026-05-09
**Phase:** 1 (model-side foundation)
**Status:** Design approved (autonomous run); awaiting implementation plan

## Background

`compute_leontief_inverse` ([src/eia/multipliers/leontief.py](../../../src/eia/multipliers/leontief.py)) produces the industry-by-industry Total Requirements matrix `L` as a long-form Polars DataFrame keyed by `(output_industry, demand_industry)`. The standard use of `L` is to apply it to a final-demand vector `y`: `total_output = L @ y`. That's the operation that turns "an event drives \$X of direct spending into industry j" into "the total economic output across all industries (direct + indirect + induced rounds)."

This transform exposes that matrix-vector application as a Polars-friendly function that takes a long-form `L` and a long-form `final_demand` vector and returns a long-form `total_output` DataFrame, without exposing NumPy details to the caller.

## Goals

- One public function in `src/eia/multipliers/apply.py` that consumes the output of `compute_leontief_inverse` plus a final-demand vector and returns total output per industry.
- Polars-native: no NumPy at the call site. Pure DataFrame join + group-by under the hood.
- Industries appearing in `y` but not in `L` contribute zero (they have no multiplier defined). Industries appearing in `L` but not in `y` still appear in the output (at zero, modulo the matrix multiply).
- Re-exported from `eia.multipliers` so the call site is `from eia.multipliers import apply_multipliers`.

## Non-goals

- **Auto-deriving `L`** inside this function. Caller passes a precomputed `L`. This keeps the function fast and stateless.
- **NumPy at the public surface.** All inputs and outputs are Polars DataFrames.
- **Industry-mapping logic** (e.g. "concert events go in NAICS 711"). That belongs to the event-source `to_cleaned()` integration, not to this multiplier-application function.
- **Aggregating across multiple final-demand scenarios** in one call. Caller iterates if needed.

## Design

### Module placement

`src/eia/multipliers/apply.py`. Re-exported from `src/eia/multipliers/__init__.py` alongside `compute_leontief_inverse`:

```python
from eia.multipliers.apply import apply_multipliers
from eia.multipliers.leontief import compute_leontief_inverse

__all__ = ["apply_multipliers", "compute_leontief_inverse"]
```

### Public surface

```python
def apply_multipliers(
    L: pl.DataFrame,
    final_demand: pl.DataFrame,
    *,
    industry_col: str = "industry",
    value_col: str = "value",
) -> pl.DataFrame
```

`L` must be the long-form output of `compute_leontief_inverse` — columns `output_industry`, `demand_industry`, `total_requirement` (all required). `final_demand` is a long-form DataFrame with columns named by `industry_col` (Utf8) and `value_col` (Float64-coercible).

Returns a Polars DataFrame with columns `["industry", "total_output"]`, sorted by `industry`. Row count equals the number of distinct industries in `L`.

### Internals

`total_output[i] = sum over j of L[i, j] * y[j]`

Polars expression:

1. **Validate columns.** Both inputs must have their declared columns; raise `ValueError` if missing.
2. **Left-join `L` with `final_demand`** on `L.demand_industry == final_demand[industry_col]`. The `value_col` column comes along; missing-from-`y` industries have null.
3. **Compute contribution** = `total_requirement * coalesce(value, 0)` (treat missing as zero demand).
4. **Group by `output_industry`, sum the contributions.**
5. **Rename `output_industry` to `industry` and `sum(contribution)` to `total_output`.** Sort by `industry`.

### Edge cases

| Input | Output |
|---|---|
| Both inputs populated, all industries present | Standard L @ y |
| Industry in `y` not in `L` | Ignored (no multiplier defined). |
| Industry in `L` not in `y` | Still appears in output (its row of L gets zero contributions from missing y[j]'s; its own `total_output[i]` = 0 unless its row picks up contributions from other j's that ARE in y). |
| `final_demand` empty | All output industries return zero |
| `final_demand` has a null value | Treated as zero (via coalesce) |
| `L` empty | Empty output |

### Caller integration

```python
from eia.multipliers import apply_multipliers, compute_leontief_inverse

L = compute_leontief_inverse(use, make)

# Direct demand: $1M into NAICS 721 (Accommodation).
demand = pl.DataFrame({
    "industry": ["721"],
    "value": [1_000_000.0],
})

total = apply_multipliers(L, demand)
# total has one row per industry; "721" row's total_output is its multiplier value.
```

## Testing

`tests/multipliers/test_apply.py`:

- **Unit demand on first industry of the 2-industry reference case** → output equals first column of L (`[1.333, 0.222]`). Test value within 1e-9.
- **Unit demand on second industry** → output equals second column of L (`[0.667, 1.778]`).
- **Mixed demand** → output equals `L @ y`. Specifically, `y = [10, 5]` → `output = [10*1.333 + 5*0.667, 10*0.222 + 5*1.778] = [16.667, 11.111]` within 1e-9.
- **Industry in `y` not in `L`** → ignored; outputs unchanged from when that demand wasn't present.
- **Industry in `L` not in `y`** → still appears in output. (Use the 2-industry reference, pass demand only for I1; output should have rows for both I1 and I2 with the appropriate values from L's first column.)
- **Empty `final_demand`** → all output industries return zero.
- **Null value in `final_demand`** → treated as zero.
- **Missing column in `L` or `final_demand`** → raises `ValueError` naming the column.
- **Custom column names** in `final_demand` (`industry_col="naics"`, `value_col="dollars"`) → respected.
- **Public import smoke test** — `from eia.multipliers import apply_multipliers`.

## Operational notes

- **Performance.** O(n_industry² + n_y) for the join + group-by. BEA summary 71-industry case = 5041 join rows; sub-millisecond.
- **Determinism.** Output is sorted alphabetically; identical inputs produce identical outputs.

## References

- [docs/superpowers/specs/2026-05-09-leontief-multipliers-design.md](2026-05-09-leontief-multipliers-design.md) — the function whose output this consumes.
