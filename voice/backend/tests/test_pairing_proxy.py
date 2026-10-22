"""The Alexa panel's "Pair a device" calls: /voice/pairing-codes and /voice/paired-devices pass on to the core's /api."""

from __future__ import annotations

import httpx
from starlette.testclient import TestClient

from meanwhile.voice import devserver
from meanwhile.voice.core_client import CoreClient

FAKE_KEY = "test-key-not-real"


def _app(seen: list[httpx.Request], down: bool = False):
    """The devserver over a fake core that records each request and answers like the core's pairing API."""
    def core(request: httpx.Request) -> httpx.Response:
        """Answer one request as the core would."""
        if down:
            raise httpx.ConnectError("refused", request=request)
        seen.append(request)
        if request.url.path == "/api/pairing-codes":
            return httpx.Response(201, json={"code": "c", "pair_url": "https://h/pair?code=c", "expires_in_s": 120})
        if request.method == "DELETE":
            return httpx.Response(404, json={"error": "not_found", "message": "none"})
        return httpx.Response(200, json={"devices": []})
    client = CoreClient("http://core.test", key=FAKE_KEY, transport=httpx.MockTransport(core))
    return devserver.http_app(core=client, alexa=object())


def test_pairing_calls_reach_the_core_with_the_key_and_cookie():
    """Each call goes to the same /api path with the voice side's key and the browser's cookie; status passes through."""
    seen: list[httpx.Request] = []
    with TestClient(_app(seen)) as client:
        made = client.post("/voice/pairing-codes")
        listed = client.get("/voice/paired-devices", headers={"Cookie": "mw_key=browser-token"})
        missing = client.delete("/voice/paired-devices/dev_x")
    assert made.status_code == 201 and made.json()["pair_url"].endswith("code=c")
    assert listed.json() == {"devices": []}
    assert missing.status_code == 404 and missing.json()["error"] == "not_found"
    assert [(r.method, r.url.path) for r in seen] == [("POST", "/api/pairing-codes"), ("GET", "/api/paired-devices"),
                                                      ("DELETE", "/api/paired-devices/dev_x")]
    assert all(r.headers["x-meanwhile-key"] == FAKE_KEY for r in seen)
    assert seen[1].headers["cookie"] == "mw_key=browser-token"


def test_pairing_with_the_core_down_is_a_503():
    """With the core unreachable, the panel gets 503 core_unreachable."""
    with TestClient(_app([], down=True)) as client:
        reply = client.post("/voice/pairing-codes")
    assert reply.status_code == 503 and reply.json()["error"] == "core_unreachable"


def test_panel_page_has_the_pairing_screen():
    """The home screen carries the Pair a device button, its dialog and the widget script."""
    with TestClient(_app([])) as client:
        page = client.get("/").text
        assert client.get("/js/pairing.js").status_code == 200
    assert 'id="pair-open"' in page and 'id="pair-dialog"' in page and "js/pairing.js" in page
