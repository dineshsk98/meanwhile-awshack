"""Tests for the camera and upload endpoints, by handler and through the dev server."""

from __future__ import annotations

from datetime import timedelta

import pytest
from starlette.testclient import TestClient

from local.devserver import build_app
from meanwhile import config
from meanwhile.api import cameras, uploads
from meanwhile.store import parse, to_iso

KEY = {"X-Meanwhile-Key": config.API_KEY}

CONTRACT_CAMERA_FIELDS = {"camera_id", "id", "label", "last_seen", "online", "thumb_url"}


def later(ts: str, seconds: float) -> str:
    """A timestamp `seconds` after ts."""
    return to_iso(parse(ts) + timedelta(seconds=seconds))


def test_register_makes_an_id_from_the_label_and_reuses_it(store):
    """The same label always maps to the same camera; bad labels are refused politely."""
    status, body = cameras.register(store, {"label": "  Living Room "})
    assert status == 200 and body["camera"]["camera_id"] == "living-room" and body["camera"]["label"] == "Living Room"
    cameras.register(store, {"label": "living room"})
    assert [c["camera_id"] for c in store.list_cameras()] == ["living-room"]
    for bad in ("", "  ", "!!!", "x" * 25):
        status, body = cameras.register(store, {"label": bad})
        assert status == 400 and body["error"] == "bad_label" and body["message"].endswith(".")


def test_list_matches_the_contract_and_goes_offline_after_10_s(store):
    """GET /api/cameras has the contract's fields; a camera is offline once its heartbeat is over 10 s old."""
    cameras.register(store, {"label": "Room"})
    _, body = cameras.list_cameras(store)
    (camera,) = body["cameras"]
    assert set(camera) == CONTRACT_CAMERA_FIELDS and camera["online"] is True and camera["thumb_url"] is None
    seen = camera["last_seen"]
    assert cameras.list_cameras(store, at=later(seen, 10))[1]["cameras"][0]["online"] is True
    assert cameras.list_cameras(store, at=later(seen, 10.5))[1]["cameras"][0]["online"] is False


def test_heartbeat_swaps_the_thumbnail_and_deletes_the_old_one(store):
    """A new thumb_key replaces the old one on the camera and on disk; foreign keys are refused."""
    cameras.register(store, {"label": "Desk"})
    keys = []
    for data in (b"first", b"second"):
        _, presigned = uploads.presign(store, {"camera_id": "desk", "kind": "thumb", "ext": "jpg"})
        assert uploads.put_local(store, presigned["key"], data)[0] == 200
        status, body = cameras.heartbeat(store, "desk", {"thumb_key": presigned["key"]})
        assert status == 200 and body["camera"]["thumb_url"].split("?")[0] == f"/media/{presigned['key']}"
        keys.append(presigned["key"])
    assert keys[0] != keys[1]
    assert not store.media_path(keys[0]).exists() and store.media_path(keys[1]).read_bytes() == b"second"
    assert cameras.heartbeat(store, "desk", {"thumb_key": "thumbs/room/20261011T093200123Z.jpg"})[0] == 400
    assert cameras.heartbeat(store, "desk", {"thumb_key": "thumbs/desk/../../meanwhile.db"})[0] == 400
    assert cameras.heartbeat(store, "nope", {})[0] == 404


def test_uploads_only_accept_issued_key_shapes(store):
    """presign checks the camera and kind/ext; put_local refuses keys it didn't hand out and empty bodies."""
    cameras.register(store, {"label": "Desk"})
    _, body = uploads.presign(store, {"camera_id": "desk", "kind": "clip", "ext": "webm"})
    assert body["key"].startswith("clips/desk/") and body["key"].endswith(".webm")
    assert body["method"] == "PUT" and body["headers"] == {"Content-Type": "video/webm"}
    assert body["url"] == f"/api/media/{body['key']}"
    assert uploads.presign(store, {"camera_id": "nope", "kind": "thumb", "ext": "jpg"})[0] == 404
    assert uploads.presign(store, {"camera_id": "desk", "kind": "thumb", "ext": "png"})[0] == 400
    assert uploads.presign(store, {"camera_id": "desk", "kind": "video", "ext": "mp4"})[0] == 400
    for bad in ("../meanwhile.db", "thumbs/desk/x.jpg", "thumbs/desk/../../a/20261011T093200123Z.jpg"):
        assert uploads.put_local(store, bad, b"x")[0] == 400
    assert uploads.put_local(store, body["key"], b"")[0] == 413


def test_rename_keeps_the_id_and_the_page_finds_it_by_its_new_label(store):
    """PATCH keeps camera_id; registering the new label reuses it; a taken or bad label is refused."""
    cameras.register(store, {"label": "Kitchen"})
    cameras.register(store, {"label": "Desk"})
    status, body = cameras.rename(store, "kitchen", {"label": "Room"})
    assert status == 200 and body["camera"]["camera_id"] == "kitchen" and body["camera"]["label"] == "Room"
    assert cameras.register(store, {"label": "room"})[1]["camera"]["camera_id"] == "kitchen"
    assert cameras.heartbeat(store, "kitchen", {})[1]["camera"]["label"] == "Room"
    assert cameras.rename(store, "kitchen", {"label": "desk"})[1]["error"] == "label_taken"
    assert cameras.rename(store, "kitchen", {"label": "!!!"})[0] == 400
    assert cameras.rename(store, "nope", {"label": "Hall"})[0] == 404
    status, body = cameras.register(store, {"label": "Kitchen"})
    assert body["camera"]["camera_id"] == "kitchen-2", "the old id is taken, so a new camera gets the next one"


def test_remove_hides_the_camera_stops_its_page_and_start_brings_it_back(store, monkeypatch):
    """DELETE hides the camera and deletes its thumbnail; its heartbeats get 410 and uploads 404; registering its
    label again brings it back; the active session's focus camera can't be removed."""
    cameras.register(store, {"label": "Room"})
    _, presigned = uploads.presign(store, {"camera_id": "room", "kind": "thumb", "ext": "jpg"})
    uploads.put_local(store, presigned["key"], b"thumb")
    cameras.heartbeat(store, "room", {"thumb_key": presigned["key"]})
    assert cameras.remove(store, "room") == (200, {"removed": "room"})
    assert cameras.list_cameras(store)[1]["cameras"] == [] and not store.media_path(presigned["key"]).exists()
    assert cameras.heartbeat(store, "room", {})[1]["error"] == "camera_removed"
    assert uploads.presign(store, {"camera_id": "room", "kind": "thumb", "ext": "jpg"})[0] == 404
    assert cameras.remove(store, "room")[0] == 404 and cameras.rename(store, "room", {"label": "Hall"})[0] == 404
    assert cameras.register(store, {"label": "Room"})[1]["camera"]["camera_id"] == "room"
    assert [c["camera_id"] for c in store.list_cameras()] == ["room"]
    monkeypatch.setattr(store, "active_session", lambda: {"focus_camera_id": "room"})
    assert cameras.remove(store, "room")[1]["error"] == "camera_in_use"


@pytest.fixture
def client(store):
    """The dev server app over a throwaway store."""
    return TestClient(build_app(store), headers=KEY)


def test_camera_page_flow_over_http(client):
    """Register, presign, PUT, heartbeat, then the list and the thumbnail URL work end to end."""
    assert client.get("/camera").status_code == 200
    assert client.get("/debug.html").status_code == 200
    assert client.post("/api/cameras", json={"label": "Room"}).json()["camera"]["camera_id"] == "room"
    presigned = client.post("/api/uploads", json={"camera_id": "room", "kind": "thumb", "ext": "jpg"}).json()
    put = client.request(presigned["method"], presigned["url"], content=b"\xff\xd8jpeg", headers=presigned["headers"])
    assert put.status_code == 200
    beat = client.post("/api/cameras/room/heartbeat", json={"thumb_key": presigned["key"]})
    assert beat.status_code == 200
    (camera,) = client.get("/api/cameras").json()["cameras"]
    assert camera["online"] and client.get(camera["thumb_url"]).content == b"\xff\xd8jpeg"
    bad = client.post("/api/cameras", content=b"not json")
    assert bad.status_code == 400 and bad.json()["error"] == "bad_json"
    assert client.patch("/api/cameras/room", json={"label": "Hall"}).json()["camera"]["label"] == "Hall"
    assert client.delete("/api/cameras/room").json() == {"removed": "room"}
    assert client.get("/api/cameras").json()["cameras"] == []
    assert client.delete("/api/cameras/room", headers={"X-Meanwhile-Key": "wrong"}).status_code == 401


def test_register_returns_the_ice_servers_with_turn_credentials_only_on_turn_urls(store, monkeypatch):
    """The camera page gets the configured STUN/TURN list in RTCIceServer shape."""
    from meanwhile import config

    monkeypatch.setattr(config, "ICE_SERVERS", ["stun:stun.example.org:3478", "turn:turn.example.org:3478", "turns:turn.example.org:443"])
    monkeypatch.setattr(config, "TURN_USERNAME", "user")
    monkeypatch.setattr(config, "TURN_CREDENTIAL", "secret")
    status, body = cameras.register(store, {"label": "Desk"})
    assert status == 200 and body["ice_servers"] == [
        {"urls": "stun:stun.example.org:3478"},
        {"urls": "turn:turn.example.org:3478", "username": "user", "credential": "secret"},
        {"urls": "turns:turn.example.org:443", "username": "user", "credential": "secret"}]
    monkeypatch.setattr(config, "ICE_SERVERS", [])
    assert cameras.register(store, {"label": "Desk"})[1]["ice_servers"] == []


def test_register_returns_the_video_encodings_from_config(store, monkeypatch):
    """Registration tells the camera page how to encode: the live view's settings and the ingest's short side."""
    from meanwhile import config

    monkeypatch.setattr(config, "LIVE_SCALE_DOWN", 2.0)
    monkeypatch.setattr(config, "LIVE_MAX_BITRATE", 800_000)
    monkeypatch.setattr(config, "LIVE_MAX_FPS", 20)
    monkeypatch.setattr(config, "INGEST_SHORT_SIDE", 360)
    monkeypatch.setattr(config, "INGEST_MAX_BITRATE", 2_000_000)
    monkeypatch.setattr(config, "INGEST_MAX_FPS", 20)
    status, body = cameras.register(store, {"label": "Desk"})
    assert status == 200 and body["encodings"] == {
        "live": {"scale_down": 2.0, "max_bitrate": 800_000, "max_framerate": 20},
        "ingest": {"short_side": 360, "max_bitrate": 2_000_000, "max_framerate": 20}}
