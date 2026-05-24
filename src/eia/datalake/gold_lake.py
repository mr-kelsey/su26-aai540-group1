"""
Access to the gold lake as a single dataframe
"""

from __future__ import annotations

import boto3
import awswrangler as wr
import pandas as pd

from eia.config import settings

# ---- Constants ----
REGION     = settings.aws_region
BUCKET     = settings.aws_bucket
PROJECT    = settings.aws_project
SILVER_DB  = settings.aws_silver_db
GOLD_DB    = settings.aws_gold_db
ATHENA_OUT = f"s3://{BUCKET}/{PROJECT}/athena-results/"

boto3.setup_default_session(region_name=REGION)

def read_gold_data() -> pd.DataFrame:
    """Pull Gold data training matrix data"""
    return wr.athena.read_sql_query(
        "SELECT * FROM model_training_matrix LIMIT 2",
        database=GOLD_DB, s3_output=ATHENA_OUT
        )