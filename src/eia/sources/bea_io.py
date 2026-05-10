"""BEA Input-Output Use & Make tables.

These are the foundation for deriving Leontief multipliers locally instead of
buying RIMS II / IMPLAN. Pulled annually as XLS bundles from BEA.

Reference: https://www.bea.gov/industry/input-output-accounts-data
"""

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


class BEAIO(Source):
    name = "bea-io"
    target_table = "bea_io_use"  # also writes bea_io_make
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
        # BEA hosts on apps.bea.gov; we fetch the zipped table bundle directly.
        from urllib.parse import urlparse

        parsed = urlparse(self.url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path
        with RateLimitedClient(base, requests_per_second=0.5, timeout_s=120.0) as client:
            data = client.get_bytes(path)
        out.write_bytes(data)
        return out

    def to_cleaned(self, raw_path: Path) -> Path:
        """Extract Use and Make summary tables and write a single Parquet.

        BEA ships dozens of XLS files; for Phase 0 we extract just the
        summary-level Use and Make tables for the configured year. Detail-level
        ingestion comes in Phase 1.
        """
        # Phase 0 stub: list the files in the zip and write a manifest Parquet.
        # Full XLS-to-Parquet conversion lands in Phase 1 once we settle on
        # which sheet (industry-by-commodity vs commodity-by-industry) we want.
        with zipfile.ZipFile(raw_path) as z:
            members = z.namelist()
        manifest = pl.DataFrame(
            {
                "filename": members,
                "table_year": [self.year] * len(members),
                "fetched_at": [self.now_utc()] * len(members),
            }
        )
        out = self.cleaned_dir / f"manifest_{self.year}.parquet"
        manifest.write_parquet(out)
        return out

    def load(self, cleaned_path: Path, warehouse) -> None:  # type: ignore[override]
        # The manifest goes into a side table; the actual Use/Make tables get
        # populated in Phase 1 when we select specific sheets.
        warehouse.register_table_from_parquet(
            "bea_io_manifest", cleaned_path, replace=True
        )


register(BEAIO.name, BEAIO)


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
