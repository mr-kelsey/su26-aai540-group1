"""
Feature engineer gold lake data for feature stroe consumption
"""

from __future__ import annotations

from eia.datalake import read_gold_data

class Single_Input_Baseline:
    def __init__(self):
        self.data = self.single_input()

    def single_input(self):
        df = read_gold_data()
        return df[["total_est_attendance", "taxable_sales_usd"]]
    
    def get_engineered_data(self):
        return self.data
    
    def __str__(self):
        return "X_Attendance_Y_Sales"