"""Tests for things' states: recorded from watch-order clip events, answered by POST /api/state-of from the last
recorded state, and by the video when there is none or it is unclear; plus orders picking a camera by its label."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

from local.devserver import build_app
from meanwhile import config
from meanwhile.api import cameras, look
from meanwhile.pipeline import analyze, models, orders, recent, states, tracker, video_model
from meanwhile.pipeline import look as looking
from meanwhile.store import to_iso

from test_faces import noise_jpeg

KEY = {"X-Meanwhile-Key": config.API_KEY}
CAMERAS = [{"camera_id": "desk", "label": "Desk"}, {"camera_id": "room", "label": "Room"},
           {"camera_id": "door", "label": "Door"}]


def ago(seconds: float) -> str:
    """A timestamp `seconds` before now."""
    return to_iso(datetime.now(timezone.utc) - timedelta(seconds=seconds))


class Model:
    """A scripted video check: `answer`, with state `state`, said to be `visible`, shown in photo `evidence`, changed
    by `changed_by`; records the prompts."""

    def __init__(self):
        """Answer 'The front door is open.', visibly, from photo 1, nobody named."""
        self.prompts: list[str] = []
        self.answer, self.state, self.visible, self.evidence, self.changed_by = (
            "The front door is open.", "open", True, 1, "")
        self.matches = 1

    def __call__(self, messages, schema, model):
        """One models.ask call."""
        self.prompts.append(messages[0]["content"])
        return {"answer": self.answer, "state": self.state, "visible": self.visible, "evidence": self.evidence,
                "matches": self.matches, "changed_by": self.changed_by, "frame": 1}


@pytest.fixture
def model(monkeypatch) -> Model:
    """The scripted model in place of models.ask."""
    stub = Model()
    monkeypatch.setattr(models, "ask", stub)
    return stub


def door_watch(store) -> dict:
    """Desk and Door cameras, Ravi registered, and an active door watch."""
    for label in ("Desk", "Door"):
        cameras.register(store, {"label": label})
    store.put_person({"person_id": "ravi", "name": "Ravi", "role": "helper"})
    return store.start_session("watch the front door and tell me when it's locked or unlocked", {"watch_order": {
        "kind": "monitor", "target": "the front door being locked or unlocked", "cameras": ["door"],
        "clip_question": "Is the front door locked or unlocked, and by whom?", "report": ["the front door locked"],
        "alert_when": [], "summary_for_alexa": "watching the front door"}})


def write_change(store, session, camera_id: str, value: str, seconds_ago: float, actor: str = "ravi",
                 clip_key: str | None = None) -> None:
    """Write a clip event that left the front door in `value`, through the analyser's write path."""
    event = {"action": f"{value} the front door", "actor_id": actor, "summary": f"Ravi {value} the front door.",
             "alert": "none", "camera_id": camera_id, "happened_at": ago(seconds_ago),
             **tracker.changed_state({"thing": "the front door", "state": value})}
    if clip_key:
        event["clip_key"] = clip_key
    analyze.write(store, session, {"events": [event], "session": {}}, {"source": "video_model"})


def test_a_clip_event_that_changes_something_records_its_state(store):
    """An event naming a thing and its new state records it for that camera; one without either records nothing;
    the clip schema asks the model for both."""
    session = door_watch(store)
    write_change(store, session, "door", "locked", 30)
    analyze.write(store, session, {"events": [{"action": "came in", "summary": "Ravi came in.", "camera_id": "door",
                                               "happened_at": ago(20)}], "session": {}}, {})
    (state,) = store.list_states()
    assert (state["key"], state["value"], state["by"], state["camera_id"]) == ("front door", "locked", "ravi", "door")
    assert tracker.changed_state({"thing": "the lamp", "state": ""}) == {}
    assert {"thing", "state"} <= set(video_model.schema()["properties"]["events"]["items"]["required"])


def test_a_clear_recorded_state_answers_with_when_and_who(store, model):
    """'Is the front door locked?' answers from the newest recorded state, without a model call; 'door' finds
    'the front door'."""
    session = door_watch(store)
    write_change(store, session, "door", "unlocked", 300)
    write_change(store, session, "door", "locked", 60)
    status, body = look.state_of(store, {"question": "is the front door locked?", "thing": "door"}, None)
    assert status == 200 and body["source"] == "state" and body["value"] == "locked"
    assert body["by_name"] == "Ravi" and body["camera"] == "Door" and model.prompts == []


def test_conflicting_states_look_at_the_video_around_the_last_change(store, model, tmp_path):
    """Two cameras that disagree make the state unclear: the video model answers from the last change's clip."""
    session = door_watch(store)
    write_change(store, session, "desk", "locked", 120)
    clip = store.media_path("clips/door/c.mp4")
    clip.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 90))
    for n in range(20):
        writer.write(np.full((90, 160, 3), n * 10, np.uint8))
    writer.release()
    write_change(store, session, "door", "unlocked", 60, clip_key="clips/door/c.mp4")
    status, body = look.state_of(store, {"question": "is the front door locked?", "thing": "the front door"}, None)
    assert status == 200 and body["source"] == "video" and body["answer"] == "The front door is open."
    assert 'The last recorded change was the front door becoming "unlocked"' in model.prompts[0]
    assert "is the front door locked?" in model.prompts[0]
    assert body["frame_url"].startswith("/media/looks/door/")


def test_a_state_that_says_nothing_looks_at_the_recent_frames(store, model):
    """A recorded state of 'unclear' with no clip uses the camera's recent frames."""
    session = door_watch(store)
    store.write_media("thumbs/door/t.jpg", noise_jpeg(1))
    recent.keep(store, "door", "thumbs/door/t.jpg", ago(10))
    write_change(store, session, "door", "unclear", 30)
    status, body = look.state_of(store, {"question": "is the front door locked?", "thing": "front door"}, None)
    assert status == 200 and body["source"] == "video"


def test_no_status_checks_the_video_and_stores_its_conclusion(store, model):
    """Nothing stored: the video is checked (the newest frame when there are no recent frames, on the camera whose
    label is in the thing) and its confident conclusion is stored, so the next question answers from it without a
    model call."""
    door_watch(store)
    store.write_media("thumbs/door/t.jpg", noise_jpeg(1))
    store.put_camera({"camera_id": "door", "thumb_key": "thumbs/door/t.jpg", "thumb_at": ago(2)})
    status, body = look.state_of(store, {"question": "is the front door locked?", "thing": "the front door"}, None)
    assert status == 200 and body["source"] == "video" and body["stored"] is True and body["value"] == "open"
    assert body["camera_id"] == "door" and len(model.prompts) == 1
    status, body = look.state_of(store, {"question": "is the front door open?", "thing": "front door"}, None)
    assert status == 200 and body["source"] == "state" and body["checked_by"] == "video" and body["value"] == "open"
    assert len(model.prompts) == 1


def test_a_stale_status_is_checked_again(store, model, monkeypatch):
    """A status older than MEANWHILE_STATE_MAX_AGE_S is checked on the video again, and replaced."""
    monkeypatch.setattr(config, "STATE_MAX_AGE_S", 600.0)
    session = door_watch(store)
    write_change(store, session, "door", "locked", 900)
    store.write_media("thumbs/door/t.jpg", noise_jpeg(1))
    recent.keep(store, "door", "thumbs/door/t.jpg", ago(5))
    status, body = look.state_of(store, {"question": "is the front door locked?", "thing": "the front door"}, None)
    assert status == 200 and body["source"] == "video" and body["stored"] is True and len(model.prompts) == 1
    (state,) = store.list_states()
    assert (state["value"], state["source"]) == ("open", "video")


def test_an_inconclusive_video_check_stores_nothing(store, model):
    """When the model can't tell (not confident, or no state), the answer is spoken but nothing is stored."""
    door_watch(store)
    store.write_media("thumbs/door/t.jpg", noise_jpeg(1))
    recent.keep(store, "door", "thumbs/door/t.jpg", ago(5))
    model.visible = False
    status, body = look.state_of(store, {"question": "is the front door locked?", "thing": "the front door"}, None)
    assert status == 200 and body["source"] == "video" and body["stored"] is False
    model.visible, model.state = True, "unknown"
    assert look.state_of(store, {"question": "is the front door locked?", "thing": "the front door"}, None)[1]["stored"] \
        is False
    assert store.list_states() == [] and len(model.prompts) == 2


def test_state_of_errors_and_route(store, model):
    """An empty question is 400, an unknown camera 404; the route is behind the key."""
    door_watch(store)
    assert look.state_of(store, {"question": " "}, None)[0] == 400
    assert look.state_of(store, {"question": "is it locked?", "camera": "garage"}, None)[0] == 404
    client = TestClient(build_app(store, compile_order=lambda i, c: {}, faces=None))
    assert client.post("/api/state-of", json={"question": "is it locked?"}).status_code == 401


def test_a_watch_naming_a_camera_label_uses_that_camera():
    """'Watch the front door and tell me when it's locked or unlocked' becomes a monitor order on the Door camera,
    its question and report from the user's words; object orders still search every camera."""
    meant = {"kind": "monitor", "target": "the front door being locked or unlocked",
             "clip_question": "Is the front door locked or unlocked, and by whom?",
             "report": ["the front door locked", "the front door unlocked"], "alert_when": []}
    order = orders.build("watch the front door and tell me when it's locked or unlocked", meant, CAMERAS,
                         lambda *_: None)["watch_order"]
    assert order["kind"] == "monitor" and order["cameras"] == ["door"]
    assert order["clip_question"] == "Is the front door locked or unlocked, and by whom?"
    assert orders.labelled_in(CAMERAS, "keep a log of the kitchen") is None
    assert orders.labelled_in(CAMERAS, "tell me who comes into the room")["camera_id"] == "room"



def _door_frame(store) -> None:
    """The Door camera with one recent frame."""
    store.write_media("thumbs/door/t.jpg", noise_jpeg(1))
    recent.keep(store, "door", "thumbs/door/t.jpg", ago(5))


def test_a_frame_check_never_names_who_changed_it(store, model):
    """On recent frames (no change clip) an invented actor is dropped from the answer and never stored, and the
    prompt tells the model nobody can be named."""
    store.put_person({"person_id": "owner", "name": "Owner", "role": "owner"})
    door_watch(store)
    _door_frame(store)
    model.answer, model.state, model.changed_by = "The door is locked. You locked it.", "locked", "you"
    status, body = look.state_of(store, {"question": "is the door locked?", "thing": "the front door"}, None)
    assert status == 200 and body["answer"] == "The door is locked."
    assert 'changed_by: always ""' in model.prompts[0]
    (state,) = store.list_states()
    assert state["by"] == "" and state["value"] == "locked"


def test_unknown_and_evidence_less_states_are_not_stored(store, model):
    """'Unknown' is never stored; a 'locked' with no evidence photo, or not said to be visible, isn't either."""
    door_watch(store)
    _door_frame(store)
    for state, visible, evidence in (("unknown", True, 1), ("locked", True, 0), ("locked", False, 1),
                                     ("locked", True, 9)):
        model.state, model.visible, model.evidence = state, visible, evidence
        status, body = look.state_of(store, {"question": "is the door locked?", "thing": "the front door"}, None)
        assert status == 200 and body["stored"] is False, (state, visible, evidence)
    assert store.list_states() == []


def test_a_change_clip_may_name_a_face_recognised_actor_only(store, model, monkeypatch):
    """With the last change's clip, a person face ID recognised in it may be named as the one who changed it; a
    name nobody's face showed is dropped."""
    session = door_watch(store)
    clip = store.media_path("clips/door/c.mp4")
    clip.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 90))
    for n in range(20):
        writer.write(np.full((90, 160, 3), n * 10, np.uint8))
    writer.release()
    write_change(store, session, "desk", "locked", 120)
    write_change(store, session, "door", "unlocked", 60, clip_key="clips/door/c.mp4")
    monkeypatch.setattr(looking, "faces_in", lambda faces, store_, frame: [{"person_id": "ravi", "box": [0, 0, 1, 1]}])
    model.answer, model.state, model.changed_by = "The front door is unlocked.", "unlocked", "Ravi"
    body = look.state_of(store, {"question": "is the front door locked?", "thing": "the front door"}, None)[1]
    assert body["answer"] == "The front door is unlocked. I saw Ravi change it."
    model.changed_by = "Priya"
    door = next(x for x in store.list_states() if x["camera_id"] == "door")
    store.set_state({**door, "value": "unclear", "clip_key": "clips/door/c.mp4", "source": "event"})
    body = look.state_of(store, {"question": "is the front door locked?", "thing": "the front door"}, None)[1]
    assert "Priya" not in body["answer"]


def test_an_offline_cameras_old_frame_is_never_checked(store, model, monkeypatch):
    """The Door camera went offline and its last frame is older than MEANWHILE_STATE_MAX_AGE_S: no video check, no
    stored status, and a 409 saying which camera and when its last frame was."""
    monkeypatch.setattr(config, "STATE_MAX_AGE_S", 600.0)
    door_watch(store)
    store.write_media("thumbs/door/t.jpg", noise_jpeg(1))
    store.put_camera({"camera_id": "door", "thumb_key": "thumbs/door/t.jpg", "thumb_at": ago(18 * 60),
                      "last_seen": ago(18 * 60)})
    status, body = look.state_of(store, {"question": "is the door closed?", "thing": "the door"}, None)
    assert (status, body["error"], body["camera"]) == (409, "stale_frame", "Door")
    assert body["last_frame_at"] < ago(17 * 60) and model.prompts == [] and store.list_states() == []
    assert "light, shadows" in states.CHECK_PROMPT


def test_a_status_checked_on_a_fresh_frame_is_reused_after_the_camera_goes_offline(store, model, monkeypatch):
    """A conclusion from a fresh frame is stored at that frame's time and answers the next asks with no model call,
    even once the camera is offline, until it is older than MEANWHILE_STATE_MAX_AGE_S."""
    monkeypatch.setattr(config, "STATE_MAX_AGE_S", 600.0)
    door_watch(store)
    store.write_media("thumbs/door/t.jpg", noise_jpeg(1))
    store.put_camera({"camera_id": "door", "thumb_key": "thumbs/door/t.jpg", "thumb_at": ago(30)})
    assert look.state_of(store, {"question": "is the door closed?", "thing": "the door"}, None)[1]["stored"] is True
    store.put_camera({"camera_id": "door", "last_seen": ago(400)})
    for question, thing in (("is the door closed?", "the door"), ("is the front door locked?", "the front door")):
        body = look.state_of(store, {"question": question, "thing": thing}, None)[1]
        assert body["source"] == "state" and body["value"] == "open"
    assert len(model.prompts) == 1


def test_several_matching_things_in_view_are_described_and_nothing_is_stored(store, model):
    """Two doors in view and a question that doesn't say which: the answer describes each, the state is unknown and
    nothing is stored, even if the model still names a state; the prompt asks it not to pick one."""
    door_watch(store)
    store.write_media("thumbs/door/t.jpg", noise_jpeg(1))
    recent.keep(store, "door", "thumbs/door/t.jpg", ago(5))
    model.answer = "The door on the left is open, and the one in front is closed."
    model.state, model.matches = "open", 2
    status, body = look.state_of(store, {"question": "is the door closed?", "thing": "the door"}, None)
    assert status == 200 and body["answer"] == model.answer and body["stored"] is False and body["value"] == "unknown"
    assert store.list_states() == [] and "don't pick one" in model.prompts[0]
