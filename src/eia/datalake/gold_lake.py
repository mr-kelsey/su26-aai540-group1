"""
Access to the gold lake as a single dataframe
"""

from __future__ import annotations

import boto3
import awswrangler as wr
import pandas as pd

# ---- Constants ----
REGION     = "us-east-2"
BUCKET     = "jonno-lucas-steve-bucket"
PROJECT    = "usd-aai540-group1"
SILVER_DB  = "aai540_silver"
GOLD_DB    = "aai540_gold"
ATHENA_OUT = f"s3://{BUCKET}/{PROJECT}/athena-results/"

boto3.setup_default_session(region_name=REGION)

def read_gold_data() -> pd.DataFrame:
    """Pull Gold data training matrix data"""
    return wr.athena.read_sql_query(
        "SELECT * FROM model_training_matrix LIMIT 2",
        database=GOLD_DB, s3_output=ATHENA_OUT
        )