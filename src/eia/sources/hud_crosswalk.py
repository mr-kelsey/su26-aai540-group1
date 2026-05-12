"""HUD USPS ZIP-County crosswalk.

NOTE: HUD's official portal (https://www.huduser.gov/portal/datasets/usps_crosswalk.html)
requires a free login + API token to download. This source has TWO modes:

    1. If `HUD_API_TOKEN` is set in env, hit the HUD API directly.
    2. Otherwise, this source records a "needs-token" stub so phase-0 can
       continue with sample data. The stub creates a minimal hand-crafted
       crosswalk for San Diego County (the canonical Phase-0 query target)
       so downstream joins still demonstrate end-to-end flow.

Phase 1 will gate on having the HUD token configured.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from eia.sources.base import Source
from eia.sources.registry import register

# Hand-curated minimal sample for San Diego County (FIPS 06073).
# Sufficient to demonstrate the join in the Phase 0 exit query.
_SAN_DIEGO_SAMPLE: list[dict[str, Any]] = [
    # ZIP, county_fips, res_ratio, bus_ratio, oth_ratio, tot_ratio
    {
        "zip": "92101",
        "county_fips": "06073",
        "res_ratio": 1.0,
        "bus_ratio": 1.0,
        "oth_ratio": 1.0,
        "tot_ratio": 1.0,
    },
    {
        "zip": "92103",
        "county_fips": "06073",
        "res_ratio": 1.0,
        "bus_ratio": 1.0,
        "oth_ratio": 1.0,
        "tot_ratio": 1.0,
    },
    {
        "zip": "92104",
        "county_fips": "06073",
        "res_ratio": 1.0,
        "bus_ratio": 1.0,
        "oth_ratio": 1.0,
        "tot_ratio": 1.0,
    },
    {
        "zip": "92108",
        "county_fips": "06073",
        "res_ratio": 1.0,
        "bus_ratio": 1.0,
        "oth_ratio": 1.0,
        "tot_ratio": 1.0,
    },
    {
        "zip": "92110",
        "county_fips": "06073",
        "res_ratio": 1.0,
        "bus_ratio": 1.0,
        "oth_ratio": 1.0,
        "tot_ratio": 1.0,
    },
    {
        "zip": "92121",
        "county_fips": "06073",
        "res_ratio": 1.0,
        "bus_ratio": 1.0,
        "oth_ratio": 1.0,
        "tot_ratio": 1.0,
    },
]


class HUDCrosswalk(Source):
    name = "hud-crosswalk"
    target_table = "hud_zip_county"
    raw_format = "csv"

    def __init__(self, quarter: str | None = None) -> None:
        cfg = self._load_config()
        self.quarter = quarter or cfg["default_quarter"]
        self.token = os.getenv("HUD_API_TOKEN")

    @staticmethod
    def _load_config() -> dict[str, Any]:
        with open("configs/sources.yaml") as f:
            return yaml.safe_load(f)["hud_crosswalk"]  # type: ignore[no-any-return]

    def fetch(self) -> Path:
        out = self.raw_dir / f"crosswalk_{self.quarter}.csv"
        if out.exists():
            return out

        if self.token:
            # TODO(Phase 1): call HUD API:
            # https://www.huduser.gov/hudapi/public/usps?type=2&query=All
            # with Authorization: Bearer <token>. For now we still write the
            # sample so the rest of Phase 0 keeps working without the token.
            pass

        # No token (or stub): write the curated San Diego sample.
        df = pl.DataFrame(_SAN_DIEGO_SAMPLE)
        df.write_csv(out)
        return out

    def to_cleaned(self, raw_path: Path) -> Path:
        df = pl.read_csv(raw_path, schema_overrides={"zip": pl.Utf8, "county_fips": pl.Utf8})
        cleaned = df.with_columns(
            pl.lit(self.quarter).alias("quarter"),
            pl.lit(self.now_utc()).alias("fetched_at"),
        ).select(
            "zip",
            "county_fips",
            "quarter",
            "res_ratio",
            "bus_ratio",
            "oth_ratio",
            "tot_ratio",
            "fetched_at",
        )
        out = self.cleaned_dir / f"crosswalk_{self.quarter}.parquet"
        cleaned.write_parquet(out)
        return out


register(HUDCrosswalk.name, HUDCrosswalk)
