"""Device pairing: one-time codes shown as QR codes, exchanged for per-device tokens that open the API like the key."""

from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

import segno

from meanwhile.api import Response, error
from meanwhile.api.auth import token_hash
from meanwhile.store import Store, new_id, now, to_iso

CODE_BYTES = 16
TOKEN_BYTES = 32
MAX_NAME = 60
KINDS = ("app", "browser")


class RateLimit:

    """At most `tries` attempts per client in any `window_s` seconds."""
    def __init__(self, tries: int, window_s: float, clock=time.monotonic):
        """Allow `tries` per `window_s`, timed with `clock`."""
        self.tries, self.window_s, self.clock = tries, window_s, clock
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def wait_s(self, client: str) -> float:
        """Count one attempt by `client`: 0 if it is allowed, else the seconds until the next one would be."""
        at, attempts = self.clock(), self._attempts[client]
        while attempts and attempts[0] <= at - self.window_s:
            attempts.popleft()
        if len(attempts) >= self.tries:
            return attempts[0] + self.window_s - at
        attempts.append(at)
        return 0.0


def qr_svg(text: str) -> str:
    """An inline SVG QR code for `text` (dark on white, with a quiet zone, so it scans from a dark page), sized by
    its viewBox so the page sets its width."""
    return segno.make(text, error="m").svg_inline(border=4, dark="#000", light="#fff", omitsize=True)


def create_code(store: Store, base_url: str, ttl_s: float) -> Response:
    """POST /api/pairing-codes: a one-time code valid for ttl_s; 201 {"code", "pair_url", "expires_at",
    "expires_in_s", "qr_svg"} (pair_url is base_url/pair?code=..., the QR code holds pair_url)."""
    code = secrets.token_urlsafe(CODE_BYTES)
    expires = to_iso(datetime.now(timezone.utc) + timedelta(seconds=ttl_s))
    store.add_pairing_code(token_hash(code), expires)
    pair_url = f"{base_url}/pair?code={code}"
    return 201, {"code": code, "pair_url": pair_url, "expires_at": expires, "expires_in_s": round(ttl_s),
                 "qr_svg": qr_svg(pair_url)}


def unique_name(store: Store, name: str) -> str:
    """The name, with " (2)", " (3)"… added when another paired device already has it (ignoring case)."""
    taken = {d["name"].lower() for d in store.list_paired_devices()}
    candidate, n = name, 1
    while candidate.lower() in taken:
        n += 1
        candidate = f"{name} ({n})"
    return candidate


def pair(store: Store, body: dict[str, Any], limit: RateLimit, client: str,
         replaces: set[str] = frozenset()) -> tuple[Response, str | None]:
    """POST /api/pair {"code", "device_name", "browser"?}: spend a code and pair a new device. `replaces` are the
    paired devices whose tokens the request carries (a browser's old cookie, the app's old token): pairing again from
    the same device replaces them rather than adding a second entry. Names are kept unique. Returns the response
    (201 {"device_id", "id", "name", "kind", "device_token"}) and the new token (None on failure)."""
    if wait := limit.wait_s(client):
        return error(429, "too_many_tries", "Too many pairing attempts; wait a moment and try again.",
                     retry_after_s=round(wait, 1)), None
    code, name = body.get("code"), body.get("device_name")
    if not isinstance(code, str) or not code.strip():
        return error(400, "bad_code", "Send the pairing code from the QR code as code."), None
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_NAME:
        return error(400, "bad_name", f"Send a device_name of 1-{MAX_NAME} characters."), None
    if not store.use_pairing_code(token_hash(code.strip()), now()):
        return error(403, "invalid_code", "That pairing code is wrong, used or expired. Show a new QR code."), None
    for device_id in replaces:
        store.remove_paired_device(device_id)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    device = store.add_paired_device({"device_id": new_id("dev"), "name": unique_name(store, name.strip()),
                                      "kind": KINDS[bool(body.get("browser"))], "created": now(), "last_seen": now()},
                                     token_hash(token))
    return (201, {**shown(device, set()), "device_token": token}), token


def shown(device: dict[str, Any], current: set[str]) -> dict[str, Any]:
    """A paired device as the API shows it: with `id`, and `current` when it is the caller."""
    return {**device, "id": device["device_id"], "current": device["device_id"] in current}


def list_devices(store: Store, current: set[str]) -> Response:
    """GET /api/paired-devices: {"devices": [...]} oldest first; `current` marks the caller's own device."""
    return 200, {"devices": [shown(d, current) for d in store.list_paired_devices()]}


def revoke(store: Store, device_id: str) -> Response:
    """DELETE /api/paired-devices/{id}: its token stops working at once; 404 if there is no such device."""
    if not store.remove_paired_device(device_id):
        return error(404, "not_found", "There is no paired device with that id.")
    return 200, {"revoked": True, "device_id": device_id}
