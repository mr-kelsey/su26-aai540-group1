"""Postgres implementation of the Warehouse Protocol.

STATUS: Stub. Phase 0 ships DuckDB only. This module documents the planned
interface and ensures the import path works so the factory in
`eia.warehouse.__init__` can switch backends without changes elsewhere.

To implement: install with `make install-postgres`, then fill in methods
using `psycopg` (3.x) and SQLAlchemy if convenient. The migration runner
should track applied migrations in a `_migrations` table identical to the
DuckDB version.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl


class PostgresWarehouse:
    """Stub Postgres warehouse — not yet implemented."""

    backend_name = "postgres"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def execute_sql(self, sql: str, params: dict[str, Any] | None = None) -> None:
        raise NotImplementedError("PostgresWarehouse.execute_sql not yet implemented")

    def query(self, sql: str, params: dict[str, Any] | None = None) -> pl.DataFrame:
        raise NotImplementedError("PostgresWarehouse.query not yet implemented")

    def register_table_from_parquet(
        self, table_name: str, parquet_path: Path, replace: bool = True
    ) -> None:
        raise NotImplementedError(
            "PostgresWarehouse.register_table_from_parquet not yet implemented"
        )

    def table_exists(self, table_name: str) -> bool:
        raise NotImplementedError("PostgresWarehouse.table_exists not yet implemented")

    def list_tables(self) -> list[str]:
        raise NotImplementedError("PostgresWarehouse.list_tables not yet implemented")

    def migrate(self, migrations_dir: Path = Path("migrations")) -> list[str]:
        raise NotImplementedError("PostgresWarehouse.migrate not yet implemented")

    def reset(self) -> None:
        raise NotImplementedError("PostgresWarehouse.reset not yet implemented")

    def info(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "dsn": self.dsn,
            "status": "not yet implemented",
        }
