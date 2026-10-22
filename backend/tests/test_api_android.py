"""Tests for what the Android app needs: the session list, status and lag, device registration and push, the key gate,
and the `id` fields the app reads.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from local.devserver import build_app
from meanwhile import push
from meanwhile.api import cameras, people, sessions
from meanwhile.pipeline.analyze import Analyser

ORDER = {"watch_order": {"kind": "notes", "target": "who comes in", "cameras": "all", "alert_when": [],
                         "summary_for_alexa": "taking notes"}}


def make_session(store, instruction="keep an eye on my watch", verdict=None, end=True):
    """Start a session, optionally with a verdict event, and end it."""
    session = store.start_session(instruction, ORDER)
    if verdict:
        store.add_event({"session_id": session["session_id"], "camera_id": "desk", "action": "put_back",
                         "summary": verdict, "alert": "verdict"})
    store.add_event({"session_id": session["session_id"], "camera_id": "desk", "action": "x", "summary": "quiet"})
    if end:
        store.end_session(session["session_id"])
    return session


def test_session_list_newest_first_with_last_verdict(store):
    """Past sessions list newest first, with the last verdict summary (or null), count and the app's id/active."""
    first = make_session(store, "first", verdict="Your watch is back.")
    time.sleep(0.01)
    second = make_session(store, "second", end=False)
    status, body = sessions.list_sessions(store, None)
    assert status == 200
    newest, oldest = body["sessions"]
    assert newest["id"] == second["session_id"] and newest["active"] and newest["last_verdict"] is None
    assert oldest["id"] == first["session_id"] and not oldest["active"]
    assert oldest["last_verdict"] == oldest["summary"] == "Your watch is back." and oldest["event_count"] == 2
    assert [s["instruction"] for s in sessions.list_sessions(store, "1")[1]["sessions"]] == ["second"]
    assert sessions.list_sessions(store, "many")[0] == 400


def test_session_and_event_carry_ids_for_the_app(store):
    """Sessions, events, cameras and people also carry `id`, and events always have happened_at."""
    session = make_session(store, verdict="Back.", end=False)
    assert sessions.latest_session(store)[1]["session"]["id"] == session["session_id"]
    event = sessions.session_events(store, session["session_id"], None)[1]["events"][0]
    assert event["id"] == event["event_id"] and event["happened_at"] == event["ts"]
    camera = cameras.register(store, {"label": "Desk"})[1]["camera"]
    assert camera["id"] == "desk"
    person = people.add(store, {"name": "Ravi", "role": "helper", "note": ""})[1]["person"]
    assert person["id"] == "ravi" and people.list_people(store)[1]["people"][0]["id"] == "ravi"


def test_lag_from_the_analyser(store):
    """Lag is the age of the oldest waiting upload; idle, it is the latest analysed item's total delay."""
    analyser = Analyser(store)
    assert analyser.lag_s() == 0.0
    analyser.submit("frames/desk/20261011T093200123Z.jpg")
    time.sleep(0.05)
    assert 0.04 <= analyser.lag_s() < 2
    analyser.frames.clear()
    analyser.record("frames/desk/x.jpg", "checked", time.time() - 3.0, 1.0)
    assert 2.9 <= analyser.lag_s() <= 3.2
    assert sessions.status(store, analyser.status(), None)[1]["lag_s"] == analyser.lag_s()
    status = sessions.status(store, None, None)[1]
    assert status["analyser"] is False and status["lag_s"] == 0.0 and status["push"] == {"enabled": False, "devices": 0}


def test_devices_register_and_status(store):
    """POST /api/devices stores tokens (no duplicates); bad tokens are 400; /api/status shows the count."""
    client = TestClient(build_app(store, api_key="s3cret"), headers={"X-Meanwhile-Key": "s3cret"})
    assert client.post("/api/devices", json={"fcm_token": "tok-1"}).json() == {"registered": True, "devices": 1}
    assert client.post("/api/devices", json={"fcm_token": "tok-1"}).json()["devices"] == 1
    assert client.post("/api/devices", json={"fcm_token": "tok-2"}).json()["devices"] == 2
    assert client.post("/api/devices", json={"fcm_token": ""}).status_code == 400
    assert client.post("/api/devices", json={}).status_code == 400
    body = client.get("/api/status").json()
    assert body["push"]["devices"] == 2 and body["push"]["enabled"] is False
    assert client.get("/api/sessions?limit=5").json() == {"sessions": []}


def test_key_gate(store):
    """Every /api route and media file answers 401 without the key; pages stay open; there is no app without a key."""
    client = TestClient(build_app(store, api_key="s3cret"))
    assert client.get("/api/cameras").status_code == 401
    assert client.get("/api/cameras").json()["error"] == "unauthorized"
    assert client.get("/api/cameras", headers={"X-Meanwhile-Key": "wrong"}).status_code == 401
    assert client.post("/api/devices", json={"fcm_token": "t"}).status_code == 401
    assert client.get("/api/cameras", headers={"X-Meanwhile-Key": "s3cret"}).status_code == 200
    assert client.get("/media/none.jpg").status_code == 401
    assert client.get("/media/none.jpg", headers={"X-Meanwhile-Key": "s3cret"}).status_code == 404
    with pytest.raises(ValueError, match="MEANWHILE_KEY"):
        build_app(store, api_key="")


@pytest.fixture
def account():
    """A fake service-account file's contents with a real RSA key."""
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return {"project_id": "meanwhile-test", "client_email": "svc@meanwhile-test.iam.gserviceaccount.com",
            "private_key": pem, "token_uri": "https://oauth2.example/token"}


def fake_fcm(sent, dead=()):
    """An httpx client standing in for Google: issues tokens and records messages; `dead` tokens answer 404."""
    def handler(request: httpx.Request) -> httpx.Response:
        """Answer the token and send endpoints."""
        if request.url.host == "oauth2.example":
            assert b"assertion=" in request.content
            return httpx.Response(200, json={"access_token": "ya29.fake", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer ya29.fake"
        message = json.loads(request.content)["message"]
        sent.append(message)
        return httpx.Response(404 if message["token"] in dead else 200, json={"error": {"status": "UNREGISTERED"}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_push_sends_alert_events_only(store, account):
    """A heads-up goes to every device with its data fields and channel; quiet events send nothing; dead tokens drop."""
    sent: list = []
    pusher = push.Push(store, account, fake_fcm(sent, dead=("tok-dead",)))
    store.event_listeners.append(pusher.send_event)
    push.register_device(store, "tok-a")
    push.register_device(store, "tok-dead")
    session = store.start_session("watch", ORDER)
    store.add_event({"session_id": session["session_id"], "camera_id": "desk", "action": "left_view",
                     "summary": "Your watch left view.", "alert": "none"})
    assert sent == []
    event = store.add_event({"session_id": session["session_id"], "camera_id": "desk", "action": "left_view",
                             "summary": "Your watch left view.", "alert": "heads_up", "frame_key": "frames/desk/a.jpg"})
    assert [m["token"] for m in sent] == ["tok-a", "tok-dead"]
    frame_url = sent[0]["data"]["frame_url"]
    assert sent[0]["data"] == {"event_id": event["event_id"], "summary": "Your watch left view.", "alert": "heads_up",
                               "frame_url": frame_url, "session_id": session["session_id"]}
    assert frame_url.startswith("/media/frames/desk/a.jpg?") and store.links.valid(*frame_url.split("?"))
    assert sent[0]["android"]["notification"]["channel_id"] == "heads_up"
    assert push.device_tokens(store) == ["tok-a"]
    assert pusher.last["sent"] == 1 and pusher.last["ms"] >= 0


def test_push_is_off_without_a_key_file(store, tmp_path):
    """No configured path, or a missing file, means no pusher and nothing to break."""
    assert push.from_config(store, "") is None
    assert push.from_config(store, str(tmp_path / "missing.json")) is None


def test_a_failing_listener_never_breaks_event_writing(store):
    """An event listener that raises is reported, and the event is still stored."""
    store.event_listeners.append(lambda event: 1 / 0)
    session = store.start_session("watch", ORDER)
    store.add_event({"session_id": session["session_id"], "summary": "x"})
    assert len(store.list_events(session["session_id"])) == 1
