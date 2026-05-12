# County-Month Training Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `build_county_month_panel` Polars transform that produces the dense `(county_fips × period_month)` cartesian product needed for treatment-effect modeling and DOR-level evaluation.

**Architecture:** Single Polars function in a new `src/eia/transforms/panel.py` module — no warehouse coupling, no migration, no caching. Caller passes a `counties` DataFrame (typically `wh.query("SELECT county_fips FROM dim_county")`) plus a year range; function returns a Polars DataFrame with `(fips, period_month, period_id, year, month)` columns. Output formats are byte-identical to what `attach_period_id` produces, so panel and event rows join cleanly.

**Tech Stack:** Polars 1.x, pytest 8.x. No new dependencies. No database access in tests.

---

## File Structure

**Create:**
- `src/eia/transforms/panel.py` — module with one public function `build_county_month_panel`
- `tests/transforms/test_panel.py` — unit tests (8 tests at Task 1, +1 public-import smoke test at Task 2)

**Modify:**
- `src/eia/transforms/__init__.py` — extend to re-export `build_county_month_panel` alongside `attach_county_fips` and `attach_period_id`

---

## Task 1: `build_county_month_panel` function (TDD)

Write all eight unit tests first, confirm they fail (the module doesn't exist yet), then create `panel.py` with the function. The function is a single Polars expression chain plus two input-validation guards. No helpers, no logging, no caching.

**Files to create:**
- `src/eia/transforms/panel.py`
- `tests/transforms/test_panel.py`

### Step 1: Write the failing tests in `tests/transforms/test_panel.py`

```python
"""Tests for transforms.panel."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest


def test_empty_counties_returns_empty_panel() -> None:
    """Empty input yields empty output with the five expected columns and dtypes."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame({"county_fips": []}, schema={"county_fips": pl.Utf8})
    panel = build_county_month_panel(counties, start_year=2020, end_year=2020)

    assert panel.height == 0
    assert panel.columns == ["county_fips", "period_month", "period_id", "year", "month"]
    assert panel.schema["county_fips"] == pl.Utf8
    assert panel.schema["period_month"] == pl.Utf8
    assert panel.schema["period_id"] == pl.Utf8
    assert panel.schema["year"] == pl.Int32
    assert panel.schema["month"] == pl.Int32


def test_single_county_single_year_yields_12_rows() -> None:
    """One county × one year produces 12 rows in calendar order with correct period strings."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame({"county_fips": ["06073"]})
    panel = build_county_month_panel(counties, start_year=2015, end_year=2015)

    assert panel.height == 12
    assert panel["county_fips"].unique().to_list() == ["06073"]
    assert panel["month"].to_list() == list(range(1, 13))
    assert panel["period_month"].to_list() == [
        "2015-01", "2015-02", "2015-03", "2015-04",
        "2015-05", "2015-06", "2015-07", "2015-08",
        "2015-09", "2015-10", "2015-11", "2015-12",
    ]
    assert panel["period_id"].to_list() == [
        "2015Q1", "2015Q1", "2015Q1",
        "2015Q2", "2015Q2", "2015Q2",
        "2015Q3", "2015Q3", "2015Q3",
        "2015Q4", "2015Q4", "2015Q4",
    ]


def test_two_counties_two_years_yields_48_rows_sorted() -> None:
    """Output is sorted (county_fips, year, month) deterministically."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame({"county_fips": ["06073", "36061"]})
    panel = build_county_month_panel(counties, start_year=2015, end_year=2016)

    assert panel.height == 48  # 2 counties × 2 years × 12 months

    # First 24 rows are "06073" (alphabetically first), last 24 are "36061".
    first_24 = panel.head(24)
    last_24 = panel.tail(24)
    assert first_24["county_fips"].unique().to_list() == ["06073"]
    assert last_24["county_fips"].unique().to_list() == ["36061"]

    # Within each county, order is (year asc, month asc).
    assert first_24["year"].to_list()[:12] == [2015] * 12
    assert first_24["year"].to_list()[12:] == [2016] * 12
    assert first_24["month"].to_list() == list(range(1, 13)) * 2


def test_format_consistency_with_attach_period_id() -> None:
    """The panel's period strings must match attach_period_id byte-for-byte for the same dates."""
    from eia.transforms.panel import build_county_month_panel
    from eia.transforms.temporal import attach_period_id

    counties = pl.DataFrame({"county_fips": ["00001"]})
    panel = build_county_month_panel(counties, start_year=2023, end_year=2023)

    # Run attach_period_id on the first-of-month dates for the same year.
    dates_df = pl.DataFrame(
        {"event_date": [date(2023, m, 1) for m in range(1, 13)]}
    )
    via_attach = attach_period_id(dates_df)

    assert panel["period_month"].to_list() == via_attach["period_month"].to_list()
    assert panel["period_id"].to_list() == via_attach["period_id"].to_list()


def test_duplicate_fips_silently_deduped() -> None:
    """Repeated FIPS in input collapse to one set of months in output."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame({"county_fips": ["06073", "06073", "06073"]})
    panel = build_county_month_panel(counties, start_year=2020, end_year=2020)

    assert panel.height == 12  # not 36
    assert panel["county_fips"].unique().to_list() == ["06073"]


def test_start_year_greater_than_end_year_raises() -> None:
    """Reversed year range is a programmer error, not a silent empty result."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame({"county_fips": ["06073"]})
    with pytest.raises(ValueError, match=r"start_year .* > end_year"):
        build_county_month_panel(counties, start_year=2025, end_year=2020)


def test_null_fips_raises() -> None:
    """A null FIPS in the input is a data-quality bug — fail loud, not silent."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame(
        {"county_fips": ["06073", None, "36061"]},
        schema={"county_fips": pl.Utf8},
    )
    with pytest.raises(ValueError, match=r"county_fips contains null"):
        build_county_month_panel(counties)


def test_custom_fips_col() -> None:
    """Caller can pass an alternative column name for the input FIPS column."""
    from eia.transforms.panel import build_county_month_panel

    counties = pl.DataFrame({"my_fips": ["06073"]})
    panel = build_county_month_panel(
        counties, fips_col="my_fips", start_year=2020, end_year=2020
    )

    assert panel.columns == ["my_fips", "period_month", "period_id", "year", "month"]
    assert panel["my_fips"].to_list() == ["06073"] * 12
```

### Step 2: Run the tests to confirm they all fail

```bash
uv run pytest tests/transforms/test_panel.py -v --no-cov
```

Expected: 8 errors / failures with `ImportError: cannot import name 'build_county_month_panel' from 'eia.transforms.panel'` (or `ModuleNotFoundError: No module named 'eia.transforms.panel'`).

### Step 3: Create `src/eia/transforms/panel.py`

```python
"""(county_fips × period_month) cartesian-product panel for treatment-effect modeling.

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
    """Produce the (county_fips × period_month) cartesian product as a Polars DataFrame.

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
```

### Step 4: Run the tests to confirm they pass

```bash
uv run pytest tests/transforms/test_panel.py -v --no-cov
```

Expected: 8 passed.

If `test_empty_counties_returns_empty_panel` fails because Polars' `pl.Series([], dtype=pl.Int32)` empty-list construction has unexpected behavior on this version, the issue is likely in the `months` frame construction when `n_years == 1` and the input is empty. The empty-input path goes through the cross-join with a non-empty `months` frame and produces zero rows — schema should still be correct. If a schema mismatch surfaces, ensure the explicit `dtype=pl.Int32` on the `pl.Series` constructions is present (it is in the code above).

If `test_format_consistency_with_attach_period_id` fails, the panel and the period_id transform are producing different format strings. Compare side-by-side: the panel uses `pl.date(...).dt.strftime("%Y-%m")` and `pl.format("{}Q{}", year, quarter)`; `attach_period_id` uses `pl.col(date_col).dt.strftime("%Y-%m")` and `pl.format("{}Q{}", year, quarter)`. Both should produce identical output for the same inputs. If they don't, inspect both columns' string content to find the divergence (most likely a zero-padding difference on quarter — both should produce `"Q3"` not `"Q03"`).

### Step 5: Confirm no regression in existing tests

```bash
uv run pytest tests/transforms/ tests/sources/test_tiger.py tests/test_warehouse.py -v --no-cov
```

Expected: 30 passed (5 warehouse + 2 tiger + 8 geo + 7 temporal + 8 new panel).

### Step 6: Commit

```bash
git add tests/transforms/test_panel.py src/eia/transforms/panel.py
git commit -m "feat(transforms): build_county_month_panel cartesian-product transform"
```

The Bash tool will append the standard `Co-Authored-By` footer automatically.

---

## Task 2: Public export from `transforms/__init__.py`

Re-export `build_county_month_panel` from the package alongside the existing two transforms.

**Files to modify:**
- `src/eia/transforms/__init__.py`
- `tests/transforms/test_panel.py`

### Step 1: Append a smoke test to `tests/transforms/test_panel.py`

```python
def test_public_import() -> None:
    """The canonical caller form — `from eia.transforms import build_county_month_panel`."""
    from eia.transforms import build_county_month_panel as imported

    counties = pl.DataFrame({"county_fips": ["06073"]})
    panel = imported(counties, start_year=2023, end_year=2023)
    assert panel.height == 12
    assert panel["county_fips"].unique().to_list() == ["06073"]
```

### Step 2: Run the test to confirm it fails

```bash
uv run pytest tests/transforms/test_panel.py::test_public_import -v --no-cov
```

Expected: FAIL with `ImportError: cannot import name 'build_county_month_panel' from 'eia.transforms'`.

### Step 3: Update `src/eia/transforms/__init__.py`

Replace the current contents with:

```python
"""Cleaning and feature-engineering transforms applied at to_cleaned() time."""

from eia.transforms.geo import attach_county_fips
from eia.transforms.panel import build_county_month_panel
from eia.transforms.temporal import attach_period_id

__all__ = [
    "attach_county_fips",
    "attach_period_id",
    "build_county_month_panel",
]
```

The order in `__all__` is alphabetical, matching the import statements above.

### Step 4: Run the test to confirm it passes

```bash
uv run pytest tests/transforms/test_panel.py::test_public_import -v --no-cov
```

Expected: PASS.

Then run all transforms tests to confirm no regression in the existing public-import smoke tests in `test_geo.py` and `test_temporal.py`:

```bash
uv run pytest tests/transforms/ -v --no-cov
```

Expected: 24 passed (8 geo + 7 temporal + 9 panel — including the new public-import test).

### Step 5: Commit

```bash
git add src/eia/transforms/__init__.py tests/transforms/test_panel.py
git commit -m "feat(transforms): export build_county_month_panel at package level"
```

---

## Task 3: Quality sweep — ruff, mypy, full pytest

Run the quality gates scoped to in-scope files. The codebase has pre-existing out-of-scope errors that are NOT this task's concern.

**Files:**
- Whatever ruff / mypy flags inside the touched files

### Step 1: Lint scoped to this branch's changes

```bash
uv run ruff check src/eia/transforms/panel.py src/eia/transforms/__init__.py tests/transforms/test_panel.py
```

Expected: clean. If ruff reports an issue, fix it in place. Common fixes are unused imports or missing `from __future__ import annotations`.

### Step 2: Type-check scoped to this branch's changes

```bash
uv run mypy src/eia/transforms/panel.py src/eia/transforms/__init__.py
```

Expected: `Success: no issues found in 2 source files`. If mypy reports an issue, fix the code (not the test). For unavoidable third-party type-system gaps, use a targeted `# type: ignore[<error-code>]` with a brief justification.

### Step 3: Run the full test suite

```bash
make test
```

Expected: 31 passed (5 warehouse + 2 tiger + 8 geo + 7 temporal + 9 panel). Coverage on `src/eia/transforms/panel.py` should be at or near 100% — if anything significant is uncovered, add a regression test for it.

If any test fails, STOP and report BLOCKED with the full failure output. Do NOT modify tests to make them pass.

### Step 4: Commit fixes if Steps 1-3 produced any

If you made any code changes during this sweep, commit them as a single follow-up:

```bash
git add -p   # selectively stage
git commit -m "chore: lint and type fixes for county-month panel transform"
```

If Steps 1-3 were all clean and you made no changes, do NOT commit. Just report DONE with no commit.

---

## Done

After Task 3, the panel transform is wired in and ready to be called by feature-engineering ETL alongside the events table and federal covariates. The eventual integration shape (out of scope for this plan):

```python
from eia.transforms import build_county_month_panel
from eia.warehouse import get_warehouse

wh = get_warehouse()
counties = wh.query("SELECT county_fips FROM dim_county")
panel = build_county_month_panel(counties)

event_aggs = wh.query("""
    SELECT county_fips, period_month,
           COUNT(*) AS n_events,
           SUM(expected_attendance) AS total_attendance
    FROM events
    GROUP BY county_fips, period_month
""")
training_input = panel.join(event_aggs, on=["county_fips", "period_month"], how="left")
```

The feature-engineering pipeline is its own brainstorm → spec → plan cycle.
