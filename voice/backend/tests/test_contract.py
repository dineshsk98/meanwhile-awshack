"""Checks the fake core keeps CONTRACT.md; the check_* helpers work against any core URL."""

from __future__ import annotations

import time

import httpx

from scenarios import fake_core

EVENT_FIELDS = {"event_id", "session_id", "ts", "camera_id", "action", "summary", "alert", "clip_url"}
SESSION_FIELDS = {"session_id", "started_at", "ended_at", "instruction", "watch_order"}
ORDER_FIELDS = {"kind", "cameras", "clip_question", "alert_when", "summary_for_alexa"}
GET_LIMIT_MS = 100


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


def test_watch_orders_sessions_events_end(core_url, core_store):
    """Watch orders, latest session, events (with since), and ending a session."""
    with httpx.Client(base_url=core_url) as client:
        session = check_watch_orders(client)
        latest = timed_get(client, "/api/sessions/latest").json()
        assert latest["active"] and latest["session"]["session_id"] == session["session_id"]

        played = fake_core.play(core_store, "object", session)
        events = timed_get(client, f"/api/sessions/{session['session_id']}/events").json()["events"]
        assert [e["event_id"] for e in events] == [e["event_id"] for e in played]
        for event in events:
            assert EVENT_FIELDS <= event.keys()
            assert "clip_key" not in event
        assert events[1]["actor_name"] == "Ravi"
        since = timed_get(client, f"/api/sessions/{session['session_id']}/events", since=events[0]["ts"]).json()
        assert [e["event_id"] for e in since["events"]] == [e["event_id"] for e in events[1:]]
        assert client.get("/api/sessions/ses_nope/events").status_code == 404

        ended = client.post(f"/api/sessions/{session['session_id']}/end")
        assert ended.status_code == 200 and ended.json()["session"]["ended_at"]
        assert client.post(f"/api/sessions/{session['session_id']}/end").status_code == 409
        latest = client.get("/api/sessions/latest").json()
        assert latest["active"] is False and latest["session"]["session_id"] == session["session_id"]


def test_cameras_and_latest_event(core_url, core_store):
    """Cameras, a door watch, and the owner's latest 'left' event."""
    with httpx.Client(base_url=core_url) as client:
        cameras = timed_get(client, "/api/cameras").json()["cameras"]
        assert {c["camera_id"] for c in cameras} == {"desk", "room", "door"}
        assert all(c["online"] and c["label"] for c in cameras)

        session = client.post("/api/watch-orders", json={"instruction": "keep an eye on the front door"}).json()["session"]
        assert session["watch_order"]["kind"] == "monitor"
        fake_core.play(core_store, "door", session)

        left = timed_get(client, "/api/events/latest", action="left", actor_role="owner").json()["event"]
        assert EVENT_FIELDS <= left.keys() and left["actor_id"] == "owner" and left["actor_role"] == "owner"
        assert client.get("/api/events/latest", params={"action": "left", "actor_role": "helper"}).json() == {"event": None}


def test_no_session_yet(core_url):
    """Empty answers before anything happens."""
    with httpx.Client(base_url=core_url) as client:
        assert client.get("/api/sessions/latest").json() == {"session": None, "active": False}
        assert client.get("/api/events/latest", params={"action": "left"}).json() == {"event": None}

