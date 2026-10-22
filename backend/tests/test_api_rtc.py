"""Tests for the WebRTC connection-setup relay between a viewer and a camera page."""

from __future__ import annotations

from datetime import timedelta

from starlette.testclient import TestClient

from local.devserver import build_app
from meanwhile import config
from meanwhile.api import cameras, rtc
from meanwhile.store import now, parse, to_iso

KEY = {"X-Meanwhile-Key": config.API_KEY}


def test_offer_reaches_the_camera_and_the_answer_reaches_the_viewer(store):
    """The camera lists an unanswered offer once; after it answers, the viewer gets the answer."""
    cameras.register(store, {"label": "Desk"})
    status, body = rtc.offer(store, "desk", {"sdp": "v=0 offer"})
    assert status == 201
    offer_id = body["offer_id"]
    assert rtc.answer_for(store, offer_id) == (200, {"offer_id": offer_id, "sdp": None})
    assert rtc.pending(store, "desk")[1]["offers"] == [{"offer_id": offer_id, "sdp": "v=0 offer", "purpose": "live"}]
    assert rtc.pending(store, "room")[1]["offers"] == []
    assert rtc.answer(store, offer_id, {"sdp": "v=0 answer"})[0] == 200
    assert rtc.answer_for(store, offer_id)[1]["sdp"] == "v=0 answer"
    assert rtc.pending(store, "desk")[1]["offers"] == []
    assert rtc.answer(store, offer_id, {"sdp": "again"})[0] == 404


def test_ingest_offers_say_so(store):
    """An offer from the detector's ingest is listed with purpose ingest; any other purpose is refused."""
    cameras.register(store, {"label": "Desk"})
    offer_id = rtc.offer(store, "desk", {"sdp": "v=0 offer", "purpose": "ingest"})[1]["offer_id"]
    assert rtc.pending(store, "desk")[1]["offers"][0] == {"offer_id": offer_id, "sdp": "v=0 offer", "purpose": "ingest"}
    assert rtc.offer(store, "desk", {"sdp": "x", "purpose": "spy"})[0] == 400


def test_bad_requests_and_stale_offers(store):
    """Unknown cameras, missing or huge SDP, unknown offers; offers older than the TTL are dropped."""
    cameras.register(store, {"label": "Desk"})
    assert rtc.offer(store, "nope", {"sdp": "x"})[0] == 404
    assert rtc.offer(store, "desk", {})[0] == 400
    assert rtc.offer(store, "desk", {"sdp": "x" * (rtc.MAX_SDP + 1)})[0] == 400
    assert rtc.answer(store, "rtc_nope", {"sdp": "x"})[0] == 404
    assert rtc.answer_for(store, "rtc_nope")[0] == 404
    old = store.add_rtc_offer("desk", "old")
    stale = to_iso(parse(now()) - timedelta(seconds=rtc.OFFER_TTL_S + 5))
    with store._db() as db:
        db.execute("UPDATE rtc_offers SET created = ? WHERE offer_id = ?", (stale, old["offer_id"]))
    assert rtc.pending(store, "desk")[1]["offers"] == []
    rtc.offer(store, "desk", {"sdp": "new"})
    assert store.get_rtc_offer(old["offer_id"]) is None


def test_relay_over_http(store):
    """The dev server routes the four relay endpoints."""
    cameras.register(store, {"label": "Desk"})
    client = TestClient(build_app(store, compile_order=lambda *_: {}), headers=KEY)
    offer_id = client.post("/api/rtc/desk/offers", json={"sdp": "o"}).json()["offer_id"]
    assert client.get("/api/rtc/desk/offers").json()["offers"][0]["offer_id"] == offer_id
    assert client.post(f"/api/rtc/offers/{offer_id}/answer", json={"sdp": "a"}).status_code == 200
    assert client.get(f"/api/rtc/offers/{offer_id}").json()["sdp"] == "a"


def test_ice_servers_for_viewers_match_the_camera_pages(store, monkeypatch):
    """GET /api/rtc/ice-servers gives a viewer the same STUN/TURN list, with credentials only on turn: URLs."""
    from meanwhile import config

    monkeypatch.setattr(config, "ICE_SERVERS", ["stun:stun.example.org:3478", "turn:turn.example.org:3478"])
    monkeypatch.setattr(config, "TURN_USERNAME", "user")
    monkeypatch.setattr(config, "TURN_CREDENTIAL", "not-a-real-secret")
    client = TestClient(build_app(store), headers=KEY)
    reply = client.get("/api/rtc/ice-servers")
    assert reply.status_code == 200
    assert reply.json() == {"ice_servers": cameras.ice_servers()}
    assert reply.json()["ice_servers"][0] == {"urls": "stun:stun.example.org:3478"}
    assert reply.json()["ice_servers"][1]["username"] == "user"
