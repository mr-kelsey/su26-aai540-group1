"""
Feature engineer gold lake data for feature stroe consumption
"""

from __future__ import annotations

from datetime import datetime
from eia.datalake import read_gold_data

class Single_Input_Baseline:
    def __init__(self):
        self.data = self.single_input()

    def quarter_2_iso8601(self, df_series):
        # Manually verified using CATFA docs
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

    def single_input(self):
        df = read_gold_data()
        df = df[["county_fips", "year", "quarter", "period_id", "total_est_attendance", "taxable_sales_usd"]]
        df["record-identifier"] = df["county_fips"] + "-" + df["period_id"]
        df["event-time"] = df[["year", "quarter"]].apply(self.quarter_2_iso8601, axis=1)
        return (
            df[["record-identifier", "event-time", "total_est_attendance", "taxable_sales_usd"]]
            .rename(columns={"total_est_attendance": "total-est-attendance",
                             "taxable_sales_usd": "taxable-sales-usd"})
        )
    
    def get_engineered_data(self):
        return self.data
    
    def __str__(self):
        return "X-Attendance-Y-Sales"