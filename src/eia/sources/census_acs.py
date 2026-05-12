"""Census ACS 5-year detailed county data via the Data API.

Pulls a small, hand-picked variable list per state, joins to county FIPS,
and lands a long-format Parquet.

API docs: https://api.census.gov/data.html
Variables: https://api.census.gov/data/2023/acs/acs5/variables.html
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import yaml

from eia.clients import RateLimitedClient
from eia.config import settings
from eia.sources.base import Source
from eia.sources.registry import register


class CensusACS(Source):
    name = "census-acs"
    target_table = "census_acs_county"
    raw_format = "json"

    def __init__(self, end_year: int | None = None, states: list[str] | None = None) -> None:
        cfg = self._load_config()
        self.end_year = end_year or cfg["default_end_year"]
        self.states = states or list(cfg["states"])
        self.variables: dict[str, str] = cfg["variables"]
        self.base_url = cfg["base_url"]
        self.api_key = settings.census_api_key

    @staticmethod
    def _load_config() -> dict[str, Any]:
        with open("configs/sources.yaml") as f:
            return yaml.safe_load(f)["census_acs"]  # type: ignore[no-any-return]

    def fetch(self) -> Path:
        """Hit the ACS 5-year endpoint once per state and dump JSON."""
        out_dir = self.raw_dir / f"end_{self.end_year}"
        out_dir.mkdir(parents=True, exist_ok=True)
        var_list = ",".join(self.variables.values())
        with RateLimitedClient(self.base_url, requests_per_second=2.0) as client:
            for st in self.states:
                target = out_dir / f"state_{st}.json"
                if target.exists() and target.stat().st_size > 0:
                    continue
                params = {
                    "get": f"NAME,{var_list}",
                    "for": "county:*",
                    "in": f"state:{st}",
                }
                if self.api_key:
                    params["key"] = self.api_key
                data = client.get_json(f"/{self.end_year}/acs/acs5", params=params)
                import json

                target.write_text(json.dumps(data))
        return out_dir

    def to_cleaned(self, raw_path: Path) -> Path:
        """Combine all state JSON pulls into one wide Parquet."""
        import json

        rows: list[dict[str, Any]] = []
        var_names = list(self.variables.keys())
        var_codes = list(self.variables.values())
        col_to_var = dict(zip(var_codes, var_names, strict=True))

        for state_file in sorted(raw_path.glob("state_*.json")):
            data = json.loads(state_file.read_text())
            header, *records = data
            # Identify positions of each variable in the header.
            var_positions = {col_to_var[code]: header.index(code) for code in var_codes}
            state_pos = header.index("state")
            county_pos = header.index("county")
            for rec in records:
                county_fips = f"{rec[state_pos]}{rec[county_pos]}"
                row: dict[str, Any] = {"county_fips": county_fips}
                for var_name, pos in var_positions.items():
                    val = rec[pos]
                    row[var_name] = float(val) if val not in (None, "", "null") else None
                rows.append(row)

        df = pl.DataFrame(rows)

        # Compute "bachelor or higher" pct.
        df = df.with_columns(
            bachelor_or_higher_pct=(
                (
                    pl.col("bachelors_count")
                    + pl.col("bachelors_master")
                    + pl.col("bachelors_prof")
                    + pl.col("bachelors_doctor")
                )
                / pl.col("population_25_plus")
                * 100.0
            )
        )

        cleaned = df.select(
            "county_fips",
            pl.lit(self.end_year).cast(pl.Int16).alias("acs_5yr_end_year"),
            pl.col("population").cast(pl.Int32),
            pl.col("median_household_income").cast(pl.Int32),
            pl.col("median_age"),
            pl.col("bachelor_or_higher_pct"),
            pl.lit(self.now_utc()).alias("fetched_at"),
        )

        out = self.cleaned_dir / f"end_{self.end_year}.parquet"
        cleaned.write_parquet(out)
        return out


register(CensusACS.name, CensusACS)
