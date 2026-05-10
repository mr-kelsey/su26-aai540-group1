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
