"""Push to the phone: registered Firebase device tokens, and FCM HTTP v1 sends for heads-up, verdict and alert events.
Off (a no-op) unless a service-account key file is configured.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from meanwhile import config
from meanwhile.contract import Event
from meanwhile.store import Store, now

PUSH_ALERTS = ("heads_up", "verdict", "alert")
TITLES = {"heads_up": "Heads-up", "verdict": "Verdict", "alert": "Alert"}
MAX_DEVICES = 20
MAX_TOKEN = 4096


def register_device(store: Store, token: str) -> int:
    """Remember a device's FCM token (newest last, at most MAX_DEVICES); returns how many devices are registered."""
    devices = [d for d in store.get_meta("devices", []) if d["fcm_token"] != token]
    devices = (devices + [{"fcm_token": token, "registered_at": now()}])[-MAX_DEVICES:]
    store.put_meta("devices", devices)
    return len(devices)


def device_tokens(store: Store) -> list[str]:
    """The registered FCM tokens."""
    return [d["fcm_token"] for d in store.get_meta("devices", [])]


def drop_device(store: Store, token: str) -> None:
    """Forget a token FCM says is no longer valid."""
    store.put_meta("devices", [d for d in store.get_meta("devices", []) if d["fcm_token"] != token])


def b64url(data: bytes) -> str:
    """URL-safe base64 without padding, as JWTs use."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def message(store: Store, event: Event, token: str) -> dict[str, Any]:
    """The FCM v1 message for one event: a visible notification plus the data the app reads."""
    data = {"event_id": event["event_id"], "summary": event.get("summary", ""), "alert": event["alert"],
            "frame_url": store.media_url(event.get("frame_key")) or "", "session_id": event["session_id"]}
    return {"message": {"token": token,
                        "notification": {"title": TITLES[event["alert"]], "body": data["summary"]},
                        "data": data,
                        "android": {"priority": "HIGH", "notification": {"channel_id": event["alert"]}}}}


class Push:

    """Sends events to registered phones through FCM HTTP v1, authenticating with a service-account key file."""
    def __init__(self, store: Store, service_account: dict[str, Any], client: httpx.Client | None = None):
        """Set up with the parsed service-account JSON; `client` is replaceable for tests."""
        self.store, self.account = store, service_account
        self.client = client or httpx.Client(timeout=10)
        self.project = service_account["project_id"]
        self.token: str | None = None
        self.token_expires = 0.0
        self.last: dict[str, Any] = {}

    def access_token(self) -> str:
        """An OAuth access token for FCM, from a signed service-account JWT; cached until shortly before it expires."""
        if self.token and time.time() < self.token_expires - 60:
            return self.token
        issued = int(time.time())
        claims = {"iss": self.account["client_email"], "scope": config.FCM_SCOPE, "aud": self.account["token_uri"],
                  "iat": issued, "exp": issued + 3600}
        header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        signing = header + "." + b64url(json.dumps(claims).encode())
        key = serialization.load_pem_private_key(self.account["private_key"].encode(), password=None)
        signature = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
        reply = self.client.post(self.account["token_uri"], data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": signing + "." + b64url(signature)})
        reply.raise_for_status()
        body = reply.json()
        self.token, self.token_expires = body["access_token"], time.time() + int(body.get("expires_in", 3600))
        return self.token

    def send_event(self, event: Event) -> int:
        """Send one event to every registered device now; returns how many accepted it. Dead tokens are dropped."""
        tokens = device_tokens(self.store)
        if event.get("alert") not in PUSH_ALERTS or not tokens:
            return 0
        started, sent = time.perf_counter(), 0
        headers = {"Authorization": f"Bearer {self.access_token()}"}
        for token in tokens:
            reply = self.client.post(config.FCM_SEND_URL.format(project=self.project), headers=headers,
                                     json=message(self.store, event, token))
            if reply.status_code == 200:
                sent += 1
            elif reply.status_code == 404:
                drop_device(self.store, token)
        self.last = {"event_id": event["event_id"], "sent": sent, "ms": round((time.perf_counter() - started) * 1000)}
        return sent

    def notify(self, event: Event) -> None:
        """Send in a background thread, so the analyser never waits on the network; failures are printed."""
        if event.get("alert") not in PUSH_ALERTS:
            return

        def run() -> None:
            """Send, reporting any error."""
            try:
                self.send_event(event)
            except Exception as exc:
                print(f"Push failed: {exc!r}", flush=True)
        threading.Thread(target=run, daemon=True, name="push").start()


def from_config(store: Store, service_account_path: str) -> Push | None:
    """A Push for the key file at that path, or None (push off) when no path is configured or the file is missing."""
    if not service_account_path or not Path(service_account_path).is_file():
        return None
    return Push(store, json.loads(Path(service_account_path).read_text(encoding="utf-8")))
