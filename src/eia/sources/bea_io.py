"""BEA Input-Output Use & Make tables.

These are the foundation for deriving Leontief multipliers locally instead of
buying RIMS II / IMPLAN. Pulled annually as XLS bundles from BEA.

Reference: https://www.bea.gov/industry/input-output-accounts-data
"""

from __future__ import annotations

import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import polars as pl
import yaml  # type: ignore[import-untyped]

from eia.clients import RateLimitedClient
from eia.sources.base import Source
from eia.sources.registry import register
from eia.warehouse.base import Warehouse

logger = logging.getLogger(__name__)

USE_MEMBER = "IOUse_After_Redefinitions_PRO_1997-2023_Summary.xlsx"
MAKE_MEMBER = "IOMake_After_Redefinitions_PRO_1997-2023_Summary.xlsx"

# BEA IOCodes are short alphanumeric (sometimes letters only, sometimes
# digits+letters). Footnote text rows that BEA includes at the bottom of
# Make sheets ("Note. Detail may not add to total due to rounding.",
# "1. Consists of only scrap...") have spaces and punctuation and must
# be filtered out. This regex matches the legitimate IOCode shape.
_IOCODE_PATTERN = re.compile(r"^[A-Za-z0-9]+$")


def _is_iocode(value: object) -> bool:
    """True if value looks like a BEA IOCode (short alphanumeric, no spaces or punctuation)."""
    if not isinstance(value, str):
        return False
    s = value.strip()
    return bool(s) and bool(_IOCODE_PATTERN.match(s))


class BEAIO(Source):
    name = "bea-io"
    target_table = "bea_io_use"  # primary; also writes bea_io_make
    raw_format = "xlsx"

    def __init__(self, year: int | None = None) -> None:
        cfg = self._load_config()
        self.year = year or cfg["default_year"]
        self.url = cfg["use_url"]

    @staticmethod
    def _load_config() -> dict[str, Any]:
        with open("configs/sources.yaml") as f:
            return yaml.safe_load(f)["bea_io"]  # type: ignore[no-any-return]

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

    def load(self, cleaned_path: Path, warehouse: Warehouse) -> None:
        """Register Use and Make parquets into the warehouse; drop legacy manifest."""
        make_path = cleaned_path.parent / cleaned_path.name.replace("use_", "make_")
        warehouse.register_table_from_parquet(
            "bea_io_use", cleaned_path, replace=True
        )
        warehouse.register_table_from_parquet(
            "bea_io_make", make_path, replace=True
        )
        warehouse.execute_sql("DROP TABLE IF EXISTS bea_io_manifest")


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
            if _is_iocode(v):
                commodities.add(v.strip())
        # Rows 7+ (0-indexed 6+): col 1 is the industry IOCode for that row.
        # Pattern-filter to skip footnote-text rows ("Note. ...", "1. Consists ...").
        for ridx in range(6, df.height):
            v = df.row(ridx)[0]
            if _is_iocode(v):
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
        if not _is_iocode(ind_raw):
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
