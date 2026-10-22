"""Checks the core's HTTP API keeps CONTRACT.md, over a real local port.
The check_* helpers are copied from voice/backend/tests/test_contract.py, so both sides test the same promises.
"""

from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from local.devserver import build_app
from meanwhile import config
from meanwhile.api import cameras
from meanwhile.pipeline import orders

KEY = {"X-Meanwhile-Key": config.API_KEY}

EVENT_FIELDS = {"event_id", "session_id", "ts", "camera_id", "action", "summary", "alert", "clip_url"}
SESSION_FIELDS = {"session_id", "started_at", "ended_at", "instruction", "watch_order"}
ORDER_FIELDS = {"kind", "cameras", "clip_question", "alert_when", "summary_for_alexa"}
GET_LIMIT_MS = 100

READINGS = {
    "watch it": {"kind": "unclear"},
    "keep an eye on my umbrella": {"kind": "object", "target": "umbrella"},
    "keep an eye on my watch": {"kind": "object", "target": "watch"},
    "take notes": {"kind": "notes"},
    "keep an eye on the front door": {
        "kind": "monitor", "target": "the front door", "clip_question": "Who comes to the front door, and is it locked?",
        "report": ["someone at the front door", "the door locked or unlocked"],
        "alert_when": ["someone I don't recognise at the front door"]},
}


def fixed_understand(instruction: str, cams: list[dict]) -> dict:
    """Stand-in for the model: a fixed reading of each test sentence."""
    return {"target": "", "camera": "", **READINGS[instruction]}


def fake_ground(target: str, cams: list[dict]) -> dict | None:
    """Stand-in for grounding: the watch is on the Desk camera; nothing else is found."""
    return {"camera_id": "desk", "box": [0.4, 0.4, 0.5, 0.5]} if target == "watch" else None


def compile_order(instruction: str, cams: list[dict]) -> dict:
    """The real order builder with the stand-in model and grounding."""
    return orders.compile_order(instruction, cams, ground=fake_ground, understand=fixed_understand)


def timed_get(client: httpx.Client, path: str, **params) -> httpx.Response:
    """GET a path and assert it answered within GET_LIMIT_MS."""
    start = time.perf_counter()
    reply = client.get(path, params=params)
    assert (time.perf_counter() - start) * 1000 < GET_LIMIT_MS, path
    return reply


def check_watch_orders(client: httpx.Client) -> dict:
    """POST /api/watch-orders: clarify, not_found, started, already watching."""
    unclear = client.post("/api/watch-orders", json={"instruction": "watch it"})
    assert unclear.status_code == 200 and set(unclear.json()) == {"clarify"} and unclear.json()["clarify"]
    missing = client.post("/api/watch-orders", json={"instruction": "keep an eye on my umbrella"})
    assert missing.status_code == 422 and missing.json()["error"] == "not_found"
    started = client.post("/api/watch-orders", json={"instruction": "keep an eye on my watch"})
    assert started.status_code == 201
    session = started.json()["session"]
    assert SESSION_FIELDS <= session.keys() and ORDER_FIELDS <= session["watch_order"].keys()
    assert session["ended_at"] is None
    again = client.post("/api/watch-orders", json={"instruction": "take notes"})
    assert again.status_code == 409 and again.json()["error"] == "already_watching"
    assert again.json()["session"]["session_id"] == session["session_id"]
    return session


def play_object(store, session: dict) -> list[dict]:
    """Write the object scenario's events the way the analyser will: picked up by Ravi, then handed back."""
    sid = session["session_id"]
    return [
        store.add_event({"session_id": sid, "camera_id": "room", "action": "person_seen", "actor_id": "ravi",
                         "summary": "Ravi came into the room.", "source": "rekognition"}),
        store.add_event({"session_id": sid, "camera_id": "desk", "action": "picked_up", "actor_id": "ravi",
                         "summary": "Your watch was picked up by Ravi.", "alert": "heads_up", "source": "tracker",
                         "clip_key": "clips/desk/20261013T100000000Z.mp4"}),
        store.add_event({"session_id": sid, "camera_id": "room", "action": "handed_to", "actor_id": "ravi",
                         "recipient_id": "owner", "summary": "Ravi handed your watch back to you. Not taken.",
                         "alert": "verdict", "source": "tracker"}),
    ]


def play_door(store, session: dict) -> None:
    """Write the door scenario's events: Owner leaves, then Ravi locks the door."""
    sid = session["session_id"]
    store.add_event({"session_id": sid, "camera_id": "door", "action": "left", "actor_id": "owner",
                     "summary": "You left the house."})
    time.sleep(0.002)
    store.add_event({"session_id": sid, "camera_id": "door", "action": "locked the front door", "actor_id": "ravi",
                     "summary": "Ravi locked the front door."})


@pytest.fixture
def live(store):
    """A store with Desk, Room and Door cameras online, and Owner and Ravi registered."""
    for label in ("Desk", "Room", "Door"):
        cameras.register(store, {"label": label})
    store.put_person({"person_id": "owner", "name": "Owner", "role": "owner"})
    store.put_person({"person_id": "ravi", "name": "Ravi", "role": "helper"})
    return store


@pytest.fixture
def core_url(live):
    """The core's dev server app on a free local port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(build_app(live, compile_order=compile_order),
                                           host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_watch_orders_sessions_events_end(core_url, live):
    """Watch orders, latest session, events (with since), and ending a session."""
    with httpx.Client(base_url=core_url, headers=KEY) as client:
        session = check_watch_orders(client)
        latest = timed_get(client, "/api/sessions/latest").json()
        assert latest["active"] and latest["session"]["session_id"] == session["session_id"]

        played = play_object(live, session)
        events = timed_get(client, f"/api/sessions/{session['session_id']}/events").json()["events"]
        assert [e["event_id"] for e in events] == [e["event_id"] for e in played]
        for event in events:
            assert EVENT_FIELDS <= event.keys()
            assert "clip_key" not in event
        assert events[1]["actor_name"] == "Ravi" and events[1]["alert"] == "heads_up"
        since = timed_get(client, f"/api/sessions/{session['session_id']}/events", since=events[0]["ts"]).json()
        assert [e["event_id"] for e in since["events"]] == [e["event_id"] for e in events[1:]]
        assert client.get("/api/sessions/ses_nope/events").status_code == 404

        ended = client.post(f"/api/sessions/{session['session_id']}/end")
        assert ended.status_code == 200 and ended.json()["session"]["ended_at"]
        assert client.post(f"/api/sessions/{session['session_id']}/end").status_code == 409
        latest = client.get("/api/sessions/latest").json()
        assert latest["active"] is False and latest["session"]["session_id"] == session["session_id"]


def test_cameras_and_a_door_order(core_url, live):
    """Cameras; "keep an eye on the front door" is an ordinary order made from the user's words; the owner's latest
    'left' event."""
    with httpx.Client(base_url=core_url, headers=KEY) as client:
        found = timed_get(client, "/api/cameras").json()["cameras"]
        assert {c["camera_id"] for c in found} == {"desk", "room", "door"}
        assert all(c["online"] and c["label"] for c in found)

        started = client.post("/api/watch-orders", json={"instruction": "keep an eye on the front door"})
        order = started.json()["session"]["watch_order"]
        assert started.status_code == 201 and order["kind"] == "monitor"
        assert order["clip_question"] == "Who comes to the front door, and is it locked?"
        play_door(live, started.json()["session"])

        left = timed_get(client, "/api/events/latest", action="left", actor_role="owner").json()["event"]
        assert EVENT_FIELDS <= left.keys() and left["actor_id"] == "owner" and left["actor_role"] == "owner"
        assert client.get("/api/events/latest", params={"action": "left", "actor_role": "helper"}).json() == {"event": None}


def test_no_session_yet(core_url):
    """Empty answers before anything happens."""
    with httpx.Client(base_url=core_url, headers=KEY) as client:
        assert client.get("/api/sessions/latest").json() == {"session": None, "active": False}
        assert client.get("/api/events/latest", params={"action": "left"}).json() == {"event": None}
