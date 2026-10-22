"""Shared settings for the SageMaker vision endpoint scripts, all read from the environment (.env at the repo root):
names, the container image, the model, the container's LMI options and the AWS session."""

from __future__ import annotations

import os
from pathlib import Path

import boto3
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

OPTION_PREFIX = "MEANWHILE_SM_OPT_"


def _need(name: str) -> str:
    """A required setting's value; stops with a message naming it when it is missing or empty."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is not set. Add it to .env at the repo root (see .env.example).")
    return value


PROFILE = _need("MEANWHILE_SM_AWS_PROFILE")
REGION = _need("MEANWHILE_SM_AWS_REGION")
NAME = _need("MEANWHILE_SM_NAME")
ROLE_NAME = _need("MEANWHILE_SM_ROLE")
INSTANCE_TYPE = _need("MEANWHILE_SM_INSTANCE")
HF_MODEL_ID = _need("MEANWHILE_SM_HF_MODEL")
DLC_ACCOUNT = _need("MEANWHILE_SM_DLC_ACCOUNT")
LMI_TAG = _need("MEANWHILE_SM_LMI_TAG")
STARTUP_TIMEOUT_S = int(_need("MEANWHILE_SM_STARTUP_TIMEOUT_S"))


def container_env() -> dict[str, str]:
    """The container's settings: the model id plus every MEANWHILE_SM_OPT_<NAME> as OPTION_<NAME>."""
    options = {f"OPTION_{key[len(OPTION_PREFIX):]}": value for key, value in os.environ.items()
               if key.startswith(OPTION_PREFIX)}
    if not options:
        raise SystemExit(f"No {OPTION_PREFIX}* settings found. Add them to .env at the repo root (see .env.example).")
    return {"HF_MODEL_ID": HF_MODEL_ID, **options}


def session() -> boto3.Session:
    """A boto3 session on the configured profile and region (never the default profile)."""
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def image_uri() -> str:
    """The LMI (DJL + vLLM) container image in this region."""
    return f"{DLC_ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/djl-inference:{LMI_TAG}"
