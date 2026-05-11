"""Census Bureau State Government Tax Collections (annual, state-level).

Pulls every year of state tax collections via the Census timeseries API.
Covers all 50 states + DC. Used as the universal Y-target floor for the
economic-impact model where deeper per-state DOR sources don't exist.

Endpoint: https://api.census.gov/data/timeseries/govsstatetax
No auth required. Use existing CENSUS_API_KEY if set (raises rate limit).

Key item codes:
    T09 = General Sales and Gross Receipts Tax  — primary Y for events model
    T01 = Alcoholic Beverages Sales Tax
    T10 = Motor Fuels Sales Tax
    T12 = Public Utilities Sales Tax
    T13 = Tobacco Products Sales Tax
    T15 = Amusements Sales Tax  — especially relevant for event-impact
    T16 = Insurance Premiums Sales Tax
    T19 = Other Selective Sales Tax
    AGG = aggregate roll-up at the agg_desc level (NOT a per-tax type)

See docs/state_dor_inventory.md for the broader strategy.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from eia.clients import RateLimitedClient
from eia.config import settings
from eia.sources.base import Source
from eia.sources.registry import register

logger = logging.getLogger(__name__)


class CensusStateTax(Source):
    name = "census-state-tax"
    target_table = "census_state_tax_collections"
    raw_format = "json"

    def __init__(self) -> None:
        cfg = self._load_config()
        self.base_url = cfg["base_url"]
        self.dataset_path = cfg["dataset_path"]
        self.start_year = cfg["start_year"]
        self.end_year = cfg["end_year"]
        self.requests_per_second = cfg["requests_per_second"]
        self.api_key = settings.census_api_key

    @staticmethod
    def _load_config() -> dict[str, Any]:
        with open("configs/sources.yaml") as f:
            return yaml.safe_load(f)["census_state_tax"]  # type: ignore[no-any-return]

    def fetch(self) -> Path:
        """One GET per year (Census API requires per-year time filter).

        Each response is ~100KB (~1600 rows). 9 years = ~900KB total.
        """
        out_dir = self.raw_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        n_rows = 0
        with RateLimitedClient(
            self.base_url,
            requests_per_second=self.requests_per_second,
            timeout_s=60.0,
        ) as client:
            for year in range(self.start_year, self.end_year + 1):
                out_path = out_dir / f"year_{year}.json"
                params: dict[str, Any] = {
                    "get": "AGG_DESC,GOVTYPE,ITEM_CODE,NAME,YEAR,AMOUNT",
                    "for": "state:*",
                    "time": str(year),
                }
                if self.api_key:
                    params["key"] = self.api_key
                data = client.get_json(self.dataset_path, params=params)
                out_path.write_text(json.dumps(data))
                # First row is the column header.
                row_count = len(data) - 1 if isinstance(data, list) else 0
                n_rows += row_count
                logger.info("Census STC %d: %d rows", year, row_count)
        logger.info("Census STC fetch complete: %d rows total", n_rows)
        return out_dir

    def to_cleaned(self, raw_path: Path) -> Path:
        """Parse per-year JSONs into a single long-form parquet."""
        all_rows: list[dict[str, Any]] = []
        fetched = datetime.utcnow()

        for year_file in sorted(raw_path.glob("year_*.json")):
            data = json.loads(year_file.read_text())
            if not isinstance(data, list) or len(data) < 2:
                continue
            header = data[0]
            col_idx = {col: i for i, col in enumerate(header)}
            for row in data[1:]:
                year = int(row[col_idx["YEAR"]])
                state_fips = row[col_idx["state"]]
                amount_raw = row[col_idx["AMOUNT"]]
                try:
                    amount = int(amount_raw) if amount_raw is not None else None
                except (TypeError, ValueError):
                    amount = None
                all_rows.append(
                    {
                        "table_year": year,
                        "period_id": f"{year}-annual",
                        "state_fips": str(state_fips).zfill(2),
                        "state_name": row[col_idx["NAME"]],
                        "govtype": row[col_idx["GOVTYPE"]],
                        "agg_desc": row[col_idx["AGG_DESC"]],
                        "item_code": row[col_idx["ITEM_CODE"]],
                        "amount_thousands_usd": amount,
                        "fetched_at": fetched,
                    }
                )

        df = pl.DataFrame(
            all_rows,
            schema={
                "table_year": pl.Int16,
                "period_id": pl.Utf8,
                "state_fips": pl.Utf8,
                "state_name": pl.Utf8,
                "govtype": pl.Utf8,
                "agg_desc": pl.Utf8,
                "item_code": pl.Utf8,
                "amount_thousands_usd": pl.Int64,
                "fetched_at": pl.Datetime,
            },
        )
        # Dedup against the PK (year, state_fips, agg_desc, item_code).
        if df.height > 0:
            df = df.unique(
                subset=["table_year", "state_fips", "agg_desc", "item_code"],
                keep="first",
            )

        out = self.cleaned_dir / "state_tax_collections.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out)
        logger.info("Census STC to_cleaned: %d rows written to %s", df.height, out)
        return out


register(CensusStateTax.name, CensusStateTax)
