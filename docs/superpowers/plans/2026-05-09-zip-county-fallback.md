# `attach_county_fips_via_zip` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `attach_county_fips_via_zip` to `src/eia/transforms/geo.py` — fills null `county_fips` values from a HUD ZIP-county crosswalk lookup using max `res_ratio` per ZIP with alphabetical tiebreak.

**Architecture:** Single Polars expression chain extending the existing `geo.py` module. Caller-supplied HUD DataFrame parameter (no warehouse coupling). Strict contract — `fips_col` must already exist in input (typically from a prior `attach_county_fips` call). One INFO log line per call summarizing fills.

**Tech Stack:** Polars 1.x, pytest 8.x. No new dependencies. No migration.

---

## File Structure

**Modify:**
- `src/eia/transforms/geo.py` — append `attach_county_fips_via_zip` function (existing `attach_county_fips`, helpers, and module-level `logger` are unchanged)
- `src/eia/transforms/__init__.py` — extend `__all__` and add the import
- `tests/transforms/test_geo.py` — append 10 new tests for the function

**Create:** none

---

## Task 1: `attach_county_fips_via_zip` function (TDD)

Write all 10 tests first, confirm they fail, then implement.

**Files:**
- Modify: `src/eia/transforms/geo.py`
- Modify: `tests/transforms/test_geo.py`

### Step 1: Append the failing tests to `tests/transforms/test_geo.py`

Add this block at the end of the file:

```python
# ---- attach_county_fips_via_zip tests ----


def test_zip_already_set_fips_preserved(fake_counties_geo: Path) -> None:
    """If county_fips is already non-null, the function does not overwrite it."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {
            "venue_zip": ["92101", "92103"],
            "county_fips": ["99999", None],
        },
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {
            "zip": ["92101", "92103"],
            "county_fips": ["06073", "06073"],
            "res_ratio": [1.0, 1.0],
        }
    )

    out = attach_county_fips_via_zip(df, hud)

    # Row 0: existing "99999" preserved (not overwritten with "06073" from HUD).
    # Row 1: null filled with "06073" via ZIP lookup.
    assert out["county_fips"].to_list() == ["99999", "06073"]


def test_zip_null_fips_filled_from_single_county_zip(
    fake_counties_geo: Path,
) -> None:
    """A null county_fips + ZIP mapping to one county gets filled."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"venue_zip": ["92101"], "county_fips": [None]},
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {"zip": ["92101"], "county_fips": ["06073"], "res_ratio": [1.0]}
    )

    out = attach_county_fips_via_zip(df, hud)

    assert out["county_fips"].to_list() == ["06073"]


def test_zip_max_res_ratio_wins_multi_county(fake_counties_geo: Path) -> None:
    """A ZIP spanning multiple counties picks the max res_ratio one."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"venue_zip": ["12345"], "county_fips": [None]},
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {
            "zip": ["12345", "12345", "12345"],
            "county_fips": ["00001", "00002", "00003"],
            "res_ratio": [0.2, 0.7, 0.1],  # 00002 has the highest residential weight
        }
    )

    out = attach_county_fips_via_zip(df, hud)

    assert out["county_fips"].to_list() == ["00002"]


def test_zip_tie_on_res_ratio_breaks_alphabetically(
    fake_counties_geo: Path,
) -> None:
    """Ties on res_ratio resolve to the alphabetically-lowest county_fips."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"venue_zip": ["12345"], "county_fips": [None]},
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    # Input order is "00002" first, "00001" second; the function must still pick "00001".
    hud = pl.DataFrame(
        {
            "zip": ["12345", "12345"],
            "county_fips": ["00002", "00001"],
            "res_ratio": [0.5, 0.5],
        }
    )

    out = attach_county_fips_via_zip(df, hud)

    assert out["county_fips"].to_list() == ["00001"]


def test_zip_null_zip_stays_null(fake_counties_geo: Path) -> None:
    """A null venue_zip with null county_fips stays null."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"venue_zip": [None], "county_fips": [None]},
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {"zip": ["92101"], "county_fips": ["06073"], "res_ratio": [1.0]}
    )

    out = attach_county_fips_via_zip(df, hud)

    assert out["county_fips"].to_list() == [None]


def test_zip_not_in_hud_stays_null(fake_counties_geo: Path) -> None:
    """A ZIP that isn't in HUD leaves county_fips null."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"venue_zip": ["99999"], "county_fips": [None]},
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {"zip": ["92101"], "county_fips": ["06073"], "res_ratio": [1.0]}
    )

    out = attach_county_fips_via_zip(df, hud)

    assert out["county_fips"].to_list() == [None]


def test_zip_empty_input(fake_counties_geo: Path) -> None:
    """Empty input frame returns empty frame, columns preserved."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"venue_zip": [], "county_fips": []},
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {"zip": ["92101"], "county_fips": ["06073"], "res_ratio": [1.0]}
    )

    out = attach_county_fips_via_zip(df, hud)

    assert out.height == 0
    assert out.columns == ["venue_zip", "county_fips"]


def test_zip_missing_fips_col_raises(fake_counties_geo: Path) -> None:
    """If fips_col is absent, raise ValueError pointing the caller at attach_county_fips."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame({"venue_zip": ["92101"]})  # no county_fips column at all
    hud = pl.DataFrame(
        {"zip": ["92101"], "county_fips": ["06073"], "res_ratio": [1.0]}
    )

    with pytest.raises(ValueError, match=r"attach_county_fips"):
        attach_county_fips_via_zip(df, hud)


def test_zip_custom_column_names(fake_counties_geo: Path) -> None:
    """Caller can override zip_col and fips_col."""
    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {"the_zip": ["92101"], "the_fips": [None]},
        schema={"the_zip": pl.Utf8, "the_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {"zip": ["92101"], "county_fips": ["06073"], "res_ratio": [1.0]}
    )

    out = attach_county_fips_via_zip(
        df, hud, zip_col="the_zip", fips_col="the_fips"
    )

    assert out["the_fips"].to_list() == ["06073"]


def test_zip_logs_summary(
    fake_counties_geo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One INFO line per call with fill counts."""
    import logging

    from eia.transforms.geo import attach_county_fips_via_zip

    df = pl.DataFrame(
        {
            "venue_zip": ["92101", None, "99999", "92103"],
            "county_fips": [None, None, None, "99999"],
        },
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {
            "zip": ["92101"],
            "county_fips": ["06073"],
            "res_ratio": [1.0],
            "quarter": ["2024Q1"],
        }
    )

    with caplog.at_level(logging.INFO, logger="eia.transforms.geo"):
        attach_county_fips_via_zip(df, hud)

    matched = [
        r for r in caplog.records if "attach_county_fips_via_zip" in r.getMessage()
    ]
    assert len(matched) == 1
    msg = matched[0].getMessage()
    # 3 originally-null rows: row 0 (ZIP 92101 -> filled), row 1 (no ZIP), row 2 (ZIP not in HUD).
    # Row 3 was already set, doesn't count toward originally-null.
    assert "filled 1/3" in msg
    assert "1 no ZIP" in msg
    assert "1 ZIP not in crosswalk" in msg
    assert "2024Q1" in msg
```

### Step 2: Run the tests to confirm they all fail

```bash
uv run pytest tests/transforms/test_geo.py -v --no-cov -k "attach_county_fips_via_zip or test_zip_"
```

Expected: 10 errors / failures with `ImportError: cannot import name 'attach_county_fips_via_zip' from 'eia.transforms.geo'`.

### Step 3: Append the function to `src/eia/transforms/geo.py`

Add this function at the END of the file, after the existing `_log_summary` helper:

```python
def attach_county_fips_via_zip(
    df: pl.DataFrame,
    hud: pl.DataFrame,
    *,
    zip_col: str = "venue_zip",
    fips_col: str = "county_fips",
) -> pl.DataFrame:
    """Fill null `fips_col` values from a HUD ZIP-county crosswalk lookup.

    For each row whose `fips_col` is null, look up `zip_col` in `hud` and fill
    `fips_col` with the county_fips having max `res_ratio` (ties broken by
    alphabetically-lowest county_fips). Non-null `fips_col` values are
    preserved; null ZIP and ZIPs not in `hud` stay null.

    Caller must pre-filter `hud` to a single quarter. `df` must contain
    `fips_col` (typically populated by attach_county_fips first); raises
    ValueError otherwise.

    Logs one INFO line per call with fill counts.
    """
    if fips_col not in df.columns:
        raise ValueError(
            f"{fips_col!r} not in df.columns; run attach_county_fips first"
        )

    # Build deterministic zip -> county_fips lookup: max res_ratio per zip,
    # tie-broken by alphabetically-lowest county_fips.
    zip_to_county = (
        hud.sort(["res_ratio", "county_fips"], descending=[True, False])
        .group_by("zip", maintain_order=True)
        .first()
        .select(
            pl.col("zip"),
            pl.col("county_fips").alias("_zip_lookup_fips"),
        )
    )

    enriched = df.join(
        zip_to_county,
        left_on=zip_col,
        right_on="zip",
        how="left",
    )

    # Compute log counters BEFORE coalesce mutates fips_col.
    n_originally_null = enriched.filter(pl.col(fips_col).is_null()).height
    n_zip_missing = enriched.filter(
        pl.col(fips_col).is_null() & pl.col(zip_col).is_null()
    ).height
    n_zip_not_in_hud = enriched.filter(
        pl.col(fips_col).is_null()
        & pl.col(zip_col).is_not_null()
        & pl.col("_zip_lookup_fips").is_null()
    ).height
    n_filled = enriched.filter(
        pl.col(fips_col).is_null() & pl.col("_zip_lookup_fips").is_not_null()
    ).height

    result = enriched.with_columns(
        pl.coalesce(pl.col(fips_col), pl.col("_zip_lookup_fips")).alias(fips_col)
    ).drop("_zip_lookup_fips")

    hud_quarter = (
        hud["quarter"][0]
        if "quarter" in hud.columns and hud.height > 0
        else "unknown"
    )

    logger.info(
        "attach_county_fips_via_zip: filled %d/%d originally-null rows "
        "(%d no ZIP, %d ZIP not in crosswalk) [hud_quarter=%s]",
        n_filled,
        n_originally_null,
        n_zip_missing,
        n_zip_not_in_hud,
        hud_quarter,
    )

    return result
```

### Step 4: Run the tests to confirm they pass

```bash
uv run pytest tests/transforms/test_geo.py -v --no-cov -k "attach_county_fips_via_zip or test_zip_"
```

Expected: 10 passed.

### Step 5: Run all transform tests for regression

```bash
uv run pytest tests/transforms/ tests/sources/test_tiger.py tests/test_warehouse.py -v --no-cov
```

Expected: 42 passed (5 warehouse + 2 tiger + 9 panel + 7 temporal + 18 geo + 1 composition).

### Step 6: Commit

```bash
git add tests/transforms/test_geo.py src/eia/transforms/geo.py
git commit -m "feat(transforms): attach_county_fips_via_zip ZIP fallback"
```

The Bash tool will append the standard `Co-Authored-By` footer automatically.

## Constraints

- Touch ONLY `src/eia/transforms/geo.py` and `tests/transforms/test_geo.py`. Do NOT touch `__init__.py` (Task 2), other transforms, or pyproject.toml.
- Do NOT modify the existing `attach_county_fips`, `_load_counties_gdf`, `_counties_path`, `_log_summary`, or `_COUNTIES_CACHE` in `geo.py`.
- The new function uses the existing module-level `logger` (do NOT create a new one).

---

## Task 2: Public export from `transforms/__init__.py`

Re-export `attach_county_fips_via_zip` alongside the other three transforms.

**Files:**
- Modify: `src/eia/transforms/__init__.py`
- Modify: `tests/transforms/test_geo.py`

### Step 1: Append a smoke test to `tests/transforms/test_geo.py`

```python
def test_zip_public_import(fake_counties_geo: Path) -> None:
    """The canonical caller form — `from eia.transforms import attach_county_fips_via_zip`."""
    from eia.transforms import attach_county_fips_via_zip as imported

    df = pl.DataFrame(
        {"venue_zip": ["92101"], "county_fips": [None]},
        schema={"venue_zip": pl.Utf8, "county_fips": pl.Utf8},
    )
    hud = pl.DataFrame(
        {"zip": ["92101"], "county_fips": ["06073"], "res_ratio": [1.0]}
    )
    out = imported(df, hud)
    assert out["county_fips"].to_list() == ["06073"]
```

### Step 2: Run the test to confirm it fails

```bash
uv run pytest tests/transforms/test_geo.py::test_zip_public_import -v --no-cov
```

Expected: FAIL with `ImportError: cannot import name 'attach_county_fips_via_zip' from 'eia.transforms'`.

### Step 3: Update `src/eia/transforms/__init__.py`

Replace the file contents with:

```python
"""Cleaning and feature-engineering transforms applied at to_cleaned() time."""

from eia.transforms.geo import attach_county_fips, attach_county_fips_via_zip
from eia.transforms.panel import build_county_month_panel
from eia.transforms.temporal import attach_period_id

__all__ = [
    "attach_county_fips",
    "attach_county_fips_via_zip",
    "attach_period_id",
    "build_county_month_panel",
]
```

`__all__` is alphabetically ordered, four entries, list (not tuple). The `geo` import line groups both geo-module functions on one line for readability.

### Step 4: Run the test to confirm it passes

```bash
uv run pytest tests/transforms/test_geo.py::test_zip_public_import -v --no-cov
```

Expected: PASS.

Then run all transforms tests:

```bash
uv run pytest tests/transforms/ -v --no-cov
```

Expected: 36 passed (1 composition + 18 geo + 9 panel + 7 temporal + 1 zip-public-import inside geo). Wait — `test_zip_public_import` is in `test_geo.py`, so the geo file count goes from 18 to 19. Total: 1 + 19 + 9 + 7 = 36.

### Step 5: Commit

```bash
git add src/eia/transforms/__init__.py tests/transforms/test_geo.py
git commit -m "feat(transforms): export attach_county_fips_via_zip at package level"
```

## Constraints

- Touch ONLY `src/eia/transforms/__init__.py` and `tests/transforms/test_geo.py`.
- `__all__` MUST contain exactly the four listed names, alphabetically ordered.

---

## Task 3: Quality sweep — ruff, mypy, full pytest

Run quality gates scoped to in-scope files; fix any in-scope issue.

### Step 1: Lint

```bash
uv run ruff check src/eia/transforms/geo.py src/eia/transforms/__init__.py tests/transforms/test_geo.py
```

Expected: clean. If ruff flags Unicode characters (RUF002/RUF003 — `×`, `→`, etc. in docstrings or comments), replace with ASCII equivalents (`x`, `->`). This is a known sensitivity in this codebase from prior cycles.

### Step 2: Type-check

```bash
uv run mypy src/eia/transforms/geo.py src/eia/transforms/__init__.py
```

Expected: `Success: no issues found in 2 source files`. Fix any in-scope error in code (not tests). For unavoidable third-party type-system gaps, use targeted `# type: ignore[<error-code>]` with brief inline justification.

### Step 3: Run the full test suite

```bash
make test
```

Expected: 43 passed (5 warehouse + 2 tiger + 19 geo + 7 temporal + 9 panel + 1 composition). Coverage on the new function should be at or near 100%.

### Step 4: Commit fixes if any

If you made any code changes during this sweep, commit them:

```bash
git add -p   # selectively stage what you changed
git commit -m "chore: lint and type fixes for ZIP-county fallback"
```

If Steps 1-3 were clean, do NOT commit.

## Constraints

- Touch ONLY `src/eia/transforms/geo.py`, `src/eia/transforms/__init__.py`, `tests/transforms/test_geo.py`.
- Do NOT modify other source modules or test files.
- Do NOT modify `pyproject.toml`, `Makefile`, `migrations/`, `configs/`.

---

## Done

After Task 3, the geo-enrichment chain is complete:

```python
from eia.transforms import (
    attach_county_fips,
    attach_county_fips_via_zip,
    attach_period_id,
)

df = attach_county_fips(df)              # spatial join (lat/lon)
df = attach_county_fips_via_zip(df, hud) # fill remaining nulls from ZIP
df = attach_period_id(df)                # period keys
```

Event sources can now wire all three transforms in their `to_cleaned()`. The wire-up itself remains out of scope — each event source gets its own brainstorm → spec → plan cycle when its API key arrives.
