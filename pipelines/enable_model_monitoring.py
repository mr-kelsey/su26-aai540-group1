"""
Model monitoring
"""

from __future__ import annotations

import boto3
import pandas as pd

from pathlib import Path
from rich.console import Console
from sys import exit as sys_exit
from time import sleep

from eia.config import settings

MODEL_NAME = "xgb-foodsvc"
DATA_PATH = Path(__file__).parents[1] / "data" / "model" / "xgboost" / "validation_data.csv"
FEATURE_NAMES = ["avg_employment", "bachelor_or_higher_pct", "covid",
        "establishment_count", "land_area_sqmi", "latitude", "longitude",
        "median_age", "median_household_income", "n_events", "n_festivals",
        "n_setlistfm", "n_ticketmaster", "population", "quarter",
        "total_est_attendance", "total_expected_attendance",
        "total_festival_attendance", "total_wages_usd"]

region = settings.aws_region
role_arn = settings.aws_role_arn
s3 = boto3.client("s3")
sagemaker = boto3.client("sagemaker", region_name=region)
runtime = boto3.client("sagemaker-runtime", region_name=region)

console = Console()

def _create_endpoint_config(model_name):
    """Create endpoint configuration for using pre-existing model"""
    s3_uri = f"s3://{settings.aws_bucket}/{settings.aws_project}/captured_data/{model_name}"

    endpoint_config_response = sagemaker.create_endpoint_config(
        EndpointConfigName=f"{model_name}-capture-cfg",
        ProductionVariants=[
            {
                "VariantName": "AllTraffic",
                "ModelName": model_name,
                "InitialInstanceCount": 1,
                "InstanceType": "ml.m5.xlarge",
                "InitialVariantWeight": 1.0,
            }
        ],
        DataCaptureConfig={
            "EnableCapture": True,
            "InitialSamplingPercentage": 100,
            "DestinationS3Uri": s3_uri,
            "CaptureOptions": [
                {"CaptureMode": "Input"},
                {"CaptureMode": "Output"},
            ],
            "CaptureContentTypeHeader": {
                "CsvContentTypes": ["text/csv"],
                "JsonContentTypes": ["application/json"],
            },
        },
    )

    return endpoint_config_response["EndpointConfigArn"]

def _create_endpoint(model_name):
    """Create Endpoint"""
    endpoint_name = f"{model_name}-capture"
    sagemaker.create_endpoint(
        EndpointName=endpoint_name,
        EndpointConfigName=f"{model_name}-capture-cfg",
    )

    while True:
        desc = sagemaker.describe_endpoint(EndpointName=endpoint_name)
        status = desc["EndpointStatus"]
        print(status)
        if status in ("InService", "Failed"):
            if status == "Failed":
                raise RuntimeError(desc.get("FailureReason", "Endpoint creation failed"))
            break
        sleep(30)

    return endpoint_name

def _send_traffic(endpoint_name, payload):
    """Write data to and read data from endpoint to trigger proper data captureing"""

    response = runtime.invoke_endpoint(
        EndpointName=endpoint_name,
        ContentType="text/csv",
        Body=payload,
    )
    # trigger read capture
    _ = response["Body"].read()

    return True

def _make_dummy_ground_truth():
    """Configure Model Bias Monitor baseline dataset"""
    baseline_df = pd.read_csv(DATA_PATH, header=None, names=FEATURE_NAMES)
    baseline_df.insert(0, "dummy_truth", (
            ((baseline_df["median_household_income"] * baseline_df["population"] * 0.005) # food baseline
            +
            (baseline_df["total_est_attendance"] * 36 * 0.23) # food delta
            ).astype(float))
        )
    baseline_df.head()

    baseline_csv = DATA_PATH.parent / "bias_baseline.csv"
    baseline_df.to_csv(baseline_csv, index=False)

    baseline_prefix = f"{settings.aws_project}/bias-baseline"
    baseline_s3_key = f"{baseline_prefix}/data/{baseline_csv}"
    s3.upload_file(baseline_csv, settings.aws_bucket, baseline_s3_key)
    
    baseline_s3_uri = f"s3://{settings.aws_bucketket}/{settings.aws_project}/{baseline_s3_key}"
    
    return baseline_s3_uri

def _create_monitoring_job(model_name, baseline_s3_uri):
    from time import gmtime, strftime

    bucket = settings.aws_bucket
    baseline_prefix = f"{settings.aws_project}/bias-baseline"
    baseline_job_name = f"bias-baseline-{strftime('%Y-%m-%d-%H-%M-%S', gmtime())}"

    analysis_config = {
        "dataset_type": "text/csv",
        "headers": ["dummy_truth"] + FEATURE_NAMES,
        "label": "dummy_truth",
        "s3_output_path": f"s3://{bucket}/{baseline_prefix}/output",
        "methods": {
            "pre_training_bias": {
                "facet_name": "population",
                "facet_values_or_threshold": [676599], # Mean population
            }
        },
        "predictor": {
            "model_name": model_name,
            "instance_type": "ml.m5.xlarge",
            "instance_count": 1,
            "content_type": "text/csv",
            "accept_type": "text/csv",
            "probability_threshold": 0.5,
        },
    }

    sagemaker.create_processing_job(
        ProcessingJobName=baseline_job_name,
        AppSpecification={
            "ImageUri": "763104351884.dkr.ecr.us-east-1.amazonaws.com/sagemaker-clarify-processing:latest"
        },
        RoleArn=role_arn,
        ProcessingInputs=[
            {
                "InputName": "analysis_config",
                "S3Input": {
                    "S3Uri": f"s3://{bucket}/{baseline_prefix}/analysis_config.json",
                    "LocalPath": "/opt/ml/processing/input/analysis_config",
                    "S3DataType": "S3Prefix",
                    "S3InputMode": "File",
                },
            },
            {
                "InputName": "dataset",
                "S3Input": {
                    "S3Uri": baseline_s3_uri,
                    "LocalPath": "/opt/ml/processing/input/data",
                    "S3DataType": "S3Prefix",
                    "S3InputMode": "File",
                },
            },
        ],
        ProcessingOutputConfig={
            "Outputs": [
                {
                    "OutputName": "analysis_result",
                    "S3Output": {
                        "S3Uri": f"s3://{bucket}/{baseline_prefix}/output",
                        "LocalPath": "/opt/ml/processing/output",
                        "S3UploadMode": "EndOfJob",
                    },
                }
            ]
        },
        ProcessingResources={
            "ClusterConfig": {
                "InstanceCount": 1,
                "InstanceType": "ml.m5.xlarge",
                "VolumeSizeInGB": 20,
            }
        },
        StoppingCondition={"MaxRuntimeInSeconds": 1800},
    )

def main(model_name) -> int:
    # ------ Spin up endpoint ------
    console.rule("[bold]Creating endpoint configuration")
    endpoint_config_arn = _create_endpoint_config(model_name)
    console.print(f"Done\nEndpointConfig: {endpoint_config_arn}")

    console.rule("[bold]Creating endpoint")
    endpoint_name = _create_endpoint(model_name)
    console.print(f"Done\nEndpoint: {endpoint_name}\n")

    # ------ Read and write for capture ------
    console.rule("[bold]Sending traffic for capture")
    console.print(f"Please wait", end="")
    data = pd.read_csv(DATA_PATH, header=None)
    record_count_to_send = max(200, len(data))

    for index in range(record_count_to_send):
        if index % 10 == 0:
            console.print(f".", end="")
        payload = ",".join(str(value) for value in data.iloc[index].tolist())
        _send_traffic(endpoint_name, payload)
        sleep(0.5)
    console.print(f"\nDone\n")

    # ------ Use simple heuristic to get dummy truth ------
    console.rule("[bold]Makeing dummy baseline to monitor against")
    baseline_uri = _make_dummy_ground_truth()
    console.print(f"Done\nBaseline uploaded: {baseline_uri}\n")

    # ------ Create Monitoring Job------
    console.rule("[bold]Creating and starting monitoring job")
    _create_monitoring_job(model_name, baseline_uri)
    console.print(f"Monitoring job created.\nAll Set!")

    return 0

if __name__ == "__main__":
    sys_exit(main(MODEL_NAME))
