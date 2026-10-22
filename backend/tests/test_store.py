"""Tests for the core's store."""

from __future__ import annotations

import pytest

from meanwhile.store import Store, StoreError, now


def order(kind="object"):
    """A minimal compiled watch order of the given kind."""
    return {"watch_order": {"kind": kind, "summary_for_alexa": "Watching."}, "focus_object": "watch"}


def test_one_active_session_at_a_time(store):
    """Only one session can be active; latest_session follows the newest."""
    first = store.start_session("keep an eye on my watch", order())
    assert first["state"] == "on_surface" and first["focus_object"] == "watch"
    with pytest.raises(StoreError):
        store.start_session("take notes", order("notes"))
    store.end_session(first["session_id"])
    assert store.active_session() is None
    assert store.latest_session()["session_id"] == first["session_id"]
    second = store.start_session("take notes", order("notes"))
    assert "state" not in second
    assert store.latest_session()["session_id"] == second["session_id"]


def test_events_come_back_in_time_order_with_ids(store):
    """Events sort by ts, get ids, and `since` filters."""
    session = store.start_session("x", order())
    late = store.add_event({"session_id": session["session_id"], "ts": "2026-10-10T10:00:05.000Z", "summary": "b"})
    early = store.add_event({"session_id": session["session_id"], "ts": "2026-10-10T10:00:01.000Z", "summary": "a"})
    assert early["event_id"].startswith("evt_") and early["alert"] == "none"
    assert [e["summary"] for e in store.list_events(session["session_id"])] == ["a", "b"]
    assert [e["summary"] for e in store.list_events(session["session_id"], since=early["ts"])] == ["b"]
    assert store.get_event(late["event_id"])["summary"] == "b"
    with pytest.raises(StoreError):
        store.add_event({"summary": "no session"})


def test_events_in_the_same_millisecond_keep_write_order(store):
    """Events with the same ts keep their write order."""
    session = store.start_session("x", order())
    ts = "2026-10-10T10:00:00.000Z"
    for i in range(30):
        store.add_event({"session_id": session["session_id"], "ts": ts, "summary": str(i)})
    assert [e["summary"] for e in store.list_events(session["session_id"])] == [str(i) for i in range(30)]
    assert store.list_events(session["session_id"], since=ts) == []


def test_cameras_merge_and_go_offline(store):
    """Camera fields merge, and an old heartbeat means offline."""
    store.heartbeat("desk", label="Desk")
    store.put_camera({"camera_id": "desk", "focus_box": [0.1, 0.1, 0.4, 0.5]})
    desk = store.get_camera("desk")
    assert desk["label"] == "Desk" and desk["focus_box"] and store.is_online(desk)
    assert not store.is_online({**desk, "last_seen": "2026-01-01T00:00:00.000Z"})




def test_two_processes_share_one_file(tmp_path):
    """Two Store objects on one folder see each other's writes."""
    a, b = Store(tmp_path), Store(tmp_path)
    session = a.start_session("x", order())
    b.add_event({"session_id": session["session_id"], "summary": "seen by b", "ts": now()})
    assert a.list_events(session["session_id"])[0]["summary"] == "seen by b"
