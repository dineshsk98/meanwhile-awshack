"""The analyser's plumbing with stand-in models: which uploads are analysed, the events written, clip timing,
the queue order, the dev server hook, and the people endpoints."""

from __future__ import annotations

import time
from datetime import timedelta

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

from local.devserver import build_app
from meanwhile import config
from meanwhile.api import cameras, people, sessions, uploads
from meanwhile.pipeline import analyze, identity, naming, video_model, vision
from meanwhile.pipeline.models import ModelUnavailable
from meanwhile.store import now, parse, to_iso

KEY = {"X-Meanwhile-Key": config.API_KEY}

BOX = [0.4, 0.4, 0.5, 0.5]
WATCH = {"kind": "object", "target": "watch", "cameras": "all", "alert_when": ["picked_up"],
         "clip_question": "What happens to the watch?", "summary_for_alexa": "keeping an eye on your watch"}
NOTES = {"kind": "notes", "target": "who comes in", "cameras": ["room"], "alert_when": [],
         "clip_question": "Who comes in?", "summary_for_alexa": "taking notes"}
JPEG = cv2.imencode(".jpg", np.zeros((90, 160, 3), np.uint8))[1].tobytes()


@pytest.fixture(autouse=True)
def vision_tracking(monkeypatch):
    """These tests cover the vision-model path, so the focus camera is followed by frame checks."""
    monkeypatch.setattr(config, "TRACKING", "vision")


def held_by_ravi(*_args) -> dict:
    """A frame check that sees Ravi holding the watch."""
    return {"visible": True, "held": True, "in_place": False, "box": [0.42, 0.3, 0.55, 0.45], "person": "Ravi"}


def not_seen(*_args) -> dict:
    """A frame check that can't see the watch, with Ravi nearby."""
    return {"visible": False, "held": False, "in_place": False, "box": None, "person": "Ravi"}


def resting_at(box: list[float]):
    """A frame check that sees the watch at rest in `box`, nobody near it."""
    return lambda *_args: {"visible": True, "held": False, "in_place": box == BOX, "box": box, "person": ""}


def later(seconds: float) -> str:
    """The time `seconds` from now."""
    return to_iso(parse(now()) + timedelta(seconds=seconds))


def clip_answer(*events):
    """A stand-in describe() answering with the given events over a 6 s clip."""
    def describe(path, session, label, people_):
        """Return the fixed answer."""
        return {"relevant": bool(events), "events": list(events)}, {"frames": [], "times": [], "duration_s": 6.0}
    return describe


def upload(store, kind: str, camera_id: str, ext: str = "jpg", at: str | None = None) -> str:
    """Write a frame or clip the way a camera page's upload would; returns its key."""
    key = f"{kind}s/{camera_id}/{uploads.stamp(at or now())}.{ext}"
    store.write_media(key, JPEG)
    return key


def start(store, order: dict) -> dict:
    """Desk and Room online, Ravi and Owner registered, and a session with the given order."""
    for label in ("Desk", "Room"):
        cameras.register(store, {"label": label})
    people.add(store, {"name": "Owner", "role": "owner", "note": "a blue shirt"})
    people.add(store, {"name": "Ravi", "role": "helper", "note": "a red shirt"})
    result = {"watch_order": order}
    if order["kind"] == "object":
        result.update(focus_object="watch", focus_camera_id="desk", focus_box=BOX)
    time.sleep(0.002)
    return store.start_session("keep an eye on my watch", result)


def test_focus_frame_logs_a_pick_up(store):
    """A focus-camera frame where the watch is held (in view) writes a quiet pick-up with the frame, its time and
    the person, and moves the box with the object."""
    session = start(store, WATCH)
    key = upload(store, "frame", "desk")
    outcome = analyze.handle_upload(store, key, check_frame=held_by_ravi)
    assert "picked_up/none" in outcome
    assert store.get_session(session["session_id"])["focus_box"] == [0.42, 0.3, 0.55, 0.45]
    (e,) = store.list_events(session["session_id"])
    assert (e["actor_id"], e["source"], e["frame_key"], e["camera_id"]) == ("ravi", "vision", key, "desk")
    assert e["happened_at"] == analyze.stamp_time(key.split("/")[2][:-4]) and e["ts"] >= e["happened_at"]
    assert store.get_session(session["session_id"])["state"] == "with_person"


def test_uploads_that_are_skipped(store):
    """No session, before the session, an unwatched camera, a frame from a non-focus camera, a thumbnail."""
    early = upload(store, "frame", "desk")
    assert analyze.handle_upload(store, early, check_frame=held_by_ravi).startswith("skipped: no active session")
    start(store, NOTES)
    assert "before the session" in analyze.handle_upload(store, early, check_frame=held_by_ravi)
    assert "doesn't watch" in analyze.handle_upload(store, upload(store, "clip", "desk", "mp4"), describe=clip_answer())
    assert "only the focus camera" in analyze.handle_upload(store, upload(store, "frame", "room"), check_frame=held_by_ravi)
    assert "not a frame or clip" in analyze.handle_upload(store, upload(store, "thumb", "room"))


def test_clips_attach_and_another_camera_brings_it_back(store):
    """A focus-camera clip adds no events but gives the pick-up its clip; after the watch left view, a Room clip's
    hand-back to the owner is the verdict, timed back from the clip's end (its key time). The owner was named by
    face earlier in the session; the giver the model can't name is the person seen holding the watch."""
    session = start(store, WATCH)
    naming.confirm(store, session["session_id"], "owner")
    analyze.handle_upload(store, upload(store, "frame", "desk"), check_frame=held_by_ravi)
    desk_clip = upload(store, "clip", "desk", "mp4", at=later(1))
    pick = {"t": 1.0, "action": "picked_up", "actor": "Ravi", "recipient": "", "summary": "", "alert": True}
    assert analyze.handle_upload(store, desk_clip, describe=clip_answer(pick)).endswith("no event")
    analyze.handle_upload(store, upload(store, "frame", "desk", at=later(2)), check_frame=not_seen)
    (left,) = analyze.tick(store, at=later(7))
    assert (left["action"], left["alert"], left["summary"]) == ("left_view", "heads_up", "Ravi carried your watch out of view.")
    end = later(20)
    room_clip = upload(store, "clip", "room", "mp4", at=end)
    outcome = analyze.handle_upload(store, room_clip, describe=clip_answer(
        {"t": 5.5, "action": "handed_to", "actor": "the person in red", "recipient": "you", "summary": "", "alert": True}))
    assert "handed_to/verdict" in outcome
    picked, _, verdict = store.list_events(session["session_id"])
    assert picked["clip_key"] == desk_clip and verdict["clip_key"] == room_clip
    assert round((parse(end) - parse(verdict["happened_at"])).total_seconds(), 1) == 0.5
    assert (verdict["actor_id"], verdict["recipient_id"]) == ("ravi", "owner")


def test_notes_from_a_clip(store):
    """A notes clip writes the model's sentence and never alerts; it names a registered person only once face ID
    recognised them in the session, and the prompt lists only those people."""
    session = start(store, NOTES)
    listed = []

    def describe(path, session_, label, people_):
        """Note who the prompt would list, then answer that Ravi came in."""
        listed.append([p["person_id"] for p in people_])
        return clip_answer({"t": 2, "action": "came into the room", "actor": "Ravi", "actor_wears": "a red shirt",
                            "recipient": "", "summary": "Ravi came in.", "alert": True})(path, session_, label, people_)
    analyze.handle_upload(store, upload(store, "clip", "room", "webm"), describe=describe)
    naming.confirm(store, session["session_id"], "ravi", "a red shirt")
    analyze.handle_upload(store, upload(store, "clip", "room", "webm", at=later(30)), describe=describe)
    clothes_only, by_face = store.list_events(session["session_id"])
    assert (clothes_only["summary"], clothes_only["alert"], clothes_only["source"]) ==         ("An unnamed person (Person 1) came into the room.", "none", "video_model")
    assert (by_face["summary"], by_face["actor_id"]) == ("Ravi came in.", "ravi")
    assert listed[0] == [] and set(listed[1]) == {"ravi", clothes_only["actor_id"]}


def test_settling_somewhere_new_moves_the_box_and_the_reference(store):
    """Set down on the keyboard: a quiet 'moved', the new box on the session and the heartbeat, and that frame
    kept as the new reference; while held, the heartbeat says so."""
    session = start(store, WATCH)
    analyze.handle_upload(store, upload(store, "frame", "desk"), check_frame=held_by_ravi)
    assert cameras.heartbeat(store, "desk", {})[1]["watch"]["held"] is True
    frame = upload(store, "frame", "desk", at=later(3))
    assert "moved/none" in analyze.handle_upload(store, frame, check_frame=resting_at([0.7, 0.5, 0.95, 0.7]))
    after = store.get_session(session["session_id"])
    assert after["focus_box"] == [0.7, 0.5, 0.95, 0.7] and after["state"] == "on_surface"
    assert after["ref_frame_key"].startswith("refs/desk/") and store.media_path(after["ref_frame_key"]).read_bytes() == JPEG
    assert cameras.heartbeat(store, "desk", {})[1]["watch"] == {"active": True, "session_id": session["session_id"],
                                                                 "focus_box": [0.7, 0.5, 0.95, 0.7], "held": False}


def test_the_majority_relabels_stored_events(store):
    """A stray reading names Ravi; two more name the owner, so the stored pick-up is corrected."""
    session = start(store, WATCH)
    def held_by(name):
        """A frame check that sees `name` holding the watch."""
        return lambda *_args: {**held_by_ravi(), "person": name}
    for i, name in enumerate(["Ravi", "Owner", "Owner"]):
        analyze.handle_upload(store, upload(store, "frame", "desk", at=later(i)), check_frame=held_by(name))
    (picked,) = store.list_events(session["session_id"])
    assert (picked["actor_id"], picked["summary"]) == ("owner", "You picked up your watch.")
    assert store.get_session(session["session_id"])["state"] == "with_owner"


def test_queue_takes_frames_before_clips_and_survives_a_model_error(store):
    """Queued before the worker starts, the frame is analysed before the earlier clip; a model error is logged."""
    start(store, WATCH)
    order, logs = [], []
    def describe(path, *_):
        """Record the call, then fail like a stopped Ollama."""
        order.append("clip")
        raise ModelUnavailable("Start Ollama.")
    def check_frame(*args):
        """Record the call."""
        order.append("frame")
        return held_by_ravi()
    analyser = analyze.Analyser(store, tick_s=60, check_frame=check_frame, describe=describe, log=logs.append)
    analyser.submit(upload(store, "clip", "desk", "mp4"))
    analyser.submit(upload(store, "frame", "desk"))
    analyser.submit("thumbs/desk/20261013T100000000Z.jpg")
    analyser.start()
    analyser.drain()
    analyser.stop()
    assert order == ["frame", "clip"]
    assert any("model unavailable" in line for line in logs)
    assert analyser.status()["recent"][0]["outcome"].startswith("model unavailable")


def test_older_frames_are_skipped_when_behind(store):
    """With two frames from one camera waiting, only the newer is checked; another camera's frame still is."""
    start(store, WATCH)
    checked = []
    def check_frame(*_args):
        """Record that a frame was checked."""
        checked.append(1)
        return held_by_ravi()
    analyser = analyze.Analyser(store, tick_s=60, check_frame=check_frame, log=lambda _: None)
    old = upload(store, "frame", "desk")
    time.sleep(0.002)
    new = upload(store, "frame", "desk")
    analyser.submit(old)
    analyser.submit(new)
    analyser.start()
    analyser.drain()
    analyser.stop()
    outcomes = {r["key"]: r["outcome"] for r in analyser.status()["recent"]}
    assert outcomes[old].startswith("skipped: a newer frame") and "picked_up" in outcomes[new]
    assert len(checked) == 1


def test_a_clip_that_waited_long_enough_goes_before_frames(store, monkeypatch):
    """Once the oldest clip has waited CLIP_MAX_WAIT_S, it is analysed before a waiting frame."""
    start(store, WATCH)
    monkeypatch.setattr(analyze, "CLIP_MAX_WAIT_S", 0)
    analyser = analyze.Analyser(store, tick_s=60, log=lambda _: None)
    clip, frame = upload(store, "clip", "desk", "mp4"), upload(store, "frame", "desk")
    analyser.submit(frame)
    analyser.submit(clip)
    assert analyser.next_item()[0] == clip and analyser.next_item()[0] == frame


def test_put_on_the_dev_server_starts_analysis(store):
    """A local PUT of a frame reaches the analyser and the heads-up appears in the contract's events."""
    session = start(store, WATCH)
    analyser = analyze.Analyser(store, tick_s=60, check_frame=held_by_ravi, log=lambda _: None).start()
    client = TestClient(build_app(store, compile_order=lambda *_: {}, analyser=analyser), headers=KEY)
    key = client.post("/api/uploads", json={"camera_id": "desk", "kind": "frame", "ext": "jpg"}).json()["key"]
    assert client.put(f"/api/media/{key}", content=JPEG).status_code == 200
    analyser.drain()
    analyser.stop()
    (e,) = client.get(f"/api/sessions/{session['session_id']}/events").json()["events"]
    assert (e["action"], e["actor_name"], e["frame_url"].split("?")[0]) == ("picked_up", "Ravi", f"/media/{key}")
    assert client.get("/api/debug/analysis").json()["recent"][0]["key"] == key


def test_people_endpoints(store):
    """Add, list and remove people; one owner at most; bad roles are refused politely."""
    client = TestClient(build_app(store, compile_order=lambda *_: {}), headers=KEY)
    assert client.post("/api/people", json={"name": "Owner", "role": "owner", "note": "blue shirt"}).status_code == 200
    assert client.post("/api/people", json={"name": "Ravi", "role": "owner"}).json()["error"] == "owner_exists"
    assert client.post("/api/people", json={"name": "Ravi", "role": "boss"}).json()["error"] == "bad_role"
    assert client.post("/api/people", json={"name": "Ravi", "role": "helper", "note": "red shirt"}).status_code == 200
    assert [p["person_id"] for p in client.get("/api/people").json()["people"]] == ["owner", "ravi"]
    assert client.delete("/api/people/ravi").status_code == 200
    assert client.delete("/api/people/ravi").status_code == 404


def test_box_change_and_the_still_shortcut():
    """The same box (even brighter overall) is in place without asking the model; a covered box has changed a lot."""
    scene = np.random.default_rng(1).integers(0, 255, (180, 320, 3), dtype=np.uint8)
    covered = scene.copy()
    covered[90:180, 100:220] = 30
    jpeg = lambda image: cv2.imencode(".jpg", image)[1].tobytes()
    box = [0.3, 0.5, 0.7, 1.0]
    brighter = np.clip(scene.astype(int) + 20, 0, 255).astype(np.uint8)
    assert vision.box_change(jpeg(brighter), jpeg(scene), box) < 0.05
    assert vision.box_change(jpeg(covered), jpeg(scene), box) > 0.5
    assert vision.check_frame("watch", "Desk", jpeg(scene), box, "", jpeg(scene))["in_place"] is True


def test_object_session_keeps_a_reference_frame(store):
    """Starting an object order copies the focus camera's latest thumbnail as the session's reference."""
    cameras.register(store, {"label": "Desk"})
    thumb = upload(store, "thumb", "desk")
    cameras.heartbeat(store, "desk", {"thumb_key": thumb})
    compiled = {"watch_order": WATCH, "focus_object": "watch", "focus_camera_id": "desk", "focus_box": BOX}
    status, body = sessions.watch_orders(store, {"instruction": "keep an eye on my watch"}, lambda *_: compiled)
    key = body["session"]["ref_frame_key"]
    assert status == 201 and key.startswith("refs/desk/") and store.media_path(key).read_bytes() == JPEG


def test_sampling_a_clip(tmp_path):
    """Eight evenly spaced frames with their times, from a WebM that doesn't say how many frames it has."""
    path = tmp_path / "clip.webm"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"VP80"), 10, (320, 180))
    for i in range(60):
        writer.write(np.full((180, 320, 3), i * 4, np.uint8))
    writer.release()
    clip = video_model.sample(path)
    assert len(clip["frames"]) == 8 and clip["times"][0] == 0.0 and clip["times"] == sorted(clip["times"])
    assert 5.5 <= clip["duration_s"] <= 6.5
    first = cv2.imdecode(np.frombuffer(clip["frames"][0], np.uint8), cv2.IMREAD_GRAYSCALE)
    last = cv2.imdecode(np.frombuffer(clip["frames"][-1], np.uint8), cv2.IMREAD_GRAYSCALE)
    assert first.shape[1] == video_model.FRAME_WIDTH and last.mean() > first.mean() + 150


def test_people_are_described_by_what_they_wear():
    """The models read each person with their note as typed, the owner marked as the user."""
    people = [{"person_id": "owner", "name": "Owner", "role": "owner", "note": "No shirt"},
              {"person_id": "helper", "name": "helper", "role": "helper", "note": "white shirt"}]
    assert identity.describe_people(people) == 'Owner [the user, call them "you"]: no shirt; helper: white shirt'
    assert [identity.person_id_for(n, people) for n in ("Owner", "you", "helper", "Arun", "", "none")] == \
        ["owner", "owner", "helper", "unknown", "", ""]