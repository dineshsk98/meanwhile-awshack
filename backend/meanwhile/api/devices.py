"""Device registration for push: the phone sends its Firebase token so the core can notify it."""

from __future__ import annotations

from typing import Any

from meanwhile import push
from meanwhile.api import Response, error
from meanwhile.store import Store


def register(store: Store, body: dict[str, Any]) -> Response:
    """POST /api/devices {"fcm_token"}: remember the phone; 200 {"registered": true, "devices": n}."""
    token = body.get("fcm_token")
    if not isinstance(token, str) or not token.strip() or len(token) > push.MAX_TOKEN or " " in token.strip():
        return error(400, "bad_token", "Send the phone's Firebase token as fcm_token.")
    return 200, {"registered": True, "devices": push.register_device(store, token.strip())}
