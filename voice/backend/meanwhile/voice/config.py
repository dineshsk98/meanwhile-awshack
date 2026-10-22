"""The voice side's settings, read from the environment and voice/.env. Every name must be set (values
and their meaning are in .env.example)."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")


def _need(name: str) -> str:
    """A required setting's value; stops with a message naming it when it is missing or empty."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set. Add it to voice/.env (see .env.example).")
    return value


OLLAMA_URL = _need("OLLAMA_URL")
ALEXA_SIM_MODEL = _need("ALEXA_SIM_MODEL")
CORE_URL = _need("MEANWHILE_CORE_URL")
# The core's MEANWHILE_KEY, sent with every call
CORE_KEY = _need("MEANWHILE_CORE_KEY")
# The voice dev server's port
PORT = int(_need("MEANWHILE_VOICE_PORT"))
# The home's time zone (an IANA name): spoken times use it, not the server's own zone
TIME_ZONE = ZoneInfo(_need("MEANWHILE_TIME_ZONE"))
