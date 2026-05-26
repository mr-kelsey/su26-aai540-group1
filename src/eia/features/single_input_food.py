"""
Feature engineer gold lake data for the food-sales feature store.

Parallels single_input_baseline.py on the feature/aws-feature-store branch:
same record-identifier and event-time construction (ISO-8601 + 'Z'), same X
(total-est-attendance). Y is swapped to food-services taxable sales (CDTFA
'Food Services and Drinking Places') in place of the 'Total All Outlets'
aggregate the baseline uses.

Feature names are hyphenated to match the baseline group exactly, so a model
can join X-Attendance-Y-Sales and X-Attendance-Y-Food-Sales on
(record-identifier, event-time).

One deliberate deviation from the baseline: food-services sales is suppressed
(DisclosureFlag='D') in more county-quarters than 'Total All Outlets', and the
gold LEFT JOIN leaves those as NULL. We drop those rows here so the row-by-row
put_record ingest never sends an empty value for an Integral feature. This is
the "drop disclosure-suppressed rows for v1" decision applied to the food Y.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from eia.datalake import read_gold_data


class SingleInputFoodBaseline:
    def __init__(self) -> None:
        self.data = self.single_input()

    def quarter_2_iso8601(self, df_series: pd.Series) -> str:
        # Manually verified using CDTFA docs (quarter-end dates, UTC 'Z')
        year, quarter = df_series["year"], df_series["quarter"]
        if quarter == 1:
            return datetime.strptime(f"{year}-03-31", "%Y-%m-%d").isoformat() + "Z"
        if quarter == 2:
            return datetime.strptime(f"{year}-06-30", "%Y-%m-%d").isoformat() + "Z"
        if quarter == 3:
            return datetime.strptime(f"{year}-09-30", "%Y-%m-%d").isoformat() + "Z"
        if quarter == 4:
            return datetime.strptime(f"{year}-12-31", "%Y-%m-%d").isoformat() + "Z"
        raise TypeError

    def single_input(self) -> pd.DataFrame:
        df = read_gold_data()
        df = df[["county_fips", "year", "quarter", "period_id", "total_est_attendance", "food_services_sales_usd"]]
        df["record-identifier"] = df["county_fips"] + "-" + df["period_id"]
        df["event-time"] = df[["year", "quarter"]].apply(self.quarter_2_iso8601, axis=1)
        return (
            df[["record-identifier", "event-time", "total_est_attendance", "food_services_sales_usd"]]
            .rename(columns={"total_est_attendance": "total-est-attendance",
                             "food_services_sales_usd": "food-services-sales-usd"})
            .dropna(subset=["food-services-sales-usd"])
        )

    def get_engineered_data(self) -> pd.DataFrame:
        return self.data

    def __str__(self) -> str:
        return "X-Attendance-Y-Food-Sales"
