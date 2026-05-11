"""Warehouse Protocol.

The Protocol defines the minimal interface that any warehouse backend must
implement. Source modules and the CLI depend only on this Protocol, never on
a concrete backend.

Migration model: SQL files under `migrations/` are applied in lexicographic
order. Each backend is responsible for tracking applied migrations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import polars as pl


@runtime_checkable
class Warehouse(Protocol):
    """Minimal interface for a warehouse backend."""

    backend_name: str

    def execute_sql(self, sql: str, params: dict[str, Any] | None = None) -> None:
        """Execute a non-query SQL statement (DDL, DML)."""
        ...

    def query(self, sql: str, params: dict[str, Any] | None = None) -> pl.DataFrame:
        """Execute a SELECT and return a Polars DataFrame."""
        ...

    def register_table_from_parquet(
        self, table_name: str, parquet_path: Path, replace: bool = True
    ) -> None:
        """Materialize a Parquet file into a warehouse table.

        If `replace=True`, an existing table with the same name is dropped first.
        """
        ...

    def table_exists(self, table_name: str) -> bool: ...

    def list_tables(self) -> list[str]: ...

    def migrate(self, migrations_dir: Path = Path("migrations")) -> list[str]:
        """Apply all unapplied migrations in `migrations_dir`. Returns names applied."""
        ...

    def reset(self) -> None:
        """Drop all data. DESTRUCTIVE."""
        ...

    def info(self) -> dict[str, Any]:
        """Return backend metadata (path/DSN, table count, applied migrations)."""
        ...
