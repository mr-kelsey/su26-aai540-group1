"""
Building out the feature store
"""

from __future__ import annotations

import boto3
import pandas as pd

from rich.console import Console
from rich.table import Table
from sys import exit as sys_exit
from time import sleep

from eia.config import settings
from eia.features.single_input_food import SingleInputFoodBaseline


region = settings.aws_region

sagemaker_client = boto3.client("sagemaker", region_name=region)
feature_store_client = boto3.client("sagemaker-featurestore-runtime", region_name=region)

console = Console()

def ingest_dataframe(feature_group_name, df):
    for _, row in df.iterrows():
        record = []
        for col in df.columns:
            value = row[col]
            record.append({
                "FeatureName": col,
                "ValueAsString": "" if pd.isna(value) else str(value)
            })

        feature_store_client.put_record(
            FeatureGroupName=feature_group_name,
            Record=record
        )

def load_feature_definitions(feature_group_name, record_identifier_name, event_time_feature_name):
    role_arn = settings.aws_role_arn
    s3_uri = f"s3://{settings.aws_bucket}/{settings.aws_project}/feature-store"

    sagemaker_client.create_feature_group(
        FeatureGroupName=feature_group_name,
        RecordIdentifierFeatureName=record_identifier_name,
        EventTimeFeatureName=event_time_feature_name,
        FeatureDefinitions=[
            {"FeatureName": record_identifier_name, "FeatureType": "String"},
            {"FeatureName": event_time_feature_name, "FeatureType": "String"},
            {"FeatureName": "total-est-attendance", "FeatureType": "Integral"},
            {"FeatureName": "food-services-sales-usd", "FeatureType": "Integral"},
        ],
        RoleArn=role_arn,
        OfflineStoreConfig={
            "S3StorageConfig": {
                "S3Uri": s3_uri
            },
            "DisableGlueTableCreation": False,
            "TableFormat": "Glue",
        },
        OnlineStoreConfig={
            "EnableOnlineStore": True
        },
        Description="Single input, single output, no proxy, just attendance vs sales",
        Tags=[
            {"Key": "features", "Value": "attendance and sales"},
        ],
    )
    wait_for_feature_group_creation_complete(feature_group_name=feature_group_name)

def main() -> int:
    # ------ Build Store ------
    console.rule("[bold]Building feature store")

    feature_engine = SingleInputFoodBaseline()
    feature_group_name = str(feature_engine)
    existing_feature_groups = sagemaker_client.list_feature_groups()
    for group in existing_feature_groups["FeatureGroupSummaries"]:
        if feature_group_name == group['FeatureGroupName']:
            console.print(feature_group_name, "already exists.  Skipping feature group creation")
            break
    else:
        load_feature_definitions(feature_group_name, "record-identifier", "event-time")
        console.print(sagemaker_client.list_feature_groups())

    # ------ Push data ------
    console.rule("[bold]Ingesting data")

    base_data = feature_engine.get_engineered_data()
    t = Table(title="Single Input Single Output (Sample)")
    for col in base_data.columns:
        t.add_column(col)
    for _, row in base_data.head(10).iterrows():
        t.add_row(*[str(v) for v in row])
    console.print(t)

    ingest_dataframe(feature_group_name, base_data)

    return 0

def wait_for_feature_group_creation_complete(feature_group_name, sleep_time=15):
    while True:
        response = sagemaker_client.describe_feature_group(FeatureGroupName=feature_group_name)
        status = response.get("FeatureGroupStatus")
        if status == "Created":
            console.print(f"FeatureGroup {feature_group_name} successfully created.")
            return
        if status == "CreateFailed":
            raise RuntimeError(f"Feature group creation failed: {feature_group_name}")
        console.print("Waiting for Feature Group Creation")
        sleep(sleep_time)

if __name__ == "__main__":
    sys_exit(main())
