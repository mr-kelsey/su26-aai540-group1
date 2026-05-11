"""Warehouse health check.

Lists every table in the warehouse with row count, freshness (max fetched_at
where available), and column count. Quick sanity check after a pull or
between sessions.
"""

from __future__ import annotations

import sys

import polars as pl
from rich.console import Console
from rich.table import Table

from eia.warehouse import get_warehouse
from eia.warehouse.base import Warehouse

console = Console()

# Tables we track. Anything else found in the warehouse gets a generic row.
TRACKED_TABLES = [
    "dim_county",
    "dim_time",
    "census_acs_county",
    "bls_qcew",
    "bea_io_use",
    "bea_io_make",
    "hud_zip_county",
    "ticketmaster_events",
    "setlistfm_setlists",
    "events",
]


def _row_count(wh: Warehouse, name: str) -> int:
    try:
        df = wh.query(f"SELECT COUNT(*) AS n FROM {name}")
        return int(df["n"][0])
    except Exception:
        return -1


def _max_fetched_at(wh: Warehouse, name: str) -> str | None:
    try:
        df = wh.query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = $name "
            "AND column_name = 'fetched_at'",
            params={"name": name},
        )
        if df.is_empty():
            return None
        ts = wh.query(f"SELECT MAX(fetched_at) AS t FROM {name}")
        v = ts["t"][0]
        return str(v) if v is not None else None
    except Exception:
        return None


def _col_count(wh: Warehouse, name: str) -> int:
    try:
        df = wh.query(
            "SELECT COUNT(*) AS n FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = $name",
            params={"name": name},
        )
        return int(df["n"][0])
    except Exception:
        return -1


def main() -> int:
    wh = get_warehouse()
    all_tables_df: pl.DataFrame = wh.query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_name"
    )
    present = set(all_tables_df["table_name"].to_list()) if not all_tables_df.is_empty() else set()

    t = Table(title="Warehouse tables", header_style="bold cyan")
    t.add_column("table")
    t.add_column("rows", justify="right")
    t.add_column("cols", justify="right")
    t.add_column("max(fetched_at)")
    t.add_column("status")

    ordered = TRACKED_TABLES + sorted(p for p in present if p not in TRACKED_TABLES)
    seen: set[str] = set()
    for name in ordered:
        if name in seen:
            continue
        seen.add(name)
        if name not in present:
            t.add_row(name, "—", "—", "—", "[red]MISSING[/red]")
            continue
        rows = _row_count(wh, name)
        cols = _col_count(wh, name)
        latest = _max_fetched_at(wh, name) or "—"
        status = "[green]ok[/green]" if rows > 0 else "[yellow]empty[/yellow]"
        t.add_row(name, f"{rows:,}" if rows >= 0 else "?", str(cols), latest, status)
    console.print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
