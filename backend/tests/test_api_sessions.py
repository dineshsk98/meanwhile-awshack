"""Tests for the session endpoints and the watch state each camera's heartbeat returns."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from local.devserver import build_app
from meanwhile import config
from meanwhile.api import cameras, sessions
from meanwhile.pipeline.models import ModelUnavailable

KEY = {"X-Meanwhile-Key": config.API_KEY}

BOX = [0.1, 0.2, 0.3, 0.4]


def fake_compile(instruction, cams):
    """A stand-in compiler: objects on Desk, notes on Room, 'umbrella' not found, anything else unclear."""
    if "umbrella" in instruction:
        return {"watch_order": {"kind": "object"}, "not_found": "I can't see your umbrella on the Desk camera."}
    if "watch" in instruction:
        return {"watch_order": {"kind": "object", "target": "watch", "cameras": "all", "alert_when": ["picked_up"],
                                "summary_for_alexa": "keeping an eye on your watch on the Desk camera"},
                "focus_object": "watch", "focus_camera_id": "desk", "focus_box": BOX}
    if "notes" in instruction:
        return {"watch_order": {"kind": "notes", "target": "who comes in", "cameras": ["room"], "alert_when": [],
                                "summary_for_alexa": "taking notes on who comes in"}}
    return {"watch_order": {"clarify": "Sorry, could you say it again?"}}


@pytest.fixture
def live(store):
    """A store with Desk, Room and Door cameras online."""
    for label in ("Desk", "Room", "Door"):
        cameras.register(store, {"label": label})
    return store


def test_watch_order_outcomes(live):
    """201 started, 200 clarify, 422 not found, 409 already watching; only online cameras reach the compiler."""
    seen = []
    def compile_(instruction, cams):
        """Record which cameras the compiler was given."""
        seen.append([c["camera_id"] for c in cams])
        return fake_compile(instruction, cams)
    assert sessions.watch_orders(live, {"instruction": "hmm"}, compile_) == (200, {"clarify": "Sorry, could you say it again?"})
    status, body = sessions.watch_orders(live, {"instruction": "my umbrella"}, compile_)
    assert status == 422 and body["error"] == "not_found"
    assert live.active_session() is None
    status, body = sessions.watch_orders(live, {"instruction": "  keep an eye on my   watch "}, compile_)
    assert status == 201 and body["session"]["focus_box"] == BOX and body["session"]["instruction"] == "keep an eye on my watch"
    status, body = sessions.watch_orders(live, {"instruction": "take notes"}, compile_)
    assert status == 409 and body["session"]["session_id"] == live.active_session()["session_id"]
    assert seen[0] == ["desk", "door", "room"]


def test_model_down_is_503(live):
    """A model that can't answer gives 503 model_unavailable and no session."""
    def broken(*_):
        """A compiler whose model is down."""
        raise ModelUnavailable("Start Ollama.")
    status, body = sessions.watch_orders(live, {"instruction": "keep an eye on my watch"}, broken)
    assert status == 503 and body["error"] == "model_unavailable" and live.active_session() is None


def test_heartbeat_tells_each_camera_what_to_watch(live):
    """Object orders watch every camera, with the box only on the focus camera; notes only on the named one."""
    assert cameras.heartbeat(live, "desk", {})[1]["watch"] == {"active": False, "session_id": None, "focus_box": None, "held": False}
    _, body = sessions.watch_orders(live, {"instruction": "keep an eye on my watch"}, fake_compile)
    session_id = body["session"]["session_id"]
    assert cameras.heartbeat(live, "desk", {})[1]["watch"] == {"active": True, "session_id": session_id, "focus_box": BOX, "held": False}
    assert cameras.heartbeat(live, "room", {})[1]["watch"]["focus_box"] is None
    sessions.end_session(live, session_id)
    sessions.watch_orders(live, {"instruction": "take notes"}, fake_compile)
    assert cameras.heartbeat(live, "room", {})[1]["watch"]["active"] is True
    assert cameras.heartbeat(live, "desk", {})[1]["watch"]["active"] is False


def test_read_endpoints_follow_the_contract(live):
    """latest session, events with names and URLs, end twice, and the latest matching event."""
    assert sessions.latest_session(live) == (200, {"session": None, "active": False})
    _, body = sessions.watch_orders(live, {"instruction": "keep an eye on my watch"}, fake_compile)
    sid = body["session"]["session_id"]
    live.put_person({"person_id": "ravi", "name": "Ravi", "role": "helper"})
    first = live.add_event({"session_id": sid, "camera_id": "desk", "action": "picked_up", "actor_id": "ravi",
                            "summary": "Your watch was picked up by Ravi.", "alert": "heads_up", "clip_key": "clips/desk/a.mp4"})
    live.add_event({"session_id": sid, "camera_id": "door", "action": "left", "actor_id": "ravi", "summary": "Ravi left."})
    _, events = sessions.session_events(live, sid, None)
    assert events["events"][0]["actor_name"] == "Ravi" and events["events"][0]["clip_url"].split("?")[0] == "/media/clips/desk/a.mp4"
    assert "clip_key" not in events["events"][0]
    assert [e["action"] for e in sessions.session_events(live, sid, first["ts"])[1]["events"]] == ["left"]
    assert sessions.session_events(live, "ses_nope", None)[0] == 404
    assert sessions.latest_event(live, {"actor_role": "helper", "camera_id": "desk"})[1]["event"]["action"] == "picked_up"
    assert sessions.latest_event(live, {"action": "locked"})[1]["event"] is None
    assert sessions.end_session(live, sid)[0] == 200 and sessions.end_session(live, sid)[0] == 409
    assert sessions.latest_session(live)[1]["active"] is False


def test_watch_order_over_http(live):
    """The dev server routes the contract's endpoints."""
    client = TestClient(build_app(live, compile_order=fake_compile), headers=KEY)
    started = client.post("/api/watch-orders", json={"instruction": "keep an eye on my watch"})
    assert started.status_code == 201
    sid = started.json()["session"]["session_id"]
    assert client.get("/api/sessions/latest").json()["active"] is True
    assert client.get(f"/api/sessions/{sid}/events").json() == {"events": []}
    assert client.get("/api/state", params={"key": "door"}).status_code != 200
    assert client.get("/api/events/latest", params={"action": "left"}).json() == {"event": None}
    assert client.get("/api/debug/uploads").json() == {"uploads": []}
    assert client.post(f"/api/sessions/{sid}/end").status_code == 200


def test_caught_up_keeps_the_latest_cursor_for_active_and_ended_sessions(live):
    """POST /api/sessions/{id}/caught-up keeps the later of two cursors and when it was set, on an active or an
    ended session; an unknown session is 404 and a bad time 400."""
    _, body = sessions.watch_orders(live, {"instruction": "keep an eye on my watch"}, fake_compile)
    sid = body["session"]["session_id"]
    first = sessions.caught_up(live, sid, {"until": "2026-10-22T10:00:00.000Z"})[1]["session"]
    assert first["caught_up_until"] == "2026-10-22T10:00:00.000Z" and first["caught_up_at"] and first["active"]
    earlier = sessions.caught_up(live, sid, {"until": "2026-10-22T09:00:00.000Z"})[1]["session"]
    assert earlier["caught_up_until"] == "2026-10-22T10:00:00.000Z"
    sessions.end_session(live, sid)
    ended = sessions.caught_up(live, sid, {"until": "2026-10-22T11:00:00.000Z"})[1]["session"]
    assert ended["caught_up_until"] == "2026-10-22T11:00:00.000Z" and ended["ended_at"]
    assert sessions.caught_up(live, "ses_nope", {"until": "2026-10-22T11:00:00.000Z"})[0] == 404
    assert sessions.caught_up(live, sid, {"until": "yesterday"})[0] == 400
    client = TestClient(build_app(live, compile_order=fake_compile), headers=KEY)
    assert client.post(f"/api/sessions/{sid}/caught-up", json={"until": "2026-10-22T12:00:00.000Z"}).status_code == 200
