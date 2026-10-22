"""HTTP client for the Meanwhile core API (CONTRACT.md); MEANWHILE_CORE_URL selects the core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from meanwhile.voice import config

TIMEOUT_S = 10
WATCH_ORDER_TIMEOUT_S = 30
LOOK_TIMEOUT_S = 30


class CoreError(Exception):
    """The core answered with a status the voice side doesn't expect."""
    pass


@dataclass
class WatchOrderReply:
    """The outcome of POST /api/watch-orders; exactly one field is set."""
    session: dict | None = None
    clarify: str | None = None
    not_found: str | None = None
    already: dict | None = None


class CoreClient:
    """Calls the core's endpoints and returns their JSON bodies."""
    def __init__(self, base_url: str | None = None, key: str | None = None, transport: httpx.BaseTransport | None = None):
        """Connect to base_url (or MEANWHILE_CORE_URL), sending `key` (or MEANWHILE_CORE_KEY)."""
        headers = {"X-Meanwhile-Key": key or config.CORE_KEY}
        self.base_url = (base_url or config.CORE_URL).rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, headers=headers, timeout=TIMEOUT_S, transport=transport)

    def _get(self, path: str, **params: Any) -> dict:
        """GET a path with the non-empty query params; raise CoreError unless 200."""
        reply = self.http.get(path, params={k: v for k, v in params.items() if v is not None})
        if reply.status_code != 200:
            raise CoreError(f"GET {path} -> {reply.status_code}: {reply.text[:200]}")
        return reply.json()

    def start_watch(self, instruction: str) -> WatchOrderReply:
        """Ask the core to start watching for an instruction (compiling and grounding can take several seconds)."""
        reply = self.http.post("/api/watch-orders", json={"instruction": instruction}, timeout=WATCH_ORDER_TIMEOUT_S)
        body = reply.json()
        if reply.status_code == 201:
            return WatchOrderReply(session=body["session"])
        if reply.status_code == 200 and body.get("clarify"):
            return WatchOrderReply(clarify=body["clarify"])
        if reply.status_code == 422:
            return WatchOrderReply(not_found=body["message"])
        if reply.status_code == 409:
            return WatchOrderReply(already=body.get("session") or {})
        raise CoreError(f"POST /api/watch-orders -> {reply.status_code}: {reply.text[:200]}")

    def latest_session(self) -> tuple[dict | None, bool]:
        """Return (the active session or else the most recent one, whether it is active)."""
        body = self._get("/api/sessions/latest")
        return body["session"], body["active"]

    def events(self, session_id: str, since: str | None = None) -> list[dict]:
        """A session's events in time order, only those after `since` if given."""
        return self._get(f"/api/sessions/{session_id}/events", since=since)["events"]

    def end_session(self, session_id: str) -> dict | None:
        """End a session; None if it had already ended."""
        reply = self.http.post(f"/api/sessions/{session_id}/end")
        if reply.status_code == 409:
            return None
        if reply.status_code != 200:
            raise CoreError(f"POST /api/sessions/{session_id}/end -> {reply.status_code}: {reply.text[:200]}")
        return reply.json()["session"]

    def caught_up(self, session_id: str, until: str) -> dict:
        """Tell the core the user has been told the session's events up to `until`; returns the session."""
        reply = self.http.post(f"/api/sessions/{session_id}/caught-up", json={"until": until})
        if reply.status_code != 200:
            raise CoreError(f"POST /api/sessions/{session_id}/caught-up -> {reply.status_code}: {reply.text[:200]}")
        return reply.json()["session"]

    def cameras(self) -> list[dict]:
        """All cameras, with `online` computed by the core."""
        return self._get("/api/cameras")["cameras"]

    def look(self, question: str, camera: str | None = None) -> tuple[int, dict]:
        """Ask the core what a camera (or every online camera) sees now (face ID and the vision model take a few
        seconds); returns (status, JSON body): 200 with the answer, or a 400/404/409/503 error with a spoken message."""
        body = {"question": question, **({"camera": camera} if camera else {})}
        reply = self.http.post("/api/look", json=body, timeout=LOOK_TIMEOUT_S)
        if reply.status_code not in (200, 400, 404, 409, 503):
            raise CoreError(f"POST /api/look -> {reply.status_code}: {reply.text[:200]}")
        return reply.status_code, reply.json()

    def look_back(self, question: str, camera: str | None = None) -> tuple[int, dict]:
        """Ask the core what happened in the cameras' recent window (face ID and the video model take a few
        seconds); returns (status, JSON body): 200 with the answer, or a 400/404/409/503 error with a spoken message."""
        body = {"question": question, **({"camera": camera} if camera else {})}
        reply = self.http.post("/api/look-back", json=body, timeout=LOOK_TIMEOUT_S)
        if reply.status_code not in (200, 400, 404, 409, 503):
            raise CoreError(f"POST /api/look-back -> {reply.status_code}: {reply.text[:200]}")
        return reply.status_code, reply.json()

    def state_of(self, question: str, thing: str, camera: str | None = None) -> tuple[int, dict]:
        """Ask the core how a thing is: its last recorded state, or the video's answer when there is none or it is
        unclear (a few seconds then); returns (status, JSON body), 200 or a 400/404/409/503 error with a spoken
        message."""
        body = {"question": question, "thing": thing, **({"camera": camera} if camera else {})}
        reply = self.http.post("/api/state-of", json=body, timeout=LOOK_TIMEOUT_S)
        if reply.status_code not in (200, 400, 404, 409, 503):
            raise CoreError(f"POST /api/state-of -> {reply.status_code}: {reply.text[:200]}")
        return reply.status_code, reply.json()

    def recent_window_s(self) -> float:
        """How many seconds back the core's look-back can see (from GET /api/status)."""
        return float(self._get("/api/status")["recent_window_s"])

    def pairing(self, method: str, path: str, cookie: str | None = None) -> tuple[int, dict]:
        """A device-pairing call under /api (pairing-codes, paired-devices), passed on with the browser's cookie so the
        core can mark the caller's own device; returns (status, JSON body) whatever the status."""
        reply = self.http.request(method, f"/api/{path}", headers={"Cookie": cookie} if cookie else None)
        return reply.status_code, reply.json()
