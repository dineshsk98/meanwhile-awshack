"""Create the SageMaker real-time endpoint that serves the vision model, wait until it is InService and print
its name. Re-uses the execution role and the SageMaker model if they already exist.
Usage: python deploy.py [--recreate-model]"""

from __future__ import annotations

import json
import sys
import time

from botocore.exceptions import ClientError

import common

TRUST = {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "sagemaker.amazonaws.com"},
                   "Action": "sts:AssumeRole"}],
}
POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        {"Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
                                       "ecr:BatchCheckLayerAvailability"],
         "Resource": f"arn:aws:ecr:*:{common.DLC_ACCOUNT}:repository/*"},
        {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                                       "logs:DescribeLogStreams"],
         "Resource": "arn:aws:logs:*:*:log-group:/aws/sagemaker/*"},
        {"Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*"},
    ],
}


def ensure_role(iam) -> str:
    """The execution role's ARN, creating the role with only ECR pull and logging rights if it is missing."""
    try:
        return iam.get_role(RoleName=common.ROLE_NAME)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        pass
    arn = iam.create_role(RoleName=common.ROLE_NAME, AssumeRolePolicyDocument=json.dumps(TRUST),
                          Description="Execution role for the Meanwhile vision endpoint")["Role"]["Arn"]
    iam.put_role_policy(RoleName=common.ROLE_NAME, PolicyName="pull-image-and-log", PolicyDocument=json.dumps(POLICY))
    print(f"created role {common.ROLE_NAME}; waiting for it to propagate")
    time.sleep(15)
    return arn


def ensure_model(sm, role_arn: str, recreate: bool) -> None:
    """Create the SageMaker model (container image plus settings) unless it already exists."""
    try:
        sm.describe_model(ModelName=common.NAME)
        if not recreate:
            return
        sm.delete_model(ModelName=common.NAME)
    except ClientError:
        pass
    sm.create_model(ModelName=common.NAME, ExecutionRoleArn=role_arn,
                    PrimaryContainer={"Image": common.image_uri(), "Environment": common.container_env()})
    print(f"created model {common.NAME} ({common.HF_MODEL_ID} on {common.image_uri()})")


def create_endpoint(sm) -> None:
    """Create a fresh endpoint config and the endpoint on one GPU instance."""
    try:
        sm.delete_endpoint_config(EndpointConfigName=common.NAME)
    except ClientError:
        pass
    sm.create_endpoint_config(EndpointConfigName=common.NAME, ProductionVariants=[{
        "VariantName": "main", "ModelName": common.NAME, "InstanceType": common.INSTANCE_TYPE,
        "InitialInstanceCount": 1, "ContainerStartupHealthCheckTimeoutInSeconds": common.STARTUP_TIMEOUT_S,
    }])
    sm.create_endpoint(EndpointName=common.NAME, EndpointConfigName=common.NAME)


def wait(sm, started: float) -> str:
    """Poll the endpoint until it leaves Creating; return the final status."""
    last = ""
    while True:
        info = sm.describe_endpoint(EndpointName=common.NAME)
        status = info["EndpointStatus"]
        if status != last:
            print(f"{time.time() - started:6.0f} s  {status}", flush=True)
            last = status
        if status not in ("Creating", "Updating"):
            if status != "InService":
                print(info.get("FailureReason", ""))
            return status
        time.sleep(20)


def main() -> int:
    """Create everything needed and wait for the endpoint."""
    aws = common.session()
    sm = aws.client("sagemaker")
    try:
        status = sm.describe_endpoint(EndpointName=common.NAME)["EndpointStatus"]
        print(f"endpoint {common.NAME} already exists ({status}); run delete.py first")
        return 1
    except ClientError:
        pass
    role_arn = ensure_role(aws.client("iam"))
    ensure_model(sm, role_arn, "--recreate-model" in sys.argv)
    started = time.time()
    create_endpoint(sm)
    status = wait(sm, started)
    print(f"cold start: {time.time() - started:.0f} s ({common.INSTANCE_TYPE})")
    if status != "InService":
        print("endpoint failed; run delete.py to clean up")
        return 1
    print(f"endpoint: {common.NAME}")
    print(f"set MEANWHILE_VISION_MODEL=sagemaker:{common.NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
