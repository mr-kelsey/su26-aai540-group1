"""CDTFA California Department of Tax and Fee Administration source.

Pulls Taxable Table 3 (Taxable Sales by County, by Type of Business) via
CDTFA's public OData JSON endpoint. Covers 2015 Q1 through the most recent
published quarter (typically ~5 months lagged). California-only; other
state DORs need their own source classes.

Endpoint: https://cdtfa.ca.gov/dataportal/api/odata/Taxable_Sales_Counties
No auth. No rate limit observed; we still go through RateLimitedClient.

See docs/superpowers/specs/2026-05-10-cdtfa-source-design.md for design.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from eia.clients import RateLimitedClient
from eia.sources.base import Source
from eia.sources.registry import register

logger = logging.getLogger(__name__)

_QUARTER_RE = re.compile(r"^Q([1-4])$")


class CDTFA(Source):
    name = "cdtfa-taxable-sales"
    target_table = "cdtfa_taxable_sales"
    raw_format = "json"

    def __init__(self) -> None:
        cfg = self._load_config()
        self.base_url = cfg["base_url"]
        self.dataset_path = cfg["dataset_path"]
        self.top = cfg["top"]
        self.requests_per_second = cfg["requests_per_second"]

    @staticmethod
    def _load_config() -> dict[str, Any]:
        with open("configs/sources.yaml") as f:
            return yaml.safe_load(f)["cdtfa"]  # type: ignore[no-any-return]

    @staticmethod
    def _period_id(year: int, quarter_str: str) -> str | None:
        """Map (2024, 'Q3') -> '2024Q3'. Returns None for malformed quarter."""
        if not isinstance(quarter_str, str):
            return None
        m = _QUARTER_RE.match(quarter_str)
        if not m:
            return None
        return f"{year}Q{m.group(1)}"

    @staticmethod
    def _parse_row(payload: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize one OData row into the cdtfa_taxable_sales-schema dict.

        Returns None if a required field (CalendarYear or Quarter) is missing
        or unparseable.
        """
        year_raw = payload.get("CalendarYear")
        quarter_str = payload.get("Quarter")
        if year_raw is None or quarter_str is None:
            return None
        try:
            year = int(year_raw)
        except (TypeError, ValueError):
            return None
        period_id = CDTFA._period_id(year, quarter_str)
        if period_id is None:
            return None
        quarter_num = int(period_id[-1])

        return {
            "table_year": year,
            "quarter": quarter_num,
            "period_id": period_id,
            "county_fips": None,  # filled by FIPS lookup later
            "cdtfa_county_code": int(payload.get("CountyCode") or 0),
            "cdtfa_county_name": payload.get("County") or "",
            "business_group_code": payload.get("BusinessGroupCode"),
            "business_type": payload.get("BusinessType"),
            "permit_count": payload.get("NumberOfPermits"),
            "taxable_sales_usd": payload.get("TaxableTransactions"),
            "disclosure_flag": payload.get("DisclosureFlag"),
            "fetched_at": datetime.utcnow(),
        }

    @staticmethod
    def _build_county_lookup(dim_county: pl.DataFrame) -> dict[str, str]:
        """Build {CDTFA_uppercase_name: county_fips} dict from dim_county.

        Restricts to CA counties (state_fips='06'). Normalizes by uppercasing
        and stripping a trailing ' COUNTY' so CDTFA's 'ALAMEDA' matches
        dim_county's 'Alameda County'.
        """
        ca = dim_county.filter(pl.col("state_fips") == "06").select(
            pl.col("county_fips"),
            pl.col("county_name").str.to_uppercase().str.replace(r" COUNTY$", "").alias("_norm"),
        )
        return dict(zip(ca["_norm"].to_list(), ca["county_fips"].to_list(), strict=True))

    def fetch(self) -> Path:
        """Single GET to CDTFA's OData endpoint; write JSON to raw_dir."""
        out_dir = self.raw_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "taxable_sales_counties.json"
        with RateLimitedClient(
            self.base_url,
            requests_per_second=self.requests_per_second,
            timeout_s=60.0,
        ) as client:
            params = {"$top": self.top}
            headers = {"Accept": "application/json"}
            data = client.get_json(self.dataset_path, params=params, headers=headers)
        out_path.write_text(json.dumps(data))
        n = len(data.get("value", []))
        logger.info("CDTFA fetch: %d rows written to %s", n, out_path)
        return out_dir

    def to_cleaned(self, raw_path: Path) -> Path:
        """Parse raw JSON, normalize rows, join FIPS lookup, write parquet."""
        json_path = raw_path / "taxable_sales_counties.json"
        data = json.loads(json_path.read_text())
        raw_rows = data.get("value", [])

        parsed: list[dict[str, Any]] = []
        for r in raw_rows:
            row = self._parse_row(r)
            if row is not None:
                parsed.append(row)

        from eia.warehouse import get_warehouse

        dim_county = get_warehouse().query(
            "SELECT county_fips, county_name, state_fips FROM dim_county"
        )
        lookup = self._build_county_lookup(dim_county)

        for row in parsed:
            row["county_fips"] = lookup.get(row["cdtfa_county_name"])

        n_unmapped = sum(1 for r in parsed if r["county_fips"] is None)
        if n_unmapped:
            unmapped_names = sorted(
                {r["cdtfa_county_name"] for r in parsed if r["county_fips"] is None}
            )
            logger.warning(
                "CDTFA: %d rows unmapped to county_fips; CDTFA names not in dim_county: %s",
                n_unmapped,
                unmapped_names,
            )

        df = pl.DataFrame(
            parsed,
            schema={
                "table_year": pl.Int16,
                "quarter": pl.Int16,
                "period_id": pl.Utf8,
                "county_fips": pl.Utf8,
                "cdtfa_county_code": pl.Int16,
                "cdtfa_county_name": pl.Utf8,
                "business_group_code": pl.Utf8,
                "business_type": pl.Utf8,
                "permit_count": pl.Int32,
                "taxable_sales_usd": pl.Int64,
                "disclosure_flag": pl.Utf8,
                "fetched_at": pl.Datetime,
            },
        )

        out = self.cleaned_dir / "taxable_sales_counties.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out)
        logger.info(
            "CDTFA to_cleaned: %d rows written to %s (%d unmapped to FIPS)",
            df.height,
            out,
            n_unmapped,
        )
        return out


register(CDTFA.name, CDTFA)
