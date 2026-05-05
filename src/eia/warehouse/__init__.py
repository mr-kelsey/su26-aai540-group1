"""Warehouse abstraction layer.

Use `get_warehouse()` to obtain the configured backend. All source modules
should depend only on the `Warehouse` Protocol from `.base`, not on any
concrete implementation.
"""

from __future__ import annotations

from eia.config import settings
from eia.warehouse.base import Warehouse


def get_warehouse() -> Warehouse:
    """Return the configured warehouse backend instance."""
    backend = settings.eia_warehouse_backend
    if backend == "duckdb":
        from eia.warehouse.duckdb_impl import DuckDBWarehouse

        return DuckDBWarehouse(path=settings.eia_duckdb_path)
    elif backend == "postgres":
        from eia.warehouse.postgres_impl import PostgresWarehouse

        if settings.eia_postgres_dsn is None:
            raise ValueError("EIA_POSTGRES_DSN must be set when backend=postgres")
        return PostgresWarehouse(dsn=settings.eia_postgres_dsn)
    else:
        raise ValueError(f"Unknown warehouse backend: {backend}")


__all__ = ["Warehouse", "get_warehouse"]
