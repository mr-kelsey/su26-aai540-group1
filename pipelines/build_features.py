"""
Building out the feature store
"""

from __future__ import annotations

import boto3
import pandas as pd

from rich.console import Console
from rich.table import Table
from sagemaker.core.helper.session_helper import Session, get_execution_role
from sagemaker.mlops.feature_store.feature_definition import FeatureDefinition, FeatureTypeEnum
from sagemaker.mlops.feature_store import FeatureGroup
from sys import exit as sys_exit
from time import sleep

from eia.config import settings
from eia.features.single_input_baseline import Single_Input_Baseline


region = settings.aws_region
#  boto3.setup_default_session(region_name=region) # Seems to be the way Claude is making sessions
boto_session = boto3.Session(region_name=region)

sagemaker_client = boto_session.client(service_name="sagemaker", region_name=region)
featurestore_runtime = boto_session.client(
    service_name="sagemaker-featurestore-runtime", region_name=region
)

console = Console()

def infer_feature_type(dtype):
    if pd.api.types.is_integer_dtype(dtype):
        return FeatureTypeEnum.INTEGRAL
    elif pd.api.types.is_float_dtype(dtype):
        return FeatureTypeEnum.FRACTIONAL
    else:
        return FeatureTypeEnum.STRING

def ingest_dataframe(feature_group_name, df):
    for _, row in df.iterrows():
        record = []
        for col in df.columns:
            value = row[col]
            record.append({
                "FeatureName": col,
                "ValueAsString": "" if pd.isna(value) else str(value)
            })

        featurestore_runtime.put_record(
            FeatureGroupName=feature_group_name,
            Record=record
        )

def load_feature_definitions(final_features, feature_group_name,
                             record_identifier_feature_name, event_time_feature_name):
    
    feature_definitions = [
        FeatureDefinition(
            feature_name=col,
            feature_type=infer_feature_type(final_features[col].dtype)
        )
        for col in final_features.columns
    ]

    feature_group = FeatureGroup(feature_group_name=feature_group_name)

    feature_group.create(
        region=region,
        feature_group_name=feature_group_name,
        record_identifier_feature_name=record_identifier_feature_name,
        event_time_feature_name=event_time_feature_name,
        feature_definitions=feature_definitions,
        role_arn=role,
        online_store_config={"enable_online_store": True},
        offline_store_config={
            "s3_storage_config": {
                "s3_uri": "s3://jonno-lucas-steve-bucket/usd-aai540-group1/feature-store/"
            }
        }
    )
    wait_for_feature_group_creation_complete(feature_group_name=feature_group_name)

def main() -> int:
    # ------ Build Store ------
    console.rule("[bold]Building feature store")

    feature_engine = Single_Input_Baseline()
    feature_group_name = str(feature_engine)

    base_data = feature_engine.get_engineered_data()
    load_feature_definitions(base_data, feature_group_name, "", "")
    console.print(sagemaker_client.list_feature_groups())

    # ------ Push data ------
    console.rule("[bold]Ingesting data")

    t = Table(title="Single Input Single Output (Sample)")
    for col in base_data.columns:
        t.add_column(col)
    for _, row in base_data.head(10).iterrows():
        t.add_row(*[str(v) for v in row])
    console.print(t)

    ingest_dataframe(feature_group_name, base_data)

    return 0

def wait_for_feature_group_creation_complete(feature_group_name, sleep_time=5):
    while True:
        response = sagemaker_client.describe_feature_group(FeatureGroupName=feature_group_name)
        status = response.get("FeatureGroupStatus")
        if status == "Created":
            print(f"FeatureGroup {feature_group_name} successfully created.")
            return
        if status == "CreateFailed":
            raise RuntimeError(f"Feature group creation failed: {feature_group_name}")
        print("Waiting for Feature Group Creation")
        sleep(sleep_time)

if __name__ == "__main__":
    sys_exit(main())

"""
Rewrite build_features.py against the real SDK: correct imports, a passed-in/get_execution_role() role, valid hyphenated group name, dedicated offline-store prefix, feature_group.ingest(df) instead of the row-by-row put_record loop.
"""