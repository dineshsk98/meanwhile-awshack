"""Tests for the recent window (heartbeat thumbnails kept for the last few minutes, pruned by age and count) and
POST /api/look-back, with a scripted video model and a stub Rekognition client."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from local.devserver import build_app
from meanwhile import config
from meanwhile.api import cameras, look, sessions, uploads
from meanwhile.pipeline import look as looking, models, recent
from meanwhile.store import to_iso

from test_faces import StubRekognition, faces_with, noise_jpeg

KEY = {"X-Meanwhile-Key": config.API_KEY}


def ago(seconds: float) -> str:
    """A timestamp `seconds` before now."""
    return to_iso(datetime.now(timezone.utc) - timedelta(seconds=seconds))


class Model:
    """A scripted video model: answers with fixed text, whether the question reached earlier, and a frame number,
    recording the prompts, image counts and model names it was given."""

    def __init__(self, answer: str = "In the last 5 minutes, you sat down at the desk.", earlier: bool = False,
                 frame: int = 1, fail: bool = False):
        """Answer as given, or raise ModelUnavailable when `fail`."""
        self.answer, self.earlier, self.frame, self.fail = answer, earlier, frame, fail
        self.prompts: list[str] = []
        self.images: list[int] = []
        self.models: list[str] = []

    def __call__(self, messages, schema, model):
        """One models.ask call."""
        if self.fail:
            raise models.ModelUnavailable("SageMaker meanwhile-test: timed out")
        self.prompts.append(messages[0]["content"])
        self.images.append(len(messages[0]["images"]))
        self.models.append(model)
        return {"answer": self.answer, "asks_earlier": self.earlier, "frame": self.frame}


@pytest.fixture
def model(monkeypatch) -> Model:
    """The scripted model in place of models.ask."""
    stub = Model()
    monkeypatch.setattr(models, "ask", stub)
    return stub


def camera_with_window(store, label: str, ages: list[float]) -> str:
    """Register a camera and keep frames taken `ages` seconds ago in its window; returns its id."""
    camera_id = cameras.register(store, {"label": label})[1]["camera"]["camera_id"]
    for n, seconds in enumerate(ages):
        key = f"thumbs/{camera_id}/x{n}.jpg"
        store.write_media(key, noise_jpeg(n + 1))
        recent.keep(store, camera_id, key, ago(seconds))
    return camera_id


def test_heartbeat_thumbnails_are_kept_for_the_window_and_pruned_by_age_and_count(store, monkeypatch):
    """Each new heartbeat thumbnail joins the camera's window; frames older than the window, or beyond the cap, are
    deleted; removing the camera clears its window."""
    monkeypatch.setattr(config, "RECENT_WINDOW_S", 300.0)
    monkeypatch.setattr(config, "RECENT_MAX_FRAMES", 3)
    cameras.register(store, {"label": "Desk"})
    for data in (b"one", b"two"):
        _, presigned = uploads.presign(store, {"camera_id": "desk", "kind": "thumb", "ext": "jpg"})
        uploads.put_local(store, presigned["key"], noise_jpeg(len(data)))
        cameras.heartbeat(store, "desk", {"thumb_key": presigned["key"]})
    assert len(recent.frames(store, "desk")) == 2
    recent.clear(store, "desk")
    for seconds in (400, 250, 200, 100, 0):
        store.write_media("thumbs/desk/t.jpg", noise_jpeg(1))
        recent.keep(store, "desk", "thumbs/desk/t.jpg", ago(seconds))
    kept = recent.frames(store, "desk")
    assert len(kept) == 3 and len(list(store.media_path("recent/desk").iterdir())) == 3
    assert kept[0][0] > ago(210) and kept[-1][0] > ago(5)
    cameras.remove(store, "desk")
    assert recent.frames(store, "desk") == []


@pytest.mark.parametrize("seconds, words", [(300, "5 minutes"), (60, "minute"), (90, "90 seconds")])
def test_the_window_is_spoken_from_the_setting(seconds, words):
    """The window's length in words comes from the setting."""
    assert recent.window_words(seconds) == words


def test_frames_are_spread_over_the_window():
    """At most n frames, evenly spaced, always with the first and the last."""
    assert recent.spread(list(range(10)), 4) == [0, 3, 6, 9]
    assert recent.spread([1, 2], 8) == [1, 2] and recent.spread(list(range(5)), 1) == [4]


def test_look_back_sends_the_windows_frames_with_their_times_and_faces(store, model):
    """'What did I do?': the camera's recent frames (at most RECENT_MODEL_FRAMES) go to the video model in one call,
    oldest first, each with how long ago it was taken and the faces in it; the answer comes back with the window and
    the frame the model picked."""
    store.put_person({"person_id": "owner", "name": "Owner", "role": "owner", "note": ""})
    camera_with_window(store, "Desk", [float(s) for s in range(290, -1, -10)])
    found = [[]] * (config.RECENT_MODEL_FRAMES - 1) + [[[0.45, 0.2, 0.1, 0.15]]]
    stub = StubRekognition(["owner"], found=found)
    model.frame = config.RECENT_MODEL_FRAMES
    status, body = look.look_back(store, {"question": "what did I do?"}, faces_with(stub))
    assert status == 200 and body["answer"] == model.answer and body["asks_earlier"] is False
    assert body["window"] == "5 minutes" and body["window_s"] == config.RECENT_WINDOW_S
    assert model.images == [config.RECENT_MODEL_FRAMES] and model.models == [config.VIDEO_MODEL]
    prompt = model.prompts[0]
    assert 'Photo 1: the "Desk" camera, 4 min 5' in prompt and "s ago." in prompt
    assert "you, the user (in the middle)" in prompt and "the last 5 minutes" in prompt
    assert body["people"] == [{"person_id": "owner", "name": "Owner", "role": "owner", "camera_id": "desk"}]
    assert body["frame_at"] == recent.frames(store, "desk")[-1][0] and body["frame_url"].startswith("/media/looks/desk/")
    assert body["looked_at"][0]["kept"] == 30 and body["looked_at"][0]["frames"] == config.RECENT_MODEL_FRAMES


def test_a_question_about_earlier_is_flagged_and_cameras_share_the_frames(store, model):
    """Without a camera, every camera with recent frames shares the model's frame budget; the model's asks_earlier
    comes back as given. Offline cameras still count while their frames are in the window."""
    camera_with_window(store, "Desk", [float(s) for s in range(100, -1, -10)])
    camera_with_window(store, "Room", [float(s) for s in range(100, -1, -10)])
    store.put_camera({"camera_id": "room", "last_seen": ago(60)})
    model.earlier = True
    status, body = look.look_back(store, {"question": "what did I do 10 minutes ago?"}, None)
    assert status == 200 and body["asks_earlier"] is True
    assert model.images == [config.RECENT_MODEL_FRAMES]
    assert sorted(c["camera_id"] for c in body["looked_at"]) == ["desk", "room"]
    assert {c["frames"] for c in body["looked_at"]} == {config.RECENT_MODEL_FRAMES // 2}


def test_look_back_errors_are_spoken_sentences(store, model):
    """An empty question, an unknown camera, no recent pictures and a model that doesn't answer each get a code and
    a sentence Alexa can say, naming the window."""
    assert look.look_back(store, {"question": " "}, None)[1]["error"] == "bad_question"
    status, body = look.look_back(store, {"question": "what did I do?"}, None)
    assert (status, body["error"]) == (409, "no_frames")
    assert body["message"] == "I don't have any pictures from any camera in the last 5 minutes."
    cameras.register(store, {"label": "Desk"})
    assert look.look_back(store, {"question": "what did I do?", "camera": "garage"}, None)[0] == 404
    status, body = look.look_back(store, {"question": "what happened?", "camera": "the desk camera"}, None)
    assert body["message"] == "I don't have any pictures from the Desk camera in the last 5 minutes."
    camera_with_window(store, "Room", [10.0])
    model.fail = True
    assert look.look_back(store, {"question": "what did I do?"}, None)[1]["error"] == "model_unavailable"


def test_post_api_look_back_and_status_show_the_window(store, model):
    """The route is behind the key and answers through the dev server; /api/status says how far back it sees."""
    camera_with_window(store, "Desk", [20.0, 10.0])
    client = TestClient(build_app(store, compile_order=lambda i, c: {}, faces=None))
    assert client.post("/api/look-back", json={"question": "what did I do?"}).status_code == 401
    reply = client.post("/api/look-back", json={"question": "what did I do?"}, headers=KEY)
    assert reply.status_code == 200 and reply.json()["camera_id"] == "desk"
    assert client.get(reply.json()["frame_url"]).status_code == 200
    assert sessions.status(store, None, None)[1]["recent_window_s"] == config.RECENT_WINDOW_S


def test_the_prompt_never_names_people_by_clothes(store, model):
    """With no face recognised, the look-back prompt says so and tells the model to call them someone."""
    store.put_person({"person_id": "ravi", "name": "Ravi", "role": "helper", "note": "a red shirt"})
    camera_with_window(store, "Desk", [10.0])
    look.look_back(store, {"question": "did anyone come in?"}, faces_with(StubRekognition(found=[[]])))
    assert looking.NOBODY in model.prompts[0] and "clothes" in model.prompts[0] and "red shirt" not in model.prompts[0]


def test_look_back_tells_what_was_done_not_what_people_wore(store, model):
    """'What did I do?': the prompt asks for actions and objects only, and a sentence about clothing or body is
    dropped from the answer; 'what am I wearing?' keeps it."""
    camera_with_window(store, "Desk", [20.0, 10.0])
    model.answer = ("In the last 5 minutes, you were sitting on the bed, looking around. You were shirtless and "
                    "wearing a necklace.")
    status, body = look.look_back(store, {"question": "what did I do?"}, None)
    assert status == 200 and body["answer"] == "In the last 5 minutes, you were sitting on the bed, looking around."
    assert "never mention clothing, body, state of dress or appearance" in model.prompts[0]
    model.answer = "In the last 5 minutes, you were wearing a blue t-shirt."
    assert look.look_back(store, {"question": "what was I wearing?"}, None)[1]["answer"] == model.answer
