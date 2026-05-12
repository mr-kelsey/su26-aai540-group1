# `period_id` / `period_month` Transform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `attach_period_id` Polars transform that derives quarterly (`period_id`, format `'YYYYQQ'`) + monthly (`period_month`, format `'YYYY-MM'`) keys from `event_date`, plus the additive migration that adds `events.period_month`.

**Architecture:** Single Polars expression chain in a new `src/eia/transforms/temporal.py` module — no out-of-process roundtrips, no caching, no logging (date derivation has no analogous "miss" to surface). New SQL migration `004_events_period_month.sql` is purely additive (`ALTER TABLE events ADD COLUMN ...`). Public function re-exported from `eia.transforms` alongside `attach_county_fips`.

**Tech Stack:** Polars 1.x, pytest 8.x, DuckDB 1.x (for migration application verification only — no new warehouse code). Same `uv`-managed environment as the rest of the project.

---

## File Structure

**Create:**
- `migrations/004_events_period_month.sql` — `ALTER TABLE events ADD COLUMN period_month VARCHAR;` plus index
- `src/eia/transforms/temporal.py` — module with `attach_period_id` (one public function, no helpers)
- `tests/transforms/test_temporal.py` — unit tests (six tests covering quarter boundaries, mid-quarter, year boundary, null propagation, custom column names, realistic-shape passthrough; plus a public-import smoke test in Task 3)

**Modify:**
- `src/eia/transforms/__init__.py` — extend to re-export `attach_period_id` alongside the existing `attach_county_fips`

---

## Task 1: Schema migration — add `events.period_month`

A purely additive `ALTER TABLE` migration that adds `period_month VARCHAR` to the events table and creates an index on it. The DuckDB warehouse migration runner ([duckdb_impl.py:76](../../src/eia/warehouse/duckdb_impl.py:76)) applies SQL files in lexicographic order and tracks them in a `_migrations` table; numbering 004 puts this after the existing 001/002/003.

**Files:**
- Create: `migrations/004_events_period_month.sql`

- [ ] **Step 1: Write the migration**

Create `migrations/004_events_period_month.sql` with exactly this content:

```sql
-- 004_events_period_month.sql
-- Adds the period_month column to events for monthly joins (state DOR, county-month panel).
-- Quarterly joins continue to use the existing period_id column.

ALTER TABLE events ADD COLUMN period_month VARCHAR;

CREATE INDEX IF NOT EXISTS ix_events_period_month ON events (period_month);
```

- [ ] **Step 2: Verify the migration applies cleanly against a fresh temp warehouse**

```bash
TMP_DB=/tmp/eia_period_id_check.duckdb
rm -f "$TMP_DB"
EIA_DUCKDB_PATH="$TMP_DB" uv run eia warehouse init
EIA_DUCKDB_PATH="$TMP_DB" uv run python -c "
import duckdb
con = duckdb.connect('$TMP_DB', read_only=True)
cols = [r[0] for r in con.execute(\"SELECT name FROM pragma_table_info('events')\").fetchall()]
assert 'period_month' in cols, f'period_month not in events columns: {cols}'
applied = [r[0] for r in con.execute('SELECT name FROM _migrations ORDER BY name').fetchall()]
assert '004_events_period_month.sql' in applied, f'004 not applied: {applied}'
print('OK: events.period_month present, 004 in _migrations')
"
rm -f "$TMP_DB"
```

Expected: prints `OK: events.period_month present, 004 in _migrations` and the script exits 0.

If the migration errors, read the message and fix the SQL syntax — DuckDB's error output names the offending token. Do not skip this step.

- [ ] **Step 3: Commit**

```bash
git add migrations/004_events_period_month.sql
git commit -m "feat(migrations): add events.period_month column for monthly joins"
```

The Bash tool will append the standard `Co-Authored-By` footer automatically.

---

## Task 2: `attach_period_id` function (TDD)

Write all six unit tests first, confirm they fail (the module doesn't exist yet), then create `temporal.py` with the function. The function is a single Polars expression chain — no helpers, no logging.

**Files:**
- Create: `src/eia/transforms/temporal.py`
- Create: `tests/transforms/test_temporal.py`

- [ ] **Step 1: Write the failing tests in `tests/transforms/test_temporal.py`**

```python
"""Tests for transforms.temporal."""

from __future__ import annotations

from datetime import date

import polars as pl


def test_quarter_boundaries() -> None:
    """First day of each calendar quarter maps to the right Q."""
    from eia.transforms.temporal import attach_period_id

    df = pl.DataFrame(
        {
            "event_date": [
                date(2023, 1, 1),
                date(2023, 4, 1),
                date(2023, 7, 1),
                date(2023, 10, 1),
            ],
        }
    )
    out = attach_period_id(df)
    assert out["period_id"].to_list() == ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
    assert out["period_month"].to_list() == ["2023-01", "2023-04", "2023-07", "2023-10"]


def test_mid_quarter_dates() -> None:
    """Dates inside a quarter still map to that quarter."""
    from eia.transforms.temporal import attach_period_id

    df = pl.DataFrame(
        {"event_date": [date(2023, 8, 15), date(2023, 12, 31)]}
    )
    out = attach_period_id(df)
    assert out["period_id"].to_list() == ["2023Q3", "2023Q4"]
    assert out["period_month"].to_list() == ["2023-08", "2023-12"]


def test_year_boundary() -> None:
    """Jan 1 of new year yields the new year, not the old one."""
    from eia.transforms.temporal import attach_period_id

    df = pl.DataFrame({"event_date": [date(2024, 1, 1)]})
    out = attach_period_id(df)
    assert out["period_id"].to_list() == ["2024Q1"]
    assert out["period_month"].to_list() == ["2024-01"]


def test_null_date_propagates_to_both_columns() -> None:
    """A null event_date produces null in both output columns."""
    from eia.transforms.temporal import attach_period_id

    df = pl.DataFrame(
        {"event_date": [date(2023, 7, 1), None, date(2023, 8, 15)]},
        schema={"event_date": pl.Date},
    )
    out = attach_period_id(df)
    assert out["period_id"].to_list() == ["2023Q3", None, "2023Q3"]
    assert out["period_month"].to_list() == ["2023-07", None, "2023-08"]


def test_custom_column_names() -> None:
    """Caller can override the input and output column names."""
    from eia.transforms.temporal import attach_period_id

    df = pl.DataFrame({"the_date": [date(2023, 7, 15)]})
    out = attach_period_id(df, date_col="the_date", quarter_col="Q", month_col="M")
    assert out["Q"].to_list() == ["2023Q3"]
    assert out["M"].to_list() == ["2023-07"]
    # Defaults must NOT appear when custom names are used.
    assert "period_id" not in out.columns
    assert "period_month" not in out.columns


def test_passes_other_columns_through() -> None:
    """Realistic events-shape input — all original columns intact."""
    from eia.transforms.temporal import attach_period_id

    df = pl.DataFrame(
        {
            "event_id": ["tm_1", "tm_2"],
            "event_name": ["Show A", "Show B"],
            "venue_lat": [32.7, 40.7],
            "venue_lon": [-117.2, -74.0],
            "event_date": [date(2023, 7, 15), date(2023, 8, 20)],
        }
    )
    out = attach_period_id(df)
    assert out.columns == [*df.columns, "period_id", "period_month"]
    for col in df.columns:
        assert out[col].to_list() == df[col].to_list()
    assert out["period_id"].to_list() == ["2023Q3", "2023Q3"]
    assert out["period_month"].to_list() == ["2023-07", "2023-08"]
```

- [ ] **Step 2: Run the tests to confirm they all fail**

```bash
uv run pytest tests/transforms/test_temporal.py -v --no-cov
```

Expected: 6 collection-time errors / failures with `ImportError: cannot import name 'attach_period_id' from 'eia.transforms.temporal'` (or similar — the module doesn't exist).

- [ ] **Step 3: Create `src/eia/transforms/temporal.py`**

```python
"""event_date -> (period_id, period_month) temporal-key transform.

Consumed by event sources' to_cleaned() methods after attach_county_fips to
attach quarter and month period keys to event rows. Output formats match
the federal-side aggregators already in use:

    period_id     'YYYYQQ', e.g. '2023Q3'   -- joins bls_qcew, dim_time
    period_month  'YYYY-MM', e.g. '2023-08' -- joins state DOR, county-month panel
"""

from __future__ import annotations

import polars as pl


def attach_period_id(
    df: pl.DataFrame,
    *,
    date_col: str = "event_date",
    quarter_col: str = "period_id",
    month_col: str = "period_month",
) -> pl.DataFrame:
    """Attach quarterly and monthly period keys derived from `date_col`.

    Returns the input frame with two columns appended (Utf8, both nullable):
    `quarter_col` in 'YYYYQQ' format, `month_col` in 'YYYY-MM' format. A null
    `date_col` value produces null in both output columns. Original columns
    and their order are preserved.
    """
    return df.with_columns(
        pl.format(
            "{}Q{}",
            pl.col(date_col).dt.year(),
            pl.col(date_col).dt.quarter(),
        ).alias(quarter_col),
        pl.col(date_col).dt.strftime("%Y-%m").alias(month_col),
    )
```

- [ ] **Step 4: Run the tests to confirm they pass**

```bash
uv run pytest tests/transforms/test_temporal.py -v --no-cov
```

Expected: 6 passed.

If `test_null_date_propagates_to_both_columns` fails because Polars `.dt.strftime(...)` does NOT propagate null on this version (it returns the literal string `"None"` or similar instead of null), wrap the strftime call in an explicit `pl.when(...).then(...).otherwise(...)` guard:

```python
pl.col(date_col).dt.strftime("%Y-%m").alias(month_col),
```

becomes:

```python
pl.when(pl.col(date_col).is_not_null())
.then(pl.col(date_col).dt.strftime("%Y-%m"))
.otherwise(None)
.alias(month_col),
```

(Same fix is unlikely to be needed for `pl.format`, which generally propagates null when any operand is null — but if it does fail for the same reason, apply the same guard pattern.) Re-run the tests.

- [ ] **Step 5: Confirm no regression in existing test files**

```bash
uv run pytest tests/transforms/test_geo.py tests/sources/test_tiger.py tests/test_warehouse.py -v --no-cov
```

Expected: 15 passed (8 geo + 2 tiger + 5 warehouse).

- [ ] **Step 6: Commit**

```bash
git add tests/transforms/test_temporal.py src/eia/transforms/temporal.py
git commit -m "feat(transforms): attach_period_id derives quarterly + monthly keys"
```

---

## Task 3: Public export from `transforms/__init__.py`

Make `from eia.transforms import attach_period_id` work — that's the form callers in event-source modules will use.

**Files:**
- Modify: `src/eia/transforms/__init__.py`
- Modify: `tests/transforms/test_temporal.py`

- [ ] **Step 1: Append a smoke test to `tests/transforms/test_temporal.py`**

```python
def test_public_import() -> None:
    """The canonical caller form — `from eia.transforms import attach_period_id`."""
    from eia.transforms import attach_period_id as imported

    df = pl.DataFrame({"event_date": [date(2023, 7, 15)]})
    out = imported(df)
    assert out["period_id"].to_list() == ["2023Q3"]
    assert out["period_month"].to_list() == ["2023-07"]
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
uv run pytest tests/transforms/test_temporal.py::test_public_import -v --no-cov
```

Expected: FAIL — `ImportError: cannot import name 'attach_period_id' from 'eia.transforms'`.

- [ ] **Step 3: Update `src/eia/transforms/__init__.py`**

Read the current file first to preserve its docstring, then replace its contents:

```python
"""Cleaning and feature-engineering transforms applied at to_cleaned() time."""

from eia.transforms.geo import attach_county_fips
from eia.transforms.temporal import attach_period_id

__all__ = ["attach_county_fips", "attach_period_id"]
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
uv run pytest tests/transforms/test_temporal.py::test_public_import -v --no-cov
```

Expected: PASS.

Then run all transform tests to confirm no regression in the geo public-import test (which exercises the same `__init__.py`):

```bash
uv run pytest tests/transforms/ -v --no-cov
```

Expected: 15 passed (8 geo + 7 temporal).

- [ ] **Step 5: Commit**

```bash
git add src/eia/transforms/__init__.py tests/transforms/test_temporal.py
git commit -m "feat(transforms): export attach_period_id at package level"
```

---

## Task 4: Quality sweep — ruff, mypy, full pytest

Run the project's quality gates and surgically fix anything in scope. Out-of-scope pre-existing errors in other source files should be left alone (per CLAUDE.md and the prior cycle's experience).

**Files:**
- Whatever ruff / mypy flag inside the touched files

- [ ] **Step 1: Lint scoped to this branch's changes**

```bash
uv run ruff check src/eia/transforms/ migrations/004_events_period_month.sql tests/transforms/test_temporal.py 2>&1 || true
uv run ruff check src/eia/transforms/ tests/transforms/test_temporal.py
```

(Ruff doesn't lint SQL — the first command is just a spot check; the second is the real one.)

Expected: clean. If ruff reports an issue in any of these files, fix it in place. Common fixes are unused imports or missing `from __future__ import annotations`. Do not silence with `# noqa` unless the rule is genuinely wrong — in which case explain in your report.

- [ ] **Step 2: Type-check scoped to this branch's changes**

```bash
uv run mypy src/eia/transforms/temporal.py src/eia/transforms/__init__.py
```

Expected: `Success: no issues found in 2 source files`.

If mypy reports an issue, fix the code (not the test). For unavoidable third-party type-system gaps, use a targeted `# type: ignore[<error-code>]` with a brief justification.

- [ ] **Step 3: Run the full test suite**

```bash
make test
```

Expected: 22 passed (5 warehouse + 2 tiger + 8 geo + 7 temporal). Coverage on `src/eia/transforms/temporal.py` should be at or near 100%.

- [ ] **Step 4: Commit fixes if Steps 1-3 produced any**

If you made code changes during this sweep, commit them as a single follow-up:

```bash
git add -p   # selectively stage what you changed
git commit -m "chore: lint and type fixes for period_id transform"
```

If Steps 1-3 were all clean and you made no changes, do NOT commit. Just report DONE with the commit count from Tasks 1-3.

---

## Done

After Task 4, the period_id transform is wired into the package and ready to be called from event-source `to_cleaned()` methods alongside `attach_county_fips`. The integration line each event source will eventually add is:

```python
from eia.transforms import attach_county_fips, attach_period_id

df = attach_county_fips(df)
df = attach_period_id(df)
```

That wire-up is **out of scope for this plan** — each event source (Ticketmaster, RunSignUp, Setlist.fm) will get its own brainstorm → spec → plan cycle when its API key arrives and Phase 1 ingestion begins.
