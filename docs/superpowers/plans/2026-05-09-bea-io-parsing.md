# BEA Use+Make XLSX Parsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the manifest-stub `to_cleaned()` in `src/eia/sources/bea_io.py` with real XLSX parsing of the Summary-level Use and Make tables (1997–2023, 71-industry), landing long-form into the existing `bea_io_use` and `bea_io_make` warehouse tables.

**Architecture:** Cross-reference strategy — parse Make first to extract the canonical industries/commodities sets, then filter Use's columns/rows against those sets at parse time (final-demand columns and value-added rows naturally drop out). One source pull processes all 27 year sheets for both files. The `BEAIO.load()` override registers both parquets and drops the legacy `bea_io_manifest` table.

**Tech Stack:** Polars 1.x, `fastexcel` (already added), `openpyxl` (added in Task 1 for test fixture generation), pytest 8.x.

---

## File Structure

**Modify:**
- `pyproject.toml` — add `openpyxl` to `[project.optional-dependencies] dev`
- `src/eia/sources/bea_io.py` — replace `to_cleaned()` and `load()`, add private parsing helpers and class constants for the target XLSX filenames

**Create:**
- `tests/sources/test_bea_io.py` — 8 tests + a synthetic-XLSX fixture builder

---

## Task 1: Add openpyxl dev dependency

Tests need a way to construct synthetic XLSX files. `fastexcel` (used at runtime for reading) doesn't write, so we add `openpyxl` to dev deps.

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, find the `[project.optional-dependencies] dev = [...]` block (around line 35). Add `"openpyxl>=3.1.0",` to the list. After the change the block should read:

```toml
dev = [
    "pytest>=8.3.0",
    "pytest-cov>=5.0.0",
    "ruff>=0.7.0",
    "mypy>=1.13.0",
    "openpyxl>=3.1.0",
]
```

- [ ] **Step 2: Sync the env**

```bash
uv sync --extra dev 2>&1 | tail -3
```

Expected: `openpyxl` and its transitive deps installed.

- [ ] **Step 3: Smoke test the install**

```bash
uv run python -c "import openpyxl; print(openpyxl.__version__)"
```

Expected: prints a version `3.1.x` or higher.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock 2>/dev/null || git add pyproject.toml
git commit -m "chore: add openpyxl dev dep for BEA test fixture XLSX generation"
```

---

## Task 2: TDD `_industries_and_commodities_from_make` + fixture helper

This task introduces the synthetic-XLSX fixture builder (`_build_synthetic_bea_zip`) used by every subsequent test, plus the first private helper that extracts the canonical industries/commodities sets from Make.

**Files:**
- Create: `tests/sources/test_bea_io.py`
- Modify: `src/eia/sources/bea_io.py`

- [ ] **Step 1: Write the failing test**

Create `tests/sources/test_bea_io.py` with this content:

```python
"""Tests for the BEA Use+Make XLSX parser."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import openpyxl
import polars as pl
import pytest
from openpyxl.workbook import Workbook


# ---- fixture helper ----


USE_MEMBER = "IOUse_After_Redefinitions_PRO_1997-2023_Summary.xlsx"
MAKE_MEMBER = "IOMake_After_Redefinitions_PRO_1997-2023_Summary.xlsx"


def _populate_sheet(
    ws: Any,
    *,
    title: str,
    year: int,
    col_codes: list[str],
    row_codes: list[str],
    cell_value: float,
    blank_cell: tuple[str, str] | None = None,
) -> None:
    """Write a BEA-style sheet (rows 1-6 boilerplate; rows 7+ data)."""
    ws.cell(row=1, column=1, value=title)
    ws.cell(row=2, column=1, value="(Millions of dollars)")
    ws.cell(row=3, column=1, value="Bureau of Economic Analysis")
    ws.cell(row=4, column=1, value=str(year))
    # Row 5: column-header codes in cols 3+
    ws.cell(row=5, column=2, value="Header")
    for j, code in enumerate(col_codes):
        ws.cell(row=5, column=3 + j, value=code)
    # Row 6: column-header names
    ws.cell(row=6, column=1, value="IOCode")
    ws.cell(row=6, column=2, value="Name")
    for j, code in enumerate(col_codes):
        ws.cell(row=6, column=3 + j, value=f"{code} name")
    # Rows 7+: data
    for i, row_code in enumerate(row_codes):
        r = 7 + i
        ws.cell(row=r, column=1, value=row_code)
        ws.cell(row=r, column=2, value=f"{row_code} name")
        for j, col_code in enumerate(col_codes):
            if blank_cell == (row_code, col_code):
                continue  # leave cell empty
            ws.cell(row=r, column=3 + j, value=cell_value)


def _build_synthetic_bea_zip(
    out_path: Path,
    *,
    years: list[int] | None = None,
    industries: list[str] | None = None,
    commodities: list[str] | None = None,
    use_extra_final_demand: list[str] | None = None,
    use_extra_value_added: list[str] | None = None,
    use_value: float = 1.0,
    make_value: float = 2.0,
    blank_use_cell: tuple[str, str] | None = None,
) -> Path:
    """Build a synthetic AllTablesIO-shaped zip at `out_path`.

    Use sheet rows = commodities + use_extra_value_added (so VA rows can
    test the row-filter); columns = industries + use_extra_final_demand
    (so FD columns can test the column-filter).

    Make sheet rows = industries; columns = commodities (no extras —
    Make is structurally clean).
    """
    years = years or [2022, 2023]
    industries = industries or ["I1", "I2", "I3"]
    commodities = commodities or ["C1", "C2", "C3", "C4"]
    use_extra_final_demand = use_extra_final_demand or []
    use_extra_value_added = use_extra_value_added or []

    use_industry_codes = list(industries) + list(use_extra_final_demand)
    use_commodity_codes = list(commodities) + list(use_extra_value_added)

    use_wb = Workbook()
    use_wb.remove(use_wb.active)
    make_wb = Workbook()
    make_wb.remove(make_wb.active)

    for year in years:
        _populate_sheet(
            use_wb.create_sheet(str(year)),
            title="The Use of Commodities by Industries",
            year=year,
            col_codes=use_industry_codes,
            row_codes=use_commodity_codes,
            cell_value=use_value,
            blank_cell=blank_use_cell,
        )
        _populate_sheet(
            make_wb.create_sheet(str(year)),
            title="The Make of Commodities by Industries",
            year=year,
            col_codes=commodities,
            row_codes=industries,
            cell_value=make_value,
        )

    use_buf = io.BytesIO()
    use_wb.save(use_buf)
    make_buf = io.BytesIO()
    make_wb.save(make_buf)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w") as zf:
        zf.writestr(USE_MEMBER, use_buf.getvalue())
        zf.writestr(MAKE_MEMBER, make_buf.getvalue())
    return out_path


# ---- tests ----


def test_industries_and_commodities_from_make(tmp_path: Path) -> None:
    """Make's row codes are the industries; its column codes are the commodities."""
    from eia.sources.bea_io import _extract_make_to_temp, _industries_and_commodities_from_make

    zip_path = _build_synthetic_bea_zip(
        tmp_path / "AllTablesIO.zip",
        years=[2022, 2023],
        industries=["I1", "I2", "I3"],
        commodities=["C1", "C2", "C3", "C4"],
    )
    make_path = _extract_make_to_temp(zip_path, tmp_path / "_make.xlsx")
    industries, commodities = _industries_and_commodities_from_make(make_path)

    assert industries == ["I1", "I2", "I3"]
    assert commodities == ["C1", "C2", "C3", "C4"]
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
uv run pytest tests/sources/test_bea_io.py::test_industries_and_commodities_from_make -v --no-cov
```

Expected: FAIL with `ImportError: cannot import name '_extract_make_to_temp'` (or similar — the helpers don't exist yet).

- [ ] **Step 3: Implement the helpers in `src/eia/sources/bea_io.py`**

Open `src/eia/sources/bea_io.py`. Add the imports and helpers below. Leave the existing `BEAIO` class for now (it will be reworked in Task 5).

At the top of the file, replace the existing imports block with:

```python
from __future__ import annotations

import logging
import tempfile
import zipfile
from pathlib import Path

import polars as pl
import yaml

from eia.clients import RateLimitedClient
from eia.sources.base import Source
from eia.sources.registry import register

logger = logging.getLogger(__name__)

USE_MEMBER = "IOUse_After_Redefinitions_PRO_1997-2023_Summary.xlsx"
MAKE_MEMBER = "IOMake_After_Redefinitions_PRO_1997-2023_Summary.xlsx"
```

Then APPEND these helpers at the end of the file (after the `register(...)` line):

```python
def _extract_member_to_temp(zip_path: Path, member: str, out_path: Path) -> Path:
    """Extract a single zip member to `out_path` so fastexcel can read it from a path."""
    with zipfile.ZipFile(zip_path) as zf:
        out_path.write_bytes(zf.read(member))
    return out_path


def _extract_make_to_temp(zip_path: Path, out_path: Path) -> Path:
    """Convenience wrapper for the Make XLSX."""
    return _extract_member_to_temp(zip_path, MAKE_MEMBER, out_path)


def _extract_use_to_temp(zip_path: Path, out_path: Path) -> Path:
    """Convenience wrapper for the Use XLSX."""
    return _extract_member_to_temp(zip_path, USE_MEMBER, out_path)


def _year_sheets(xlsx_path: Path) -> list[int]:
    """Sheet names that look like 4-digit years, sorted ascending."""
    import fastexcel

    reader = fastexcel.read_excel(str(xlsx_path))
    years: list[int] = []
    for name in reader.sheet_names:
        if name.isdigit() and len(name) == 4:
            years.append(int(name))
    return sorted(years)


def _industries_and_commodities_from_make(
    make_xlsx_path: Path,
) -> tuple[list[str], list[str]]:
    """Read all year sheets in Make; union the row IOCodes (industries) and the
    column IOCodes (commodities). Return sorted lists for deterministic output.
    """
    industries: set[str] = set()
    commodities: set[str] = set()
    for year in _year_sheets(make_xlsx_path):
        df = pl.read_excel(make_xlsx_path, sheet_name=str(year), has_header=False)
        # Row 5 (0-indexed 4): cols 3+ are commodity codes (Make's column header).
        header_row = df.row(4)
        for v in header_row[2:]:
            if isinstance(v, str) and v.strip():
                commodities.add(v.strip())
        # Rows 7+ (0-indexed 6+): col 1 is the industry IOCode for that row.
        for ridx in range(6, df.height):
            v = df.row(ridx)[0]
            if isinstance(v, str) and v.strip():
                industries.add(v.strip())
    return sorted(industries), sorted(commodities)
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
uv run pytest tests/sources/test_bea_io.py::test_industries_and_commodities_from_make -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/sources/test_bea_io.py src/eia/sources/bea_io.py
git commit -m "feat(bea): industries+commodities extraction from Make + synthetic-XLSX fixture"
```

---

## Task 3: TDD `_parse_make_year`

Make is structurally clean — no filtering needed. Iterate row × col cells, output long-form rows.

**Files:**
- Modify: `src/eia/sources/bea_io.py`
- Modify: `tests/sources/test_bea_io.py`

- [ ] **Step 1: Append the failing test**

Add to the end of `tests/sources/test_bea_io.py`:

```python
def test_parse_make_year(tmp_path: Path) -> None:
    """Make is rows=industries × cols=commodities; output is long-form."""
    from datetime import datetime, timezone

    from eia.sources.bea_io import _extract_make_to_temp, _parse_make_year

    zip_path = _build_synthetic_bea_zip(
        tmp_path / "AllTablesIO.zip",
        years=[2023],
        industries=["I1", "I2", "I3"],
        commodities=["C1", "C2", "C3", "C4"],
        make_value=7.0,
    )
    make_path = _extract_make_to_temp(zip_path, tmp_path / "_make.xlsx")
    fetched_at = datetime(2026, 5, 9, tzinfo=timezone.utc)

    df = _parse_make_year(make_path, year=2023, fetched_at=fetched_at)

    # 3 industries × 4 commodities = 12 rows.
    assert df.height == 12
    assert df.columns == [
        "table_year",
        "industry_code",
        "commodity_code",
        "value_millions",
        "fetched_at",
    ]
    assert df["table_year"].unique().to_list() == [2023]
    assert sorted(df["industry_code"].unique().to_list()) == ["I1", "I2", "I3"]
    assert sorted(df["commodity_code"].unique().to_list()) == ["C1", "C2", "C3", "C4"]
    # All values are the synthetic 7.0 we passed.
    assert df["value_millions"].to_list() == [7.0] * 12
```

- [ ] **Step 2: Run, confirm fail**

```bash
uv run pytest tests/sources/test_bea_io.py::test_parse_make_year -v --no-cov
```

Expected: FAIL with `ImportError: cannot import name '_parse_make_year'`.

- [ ] **Step 3: Implement `_parse_make_year` in `src/eia/sources/bea_io.py`**

Append after the existing `_industries_and_commodities_from_make` helper:

```python
def _parse_make_year(
    make_xlsx_path: Path,
    *,
    year: int,
    fetched_at: object,
) -> pl.DataFrame:
    """Parse one year sheet of the Make XLSX into long-form rows.

    Make's rows are industries; its columns are commodities. There are no
    final-demand or value-added rows/columns to filter, so we iterate the
    full data block and emit long-form (industry, commodity, value) rows.
    """
    df = pl.read_excel(make_xlsx_path, sheet_name=str(year), has_header=False)
    header_row = df.row(4)
    commodity_codes = [
        (v.strip() if isinstance(v, str) else None) for v in header_row[2:]
    ]
    rows: list[dict[str, object]] = []
    for ridx in range(6, df.height):
        row = df.row(ridx)
        ind_raw = row[0]
        if not isinstance(ind_raw, str) or not ind_raw.strip():
            continue
        industry_code = ind_raw.strip()
        for cidx, comm_code in enumerate(commodity_codes):
            if not comm_code:
                continue
            cell = row[2 + cidx]
            value: float | None
            if cell is None or (isinstance(cell, str) and cell.strip() in ("", "...")):
                value = None
            else:
                value = float(cell)
            rows.append(
                {
                    "table_year": year,
                    "industry_code": industry_code,
                    "commodity_code": comm_code,
                    "value_millions": value,
                    "fetched_at": fetched_at,
                }
            )
    return pl.DataFrame(
        rows,
        schema={
            "table_year": pl.Int16,
            "industry_code": pl.Utf8,
            "commodity_code": pl.Utf8,
            "value_millions": pl.Float64,
            "fetched_at": pl.Datetime,
        },
    )
```

- [ ] **Step 4: Run, confirm pass**

```bash
uv run pytest tests/sources/test_bea_io.py::test_parse_make_year -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/sources/test_bea_io.py src/eia/sources/bea_io.py
git commit -m "feat(bea): _parse_make_year — long-form rows from one year sheet"
```

---

## Task 4: TDD `_parse_use_year` (cross-reference filtering)

Use is rows=commodities×cols=industries. Filter both sides against the canonical sets so final-demand columns and value-added rows are dropped. Anomaly codes log a WARNING and are excluded.

**Files:**
- Modify: `src/eia/sources/bea_io.py`
- Modify: `tests/sources/test_bea_io.py`

- [ ] **Step 1: Append three failing tests**

Add to the end of `tests/sources/test_bea_io.py`:

```python
def test_parse_use_year_filters_final_demand_columns(tmp_path: Path) -> None:
    """A column code not in the industries set (final-demand) is dropped from output."""
    from datetime import datetime, timezone

    from eia.sources.bea_io import _extract_use_to_temp, _parse_use_year

    zip_path = _build_synthetic_bea_zip(
        tmp_path / "AllTablesIO.zip",
        years=[2023],
        industries=["I1", "I2", "I3"],
        commodities=["C1", "C2", "C3", "C4"],
        use_extra_final_demand=["F010"],   # PCE — should be filtered out
    )
    use_path = _extract_use_to_temp(zip_path, tmp_path / "_use.xlsx")
    fetched_at = datetime(2026, 5, 9, tzinfo=timezone.utc)

    df = _parse_use_year(
        use_path,
        year=2023,
        industries={"I1", "I2", "I3"},
        commodities={"C1", "C2", "C3", "C4"},
        fetched_at=fetched_at,
    )

    # 4 commodities × 3 industries = 12 rows; F010 column is dropped.
    assert df.height == 12
    assert "F010" not in df["industry_code"].unique().to_list()


def test_parse_use_year_filters_value_added_rows(tmp_path: Path) -> None:
    """A row code not in the commodities set (value-added) is dropped from output."""
    from datetime import datetime, timezone

    from eia.sources.bea_io import _extract_use_to_temp, _parse_use_year

    zip_path = _build_synthetic_bea_zip(
        tmp_path / "AllTablesIO.zip",
        years=[2023],
        industries=["I1", "I2", "I3"],
        commodities=["C1", "C2", "C3", "C4"],
        use_extra_value_added=["V001"],   # compensation of employees — filtered out
    )
    use_path = _extract_use_to_temp(zip_path, tmp_path / "_use.xlsx")
    fetched_at = datetime(2026, 5, 9, tzinfo=timezone.utc)

    df = _parse_use_year(
        use_path,
        year=2023,
        industries={"I1", "I2", "I3"},
        commodities={"C1", "C2", "C3", "C4"},
        fetched_at=fetched_at,
    )

    # 4 commodities × 3 industries = 12 rows; V001 row is dropped.
    assert df.height == 12
    assert "V001" not in df["commodity_code"].unique().to_list()


def test_parse_use_year_blank_cell_becomes_null(tmp_path: Path) -> None:
    """A blank Use cell maps to a null value_millions in the long-form output."""
    from datetime import datetime, timezone

    from eia.sources.bea_io import _extract_use_to_temp, _parse_use_year

    zip_path = _build_synthetic_bea_zip(
        tmp_path / "AllTablesIO.zip",
        years=[2023],
        industries=["I1", "I2", "I3"],
        commodities=["C1", "C2", "C3", "C4"],
        blank_use_cell=("C2", "I2"),
    )
    use_path = _extract_use_to_temp(zip_path, tmp_path / "_use.xlsx")
    fetched_at = datetime(2026, 5, 9, tzinfo=timezone.utc)

    df = _parse_use_year(
        use_path,
        year=2023,
        industries={"I1", "I2", "I3"},
        commodities={"C1", "C2", "C3", "C4"},
        fetched_at=fetched_at,
    )

    target = df.filter(
        (pl.col("commodity_code") == "C2") & (pl.col("industry_code") == "I2")
    )
    assert target.height == 1
    assert target["value_millions"][0] is None
```

- [ ] **Step 2: Run, confirm 3 fail**

```bash
uv run pytest tests/sources/test_bea_io.py -v --no-cov -k "parse_use_year"
```

Expected: 3 FAIL with `ImportError: cannot import name '_parse_use_year'`.

- [ ] **Step 3: Implement `_parse_use_year` in `src/eia/sources/bea_io.py`**

Append after `_parse_make_year`:

```python
def _parse_use_year(
    use_xlsx_path: Path,
    *,
    year: int,
    industries: set[str],
    commodities: set[str],
    fetched_at: object,
) -> pl.DataFrame:
    """Parse one year sheet of the Use XLSX into long-form rows, with cross-reference filtering.

    Use's rows are commodities (+ value-added footer rows that we drop) and
    its columns are industries (+ final-demand columns that we drop). The
    `industries` and `commodities` sets come from `_industries_and_commodities_from_make`
    and define which codes we keep. Codes that aren't in those sets are
    dropped and a WARNING is logged.
    """
    df = pl.read_excel(use_xlsx_path, sheet_name=str(year), has_header=False)
    header_row = df.row(4)
    industry_codes_raw = [
        (v.strip() if isinstance(v, str) else None) for v in header_row[2:]
    ]
    keep_col_indices: list[int] = []
    for cidx, code in enumerate(industry_codes_raw):
        if not code:
            continue
        if code in industries:
            keep_col_indices.append(cidx)
        else:
            logger.warning(
                "Use column code %r (year %d) not in industries set; dropping",
                code,
                year,
            )

    rows: list[dict[str, object]] = []
    for ridx in range(6, df.height):
        row = df.row(ridx)
        comm_raw = row[0]
        if not isinstance(comm_raw, str) or not comm_raw.strip():
            continue
        commodity_code = comm_raw.strip()
        if commodity_code not in commodities:
            logger.warning(
                "Use row code %r (year %d) not in commodities set; dropping",
                commodity_code,
                year,
            )
            continue
        for cidx in keep_col_indices:
            industry_code = industry_codes_raw[cidx]
            cell = row[2 + cidx]
            value: float | None
            if cell is None or (isinstance(cell, str) and cell.strip() in ("", "...")):
                value = None
            else:
                value = float(cell)
            rows.append(
                {
                    "table_year": year,
                    "industry_code": industry_code,
                    "commodity_code": commodity_code,
                    "value_millions": value,
                    "fetched_at": fetched_at,
                }
            )
    return pl.DataFrame(
        rows,
        schema={
            "table_year": pl.Int16,
            "industry_code": pl.Utf8,
            "commodity_code": pl.Utf8,
            "value_millions": pl.Float64,
            "fetched_at": pl.Datetime,
        },
    )
```

- [ ] **Step 4: Run, confirm pass**

```bash
uv run pytest tests/sources/test_bea_io.py -v --no-cov -k "parse_use_year"
```

Expected: 3 PASS.

- [ ] **Step 5: Run full test_bea_io.py to confirm no regression**

```bash
uv run pytest tests/sources/test_bea_io.py -v --no-cov
```

Expected: 5 passed (1 industries-and-commodities + 1 parse_make + 3 parse_use).

- [ ] **Step 6: Commit**

```bash
git add tests/sources/test_bea_io.py src/eia/sources/bea_io.py
git commit -m "feat(bea): _parse_use_year with cross-reference filtering of FD cols and VA rows"
```

---

## Task 5: TDD end-to-end `to_cleaned()` + `load()`

Replace the existing manifest-stub `to_cleaned()` and `load()` with versions that produce both Use and Make parquets and load them into the warehouse, dropping the legacy `bea_io_manifest` table.

**Files:**
- Modify: `src/eia/sources/bea_io.py`
- Modify: `tests/sources/test_bea_io.py`

- [ ] **Step 1: Append two end-to-end tests**

Add to `tests/sources/test_bea_io.py`:

```python
def test_to_cleaned_writes_use_and_make_parquets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """to_cleaned writes both use_summary.parquet and make_summary.parquet, multi-year stacked."""
    from eia.sources.bea_io import BEAIO

    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    zip_path = _build_synthetic_bea_zip(
        tmp_path / "AllTablesIO.zip",
        years=[2022, 2023],
        industries=["I1", "I2", "I3"],
        commodities=["C1", "C2", "C3", "C4"],
        use_extra_final_demand=["F010"],   # Use also has FD col
        use_extra_value_added=["V001"],    # Use also has VA row
    )

    src = BEAIO(year=2023)
    use_path = src.to_cleaned(zip_path)

    assert use_path.exists()
    assert use_path.name == "use_summary.parquet"
    make_path = use_path.parent / "make_summary.parquet"
    assert make_path.exists()

    use_df = pl.read_parquet(use_path)
    # 2 years × 4 commodities × 3 industries = 24 rows (FD col + VA row dropped).
    assert use_df.height == 24
    assert sorted(use_df["table_year"].unique().to_list()) == [2022, 2023]
    assert sorted(use_df["industry_code"].unique().to_list()) == ["I1", "I2", "I3"]
    assert sorted(use_df["commodity_code"].unique().to_list()) == ["C1", "C2", "C3", "C4"]

    make_df = pl.read_parquet(make_path)
    # 2 years × 3 industries × 4 commodities = 24 rows.
    assert make_df.height == 24


def test_load_drops_manifest_and_populates_use_and_make(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load() registers bea_io_use and bea_io_make and drops bea_io_manifest."""
    from eia.sources.bea_io import BEAIO
    from eia.warehouse.duckdb_impl import DuckDBWarehouse

    monkeypatch.setattr("eia.config.settings.eia_data_root", tmp_path)
    zip_path = _build_synthetic_bea_zip(
        tmp_path / "AllTablesIO.zip",
        years=[2023],
    )

    # Build a tmp warehouse that already has the bea_io_manifest stub table to
    # prove load() drops it.
    wh = DuckDBWarehouse(path=tmp_path / "test.duckdb")
    wh.execute_sql(
        "CREATE TABLE bea_io_manifest (filename VARCHAR, table_year SMALLINT, fetched_at TIMESTAMP)"
    )
    wh.execute_sql(
        "CREATE TABLE bea_io_use (table_year SMALLINT, industry_code VARCHAR, commodity_code VARCHAR, "
        "value_millions DOUBLE, fetched_at TIMESTAMP)"
    )
    wh.execute_sql(
        "CREATE TABLE bea_io_make (table_year SMALLINT, industry_code VARCHAR, commodity_code VARCHAR, "
        "value_millions DOUBLE, fetched_at TIMESTAMP)"
    )
    assert "bea_io_manifest" in wh.list_tables()

    src = BEAIO(year=2023)
    use_path = src.to_cleaned(zip_path)
    src.load(use_path, wh)

    tables = wh.list_tables()
    assert "bea_io_manifest" not in tables
    assert "bea_io_use" in tables
    assert "bea_io_make" in tables

    n_use = wh.query("SELECT COUNT(*) AS n FROM bea_io_use")["n"][0]
    n_make = wh.query("SELECT COUNT(*) AS n FROM bea_io_make")["n"][0]
    assert n_use == 12  # 1 year × 4 commodities × 3 industries
    assert n_make == 12  # 1 year × 3 industries × 4 commodities
```

- [ ] **Step 2: Run, confirm fail**

```bash
uv run pytest tests/sources/test_bea_io.py -v --no-cov -k "to_cleaned or load"
```

Expected: 2 FAIL — current `to_cleaned` writes a manifest, current `load` registers `bea_io_manifest` only.

- [ ] **Step 3: Replace `BEAIO.to_cleaned` and `BEAIO.load` in `src/eia/sources/bea_io.py`**

In `src/eia/sources/bea_io.py`, replace the entire `BEAIO` class with:

```python
class BEAIO(Source):
    name = "bea-io"
    target_table = "bea_io_use"  # primary; also writes bea_io_make
    raw_format = "xlsx"

    def __init__(self, year: int | None = None) -> None:
        cfg = self._load_config()
        self.year = year or cfg["default_year"]
        self.url = cfg["use_url"]

    @staticmethod
    def _load_config() -> dict:
        with open("configs/sources.yaml") as f:
            return yaml.safe_load(f)["bea_io"]

    def fetch(self) -> Path:
        out = self.raw_dir / "AllTablesIO.zip"
        if out.exists() and out.stat().st_size > 0:
            return out
        from urllib.parse import urlparse

        parsed = urlparse(self.url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path
        with RateLimitedClient(base, requests_per_second=0.5, timeout_s=120.0) as client:
            data = client.get_bytes(path)
        out.write_bytes(data)
        return out

    def to_cleaned(self, raw_path: Path) -> Path:
        """Parse Summary-level Use and Make tables (1997-2023) into long-form parquets.

        Writes two cleaned outputs:
            cleaned_dir / "use_summary.parquet"
            cleaned_dir / "make_summary.parquet"

        Returns the use parquet path (canonical; make path is at the sibling
        location with name replaced).
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            use_xlsx = _extract_use_to_temp(raw_path, tmp_dir / "_use.xlsx")
            make_xlsx = _extract_make_to_temp(raw_path, tmp_dir / "_make.xlsx")

            industries_list, commodities_list = _industries_and_commodities_from_make(
                make_xlsx
            )
            industries = set(industries_list)
            commodities = set(commodities_list)
            logger.info(
                "BEA Make: %d industries, %d commodities",
                len(industries),
                len(commodities),
            )

            fetched_at = self.now_utc()
            use_frames: list[pl.DataFrame] = []
            make_frames: list[pl.DataFrame] = []
            for year in _year_sheets(use_xlsx):
                try:
                    use_frames.append(
                        _parse_use_year(
                            use_xlsx,
                            year=year,
                            industries=industries,
                            commodities=commodities,
                            fetched_at=fetched_at,
                        )
                    )
                    make_frames.append(
                        _parse_make_year(
                            make_xlsx, year=year, fetched_at=fetched_at
                        )
                    )
                except Exception as exc:  # pragma: no cover - operational
                    logger.error("BEA year %d failed: %s", year, exc)

            use_master = pl.concat(use_frames) if use_frames else pl.DataFrame()
            make_master = pl.concat(make_frames) if make_frames else pl.DataFrame()
            logger.info(
                "BEA Use: %d rows; BEA Make: %d rows",
                use_master.height,
                make_master.height,
            )

            use_out = self.cleaned_dir / "use_summary.parquet"
            make_out = self.cleaned_dir / "make_summary.parquet"
            use_master.write_parquet(use_out)
            make_master.write_parquet(make_out)
            return use_out

    def load(self, cleaned_path: Path, warehouse) -> None:  # type: ignore[override]
        """Register Use and Make parquets into the warehouse; drop legacy manifest."""
        make_path = cleaned_path.parent / cleaned_path.name.replace("use_", "make_")
        warehouse.register_table_from_parquet(
            "bea_io_use", cleaned_path, replace=True
        )
        warehouse.register_table_from_parquet(
            "bea_io_make", make_path, replace=True
        )
        warehouse.execute_sql("DROP TABLE IF EXISTS bea_io_manifest")
```

- [ ] **Step 4: Run, confirm pass**

```bash
uv run pytest tests/sources/test_bea_io.py -v --no-cov
```

Expected: 7 passed (1 industries + 1 parse_make + 3 parse_use + 2 to_cleaned/load).

- [ ] **Step 5: Confirm no regression in the broader test suite**

```bash
uv run pytest --no-cov 2>&1 | tail -3
```

Expected: previous tests pass + 7 new tests = 70 passed total (63 prior + 7 new).

- [ ] **Step 6: Commit**

```bash
git add tests/sources/test_bea_io.py src/eia/sources/bea_io.py
git commit -m "feat(bea): real to_cleaned + load for Use/Make Summary; drop manifest stub"
```

---

## Task 6: Run real BEA pull and verify warehouse

The parser is implemented. Now run it on the actual `data/raw/bea-io/AllTablesIO.zip` and confirm the warehouse populates correctly across all 27 years.

**Files:** none modified (data lands in gitignored `data/`).

- [ ] **Step 1: Run the BEA pull end-to-end**

```bash
time uv run eia pull bea-io 2>&1 | tail -10
```

Expected: completes in 5–15 seconds. Output mentions `Done bea-io`. The cleaned files at `data/cleaned/bea-io/use_summary.parquet` and `make_summary.parquet` should both exist.

- [ ] **Step 2: Verify parquet outputs**

```bash
ls -lh data/cleaned/bea-io/
```

Expected: both `use_summary.parquet` and `make_summary.parquet` present, multi-MB each.

- [ ] **Step 3: Verify warehouse population**

```bash
uv run python -c "
from eia.warehouse import get_warehouse
wh = get_warehouse()
print('Tables present:', sorted(wh.list_tables()))
print()
n_use = wh.query('SELECT COUNT(*) AS n FROM bea_io_use')['n'][0]
n_make = wh.query('SELECT COUNT(*) AS n FROM bea_io_make')['n'][0]
print(f'bea_io_use rows: {n_use:,}')
print(f'bea_io_make rows: {n_make:,}')
print()
year_counts = wh.query('SELECT table_year, COUNT(*) AS n_use_rows FROM bea_io_use GROUP BY table_year ORDER BY table_year')
print('Use rows per year:')
print(year_counts)
print()
ind_count = wh.query('SELECT COUNT(DISTINCT industry_code) AS n FROM bea_io_make')['n'][0]
com_count = wh.query('SELECT COUNT(DISTINCT commodity_code) AS n FROM bea_io_make')['n'][0]
print(f'Distinct industries (Make): {ind_count}')
print(f'Distinct commodities (Make): {com_count}')
print()
print('bea_io_manifest in tables?', 'bea_io_manifest' in wh.list_tables())
"
```

Expected:
- 27 years × ~71 industries × ~73 commodities ≈ 140K rows in each of `bea_io_use` and `bea_io_make`.
- ~71 distinct industries, ~73 distinct commodities at Summary level.
- `bea_io_manifest` is no longer in the table list.

If any of those numbers look wildly off (e.g. 0 rows, or 27,000,000 rows), STOP and investigate before merging.

- [ ] **Step 4: Sanity-check the multiplier engine on real data**

```bash
uv run python -c "
from eia.warehouse import get_warehouse
from eia.multipliers import compute_leontief_inverse

wh = get_warehouse()
use = wh.query('SELECT industry_code, commodity_code, value_millions FROM bea_io_use WHERE table_year = 2023')
make = wh.query('SELECT industry_code, commodity_code, value_millions FROM bea_io_make WHERE table_year = 2023')
print(f'Use rows for 2023: {use.height}')
print(f'Make rows for 2023: {make.height}')

L = compute_leontief_inverse(use, make)
print(f'Leontief output rows: {L.height}')
print()
import polars as pl
diag = L.filter(pl.col('output_industry') == pl.col('demand_industry')).sort('demand_industry')
print('First 10 diagonal multipliers (output_industry = demand_industry):')
print(diag.head(10))
print()
print(f'Min diagonal: {diag[\"total_requirement\"].min():.3f}')
print(f'Max diagonal: {diag[\"total_requirement\"].max():.3f}')
print(f'(All diagonals must be >= 1.0 by construction.)')
"
```

Expected: `compute_leontief_inverse` runs cleanly on real BEA 2023 data and produces a Leontief matrix with all diagonal values ≥ 1.0. The min should be very close to 1.0 (industries with little intermediate-input recycling); the max can be 2–3+ (industries deeply integrated into supply chains, e.g. food services).

If any diagonal is < 1.0, the math has gone sideways — STOP and investigate.

- [ ] **Step 5: No commit needed if everything verified**

This task is execution + validation; the data lives in gitignored `data/` and the warehouse is local. Nothing to commit. Move to Task 7.

If something needed fixing (e.g. an unexpected industry-code anomaly that the parser didn't handle), make the fix on this branch and commit before moving on.

---

## Task 7: Quality sweep + merge prep

Lint, typecheck, full test run.

**Files:** Whatever ruff / mypy flags

- [ ] **Step 1: Lint**

```bash
uv run ruff check src/eia/sources/bea_io.py tests/sources/test_bea_io.py
```

Expected: clean. Likely flags: long lines, missing trailing newlines, etc. Fix in place. Known sensitivity: RUF002/RUF003 on Unicode characters in docstrings/comments — replace with ASCII (`->` not `→`, `x` not `×`).

- [ ] **Step 2: Type-check**

```bash
uv run mypy src/eia/sources/bea_io.py
```

Expected: `Success: no issues found`. If mypy flags `_parse_*` helpers because `pl.read_excel` returns an untyped frame, narrow with explicit `pl.DataFrame` annotations on the assignment. For Polars dynamic-row iteration (`df.row(...)`), accept that returns are object-typed and cast at point of use.

- [ ] **Step 3: Full test suite**

```bash
make test
```

Expected: 70 passed (63 prior + 7 new bea tests). Coverage on `src/eia/sources/bea_io.py` should be near 100%; if anything is meaningfully uncovered (a real edge case, not just the operational `# pragma: no cover` lines), add a regression test for it.

- [ ] **Step 4: Commit fixes if any**

```bash
git add -p   # stage selectively
git commit -m "chore: lint and type fixes for BEA parser"
```

If steps 1–3 were clean, no commit needed.

---

## Done

After Task 7, the BEA Use+Make parser is real and the warehouse contains:
- `bea_io_use`: ~140K rows (27 years × 71 industries × 73 commodities, sparse cells trimmed).
- `bea_io_make`: ~140K rows (same shape).
- `bea_io_manifest`: dropped.

The multiplier engine (`compute_leontief_inverse`) can now consume real BEA data for any year 1997–2023. The federal-side data layer is fully populated except for HUD (still a placeholder, requires the HUD API token to expand).

Branch can be merged to main with:

```bash
git -C "$PARENT" checkout main
git -C "$PARENT" merge --no-ff feature/bea-io-parsing -m "Merge branch 'feature/bea-io-parsing': BEA Use+Make parser"
git -C "$PARENT" branch -d feature/bea-io-parsing
```

(Use the standard merge-commit body referencing the spec and plan paths.)
