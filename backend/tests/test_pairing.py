"""Device pairing: one-time codes (expiry, single use, stored hashed), the rate-limited exchange, browser cookies,
listing and revoking devices, and the gate with the key, a device token or the cookie."""

from __future__ import annotations

import sqlite3
import time

import pytest
from starlette.testclient import TestClient

from local.devserver import build_app
from meanwhile import config
from meanwhile.api import auth, pairing

FAKE_KEY = "test-key-not-real"
KEY = {"X-Meanwhile-Key": FAKE_KEY}


@pytest.fixture
def client(store):
    """A test client for a core with a (fake) key set."""
    return TestClient(build_app(store, api_key=FAKE_KEY))


def new_code(client) -> dict:
    """Make a pairing code with the key; return the response body."""
    reply = client.post("/api/pairing-codes", headers=KEY)
    assert reply.status_code == 201, reply.text
    return reply.json()


def pair(client, code: str, name: str = "Test phone", **extra):
    """Exchange a code for a device; return the response."""
    return client.post("/api/pair", json={"code": code, "device_name": name, **extra})


def test_code_shape_and_link(client, monkeypatch):
    """A code comes with its pair link (the request's address, or MEANWHILE_PUBLIC_URL), expiry and a QR code."""
    body = new_code(client)
    assert body["pair_url"] == f"http://testserver/pair?code={body['code']}"
    assert body["expires_in_s"] == config.PAIR_CODE_TTL_S and body["expires_at"].endswith("Z")
    assert isinstance(body["expires_in_s"], int), "the contract gives whole seconds (the app decodes it strictly)"
    assert body["qr_svg"].startswith("<svg") and "viewBox" in body["qr_svg"]
    assert len(body["code"]) >= 20
    monkeypatch.setattr(config, "PUBLIC_URL", "https://box.example.test")
    assert new_code(client)["pair_url"].startswith("https://box.example.test/pair?code=")


def test_making_a_code_needs_the_key_or_a_device(client):
    """Without credentials, no code; a paired device may make one."""
    assert client.post("/api/pairing-codes").status_code == 401
    token = pair(client, new_code(client)["code"]).json()["device_token"]
    assert client.post("/api/pairing-codes", headers={"X-Meanwhile-Key": token}).status_code == 201


def test_codes_and_tokens_are_stored_hashed(client, store):
    """Neither the code nor the device token appears in the database."""
    code = new_code(client)["code"]
    other = new_code(client)["code"]
    token = pair(client, code).json()["device_token"]
    with sqlite3.connect(store.db_path) as db:
        dump = "\n".join(db.iterdump())
    assert code not in dump and other not in dump and token not in dump
    assert auth.token_hash(token) in dump


def test_exchange_gives_a_working_token_and_the_code_works_once(client):
    """The app gets a token that opens the API in X-Meanwhile-Key; the same code fails the second time."""
    code = new_code(client)["code"]
    reply = pair(client, code, name="  Phone  ")
    assert reply.status_code == 201
    device = reply.json()
    assert device["name"] == "Phone" and device["kind"] == "app" and device["id"] == device["device_id"]
    assert client.get("/api/cameras", headers={"X-Meanwhile-Key": device["device_token"]}).status_code == 200
    again = pair(client, code)
    assert again.status_code == 403 and again.json()["error"] == "invalid_code"


def test_expired_and_unknown_codes_fail(client, monkeypatch):
    """A code past its lifetime, or one never issued, gives 403 invalid_code."""
    monkeypatch.setattr(config, "PAIR_CODE_TTL_S", 0.05)
    code = new_code(client)["code"]
    time.sleep(0.1)
    assert pair(client, code).status_code == 403
    assert pair(client, "never-issued-code").json()["error"] == "invalid_code"


def test_bad_requests(client):
    """A missing code or name, a too-long name, or a non-object body is a 400 (and the code is not spent)."""
    code = new_code(client)["code"]
    assert client.post("/api/pair", json={"device_name": "x"}).json()["error"] == "bad_code"
    assert pair(client, code, name="").json()["error"] == "bad_name"
    assert pair(client, code, name="x" * (pairing.MAX_NAME + 1)).json()["error"] == "bad_name"
    assert client.post("/api/pair", content=b"[1]").status_code == 400
    assert pair(client, code).status_code == 201


def test_exchange_is_rate_limited(store, monkeypatch):
    """After MEANWHILE_PAIR_TRIES attempts in the window, the next is 429 with Retry-After, even with a good code."""
    monkeypatch.setattr(config, "PAIR_TRIES", 3)
    client = TestClient(build_app(store, api_key=FAKE_KEY))
    for _ in range(3):
        assert pair(client, "wrong").status_code == 403
    reply = pair(client, new_code(client)["code"])
    assert reply.status_code == 429 and reply.json()["error"] == "too_many_tries"
    assert int(reply.headers["Retry-After"]) >= 1


def test_rate_limit_window_moves():
    """Attempts older than the window no longer count, and clients are counted apart."""
    clock = [0.0]
    limit = pairing.RateLimit(2, 10, clock=lambda: clock[0])
    assert limit.wait_s("a") == 0 and limit.wait_s("a") == 0
    assert limit.wait_s("a") == pytest.approx(10) and limit.wait_s("b") == 0
    clock[0] = 10.5
    assert limit.wait_s("a") == 0


def test_browser_gets_an_httponly_cookie_not_the_token(client, monkeypatch):
    """With "browser": true the token comes only as an HttpOnly, Secure, SameSite=Lax cookie (with the domain if
    set), and that cookie opens the API."""
    monkeypatch.setattr(config, "PAIR_COOKIE_DOMAIN", "box.example.test")
    reply = pair(client, new_code(client)["code"], browser=True)
    assert reply.status_code == 201 and "device_token" not in reply.json() and reply.json()["kind"] == "browser"
    cookie = reply.headers["set-cookie"]
    for part in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Domain=box.example.test",
                 f"Max-Age={int(config.PAIR_COOKIE_DAYS * 86400)}"):
        assert part in cookie, part
    token = cookie.split(";")[0].split("=", 1)[1]
    assert client.get("/api/cameras", headers={"Cookie": f"mw_key={token}"}).status_code == 200


def test_the_gate_with_key_token_and_cookie(client):
    """The key or a device token passes, in the header or the cookie (any of several cookies); anything else is 401.
    /api/auth/check answers 204 or 401 the same way; /pair, /api/pair and /media stay open."""
    token = pair(client, new_code(client)["code"]).json()["device_token"]
    ok = [KEY, {"X-Meanwhile-Key": token}, {"Cookie": f"mw_key={FAKE_KEY}"}, {"Cookie": f"a=1; mw_key={token}"},
          {"Cookie": f"mw_key=revoked; mw_key={token}"}]
    bad = [{}, {"X-Meanwhile-Key": "wrong"}, {"Cookie": "mw_key=wrong"}, {"Cookie": f"other={FAKE_KEY}"}]
    for headers in ok:
        assert client.get("/api/cameras", headers=headers).status_code == 200, headers
        assert client.get("/api/auth/check", headers=headers).status_code == 204, headers
    for headers in bad:
        assert client.get("/api/cameras", headers=headers).status_code == 401, headers
        assert client.get("/api/auth/check", headers=headers).status_code == 401, headers
    page = client.get("/pair?code=x")
    assert page.status_code == 200 and "Pair this browser" in page.text
    assert page.headers["cache-control"] == "no-store" and page.headers["referrer-policy"] == "no-referrer"
    assert client.get("/media/none.jpg").status_code == 401


def test_list_and_revoke(client, monkeypatch):
    """Devices list oldest first with name, kind, created, last seen and the caller marked; a revoked token stops
    working at once; revoking again is 404."""
    monkeypatch.setattr(auth, "SEEN_EVERY_S", 0)
    first = pair(client, new_code(client)["code"], name="Phone").json()
    second = pair(client, new_code(client)["code"], name="Laptop").json()
    mine = {"X-Meanwhile-Key": second["device_token"]}
    devices = client.get("/api/paired-devices", headers=mine).json()["devices"]
    assert [(d["name"], d["current"]) for d in devices] == [("Phone", False), ("Laptop", True)]
    assert all({"device_id", "id", "kind", "created", "last_seen"} <= d.keys() for d in devices)
    assert all("token" not in k for d in devices for k in d)
    before = devices[1]["last_seen"]
    time.sleep(0.01)
    client.get("/api/cameras", headers=mine)
    assert client.get("/api/paired-devices", headers=KEY).json()["devices"][1]["last_seen"] > before
    assert client.delete(f"/api/paired-devices/{first['device_id']}", headers=mine).json()["revoked"] is True
    assert client.get("/api/cameras", headers={"X-Meanwhile-Key": first["device_token"]}).status_code == 401
    assert client.delete(f"/api/paired-devices/{first['device_id']}", headers=KEY).status_code == 404
    assert [d["name"] for d in client.get("/api/paired-devices", headers=KEY).json()["devices"]] == ["Laptop"]


def test_pairing_again_replaces_the_old_entry_and_names_stay_unique(client):
    """A browser that pairs again (its old cookie sent) or the app that pairs again (its old token sent) replaces its
    old entry, whose token stops working; a different device with the same name gets " (2)"."""
    def names():
        """The paired devices' names, oldest first."""
        return [d["name"] for d in client.get("/api/paired-devices", headers=KEY).json()["devices"]]

    first = pair(client, new_code(client)["code"], name="Windows PC · Chrome", browser=True)
    old_cookie = first.headers["set-cookie"].split(";")[0]
    again = client.post("/api/pair", headers={"Cookie": old_cookie},
                        json={"code": new_code(client)["code"], "device_name": "Windows PC · Chrome", "browser": True})
    assert again.status_code == 201 and names() == ["Windows PC · Chrome"]
    assert client.get("/api/cameras", headers={"Cookie": old_cookie}).status_code == 401

    phone = pair(client, new_code(client)["code"], name="Kitchen tablet").json()
    other = pair(client, new_code(client)["code"], name="kitchen tablet").json()
    assert other["name"] == "kitchen tablet (2)" and names() == ["Windows PC · Chrome", "Kitchen tablet", "kitchen tablet (2)"]
    repaired = client.post("/api/pair", headers={"X-Meanwhile-Key": phone["device_token"]},
                           json={"code": new_code(client)["code"], "device_name": "Kitchen tablet"}).json()
    assert repaired["name"] == "Kitchen tablet" and names() == ["Windows PC · Chrome", "kitchen tablet (2)", "Kitchen tablet"]
    assert client.get("/api/cameras", headers={"X-Meanwhile-Key": phone["device_token"]}).status_code == 401


def test_no_key_is_refused(store, monkeypatch):
    """There is no open mode: the gate, the app and the settings all refuse an empty key."""
    with pytest.raises(ValueError, match="MEANWHILE_KEY"):
        auth.Access(store, "")
    with pytest.raises(ValueError, match="MEANWHILE_KEY"):
        build_app(store, api_key="")
    monkeypatch.setenv("MEANWHILE_KEY", "")
    with pytest.raises(RuntimeError, match="MEANWHILE_KEY is not set"):
        config._need("MEANWHILE_KEY")
