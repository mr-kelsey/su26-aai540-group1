"""Texas Comptroller — local sales tax allocations to counties (monthly).

Pulls Texas's "Sales Tax Allocation: County, MTA, SPD" dataset (id qsh8-tby8)
via the data.texas.gov Socrata API. Filters to type=COUNTY. Provides
monthly county-level data 2013-present, which is the closest publicly
available proxy for taxable sales activity in TX (the state doesn't
publish raw taxable-sales-by-county).

Endpoint: https://data.texas.gov/resource/qsh8-tby8.json
No auth. Socrata supports $where, $limit, $offset for pagination.

See docs/state_dor_inventory.md for the broader state-DOR strategy.
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
from eia.sources.base import Source
from eia.sources.registry import register

logger = logging.getLogger(__name__)


class TXComptroller(Source):
    name = "tx-comptroller"
    target_table = "tx_comptroller_county_allocations"
    raw_format = "json"

    def __init__(self) -> None:
        cfg = self._load_config()
        self.base_url = cfg["base_url"]
        self.dataset_path = cfg["dataset_path"]
        self.limit_per_page = cfg["limit_per_page"]
        self.requests_per_second = cfg["requests_per_second"]

    @staticmethod
    def _load_config() -> dict[str, Any]:
        with open("configs/sources.yaml") as f:
            return yaml.safe_load(f)["tx_comptroller"]  # type: ignore[no-any-return]

    def fetch(self) -> Path:
        """Paginated GET, filtered to type=COUNTY rows."""
        out_dir = self.raw_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        page = 0
        total_rows = 0
        with RateLimitedClient(
            self.base_url,
            requests_per_second=self.requests_per_second,
            timeout_s=120.0,
        ) as client:
            while True:
                params = {
                    "$where": "type='COUNTY'",
                    "$limit": self.limit_per_page,
                    "$offset": page * self.limit_per_page,
                    "$order": "report_year, report_month, name",
                }
                data = client.get_json(self.dataset_path, params=params)
                if not isinstance(data, list) or not data:
                    break
                out_path = out_dir / f"page_{page:04d}.json"
                out_path.write_text(json.dumps(data))
                total_rows += len(data)
                logger.info("TX Comptroller page %d: %d rows", page, len(data))
                if len(data) < self.limit_per_page:
                    break
                page += 1
        logger.info("TX Comptroller fetch complete: %d rows total", total_rows)
        return out_dir

    def to_cleaned(self, raw_path: Path) -> Path:
        """Parse paginated JSONs, normalize, FIPS-join, write parquet."""
        all_rows: list[dict[str, Any]] = []
        fetched = datetime.utcnow()

        for page_file in sorted(raw_path.glob("page_*.json")):
            data = json.loads(page_file.read_text())
            for row in data:
                try:
                    year = int(row["report_year"])
                    month = int(row["report_month"])
                except (KeyError, TypeError, ValueError):
                    continue
                all_rows.append(
                    {
                        "state_fips": "48",
                        "table_year": year,
                        "month": month,
                        "period_id": f"{year}-{month:02d}",
                        "county_fips": None,  # FIPS lookup below
                        "county_name": row.get("name"),
                        "net_payment_usd": _as_float(row.get("net_payment_this_period")),
                        "current_rate_pct": _as_float(row.get("current_rate")),
                        "comparable_prior_year_usd": _as_float(
                            row.get("comparable_payment_prior_year")
                        ),
                        "pct_change_prior_year": _as_float(row.get("percent_change_prior_year")),
                        "payments_to_date_usd": _as_float(row.get("payments_to_date")),
                        "previous_payments_to_date_usd": _as_float(
                            row.get("previous_payments_to_date")
                        ),
                        "pct_change_to_date": _as_float(row.get("percent_change_to_date")),
                        "fetched_at": fetched,
                    }
                )

        # FIPS lookup: TX dim_county names like "Anderson County" -> "48001"
        from eia.warehouse import get_warehouse

        dim_county = get_warehouse().query(
            "SELECT county_fips, county_name FROM dim_county WHERE state_fips = '48'"
        )
        lookup = {
            row["county_name"].upper().replace(" COUNTY", ""): row["county_fips"]
            for row in dim_county.iter_rows(named=True)
        }
        for row in all_rows:
            name_norm = (row["county_name"] or "").upper().replace(" COUNTY", "")
            row["county_fips"] = lookup.get(name_norm)

        n_unmapped = sum(1 for r in all_rows if r["county_fips"] is None)
        if n_unmapped:
            unmapped_names = sorted(
                {r["county_name"] for r in all_rows if r["county_fips"] is None}
            )
            logger.warning(
                "TX Comptroller: %d rows unmapped to county_fips. Sample names: %s",
                n_unmapped,
                unmapped_names[:10],
            )

        df = pl.DataFrame(
            all_rows,
            schema={
                "state_fips": pl.Utf8,
                "table_year": pl.Int16,
                "month": pl.Int16,
                "period_id": pl.Utf8,
                "county_fips": pl.Utf8,
                "county_name": pl.Utf8,
                "net_payment_usd": pl.Float64,
                "current_rate_pct": pl.Float64,
                "comparable_prior_year_usd": pl.Float64,
                "pct_change_prior_year": pl.Float64,
                "payments_to_date_usd": pl.Float64,
                "previous_payments_to_date_usd": pl.Float64,
                "pct_change_to_date": pl.Float64,
                "fetched_at": pl.Datetime,
            },
        )
        if df.height > 0:
            df = df.unique(subset=["period_id", "county_name"], keep="first")

        out = self.cleaned_dir / "tx_county_allocations.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out)
        logger.info("TX Comptroller to_cleaned: %d rows -> %s", df.height, out)
        return out


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


register(TXComptroller.name, TXComptroller)
