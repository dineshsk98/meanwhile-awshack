"""The core's HTTP API handlers: plain functions that take the store and return (status, JSON body)."""

from __future__ import annotations

from typing import Any

Response = tuple[int, dict[str, Any]]


def error(status: int, code: str, message: str, **extra: Any) -> Response:
    """A contract error response: {"error": code, "message": one sentence}."""
    return status, {"error": code, "message": message, **extra}
