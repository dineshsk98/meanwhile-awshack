"""The access gate: every /api and /media request must carry the shared key or a paired device's token, in the
X-Meanwhile-Key header or the pairing cookie; a /media request may instead carry a valid signed link. There is no
open mode: the gate refuses to be built without a key. Pairing itself (POST /api/pair) is the one open /api route.
Clients that present too many different wrong credentials are refused for a while."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import defaultdict
from typing import Callable
from urllib.parse import unquote, urlsplit

from meanwhile.store import Store, now

HEADER = b"x-meanwhile-key"
COOKIE = "mw_key"
OPEN_ROUTES = {("POST", "/api/pair")}
CHECK_PATH = "/api/auth/check"
SEEN_EVERY_S = 60
LOOPBACK = {"127.0.0.1", "::1"}
BODY = json.dumps({"error": "unauthorized",
                   "message": "Pair this device by scanning a pairing QR code, or send the key in X-Meanwhile-Key."}).encode()


def token_hash(secret: str) -> str:
    """The stored form of a pairing code or device token (SHA-256 hex; both are long random strings)."""
    return hashlib.sha256(secret.encode()).hexdigest()


def presented(headers: list[tuple[bytes, bytes]]) -> list[str]:
    """The credentials a request carries: the X-Meanwhile-Key header first, then every pairing cookie (a browser can
    hold two, one for its host and one for the whole domain)."""
    found = []
    for name, value in headers:
        name = name.lower()
        if name == HEADER and value:
            found.insert(0, value.decode("latin-1"))
        elif name == b"cookie":
            pairs = (part.strip().partition("=") for part in value.decode("latin-1").split(";"))
            found += [v.strip() for k, _, v in pairs if k == COOKIE and v.strip()]
    return found


def header(scope, name: bytes) -> str:
    """One request header's value from an ASGI scope, or ''."""
    return next((v.decode("latin-1") for k, v in scope.get("headers", []) if k.lower() == name), "")


def client_address(scope) -> str:
    """The client's address: the peer, or, when the peer is this machine (Caddy on the box), the last address in
    X-Forwarded-For, which Caddy sets."""
    peer = (scope.get("client") or ("", 0))[0]
    forwarded = header(scope, b"x-forwarded-for")
    if peer in LOOPBACK and forwarded:
        return forwarded.split(",")[-1].strip()
    return peer


class FailedAuth:

    """Counts the different wrong credentials each client presents; at `tries` of them within `window_s` seconds the
    client is refused until the oldest one leaves the window. Repeating one stale token counts once."""
    def __init__(self, tries: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        """Allow `tries` different wrong credentials per `window_s`, timed with `clock`."""
        self.tries, self.window_s, self.clock = tries, window_s, clock
        self._failed: dict[str, dict[str, float]] = defaultdict(dict)

    def _recent(self, client: str) -> dict[str, float]:
        """The client's wrong credentials (as hashes) still inside the window, with when each was last presented."""
        cutoff = self.clock() - self.window_s
        failed = {k: at for k, at in self._failed.get(client, {}).items() if at > cutoff}
        if failed:
            self._failed[client] = failed
        else:
            self._failed.pop(client, None)
        return failed

    def blocked_s(self, client: str) -> float:
        """Seconds until the client may try again, or 0 when it isn't blocked."""
        failed = self._recent(client)
        if len(failed) < self.tries:
            return 0.0
        return max(0.0, min(failed.values()) + self.window_s - self.clock())

    def failed(self, client: str, credentials: list[str]) -> None:
        """Note the wrong credentials a refused request presented (none: nothing was guessed, nothing is counted)."""
        at = self.clock()
        for secret in credentials:
            self._failed[client][token_hash(secret)] = at


class Access:

    """Decides who a request comes from: the shared key, a paired device, or nobody."""
    def __init__(self, store: Store, key: str):
        """Check against `key` and the store's paired devices; an empty key is refused (ValueError)."""
        if not key:
            raise ValueError("The access gate needs a key: set MEANWHILE_KEY.")
        self.store, self.key = store, key
        self._seen: dict[str, float] = {}

    def device_for(self, secret: str) -> dict | None:
        """The paired device a token belongs to, or None; notes it as seen (written at most once a minute)."""
        device = self.store.paired_device_by_token(token_hash(secret))
        if device:
            at = time.monotonic()
            if at - self._seen.get(device["device_id"], -SEEN_EVERY_S) >= SEEN_EVERY_S:
                self._seen[device["device_id"]] = at
                self.store.touch_paired_device(device["device_id"], now())
        return device

    def allowed(self, headers: list[tuple[bytes, bytes]]) -> bool:
        """Whether a request carries the key or a paired device's token (the key is compared in constant time)."""
        for secret in presented(headers):
            if hmac.compare_digest(secret.encode(), self.key.encode()) or self.device_for(secret):
                return True
        return False

    def signed(self, scope) -> bool:
        """Whether the request is for a media file with a valid signed link: itself, or, for the gate check a reverse
        proxy asks, the original request named in X-Forwarded-Uri."""
        path, query = scope["path"], scope.get("query_string", b"").decode("latin-1")
        if path == CHECK_PATH:
            original = urlsplit(header(scope, b"x-forwarded-uri"))
            path, query = unquote(original.path), original.query
        return self.store.links.valid(path, query)

    def devices_of(self, headers: list[tuple[bytes, bytes]]) -> set[str]:
        """The ids of the paired devices whose tokens a request carries."""
        return {d["device_id"] for s in presented(headers)
                if (d := self.store.paired_device_by_token(token_hash(s)))}


class KeyGate:

    """ASGI middleware that answers 401 to /api and /media requests without the key, a device token or (for media) a
    signed link, and 429 to a client that has presented too many different wrong credentials."""
    def __init__(self, app, access: Access, failures: FailedAuth):
        """Wrap `app`, letting through what `access` allows and refusing the clients `failures` has blocked."""
        self.app, self.access, self.failures = app, access, failures

    @staticmethod
    def guarded(scope) -> bool:
        """Whether a request needs credentials: every /api route except the open ones, and every /media file."""
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            return False
        path = scope["path"]
        return (path.startswith("/api") and (scope["method"], path) not in OPEN_ROUTES) or path.startswith("/media/")

    async def __call__(self, scope, receive, send):
        """Pass the request on, or refuse it (401 without valid credentials, 429 while its client is blocked)."""
        if not self.guarded(scope) or self.access.signed(scope):
            await self.app(scope, receive, send)
            return
        client = client_address(scope)
        if wait := self.failures.blocked_s(client):
            body = json.dumps({"error": "too_many_tries", "retry_after_s": round(wait, 1),
                               "message": "Too many wrong keys from this address. Try again in a minute."}).encode()
            await self._refuse(send, 429, body, [(b"retry-after", str(int(wait) + 1).encode())])
            return
        if self.access.allowed(scope["headers"]):
            await self.app(scope, receive, send)
            return
        self.failures.failed(client, presented(scope["headers"]))
        await self._refuse(send, 401, BODY)

    @staticmethod
    async def _refuse(send, status: int, body: bytes, extra: list[tuple[bytes, bytes]] | None = None) -> None:
        """Answer with a JSON error body."""
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()), *(extra or [])]})
        await send({"type": "http.response.body", "body": body})
