"""Phase 1 BLS QCEW pull: all US counties, all 4 quarters, multi-year.

Loops year x quarter using BLSQCEW(mode="singlefile"). Each year's singlefile
zip is downloaded once and reused across the four quarters. After all per-
(year, quarter) cleaned parquets are written, they are concatenated into one
master parquet and loaded into the warehouse with replace=True (a single
load, since the per-call default replace would overwrite each quarter).

Years and quarters come from configs/sources.yaml (bls_qcew.phase1_years and
bls_qcew.phase1_quarters). Defaults to 2015-2023 x Q1-Q4 (32 cells).

Idempotent: re-running picks up already-downloaded zips and overwrites cleaned
parquets in place. The final warehouse load drops the table and recreates from
the consolidated master parquet.

Run:
    uv run python pipelines/phase1_bls_qcew.py
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import polars as pl
import yaml
from rich.console import Console

from eia.sources.bls_qcew import BLSQCEW
from eia.warehouse import get_warehouse

console = Console()


def _load_config() -> dict[str, Any]:
    with open("configs/sources.yaml") as f:
        return yaml.safe_load(f)["bls_qcew"]  # type: ignore[no-any-return]


def main() -> None:
    cfg = _load_config()
    years: list[int] = cfg["phase1_years"]
    quarters: list[int] = cfg["phase1_quarters"]

    console.rule("[bold]Phase 1 BLS QCEW pull")
    console.print(f"Years: {years}")
    console.print(f"Quarters: {quarters}")
    console.print(f"Cells to process: {len(years) * len(quarters)}")

    # Step 1: download each year's singlefile zip (idempotent; reused across quarters).
    console.rule("[bold]Step 1: download zips")
    for year in years:
        t0 = time.monotonic()
        src = BLSQCEW(year=year, quarter=quarters[0], mode="singlefile")
        zip_path = src.fetch()
        elapsed = time.monotonic() - t0
        size_mb = zip_path.stat().st_size / 1024 / 1024
        console.print(f"  [cyan]{year}[/]: {size_mb:.0f} MB at {zip_path} ({elapsed:.1f}s)")

    # Step 2: clean each (year, quarter) cell. Each call re-reads the year's
    # singlefile and filters to one quarter; the cleaned parquet is written
    # per-cell in BLSQCEW.cleaned_dir.
    console.rule("[bold]Step 2: clean per (year, quarter)")
    cleaned_paths: list[Path] = []
    failures: list[tuple[int, int, str]] = []
    for year in years:
        for quarter in quarters:
            t0 = time.monotonic()
            src = BLSQCEW(year=year, quarter=quarter, mode="singlefile")
            zip_path = src.fetch()  # idempotent re-fetch returns the existing path
            try:
                cleaned = src.to_cleaned(zip_path)
                cleaned_paths.append(cleaned)
                elapsed = time.monotonic() - t0
                rows = pl.scan_parquet(cleaned).select(pl.len()).collect().item()
                console.print(
                    f"  [cyan]{year}Q{quarter}[/]: {rows:,} rows -> {cleaned.name} ({elapsed:.1f}s)"
                )
            except Exception as exc:  # pragma: no cover - operational
                failures.append((year, quarter, str(exc)))
                console.print(f"  [red]{year}Q{quarter} FAILED:[/] {exc}")

    if failures:
        console.print(f"[red]\n{len(failures)} cells failed:[/]")
        for y, q, msg in failures:
            console.print(f"  - {y}Q{q}: {msg}")

    if not cleaned_paths:
        console.print("[red]No cleaned parquets produced; aborting load.[/]")
        return

    # Step 3: concatenate into a single master parquet for the warehouse load.
    console.rule("[bold]Step 3: consolidate")
    master = pl.concat([pl.read_parquet(p) for p in cleaned_paths]).sort(
        "year", "quarter", "county_fips", "naics_code"
    )
    master_path = cleaned_paths[0].parent / "phase1_master.parquet"
    master.write_parquet(master_path)
    console.print(
        f"Wrote master: {master_path} ({master.height:,} rows, "
        f"{master_path.stat().st_size / 1024 / 1024:.0f} MB)"
    )

    # Step 4: load into the warehouse, replacing the table.
    console.rule("[bold]Step 4: load to warehouse")
    wh = get_warehouse()
    wh.register_table_from_parquet("bls_qcew", master_path, replace=True)
    n = wh.query("SELECT COUNT(*) AS n FROM bls_qcew").item()
    console.print(f"[green]bls_qcew loaded: {n:,} rows[/]")

    # Quick sanity summary by year.
    by_year = wh.query(
        "SELECT year, COUNT(*) AS n_rows, COUNT(DISTINCT county_fips) AS n_counties "
        "FROM bls_qcew GROUP BY year ORDER BY year"
    )
    console.print(by_year)

    console.rule("[bold green]Phase 1 BLS QCEW pull complete")


if __name__ == "__main__":
    main()
