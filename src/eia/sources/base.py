"""Source Protocol — the contract every data source must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path

from eia.config import settings
from eia.warehouse.base import Warehouse


class Source(ABC):
    """Abstract base for a data source.

    Subclasses set:
        name        — short, hyphenated identifier (also CLI subcommand name)
        target_table — warehouse table this source loads into
        raw_format   — directory format hint for raw landing zone

    And implement:
        fetch()      — download/API-pull, write verbatim to data/raw/<name>/...
        to_cleaned() — transform raw → cleaned Parquet with conformed schema
        load()       — register cleaned Parquet into the warehouse
    """

    name: str
    target_table: str
    raw_format: str = "csv"  # csv | json | parquet | shapefile | geojson

    # ---- helpers ----

    @property
    def raw_dir(self) -> Path:
        d = settings.raw_dir / self.name
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def cleaned_dir(self) -> Path:
        d = settings.cleaned_dir / self.name
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def now_iso() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(UTC)

    # ---- contract ----

    @abstractmethod
    def fetch(self) -> Path:
        """Download or API-pull. Return path to raw artifact (file or dir)."""

    @abstractmethod
    def to_cleaned(self, raw_path: Path) -> Path:
        """Transform raw artifact to cleaned Parquet. Return path to .parquet."""

    def load(self, cleaned_path: Path, warehouse: Warehouse) -> None:
        """Default: replace target_table from cleaned Parquet.

        Override for upsert / append semantics.
        """
        warehouse.register_table_from_parquet(self.target_table, cleaned_path, replace=True)
