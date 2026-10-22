"""Tests for POST /api/look: which frame is looked at, how faces name people in the prompt, the answer's shape, and
the errors, with a scripted model and a stub Rekognition client."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from local.devserver import build_app
from meanwhile import config
from meanwhile.api import cameras, look, uploads
from meanwhile.pipeline import look as looking, models
from meanwhile.store import now

from test_faces import StubRekognition, faces_with, noise_jpeg

KEY = {"X-Meanwhile-Key": config.API_KEY}


class Model:
    """A scripted vision model: answers with fixed text and camera, recording the prompts and images it was shown."""

    def __init__(self, answer: str = "Yes, I can see you at the desk.", camera: str = "Desk", fail: bool = False):
        """Answer with `answer` about `camera`, or raise ModelUnavailable when `fail`."""
        self.answer, self.camera, self.fail = answer, camera, fail
        self.prompts: list[str] = []
        self.images: list[int] = []

    def __call__(self, messages, schema, model):
        """One models.ask call."""
        if self.fail:
            raise models.ModelUnavailable("SageMaker meanwhile-test: timed out")
        self.prompts.append(messages[0]["content"])
        self.images.append(len(messages[0]["images"]))
        return {"answer": self.answer, "camera": self.camera}


@pytest.fixture
def model(monkeypatch) -> Model:
    """The scripted model in place of models.ask."""
    stub = Model()
    monkeypatch.setattr(models, "ask", stub)
    return stub


def camera_with_thumb(store, label: str, seed: int) -> str:
    """Register a camera and give it a heartbeat thumbnail; returns its id."""
    camera_id = cameras.register(store, {"label": label})[1]["camera"]["camera_id"]
    _, presigned = uploads.presign(store, {"camera_id": camera_id, "kind": "thumb", "ext": "jpg"})
    uploads.put_local(store, presigned["key"], noise_jpeg(seed))
    cameras.heartbeat(store, camera_id, {"thumb_key": presigned["key"]})
    return camera_id


def owner(store) -> None:
    """Register the owner, who face ID names 'you'."""
    store.put_person({"person_id": "owner", "name": "Owner", "role": "owner", "note": ""})


def test_the_owners_face_is_you_in_the_prompt_and_the_answer_names_the_frame(store, model):
    """'Do you see me?' on the Desk camera: the owner's face is listed as the user, the model sees one photo, and the
    answer comes back with the camera, the frame's time and a frame URL that stays after the next thumbnail."""
    owner(store)
    camera_with_thumb(store, "Desk", 1)
    stub = StubRekognition(["owner"], found=[[[0.45, 0.2, 0.1, 0.15]]])
    status, body = look.look(store, {"question": "do you see me?", "camera": "the desk camera"}, faces_with(stub))
    assert status == 200 and body["answer"] == "Yes, I can see you at the desk."
    assert body["camera_id"] == "desk" and body["camera"] == "Desk" and body["online"] is True
    assert body["frame_source"] == "thumbnail" and body["frame_at"] <= now()
    assert body["people"] == [{"person_id": "owner", "name": "Owner", "role": "owner", "camera_id": "desk"}]
    assert "you, the user (in the middle)" in model.prompts[0] and model.images == [1]
    assert '"do you see me?"' in model.prompts[0]
    camera_with_thumb(store, "Desk", 2)
    assert store.media_path(body["frame_url"].split("?")[0].removeprefix("/media/")).exists()


def test_without_a_face_nobody_is_named_and_clothes_never_name_anyone(store, model):
    """No face in view: the prompt lists no recognised face and tells the model never to name people by clothes."""
    owner(store)
    store.put_person({"person_id": "ravi", "name": "Ravi", "role": "helper", "note": "a red shirt"})
    camera_with_thumb(store, "Desk", 1)
    model.answer = "I can see someone at the desk, but I can't see their face well enough to tell who it is."
    status, body = look.look(store, {"question": "do you see me?", "camera": "desk"},
                             faces_with(StubRekognition(found=[[]])))
    assert status == 200 and body["people"] == []
    assert looking.NOBODY in model.prompts[0] and "clothes" in model.prompts[0]
    assert 'never assume a person is the user' in model.prompts[0]
    assert "red shirt" not in model.prompts[0] and "Ravi" not in model.prompts[0]


def test_strangers_and_unnamed_people_are_described_by_face_only(store, model, monkeypatch):
    """A face matching an unnamed person is spoken of by their label; a face matching nobody (unnamed people not
    kept) is someone not recognised."""
    camera_with_thumb(store, "Room", 1)
    store.put_person({"person_id": "unnamed-abc123", "name": "Person 3", "role": "", "unnamed": True, "number": 3})
    stub = StubRekognition(["unnamed-abc123", None], found=[[[0.05, 0.2, 0.1, 0.15], [0.8, 0.2, 0.05, 0.1]]])
    monkeypatch.setattr(config, "FACE_KEEP_UNNAMED", False)
    status, body = look.look(store, {"question": "who's in the room?"}, faces_with(stub))
    assert status == 200
    assert "an unnamed person (Person 3) (on the left); someone whose face I don't recognise (on the right)" \
        in model.prompts[0]
    assert [p["name"] for p in body["people"]] == ["an unnamed person (Person 3)"]


def test_no_camera_named_looks_through_every_online_camera_in_one_call(store, model):
    """Without a camera, every online camera's frame goes to the model in one call; the answer is about the camera
    the model names, and every frame looked at is listed. Offline cameras are left out."""
    camera_with_thumb(store, "Desk", 1)
    camera_with_thumb(store, "Room", 2)
    camera_with_thumb(store, "Door", 3)
    store.put_camera({"camera_id": "door", "last_seen": "2026-10-01T00:00:00.000Z"})
    model.camera, model.answer = "Room", "The lamp in the room is on."
    status, body = look.look(store, {"question": "is the lamp on?"}, None)
    assert status == 200 and body["camera_id"] == "room" and model.images == [2]
    assert [f["camera_id"] for f in body["looked_at"]] == ["desk", "room"]
    assert 'Photo 1 is from the "Desk" camera. Photo 2 is from the "Room" camera.' in model.prompts[0]


def test_a_live_frame_newer_than_the_thumbnail_is_used(store, model):
    """While the detector follows a camera, its newer live frame is looked at instead of the thumbnail."""
    camera_with_thumb(store, "Desk", 1)
    fresh = "2099-01-01T00:00:00.000Z"
    status, body = look.look(store, {"question": "what's on the table?"}, None,
                             live=lambda camera_id: (noise_jpeg(9), fresh) if camera_id == "desk" else None)
    assert status == 200 and body["frame_source"] == "live" and body["frame_at"] == fresh
    status, body = look.look(store, {"question": "what's on the table?"}, None,
                             live=lambda camera_id: (noise_jpeg(9), "2000-01-01T00:00:00.000Z"))
    assert body["frame_source"] == "thumbnail"


def test_a_named_offline_camera_answers_from_its_last_picture(store, model):
    """A camera named by the user is looked at even when offline; the reply says it is offline."""
    camera_with_thumb(store, "Desk", 1)
    store.put_camera({"camera_id": "desk", "last_seen": "2026-10-01T00:00:00.000Z"})
    status, body = look.look(store, {"question": "is my phone there?", "camera": "Desk"}, None)
    assert status == 200 and body["online"] is False


def test_errors_are_spoken_sentences(store, model):
    """An empty question, an unknown camera, no pictures yet and a model that doesn't answer each get a code and a
    sentence Alexa can say."""
    assert look.look(store, {"question": "  "}, None)[1]["error"] == "bad_question"
    status, body = look.look(store, {"question": "is the lamp on?"}, None)
    assert (status, body["error"]) == (409, "no_frame")
    cameras.register(store, {"label": "Desk"})
    status, body = look.look(store, {"question": "is the lamp on?", "camera": "desk"}, None)
    assert (status, body["error"]) == (409, "no_frame") and body["message"] == "The Desk camera hasn't sent me a picture yet."
    status, body = look.look(store, {"question": "is the lamp on?", "camera": "garage camera"}, None)
    assert (status, body["error"]) == (404, "unknown_camera")
    assert body["message"] == "I don't have a garage camera. I can look through the Desk camera."
    camera_with_thumb(store, "Desk", 1)
    model.fail = True
    status, body = look.look(store, {"question": "is the lamp on?"}, None)
    assert (status, body["error"]) == (503, "model_unavailable")


def test_post_api_look_needs_the_key_and_answers(store, model):
    """The route is behind the key like every /api route and answers through the dev server."""
    camera_with_thumb(store, "Desk", 1)
    client = TestClient(build_app(store, compile_order=lambda i, c: {}, faces=None))
    assert client.post("/api/look", json={"question": "do you see me?"}).status_code == 401
    reply = client.post("/api/look", json={"question": "do you see me?"}, headers=KEY)
    assert reply.status_code == 200 and reply.json()["camera_id"] == "desk"
    assert client.get(reply.json()["frame_url"]).status_code == 200
    assert client.get("/api/cameras", headers=KEY).json()["cameras"][0].keys().isdisjoint({"thumb_at", "thumb_key"})


def test_cameras_are_found_by_how_people_name_them(store):
    """A camera is found by its label or id however it is said; a name matching no camera finds none."""
    for label in ("Desk", "Living Room", "Door"):
        cameras.register(store, {"label": label})
    for said, camera_id in (("desk", "desk"), ("the desk camera", "desk"), ("the camera on desk", "desk"),
                            ("Living-Room", "living-room"), ("the camera in the living room", "living-room"),
                            ("garage", None), ("the camera", None)):
        assert (look.camera_named(store, said) or {}).get("camera_id") == camera_id, said


@pytest.mark.parametrize("box, where", [([0.0, 0.0, 0.2, 1.0], "on the left"), ([0.4, 0, 0.6, 1], "in the middle"),
                                        ([0.8, 0, 1.0, 1], "on the right")])
def test_face_places(box, where):
    """A face's place across the frame, in words."""
    assert looking.side(box) == where


def test_face_id_refused_answers_without_names_and_logs_one_line(store, model, capsys):
    """When Rekognition refuses DetectFaces (say, a role without the permission), the look still answers with nobody
    named, and the core logs one line naming the error instead of a traceback."""
    from botocore.exceptions import ClientError

    class Refused(StubRekognition):
        """A client whose DetectFaces is denied."""
        def detect_faces(self, **_kw):
            """Raise AccessDeniedException."""
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}}, "DetectFaces")

    camera_with_thumb(store, "Desk", 1)
    status, body = look.look(store, {"question": "do you see me?"}, faces_with(Refused()))
    out = capsys.readouterr()
    assert status == 200 and body["people"] == [] and looking.NOBODY in model.prompts[0]
    assert "face ID failed on desk, answering without names" in out.out and "AccessDeniedException" in out.out
    assert "Traceback" not in out.out + out.err


def test_a_face_matching_an_unregistered_id_is_nobody(store, model):
    """A face matching an id the store doesn't have (a collection shared with another core) names nobody and is
    spoken of as someone not recognised."""
    camera_with_thumb(store, "Desk", 1)
    stub = StubRekognition(["someone-elses-id"], found=[[[0.45, 0.2, 0.1, 0.15]]])
    status, body = look.look(store, {"question": "who's here?"}, faces_with(stub))
    assert status == 200 and body["people"] == [] and looking.STRANGER in model.prompts[0]


@pytest.mark.parametrize("question, said, spoken", [
    ("do you see me?", "Yes, I can see you at the desk. You're wearing a red t-shirt and a necklace.",
     "Yes, I can see you at the desk."),
    ("what am I doing?", "You are typing on the laptop. You're shirtless.", "You are typing on the laptop."),
    ("what am I wearing?", "You're wearing a red t-shirt.", "You're wearing a red t-shirt."),
    ("how do I look?", "Your hair looks neat.", "Your hair looks neat."),
    ("is the lamp on?", "Yes, the lamp on the desk is on.", "Yes, the lamp on the desk is on."),
])
def test_answers_say_what_happens_not_what_people_wear(store, model, question, said, spoken):
    """Look answers keep actions and objects and drop sentences about clothing, body or appearance, unless the
    question asks about appearance; the prompt says the same."""
    camera_with_thumb(store, "Desk", 1)
    model.answer = said
    assert look.look(store, {"question": question}, None)[1]["answer"] == spoken
    assert "never mention clothing, body, state of dress or appearance" in model.prompts[0]
