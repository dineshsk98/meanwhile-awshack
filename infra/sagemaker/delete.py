"""Delete the vision endpoint and its endpoint config (billing stops), keeping the model and the role so
deploy.py can re-create it, then confirm with describe calls. Usage: python delete.py"""

from __future__ import annotations

import sys
import time

from botocore.exceptions import ClientError

import common


def gone(call, **kwargs) -> bool:
    """True if the describe call says the resource doesn't exist."""
    try:
        call(**kwargs)
        return False
    except ClientError as exc:
        if "Could not find" in str(exc) or exc.response["Error"]["Code"] in ("ValidationException", "ResourceNotFound"):
            return True
        raise


def main() -> int:
    """Delete the endpoint and config, wait until both are gone, and report."""
    sm = common.session().client("sagemaker")
    for name, call in (("endpoint", sm.delete_endpoint), ("endpoint config", sm.delete_endpoint_config)):
        try:
            call(**({"EndpointName": common.NAME} if name == "endpoint" else {"EndpointConfigName": common.NAME}))
            print(f"deleting {name} {common.NAME}")
        except ClientError as exc:
            print(f"{name}: {exc.response['Error']['Message']}")
    for _ in range(60):
        if gone(sm.describe_endpoint, EndpointName=common.NAME):
            break
        time.sleep(10)
    endpoint_gone = gone(sm.describe_endpoint, EndpointName=common.NAME)
    config_gone = gone(sm.describe_endpoint_config, EndpointConfigName=common.NAME)
    print(f"describe_endpoint: {'not found (deleted)' if endpoint_gone else 'STILL EXISTS'}")
    print(f"describe_endpoint_config: {'not found (deleted)' if config_gone else 'STILL EXISTS'}")
    print(f"in service now: {[e['EndpointName'] for e in sm.list_endpoints()['Endpoints']]}")
    return 0 if endpoint_gone and config_gone else 1


if __name__ == "__main__":
    sys.exit(main())
