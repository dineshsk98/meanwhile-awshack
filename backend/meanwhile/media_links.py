"""Signed, expiring media links: /media/<key>?exp=<unix seconds>&sig=<HMAC>, so a link opens one media file without
the key or a device token, only until it expires and only for its own path."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Callable
from urllib.parse import parse_qs

PREFIX = "/media/"
PURPOSE = b"meanwhile media links"


def secret_from(key: str) -> bytes:
    """The signing secret derived from the shared key (HMAC-SHA256 of a fixed label), so links need no extra secret
    and changing the key ends every link."""
    return hmac.new(key.encode(), PURPOSE, hashlib.sha256).digest()


class MediaLinks:

    """Signs and checks media links. A link lasts between ttl_s and 1.5 x ttl_s, and the same path gets the same link
    within each half-ttl_s window, so pages and apps can cache what they show."""
    def __init__(self, secret: bytes, ttl_s: float, clock: Callable[[], float] = time.time):
        """Sign with `secret`; links last at least `ttl_s` seconds, timed with `clock` (unix seconds)."""
        if not secret:
            raise ValueError("Media links need a secret.")
        self.secret, self.ttl_s, self.clock = secret, max(1, int(ttl_s)), clock

    def _sig(self, path: str, exp: int) -> str:
        """The signature over one path and expiry (URL-safe base64, no padding)."""
        digest = hmac.new(self.secret, f"{path}\n{exp}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def sign(self, path: str) -> str:
        """`path` (a /media/ path) with its expiry and signature appended."""
        step = max(1, self.ttl_s // 2)
        exp = int(self.clock()) // step * step + self.ttl_s + step
        return f"{path}?exp={exp}&sig={self._sig(path, exp)}"

    def valid(self, path: str, query: str) -> bool:
        """Whether a request for `path` with this query string carries an unexpired signature for exactly that path."""
        if not path.startswith(PREFIX):
            return False
        fields = parse_qs(query)
        exp, sig = (fields.get("exp") or [""])[0], (fields.get("sig") or [""])[0]
        if not (exp.isdigit() and sig) or int(exp) < self.clock():
            return False
        return hmac.compare_digest(sig.encode(), self._sig(path, int(exp)).encode())
