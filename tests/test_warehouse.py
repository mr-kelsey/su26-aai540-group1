"""Smoke tests for the DuckDB warehouse implementation."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from eia.warehouse.duckdb_impl import DuckDBWarehouse


@pytest.fixture()
def wh(tmp_path: Path) -> DuckDBWarehouse:
    return DuckDBWarehouse(path=tmp_path / "test.duckdb")


def test_init_and_info(wh: DuckDBWarehouse) -> None:
    info = wh.info()
    assert info["backend"] == "duckdb"
    assert info["table_count"] == 0


def test_migrate_and_list_tables(wh: DuckDBWarehouse, tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_init.sql").write_text(
        "CREATE TABLE foo (id INTEGER PRIMARY KEY, name VARCHAR);"
    )
    applied = wh.migrate(migrations_dir=migrations)
    assert applied == ["001_init.sql"]
    assert "foo" in wh.list_tables()
    # Re-running migrate is a no-op.
    assert wh.migrate(migrations_dir=migrations) == []


def test_register_table_from_parquet(wh: DuckDBWarehouse, tmp_path: Path) -> None:
    pq = tmp_path / "sample.parquet"
    pl.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]}).write_parquet(pq)
    wh.register_table_from_parquet("sample", pq)
    out = wh.query("SELECT count(*) AS n FROM sample")
    assert out["n"][0] == 3


def test_query_with_params(wh: DuckDBWarehouse, tmp_path: Path) -> None:
    pq = tmp_path / "x.parquet"
    pl.DataFrame({"id": [1, 2, 3]}).write_parquet(pq)
    wh.register_table_from_parquet("x", pq)
    # Use DuckDB's $name syntax with a dict of bindings.
    out = wh.query("SELECT * FROM x WHERE id = $id", params={"id": 2})
    assert out["id"][0] == 2


def test_reset(wh: DuckDBWarehouse, tmp_path: Path) -> None:
    pq = tmp_path / "x.parquet"
    pl.DataFrame({"id": [1]}).write_parquet(pq)
    wh.register_table_from_parquet("x", pq)
    wh.reset()
    assert wh.list_tables() == []
