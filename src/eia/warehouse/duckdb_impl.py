"""DuckDB implementation of the Warehouse Protocol."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import duckdb
import polars as pl


class DuckDBWarehouse:
    """File-backed DuckDB warehouse.

    Concurrency note: DuckDB allows a single writer; multiple concurrent
    readers are fine. We open a fresh connection per call, which keeps
    operations independent and avoids holding write locks longer than needed.
    """

    backend_name = "duckdb"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---- internal ----

    def _connect(self, read_only: bool = False) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.path), read_only=read_only)

    # ---- Protocol implementation ----

    def execute_sql(self, sql: str, params: dict[str, Any] | None = None) -> None:
        with self._connect() as con:
            con.execute(sql, parameters=params or {})

    def query(self, sql: str, params: dict[str, Any] | None = None) -> pl.DataFrame:
        """Execute SELECT and return Polars DataFrame.

        For parameter binding, use DuckDB's `$name` syntax in `sql` and pass
        a dict of values, e.g.:
            query("SELECT * FROM t WHERE id = $id", params={"id": 7})
        """
        with self._connect(read_only=True) as con:
            return con.execute(sql, parameters=params or {}).pl()

    def register_table_from_parquet(
        self, table_name: str, parquet_path: Path, replace: bool = True
    ) -> None:
        with self._connect() as con:
            if replace:
                con.execute(f"DROP TABLE IF EXISTS {table_name}")
            con.execute(
                f"CREATE TABLE {table_name} AS SELECT * FROM read_parquet(?)",
                [str(parquet_path)],
            )

    def table_exists(self, table_name: str) -> bool:
        with self._connect(read_only=True) as con:
            row = con.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
                [table_name],
            ).fetchone()
            return bool(row and row[0] > 0)

    def list_tables(self) -> list[str]:
        if not self.path.exists():
            return []
        with self._connect(read_only=True) as con:
            rows = con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
            return [r[0] for r in rows]

    def migrate(self, migrations_dir: Path = Path("migrations")) -> list[str]:
        if not migrations_dir.exists():
            return []
        with self._connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS _migrations ("
                "name VARCHAR PRIMARY KEY, applied_at TIMESTAMP DEFAULT current_timestamp)"
            )
            applied = {
                row[0] for row in con.execute("SELECT name FROM _migrations").fetchall()
            }
            applied_now: list[str] = []
            for sql_file in sorted(migrations_dir.glob("*.sql")):
                if sql_file.name in applied:
                    continue
                con.execute(sql_file.read_text())
                con.execute("INSERT INTO _migrations (name) VALUES (?)", [sql_file.name])
                applied_now.append(sql_file.name)
        return applied_now

    def reset(self) -> None:
        if self.path.exists():
            self.path.unlink()
        wal = self.path.with_suffix(self.path.suffix + ".wal")
        if wal.exists():
            wal.unlink()

    def info(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "backend": self.backend_name,
                "path": str(self.path),
                "size_bytes": 0,
                "table_count": 0,
                "migrations_applied": [],
            }
        with self._connect(read_only=True) as con:
            tables = con.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='main'"
            ).fetchone()
            applied: list[str] = []
            with contextlib.suppress(duckdb.CatalogException):
                applied = [
                    r[0]
                    for r in con.execute(
                        "SELECT name FROM _migrations ORDER BY name"
                    ).fetchall()
                ]
        return {
            "backend": self.backend_name,
            "path": str(self.path),
            "size_bytes": self.path.stat().st_size,
            "table_count": tables[0] if tables else 0,
            "migrations_applied": applied,
        }
