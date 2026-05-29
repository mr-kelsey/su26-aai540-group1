"""Pydantic settings loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level project settings.

    Values are loaded from environment variables, with `.env` as a fallback.
    Prefix env vars with `EIA_` for project-namespaced settings.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",  # we mix EIA_-prefixed and bare API keys
        extra="ignore",
    )

    # ---- Warehouse ----
    eia_warehouse_backend: Literal["duckdb", "postgres"] = Field(
        default="duckdb",
        description="Which Warehouse implementation to use.",
    )
    eia_duckdb_path: Path = Field(
        default=Path("data/warehouse.duckdb"),
        description="Filesystem path for DuckDB database file.",
    )
    eia_postgres_dsn: str | None = Field(
        default=None,
        description="Postgres DSN, used when backend=postgres.",
    )

    # ---- Data layout ----
    eia_data_root: Path = Field(
        default=Path("data"),
        description="Root directory for raw/, cleaned/, etc.",
    )

    # ---- API keys (free tiers, all optional in dev) ----
    census_api_key: str | None = None
    bls_api_key: str | None = None
    ticketmaster_api_key: str | None = None
    runsignup_api_key: str | None = None
    runsignup_api_secret: str | None = None
    setlistfm_api_key: str | None = None
    fred_api_key: str | None = None

    # ---- AWS configuration ----
    aws_region: str | None = None
    aws_bucket: str | None = None
    aws_project: str | None = None
    aws_silver_db: str | None = None
    aws_gold_db: str | None = None
    aws_role_arn: str | None = None

    # ---- Convenience ----
    @property
    def raw_dir(self) -> Path:
        return self.eia_data_root / "raw"

    @property
    def cleaned_dir(self) -> Path:
        return self.eia_data_root / "cleaned"

    def ensure_dirs(self) -> None:
        for p in (self.raw_dir, self.cleaned_dir, self.eia_duckdb_path.parent):
            p.mkdir(parents=True, exist_ok=True)


# Module-level singleton; import this from anywhere.
settings = Settings()
