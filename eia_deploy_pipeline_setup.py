
#!/usr/bin/env python3
"""
EIA food-services XGBoost  —  deploy pipeline, end-to-end setup
================================================================
Run ONCE by the AWS account owner (admin-level permissions).

What it does, in five stages:
  1. Creates the Lambda execution role (eia-deploy-lambda-role).
  2. Creates the Lambda deploy function (eia-deploy) with the handler embedded.
  3. Grants the SageMaker execution role permission to invoke that function,
     so the pipeline can be re-run later to deploy future approved models.
  4. Defines + upserts a SageMaker Pipeline whose single LambdaStep deploys the
     latest *Approved* model package in the registry to a real-time endpoint.
  5. Runs the pipeline once and waits for the endpoint to come InService.

PREREQUISITE: SageMaker Python SDK v2  ->  pip install "sagemaker<3"
Each stage prints a [n/5] marker; if a step is denied, that line names the
permission to fix.

COST NOTE: stage 5 leaves a real-time endpoint running (bills hourly). Delete with
  boto3.client("sagemaker").delete_endpoint(EndpointName=ENDPOINT_NAME)
when finished, or comment out stage 5 to set everything up without deploying yet.

A killswitch is included at the end of this notebook which can be uncommented and run to turn off the endpoint.
"""

import json, io, zipfile, time, boto3

# ------------------------------------------------------------------- config
REGION            = "us-east-2"
SM_EXEC_ROLE      = "arn:aws:iam::541974874359:role/service-role/AmazonSageMaker-ExecutionRole-20260516T162536"
SM_EXEC_ROLE_NAME = "AmazonSageMaker-ExecutionRole-20260516T162536"
LAMBDA_ROLE_NAME  = "eia-deploy-lambda-role"
FUNCTION_NAME     = "eia-deploy"
MODEL_GROUP       = "eia-foodsvc-xgb"
ENDPOINT_NAME     = "eia-foodsvc-xgb-pipeline"
ENDPOINT_INSTANCE = "ml.m5.large"
PIPELINE_NAME     = "eia-foodsvc-xgb-deploy"

iam = boto3.client("iam")
lam = boto3.client("lambda", region_name=REGION)
sm  = boto3.client("sagemaker", region_name=REGION)

# ------------------------------------------------- 1) Lambda execution role
print("[1/5] Lambda execution role ...")
trust = {"Version": "2012-10-17", "Statement": [{
    "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
    "Action": "sts:AssumeRole"}]}
perms = {"Version": "2012-10-17", "Statement": [
    {"Sid": "DeployEndpoint", "Effect": "Allow", "Action": [
        "sagemaker:CreateModel", "sagemaker:DescribeModel",
        "sagemaker:CreateEndpointConfig", "sagemaker:DescribeEndpointConfig",
        "sagemaker:CreateEndpoint", "sagemaker:UpdateEndpoint", "sagemaker:DescribeEndpoint",
        "sagemaker:ListModelPackages", "sagemaker:DescribeModelPackage"], "Resource": "*"},
    {"Sid": "PassExecRole", "Effect": "Allow", "Action": "iam:PassRole", "Resource": SM_EXEC_ROLE}]}
try:
    role_arn = iam.create_role(
        RoleName=LAMBDA_ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(trust),
        Description="Execution role for the EIA deploy Lambda")["Role"]["Arn"]
    print("      created", role_arn)
except iam.exceptions.EntityAlreadyExistsException:
    role_arn = iam.get_role(RoleName=LAMBDA_ROLE_NAME)["Role"]["Arn"]
    print("      exists ", role_arn)
iam.put_role_policy(RoleName=LAMBDA_ROLE_NAME, PolicyName="eia-deploy-permissions",
                    PolicyDocument=json.dumps(perms))
iam.attach_role_policy(RoleName=LAMBDA_ROLE_NAME,
    PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")

# ----------------------------------------------------- 2) Lambda function
print("[2/5] Lambda deploy function ...")
HANDLER = r'''
import time, boto3
from botocore.exceptions import ClientError
sm = boto3.client("sagemaker")

def latest_approved_package(group):
    pkgs = sm.list_model_packages(
        ModelPackageGroupName=group, ModelApprovalStatus="Approved",
        SortBy="CreationTime", SortOrder="Descending", MaxResults=1)["ModelPackageSummaryList"]
    if not pkgs:
        raise RuntimeError(f"No Approved model package in group '{group}'")
    return pkgs[0]["ModelPackageArn"]

def endpoint_exists(name):
    try:
        sm.describe_endpoint(EndpointName=name); return True
    except ClientError:
        return False

def handler(event, context):
    group, endpoint = event["model_package_group"], event["endpoint_name"]
    instance, exec_role = event.get("instance_type", "ml.m5.large"), event["exec_role"]
    pkg = latest_approved_package(group)
    name = f"{endpoint}-{time.strftime('%Y%m%d-%H%M%S')}"
    sm.create_model(ModelName=name, PrimaryContainer={"ModelPackageName": pkg},
                    ExecutionRoleArn=exec_role)
    sm.create_endpoint_config(EndpointConfigName=name, ProductionVariants=[{
        "VariantName": "AllTraffic", "ModelName": name,
        "InstanceType": instance, "InitialInstanceCount": 1}])
    if endpoint_exists(endpoint):
        sm.update_endpoint(EndpointName=endpoint, EndpointConfigName=name); action = "updated"
    else:
        sm.create_endpoint(EndpointName=endpoint, EndpointConfigName=name); action = "created"
    return {"package": pkg, "endpoint": endpoint, "action": action}
'''
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("lambda_function.py", HANDLER)
zip_bytes = buf.getvalue()
try:
    lam.get_function(FunctionName=FUNCTION_NAME)
    fn_arn = lam.update_function_code(FunctionName=FUNCTION_NAME, ZipFile=zip_bytes)["FunctionArn"]
    print("      updated", fn_arn)
except lam.exceptions.ResourceNotFoundException:
    for attempt in range(6):  # a brand-new role takes a few seconds to become assumable
        try:
            fn_arn = lam.create_function(
                FunctionName=FUNCTION_NAME, Runtime="python3.12", Role=role_arn,
                Handler="lambda_function.handler", Code={"ZipFile": zip_bytes},
                Timeout=60, MemorySize=128)["FunctionArn"]
            print("      created", fn_arn); break
        except lam.exceptions.InvalidParameterValueException as e:
            if "cannot be assumed" in str(e) and attempt < 5:
                print("      role not yet assumable, retrying in 10s ..."); time.sleep(10)
            else:
                raise

# ------------------------------------ 3) let the pipeline role invoke it
print("[3/5] InvokeFunction grant for the SageMaker execution role ...")
iam.put_role_policy(RoleName=SM_EXEC_ROLE_NAME, PolicyName="eia-invoke-deploy-lambda",
    PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": fn_arn}]}))
print("      granted (the SageMaker exec role can now trigger this pipeline)")

# --------------------------------- 4) define + upsert the pipeline (SDK v2)
print("[4/5] Pipeline definition + upsert ...")
import sagemaker
_ver = getattr(sagemaker, "__version__", "")
if not _ver.startswith("2"):
    raise SystemExit(
        "This stage needs SageMaker SDK v2. Detected: " + (_ver or "v3 / unknown layout") +
        "  ->  run  pip install \"sagemaker<3\"  and re-run this script.")

from sagemaker.workflow.pipeline import Pipeline
from sagemaker.workflow.parameters import ParameterString
from sagemaker.workflow.lambda_step import LambdaStep
from sagemaker.workflow.pipeline_context import PipelineSession
from sagemaker.lambda_helper import Lambda

p_group    = ParameterString(name="ModelPackageGroup",    default_value=MODEL_GROUP)
p_endpoint = ParameterString(name="EndpointName",         default_value=ENDPOINT_NAME)
p_instance = ParameterString(name="EndpointInstanceType", default_value=ENDPOINT_INSTANCE)

deploy_step = LambdaStep(
    name="DeployApprovedModel",
    lambda_func=Lambda(function_arn=fn_arn),
    inputs={
        "model_package_group": p_group,
        "endpoint_name":       p_endpoint,
        "instance_type":       p_instance,
        "exec_role":           SM_EXEC_ROLE,
    },
)
pipeline = Pipeline(
    name=PIPELINE_NAME,
    parameters=[p_group, p_endpoint, p_instance],
    steps=[deploy_step],
    sagemaker_session=PipelineSession(),
)
pipeline.upsert(role_arn=SM_EXEC_ROLE)
print("      pipeline upserted:", PIPELINE_NAME)

# ------------------------------------------- 5) run once + confirm endpoint
print("[5/5] First deployment run ...")
execution = pipeline.start()
print("      started:", execution.arn)
try:
    execution.wait()
    print("      pipeline status:", execution.describe()["PipelineExecutionStatus"])
except Exception:
    print("      pipeline status:", execution.describe()["PipelineExecutionStatus"])
    for s in execution.list_steps():
        print("       ", s["StepName"], s["StepStatus"], "->", s.get("FailureReason", ""))
    raise

sm.get_waiter("endpoint_in_service").wait(EndpointName=ENDPOINT_NAME)
print("      endpoint:", sm.describe_endpoint(EndpointName=ENDPOINT_NAME)["EndpointStatus"])

print("\nDONE.")
print("  pipeline :", PIPELINE_NAME)
print("  function :", fn_arn)
print("  endpoint :", ENDPOINT_NAME, "(real-time, billing hourly until deleted)")
