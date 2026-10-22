"""Face ID with a stubbed Rekognition client: crops, index/search/forget, people with photos over the dev server,
naming person tracks once (retries, the episode-vote fallback, unknown faces), and naming people in clip events."""

from __future__ import annotations

import time
from concurrent.futures import Future

import cv2
import numpy as np
import pytest
from botocore.exceptions import ClientError
from starlette.testclient import TestClient

from local.devserver import build_app
from meanwhile import config
from meanwhile.pipeline import analyze, faces as face_id, identity, naming, track_rules, tracker, unnamed
from meanwhile.pipeline.faces import Faces, FaceError
from meanwhile.pipeline.track_rules import Track

KEY = {"X-Meanwhile-Key": config.API_KEY}

NO_FACE = ClientError({"Error": {"Code": "InvalidParameterException",
                                 "Message": "There are no faces in the image. Should be at least 1."}},
                      "SearchFacesByImage")


class StubRekognition:
    """A Rekognition client stand-in: indexes photos unless they are blank, and answers searches from a script
    ('noface', None for no match, or a person id)."""

    def __init__(self, answers=None, found=None):
        """Start with an empty collection, the scripted search answers and the scripted face boxes per frame."""
        self.faces: dict[str, str] = {}
        self.answers = list(answers or [])
        self.found = list(found or [])
        self.detected = 0
        self.searched: list[np.ndarray] = []
        self.deleted: list[str] = []
        self.calls: list[dict] = []

    def index_faces(self, **kw):
        """Index one face per photo, except a uniform (blank) image."""
        self.calls.append(kw)
        image = cv2.imdecode(np.frombuffer(kw["Image"]["Bytes"], np.uint8), cv2.IMREAD_COLOR)
        if image.std() == 0:
            return {"FaceRecords": []}
        face_id_ = f"face-{len(self.faces) + 1}"
        self.faces[face_id_] = kw["ExternalImageId"]
        return {"FaceRecords": [{"Face": {"FaceId": face_id_, "ExternalImageId": kw["ExternalImageId"]}}]}

    def detect_faces(self, **_kw):
        """The next scripted list of face boxes ([left, top, width, height] fractions)."""
        self.detected += 1
        boxes = self.found.pop(0)
        return {"FaceDetails": [{"BoundingBox": dict(zip(("Left", "Top", "Width", "Height"), b))} for b in boxes]}

    def search_faces_by_image(self, **kw):
        """The next scripted answer."""
        self.searched.append(cv2.imdecode(np.frombuffer(kw["Image"]["Bytes"], np.uint8), cv2.IMREAD_COLOR))
        answer = self.answers.pop(0)
        if answer == "noface":
            raise NO_FACE
        if answer is None:
            return {"FaceMatches": []}
        return {"FaceMatches": [{"Similarity": 99.7, "Face": {"FaceId": "f", "ExternalImageId": answer}}]}

    def list_faces(self, **kw):
        """The collection, two faces per page."""
        items = [{"FaceId": f, "ExternalImageId": p} for f, p in self.faces.items()]
        start = int(kw.get("NextToken") or 0)
        page = {"Faces": items[start:start + 2]}
        if start + 2 < len(items):
            page["NextToken"] = str(start + 2)
        return page

    def delete_faces(self, **kw):
        """Remove faces by id."""
        for f in kw["FaceIds"]:
            self.faces.pop(f, None)
            self.deleted.append(f)


def photo(blank: bool = False) -> bytes:
    """A small PNG: noise, or a blank one with no face."""
    image = np.zeros((120, 100, 3), np.uint8) if blank else np.random.default_rng(1).integers(0, 255, (120, 100, 3),
                                                                                              np.uint8)
    return cv2.imencode(".png", image)[1].tobytes()


def faces_with(stub: StubRekognition) -> Faces:
    """A face client over the stub with fixed settings."""
    return Faces(client=stub, collection="test-people", threshold=90, margin=0.25, upper=0.5)


def test_upper_body_crop_keeps_a_margin_and_stays_in_frame():
    """The top half of the person box, widened by the margin on every side, clipped to the frame."""
    image = np.zeros((360, 640, 3), np.uint8)
    crop = face_id.upper_body(image, [0.25, 0.25, 0.5, 0.75], margin=0.25, upper=0.5)
    assert crop.shape[:2] == (135, 240)
    edge = face_id.upper_body(image, [0.0, 0.0, 0.2, 1.0], margin=0.5, upper=0.5)
    assert edge.shape[:2] == (270, 192)


def test_search_names_a_match_and_treats_no_face_as_unknown():
    """A match gives its person id; 'no faces' gives None, not an error; other errors are FaceErrors."""
    stub = StubRekognition(["owner", "noface", None])
    faces = faces_with(stub)
    frame = np.full((360, 640, 3), 128, np.uint8)
    assert faces.identify(frame, [0.4, 0.1, 0.6, 0.9]) == "owner"
    assert faces.last["similarity"] == 99.7
    assert stub.searched[0].shape[:2] == (216, 192)
    assert faces.identify(frame, [0.4, 0.1, 0.6, 0.9]) is None and faces.last["face"] is False
    assert faces.identify(frame, [0.4, 0.1, 0.6, 0.9]) is None and faces.last["face"] is True

    class Broken:
        """A Rekognition client whose search is refused."""
        def search_faces_by_image(self, **_kw):
            """Raise AccessDenied."""
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "SearchFacesByImage")
    with pytest.raises(FaceError):
        faces_with(Broken()).identify(frame, [0.4, 0.1, 0.6, 0.9])


def test_index_and_forget_only_that_person():
    """Index stores the face under the person id; forget pages through the collection and deletes only theirs."""
    stub = StubRekognition()
    faces = faces_with(stub)
    ok = cv2.imencode(".jpg", np.random.default_rng(2).integers(0, 255, (80, 80, 3), np.uint8))[1].tobytes()
    for person in ("owner", "ravi", "owner", "ravi", "owner"):
        assert faces.index(person, ok)
    assert stub.calls[0]["ExternalImageId"] == "owner" and stub.calls[0]["MaxFaces"] == 1
    assert faces.index("owner", cv2.imencode(".jpg", np.zeros((80, 80, 3), np.uint8))[1].tobytes()) == []
    assert faces.forget("owner") == 3
    assert sorted(stub.faces.values()) == ["ravi", "ravi"]


def test_people_with_photos(store):
    """Photos are stored under the data dir and indexed; a photo without a face is refused with nothing kept;
    new photos replace the old faces; delete removes faces and photos."""
    stub = StubRekognition()
    client = TestClient(build_app(store, compile_order=lambda *_: {}, faces=faces_with(stub)), headers=KEY)
    fields = {"name": "Owner", "role": "owner", "note": "a blue shirt"}
    res = client.post("/api/people", data=fields, files=[("photo", ("me.png", photo(), "image/png"))])
    assert res.status_code == 200, res.text
    person = res.json()["person"]
    assert person["faces"] == 1 and person["photo_url"].split("?")[0] == "/media/people/owner/1.jpg"
    assert "face_ids" not in person and client.get(person["photo_url"]).status_code == 200
    assert list(stub.faces.values()) == ["owner"]

    res = client.post("/api/people", data={"name": "Ravi", "role": "helper"},
                      files=[("photo", ("a.png", photo(), "image/png")), ("photo", ("b.png", photo(True), "image/png"))])
    assert res.status_code == 400 and res.json()["error"] == "no_face"
    assert store.get_person("ravi") is None and list(stub.faces.values()) == ["owner"]
    bad = client.post("/api/people", data={"name": "Ravi"}, files=[("photo", ("x.png", b"not an image", "image/png"))])
    assert bad.json()["error"] == "bad_photo"

    res = client.post("/api/people", data=fields, files=[("photo", ("1.png", photo(), "image/png")),
                                                         ("photo", ("2.png", photo(), "image/png"))])
    assert res.json()["person"]["faces"] == 2 and stub.deleted == ["face-2", "face-1"]
    assert client.post("/api/people", json={**fields, "note": "a red shirt"}).json()["person"]["faces"] == 2

    assert client.delete("/api/people/owner").status_code == 200
    assert stub.faces == {} and not (store.media_dir / "people" / "owner" / "1.jpg").exists()


def test_people_photos_with_face_id_off_are_kept_but_not_indexed(store):
    """With face ID off the photo is still stored and shown; nothing is indexed."""
    client = TestClient(build_app(store, compile_order=lambda *_: {}), headers=KEY)
    res = client.post("/api/people", data={"name": "Ravi", "role": "helper"},
                      files=[("photo", ("r.png", photo(), "image/png"))])
    assert res.status_code == 200 and res.json()["person"]["faces"] == 0
    assert [u.split("?")[0] for u in res.json()["person"]["photo_urls"]] == ["/media/people/ravi/1.jpg"]
    assert client.delete("/api/people/ravi").status_code == 200


class Now:
    """Runs a submitted call at once (a stand-in for the face worker)."""

    def submit(self, fn, *args):
        """Run fn and return its finished future."""
        future: Future = Future()
        future.set_result(fn(*args))
        return future


class Later(Now):
    """Holds submitted calls until run() (to see what happens while a search is out)."""

    def __init__(self):
        """No calls waiting."""
        self.waiting = []

    def submit(self, fn, *args):
        """Keep the call; its future finishes on run()."""
        future: Future = Future()
        self.waiting.append((future, fn, args))
        return future

    def run(self):
        """Finish every waiting call."""
        while self.waiting:
            future, fn, args = self.waiting.pop(0)
            future.set_result(fn(*args))


class Votes:
    """A store stand-in whose session holds an episode's votes."""

    def __init__(self, votes):
        """Start with these votes."""
        self.votes = votes

    def get_session(self, _session_id):
        """The session's votes and known people."""
        return {"votes": self.votes, naming.KNOWN: getattr(self, "known", {})}

    def set_session_field(self, _session_id, field, value):
        """Keep the known people."""
        assert field == naming.KNOWN
        self.known = value
        return True

    def get_person(self, _person_id):
        """Nobody is registered here."""
        return None


class Clock:
    """A clock the test moves by hand."""

    def __init__(self):
        """Start at zero."""
        self.t = 0.0

    def __call__(self):
        """The current time."""
        return self.t


def person(track_id, box=(0.3, 0.1, 0.6, 0.9)):
    """A person track."""
    return Track(track_id, "person", list(box), 0.8)


def namer_with(answers, votes=None, executor=None, retries=3, store=None, keep_unnamed=False):
    """A TrackNamer over a stub with scripted answers, a hand clock and a 1 s retry gap, tied to fresh rules."""
    clock = Clock()
    namer = naming.TrackNamer(faces_with(StubRekognition(answers)), store or Votes(votes or {}), "s1",
                              retries=retries, gap_s=1.0, executor=executor or Now(), clock=clock,
                              keep_unnamed=keep_unnamed)
    namer.rules = track_rules.TargetRules("desk", "Desk", "game controller", identify=namer)
    return namer, clock


FRAME = np.full((360, 640, 3), 100, np.uint8)


def test_a_person_track_is_named_once_when_it_appears():
    """The first frame with the track sends one search; the name reaches the rules; later frames search no more."""
    namer, clock = namer_with(["owner"])
    namer.observe(FRAME, [person(4), Track(9, "game controller", [0.4, 0.5, 0.5, 0.6], 0.9)])
    assert namer.searches == 1 and namer.rules.names == {4: "owner"}
    clock.t = 10
    namer.observe(FRAME, [person(4)])
    assert namer.searches == 1 and namer(FRAME, 4, [0.3, 0.1, 0.6, 0.9]) == "owner"


def test_retries_use_the_best_crop_then_fall_back_to_the_episode_vote():
    """No face: retry after the gap on the largest view since the last try; out of tries, the episode's majority
    name (ignoring unknown) names the track, and follows the vote as it changes."""
    votes = {"unknown": 5, "owner": 2}
    namer, clock = namer_with(["noface", "noface", "noface"], votes)
    namer.observe(FRAME, [person(4)])
    namer.observe(FRAME, [person(4, (0.3, 0.1, 0.4, 0.3))])
    assert namer.searches == 1 and 4 not in namer.names
    clock.t = 1.0
    namer.observe(FRAME, [person(4, (0.1, 0.0, 0.9, 1.0))])
    assert namer.searches == 2 and namer.faces.client.searched[1].shape[1] == 640
    clock.t = 2.0
    namer.observe(FRAME, [person(4)])
    assert namer.searches == 3 and namer.rules.names[4] == "owner"
    votes.update(ravi=4)
    namer.observe(FRAME, [person(4)])
    assert namer.rules.names[4] == "ravi" and namer.searches == 3


def test_a_face_that_matches_nobody_stays_unknown_when_unnamed_people_are_off():
    """A visible face with no match is a stranger: never the episode's name, and not kept."""
    namer, clock = namer_with([None, None], {"owner": 3}, retries=2)
    namer.observe(FRAME, [person(7)])
    clock.t = 1.0
    namer.observe(FRAME, [person(7)])
    assert namer.rules.names[7] == tracker.UNKNOWN


def test_one_search_at_a_time_per_track_and_the_hook_answers_at_once():
    """While a search is out the frame loop isn't held up: the hook says '' and no second search starts."""
    later = Later()
    namer, clock = namer_with(["ravi"], executor=later)
    namer.observe(FRAME, [person(2)])
    clock.t = 5
    assert namer(FRAME, 2, [0.3, 0.1, 0.6, 0.9]) == "" and namer.searches == 1
    later.run()
    assert namer.rules.names[2] == "ravi"


def test_follower_uses_face_naming_only_when_on_and_not_given(store, monkeypatch):
    """With face ID on, a camera's follower names person tracks with a TrackNamer tied to its rules; a given
    identify check is kept, and with face ID off nothing changes."""

    class Detector:
        """A detector stand-in that only takes prompts."""
        name = "stub"

        def set_prompts(self, _prompts):
            """Accept the prompts."""

    monkeypatch.setattr(analyze.detection, "CameraTracks", lambda _detector: None)
    session = {"session_id": "s1", "watch_order": {"target": "game controller"}}
    stub = faces_with(StubRekognition())
    monkeypatch.setattr(face_id, "get_faces", lambda: stub)
    follower = analyze.follower_for(store, session, "desk", {}, Detector(), track_rules.accept_all,
                                    track_rules.nobody_known)
    assert isinstance(follower.rules.identify, naming.TrackNamer) and follower.rules.identify.rules is follower.rules
    given = lambda *_: "ravi"  # noqa: E731
    follower = analyze.follower_for(store, session, "room", {}, Detector(), track_rules.accept_all, given)
    assert follower.rules.identify is given
    monkeypatch.setattr(face_id, "get_faces", lambda: None)
    follower = analyze.follower_for(store, session, "door", {}, Detector(), track_rules.accept_all,
                                    track_rules.nobody_known)
    assert follower.rules.identify is track_rules.nobody_known


def test_the_configured_owner_registers_with_photos_and_is_called_you(store, monkeypatch):
    """With MEANWHILE_OWNER_ID set, that person is the owner whatever role is sent, nobody else can be, and a
    track face ID names as them is spoken as 'you'."""
    from meanwhile import config
    monkeypatch.setattr(config, "OWNER_ID", "owner")
    stub = StubRekognition(["owner"])
    client = TestClient(build_app(store, compile_order=lambda *_: {}, faces=faces_with(stub)), headers=KEY)
    res = client.post("/api/people", data={"name": "Owner", "role": "helper"},
                      files=[("photo", ("me.png", photo(), "image/png"))])
    assert res.json()["person"]["role"] == "owner" and res.json()["person"]["faces"] == 1
    assert client.post("/api/people", json={"name": "Ravi", "role": "owner"}).json()["error"] == "owner_exists"

    namer, _ = namer_with(["owner"], store=store)
    namer.observe(FRAME, [person(3)])
    people = {p["person_id"]: p for p in store.list_people()}
    assert tracker.line("picked_up", "controller", namer.rules.names[3], people) == "You picked up your controller."


NOISY = np.random.default_rng(3).integers(0, 255, (360, 640, 3), np.uint8)


def stranger(store, stub=None):
    """An unnamed person made the way the namer makes one: a track whose face matched nobody on every try."""
    stub = stub or StubRekognition()
    stub.answers += [None, None, None]
    clock = Clock()
    namer = naming.TrackNamer(faces_with(stub), store, "s1", retries=2, gap_s=1.0, executor=Now(), clock=clock,
                              keep_unnamed=True)
    namer.rules = track_rules.TargetRules("desk", "Desk", "game controller", identify=namer)
    namer.observe(NOISY, [person(7)])
    clock.t = 1.0
    namer.observe(NOISY, [person(7)])
    return namer.rules.names[7], stub


def object_session(store, actor):
    """A session with one pick-up event by `actor` and the object with them."""
    session = store.start_session("keep an eye on my controller", {
        "watch_order": {"kind": "object", "target": "controller", "cameras": ["desk"]},
        "focus_object": "controller", "focus_camera_id": "desk"})
    people_ = {p["person_id"]: p for p in store.list_people()}
    store.add_event({"session_id": session["session_id"], "action": "picked_up", "actor_id": actor,
                     "camera_id": "desk", "episode": "e1", "alert": "none",
                     "summary": tracker.line("picked_up", "controller", actor, people_)})
    store.update_session(session["session_id"], state="with_person", state_actor=actor, votes={actor: 3})
    return session


def test_a_stranger_becomes_an_unnamed_person_matched_again_later(store):
    """Out of tries with a visible face and no match: one more search, then the face is indexed under a new
    unnamed id with its crop as the photo; a later sighting of that id refreshes when they were last seen."""
    person_id, stub = stranger(store)
    assert person_id.startswith("unnamed-") and list(stub.faces.values()) == [person_id]
    found = store.get_person(person_id)
    assert found["unnamed"] and found["name"] == "Person 1" and found["camera_id"] == "desk"
    assert (store.media_dir / found["photo_key"]).exists()
    store.put_person({**found, "last_seen": "2026-01-01T00:00:00.000Z"})
    namer, _ = namer_with([person_id], store=store)
    namer.observe(FRAME, [person(1)])
    assert namer.rules.names[1] == person_id and store.get_person(person_id)["last_seen"] > "2026-01-01"
    assert stranger(store)[0] != person_id and store.get_person(stranger(store)[0])["number"] == 3


def test_unnamed_people_are_spoken_of_by_their_label(store):
    """Events say 'An unnamed person (Person 1) …', and the API gives that as the actor's name."""
    from meanwhile.api import sessions
    person_id, _ = stranger(store)
    session = object_session(store, person_id)
    event = store.list_events(session["session_id"])[0]
    assert event["summary"] == "An unnamed person (Person 1) picked up your controller."
    out = sessions.event_out(store, event, {p["person_id"]: p for p in store.list_people()})
    assert out["actor_name"] == "an unnamed person (Person 1)" and out["actor_unnamed"] is True


def test_naming_merging_and_deleting_unnamed_people(store):
    """Unnamed people are listed after named ones and flagged; naming one moves their face, photo, past events
    and session state to the new id; a taken name asks for a merge; merging into the owner makes past events
    say 'you'; delete-all removes every unnamed person and their faces."""
    stub = StubRekognition()
    client = TestClient(build_app(store, compile_order=lambda *_: {}, faces=faces_with(stub)), headers=KEY)
    client.post("/api/people", data={"name": "Owner", "role": "owner"}, files=[("photo", ("me.png", photo(), "image/png"))])
    first, _ = stranger(store, stub)
    session = object_session(store, first)
    listed = client.get("/api/people").json()["people"]
    assert [p["unnamed"] for p in listed] == [False, True] and listed[1]["photo_url"]

    res = client.patch(f"/api/people/{first}", json={"name": "Ravi", "role": "helper"})
    assert res.status_code == 200, res.text
    ravi = res.json()["person"]
    assert ravi["person_id"] == "ravi" and not ravi["unnamed"] and ravi["faces"] == 1
    assert store.get_person(first) is None and first not in stub.faces.values() and "ravi" in stub.faces.values()
    event = store.list_events(session["session_id"])[0]
    assert event["actor_id"] == "ravi" and event["summary"] == "Ravi picked up your controller."
    assert store.get_session(session["session_id"])["state_actor"] == "ravi"
    assert store.get_session(session["session_id"])["votes"] == {"ravi": 3}

    second, _ = stranger(store, stub)
    assert client.patch(f"/api/people/{second}", json={"name": "Ravi"}).json()["error"] == "name_taken"
    assert client.patch(f"/api/people/{second}", json={}).json()["error"] == "bad_name"
    store.end_session(session["session_id"])
    session2 = object_session(store, second)
    res = client.post(f"/api/people/{second}/merge", json={"into": "owner"})
    assert res.status_code == 200 and res.json()["person"]["faces"] == 2
    assert store.list_events(session2["session_id"])[0]["summary"] == "You picked up your controller."

    third, _ = stranger(store, stub)
    assert client.delete("/api/people/unnamed").json()["removed"] == [third]
    assert third not in stub.faces.values() and store.get_person("owner") and store.get_person("ravi")


def test_unnamed_people_are_deleted_after_their_days(store):
    """Unnamed people not seen for the configured days go, with their faces; named people stay."""
    stub = StubRekognition()
    old, _ = stranger(store, stub)
    store.put_person({**store.get_person(old), "last_seen": "2026-01-01T00:00:00.000Z"})
    recent, _ = stranger(store, stub)
    assert unnamed.expire(store, faces_with(stub), days=14, at="2026-10-20T00:00:00.000Z") == [old]
    assert store.get_person(recent) and old not in stub.faces.values()


def noise_jpeg(seed: int) -> bytes:
    """A 640x360 JPEG of noise, standing in for one of a clip's sampled frames."""
    return cv2.imencode(".jpg", np.random.default_rng(seed).integers(0, 255, (360, 640, 3), np.uint8))[1].tobytes()


def clip_event(actor: str, recipient: str = "") -> dict:
    """A clip event as read_clip makes it, before face ID."""
    return {"happened_at": "2026-10-21T10:00:00.000Z", "action": "turned the lamp on", "actor": actor,
            "recipient": recipient, "summary": "", "alert": False}


def test_find_gives_face_boxes_largest_first():
    """DetectFaces' boxes come back as clipped [x0, y0, x1, y1] fractions, the largest face first."""
    stub = StubRekognition(found=[[[0.1, 0.1, 0.1, 0.2], [0.9, 0.5, 0.2, 0.3]]])
    boxes = faces_with(stub).find(noise_jpeg(1))
    assert [[round(v, 2) for v in b] for b in boxes] == [[0.9, 0.5, 1.0, 0.8], [0.1, 0.1, 0.2, 0.3]]


def test_a_face_in_the_nearest_frame_names_the_clip_actor_once_per_frame(store):
    """The frame nearest the event is searched first; a face found there overrides the clothes match and joins the
    session's known people with what they wear; a second event near the same frame reuses that search, and
    nobody-involved events cost nothing."""
    session = store.start_session("watch", {"watch_order": {"kind": "notes", "target": "who comes in"}})
    stub = StubRekognition(["ravi"], found=[[[0.4, 0.2, 0.1, 0.15]]])
    events = [{**clip_event("owner"), "actor_wears": "a red shirt"}, clip_event("owner"), clip_event("")]
    took = naming.name_clip_events(faces_with(stub), store, session["session_id"], [noise_jpeg(1), noise_jpeg(2), noise_jpeg(3)],
                                   [0.0, 2.0, 4.0], events, [3.6, 4.0, 1.0], "room", tries=2)
    assert [e["actor"] for e in events] == ["ravi", "ravi", ""]
    assert naming.known_people(store, session["session_id"]) == {"ravi": "a red shirt"} and len(took) == 2
    assert (stub.detected, len(stub.searched)) == (1, 1)


def test_frames_without_a_face_are_skipped_up_to_the_tries(store):
    """With no face in the nearest frame the next one is tried; a face matching the model's pick keeps it, and with
    no face in any tried frame the model's pick stays (for only_known to check)."""
    stub = StubRekognition(["owner"], found=[[], [[0.4, 0.2, 0.1, 0.15]]])
    (event,) = events = [clip_event("owner")]
    naming.name_clip_events(faces_with(stub), store, "s1", [noise_jpeg(1), noise_jpeg(2), noise_jpeg(3)],
                            [0.0, 2.0, 4.0], events, [0.5], "room", tries=2)
    assert event["actor"] == "owner" and stub.detected == 2
    stub = StubRekognition(found=[[], []])
    (event,) = events = [clip_event("unknown")]
    naming.name_clip_events(faces_with(stub), store, "s1", [noise_jpeg(1), noise_jpeg(2), noise_jpeg(3)],
                            [0.0, 2.0, 4.0], events, [0.5], "room", tries=2)
    assert event["actor"] == "unknown" and stub.detected == 2


def test_the_recipient_is_the_other_face_and_a_failed_call_keeps_the_models_pick(store):
    """With two named faces the actor keeps their own and the recipient becomes the other one; a Rekognition
    error leaves the event as the model named it."""
    stub = StubRekognition(["owner", "ravi"], found=[[[0.1, 0.2, 0.2, 0.3], [0.6, 0.2, 0.1, 0.15]]])
    (event,) = events = [clip_event("ravi", recipient="unknown")]
    naming.name_clip_events(faces_with(stub), store, "s1", [noise_jpeg(1)], [0.0], events, [0.0], "room")
    assert (event["actor"], event["recipient"]) == ("ravi", "owner")

    class Broken:
        """A Rekognition client whose face detection is refused."""
        def detect_faces(self, **_kw):
            """Raise AccessDenied."""
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "DetectFaces")
    (event,) = events = [clip_event("owner")]
    naming.name_clip_events(faces_with(Broken()), store, "s1", [noise_jpeg(1)], [0.0], events, [0.0], "room")
    assert event["actor"] == "owner"


def test_a_face_matching_nobody_in_a_clip_becomes_an_unnamed_person(store, monkeypatch):
    """With unnamed people kept, a clip face that matches nobody is indexed as a new unnamed person and named."""
    monkeypatch.setattr(config, "FACE_KEEP_UNNAMED", True)
    stub = StubRekognition([None], found=[[[0.4, 0.2, 0.1, 0.15]]])
    (event,) = events = [clip_event("unknown")]
    naming.name_clip_events(faces_with(stub), store, "s1", [noise_jpeg(1)], [0.0], events, [0.0], "room")
    person = store.get_person(event["actor"])
    assert unnamed.is_unnamed(person) and person["name"] == "Person 1" and person["face_ids"]


def test_clip_events_are_spoken_with_the_face_id_name(store, monkeypatch):
    """Through handle_upload: a monitor note the model credits to 'unknown' and an object action it credits to
    Ravi by clothes are both credited to the owner whose face is in the clip, and spoken that way."""
    from meanwhile.api import cameras, people, uploads
    from meanwhile.store import now

    monkeypatch.setattr(config, "TRACKING", "vision")
    for label in ("Desk", "Room"):
        cameras.register(store, {"label": label})
    people.add(store, {"name": "Owner", "role": "owner", "note": "a blue shirt"})
    people.add(store, {"name": "Ravi", "role": "helper", "note": "a red shirt"})
    stub = StubRekognition(["owner", "owner"], found=[[[0.4, 0.2, 0.1, 0.15]], [[0.4, 0.2, 0.1, 0.15]]])
    monkeypatch.setattr(face_id, "get_faces", lambda: faces_with(stub))
    frames = {"frames": [noise_jpeg(1), noise_jpeg(2)], "times": [0.0, 3.0], "duration_s": 6.0}

    def run(order, event):
        """Start a session with this order and analyse one Room clip whose answer is this event."""
        session = store.start_session("watch", {"watch_order": order, "focus_object": order["target"],
                                                "focus_camera_id": "desk", "focus_box": [0.4, 0.4, 0.5, 0.5]})
        time.sleep(0.002)
        key = f"clips/room/{uploads.stamp(now())}.mp4"
        store.write_media(key, noise_jpeg(9))
        answer = ({"relevant": True, "events": [event]}, frames)
        outcome = analyze.handle_upload(store, key, describe=lambda *_: answer)
        store.end_session(session["session_id"])
        return outcome, store.list_events(session["session_id"])

    monitor = {"kind": "monitor", "target": "the room", "cameras": "all", "alert_when": [], "clip_question": "?"}
    outcome, (e,) = run(monitor, {"t": 1, "action": "came into the room", "actor": "unknown", "recipient": "",
                                  "summary": "Someone came into the room.", "alert": False})
    assert (e["actor_id"], e["summary"]) == ("owner", "You came into the room.") and "faces=owner" in outcome
    lamp = {"kind": "object", "target": "lamp", "cameras": "all", "alert_when": [], "clip_question": "?"}
    _, (e,) = run(lamp, {"t": 4, "action": "turned the lamp on", "actor": "Ravi", "recipient": "",
                         "summary": "Ravi turned on the lamp.", "alert": False})
    assert (e["actor_id"], e["summary"]) == ("owner", "You turned your lamp on.")


def test_anyone_not_known_by_face_is_an_unnamed_person_numbered_once_per_look(store):
    """Clothes alone never name a registered person; someone the session hasn't recognised by face becomes Person N
    (no face, no photo), the same N when they are seen again in the same clothes or the model names them by that
    label, and a new N for a new look; a person face ID recognised earlier is kept by clothes."""
    from meanwhile.api import people

    people.add(store, {"name": "Ravi", "role": "helper", "note": "a red shirt"})
    people.add(store, {"name": "Owner", "role": "owner", "note": "a blue shirt"})
    sid = store.start_session("watch", {"watch_order": {"kind": "notes", "target": "who comes in"}})["session_id"]
    naming.confirm(store, sid, "owner")
    first = [{**clip_event("ravi"), "actor_wears": "a green hoodie"}, {**clip_event("owner"), "actor_wears": ""}]
    naming.only_known(store, sid, first, "room")
    stranger = first[0]["actor"]
    assert stranger.startswith(unnamed.PREFIX) and first[1]["actor"] == "owner"
    assert store.get_person(stranger)["name"] == "Person 1" and store.get_person(stranger)["note"] == "a green hoodie"
    listed = {p["person_id"]: p["note"] for p in naming.session_people(store, sid, store.list_people())}
    assert listed == {"owner": "a blue shirt", stranger: "a green hoodie"}
    by_label = identity.person_id_for("Person 1", store.list_people())
    again = [{**clip_event("unknown"), "actor_wears": "A green  hoodie"}, {**clip_event(by_label)},
             {**clip_event("unknown", recipient="ravi"), "actor_wears": "a yellow cap", "recipient_wears": ""}]
    naming.only_known(store, sid, again, "room")
    assert [e["actor"] for e in again[:2]] == [stranger, stranger]
    newcomer, other = again[2]["actor"], again[2]["recipient"]
    assert store.get_person(newcomer)["name"] == "Person 2" and store.get_person(other)["name"] == "Person 3"
    assert store.get_person("ravi")["person_id"] == "ravi" and "ravi" not in naming.known_people(store, sid)


def test_an_unrecognised_giver_is_the_holder_the_session_saw(store):
    """On an object watch, a hand-over whose giver the model can't name is credited to the person the session saw
    holding the object: their name when face ID recognised them, else the Person N they already have; no new
    unnamed person is made. The recipient still follows the known-people rule."""
    from meanwhile.api import people

    people.add(store, {"name": "Ravi", "role": "helper", "note": "a red shirt"})
    sid = store.start_session("watch", {"watch_order": {"kind": "object", "target": "watch"}})["session_id"]
    naming.confirm(store, sid, "ravi")
    handed = [{**clip_event("unknown", recipient="ravi"), "action": "handed_to", "actor_wears": "a grey hoodie"}]
    naming.only_known(store, sid, handed, "room", holder="ravi")
    assert (handed[0]["actor"], handed[0]["recipient"]) == ("ravi", "ravi")

    stranger = unnamed.create_unseen(store, "a grey hoodie", "desk")["person_id"]
    count = len(store.list_people())
    handed = [{**clip_event("owner"), "action": "handed_to", "actor_wears": "a blue shirt"}]
    naming.only_known(store, sid, handed, "room", holder=stranger)
    assert handed[0]["actor"] == stranger and len(store.list_people()) == count
