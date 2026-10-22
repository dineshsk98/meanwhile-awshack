"""The detector-track rules and their path into the core: which readings come out of scripted tracks (held,
missing, settling, moved, put back, returned), when a new track needs a confirm check, the tracker's handling
of those readings, follow() / handle_upload with detector tracking (stand-ins for the detector), clips for
events written later, and which cameras the live feeds cover."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Callable

import numpy as np

from meanwhile import config
from meanwhile.api import cameras, people, uploads
from meanwhile.pipeline import analyze, naming, track_rules, tracker
from meanwhile.pipeline.track_rules import TargetRules, Track
from meanwhile.store import now, parse, to_iso

BASE = "2026-10-15T04:25:50.000Z"
STEP = 2 / 15
ASPECT = 9 / 16
TARGET = "game controller"
HOME = [0.45, 0.7, 0.55, 0.8]
FAR_SPOT = [0.75, 0.3, 0.85, 0.4]


def at(t: float) -> str:
    """BASE plus t seconds."""
    return to_iso(parse(BASE) + timedelta(seconds=t))


def box_at(cx: float, cy: float, size: float = 0.1) -> list[float]:
    """A box of the given size centred on (cx, cy)."""
    return [round(cx - size / 2, 4), round(cy - size / 2, 4), round(cx + size / 2, 4), round(cy + size / 2, 4)]


def centre(box: list[float]) -> tuple[float, float]:
    """A box's centre."""
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def rules(**kw) -> TargetRules:
    """Rules for the Desk camera with the object resting at HOME at step-away."""
    return TargetRules("desk", "Desk", TARGET, home=HOME, **kw)


def feed(r: TargetRules, t0: float, t1: float, tracks: Callable[[float], list[Track]]) -> list[tuple[float, dict]]:
    """Step the rules every STEP from t0 up to t1 with tracks(t); returns (t, reading) for each reading."""
    out, t = [], t0
    while t < t1 - 1e-9:
        seen = r.step(at(t), None, tracks(t), ASPECT)
        if seen is not None:
            out.append((round(t, 3), seen))
        t += STEP
    return out


def resting(box: list[float], track_id: int = 1, conf: float = 0.5) -> Callable[[float], list[Track]]:
    """The object still in `box`."""
    return lambda t: [Track(track_id, TARGET, box, conf)]


def carried(start: list[float], dx: float, track_id: int = 1, t0: float = 0.0,
            with_person: bool = False) -> Callable[[float], list[Track]]:
    """The object moving right by dx per second from `start`, optionally with a person track over it."""
    def tracks(t: float) -> list[Track]:
        """The moved box at time t."""
        cx, cy = centre(start)
        moved = box_at(cx + dx * (t - t0), cy)
        person = [Track(7, "person", [moved[0] - 0.1, 0.0, moved[2] + 0.1, 1.0], 0.8)] if with_person else []
        return [Track(track_id, TARGET, moved, 0.5), *person]
    return tracks


def nothing(_t: float) -> list[Track]:
    """No sighting."""
    return []


def kinds(readings: list[tuple[float, dict]]) -> list[str]:
    """Each reading as one word: settling, held, missing, returned, in_place or moved."""
    def kind(s: dict) -> str:
        """The reading's kind."""
        if s.get("settling"):
            return "settling"
        if s.get("held"):
            return "held"
        if not s["visible"]:
            return "missing"
        if s.get("returned"):
            return "returned"
        return "in_place" if s["in_place"] else "moved"
    return [kind(s) for _, s in readings]


def test_resting_object_gives_no_readings():
    """An object that never moves gives no reading at all, and a dropout shorter than the grace period is ignored."""
    r = rules()
    assert feed(r, 0, 3, resting(HOME)) == []
    assert feed(r, 3, 3 + track_rules.MISS_GRACE_S - 0.1, nothing) == []
    assert feed(r, 3.4, 5, resting(HOME)) == []
    assert r.confirm_calls == 0


def test_carried_out_of_view_then_put_back():
    """Moving is held (with the nearest person, named once); unseen past the grace period is missing, timed at
    the first unseen frame; back at the step-away spot it is settling, then in place."""
    names = []
    r = rules(identify=lambda image, tid, box: names.append(tid) or "ravi")
    feed(r, 0, 2, resting(HOME))
    out = feed(r, 2, 3, carried(HOME, 0.3, t0=2, with_person=True))
    out += feed(r, 3, 6, nothing)
    out += feed(r, 6, 8, resting(HOME))
    assert kinds(out) == ["held", "missing", "settling", "in_place"]
    held, missing = out[0][1], out[1][1]
    assert held["person"] == "ravi" and held["camera_id"] == "desk" and held["box"]
    assert missing["person"] == "ravi" and missing["box"] is None
    assert parse(missing["happened_at"]) <= parse(at(3.01)) and names == [7]


def test_moved_and_put_back_elsewhere():
    """At rest far from where it rested is moved (the new spot becomes the anchor); handled again and set down
    at that new spot is in place; then carried back home with no pick-up seen is returned."""
    r = rules()
    feed(r, 0, 2, resting(HOME))
    out = feed(r, 2, 3, carried(HOME, 0.3, t0=2))
    out += feed(r, 3, 5, resting(FAR_SPOT))
    assert kinds(out) == ["held", "moved"] and out[-1][1]["box"] == FAR_SPOT
    out = feed(r, 5, 5.6, carried(FAR_SPOT, 0.2, t0=5))
    out += feed(r, 5.6, 7, resting(FAR_SPOT))
    assert kinds(out) == ["held", "in_place"]
    out = feed(r, 7, 8, nothing) + feed(r, 8, 10, resting(HOME, track_id=3))
    assert kinds(out) == ["missing", "settling", "returned"]
    assert out[-1][1]["in_place"] and out[-1][1]["box"] == HOME


def test_new_track_where_it_rested_needs_no_check():
    """A new track id at the spot where the object was resting is the same object: no confirm call."""
    calls = []
    r = rules(confirm=lambda image, box: calls.append(box) or True)
    feed(r, 0, 2, resting(HOME, track_id=1))
    out = feed(r, 2, 4, resting(box_at(0.505, 0.75), track_id=2))
    assert out == [] and calls == [] and r.track_id == 2


def test_new_track_elsewhere_needs_one_confirm_and_a_rejected_one_is_ignored():
    """While the object is in a hand and unseen, a lookalike's new track elsewhere is checked once; rejected,
    it never takes over (no held or moved reading from it), and the real object's new track is checked and
    accepted."""
    checked = []

    def confirm(image, box) -> bool:
        """Accept only boxes on the right half (where the real object comes back)."""
        checked.append(box)
        return box[0] > 0.6

    r = rules(confirm=confirm)
    feed(r, 0, 2, resting(HOME))
    out = feed(r, 2, 3, carried(HOME, 0.3, t0=2))
    out += feed(r, 3, 5, nothing)
    wristband = box_at(0.2, 0.4)
    out += feed(r, 5, 7, lambda t: [Track(9, TARGET, box_at(0.2 + 0.1 * (t - 5), 0.4), 0.3)])
    assert kinds(out) == ["held", "missing"] and len(checked) == 1 and centre(checked[0])[0] == centre(wristband)[0]
    out = feed(r, 7, 9, lambda t: [Track(9, TARGET, wristband, 0.3), Track(4, TARGET, FAR_SPOT, 0.6)])
    assert len(checked) == 2 and r.track_id == 4 and 9 in r.rejected
    assert kinds(out) == ["settling", "moved"]


def test_weak_detection_at_the_resting_spot_keeps_it_in_view():
    """While the object rests, a detection too weak to start a track at its spot keeps it in view (no missing
    reading), but a weak one elsewhere is not a sighting, and once it is missing a weak one doesn't bring it back."""
    r = rules()
    feed(r, 0, 2, resting(HOME))
    weak_home = lambda t: [Track(-1, TARGET, HOME, 0.15)]
    weak_far = lambda t: [Track(-1, TARGET, FAR_SPOT, 0.15)]
    assert feed(r, 2, 5, weak_home) == []
    out = feed(r, 5, 6, weak_far) + feed(r, 6, 8, weak_home)
    assert kinds(out) == ["missing"] and r.track_id == 1


def test_low_confidence_boxes_are_not_the_object():
    """Boxes below MIN_CONF don't count as sightings."""
    r = rules()
    feed(r, 0, 2, resting(HOME))
    out = feed(r, 2, 4, resting(HOME, conf=track_rules.MIN_CONF / 2))
    assert kinds(out) == ["missing"]


def session_with(**fields) -> dict:
    """An object-watch session on the Desk camera with the given tracker fields."""
    return {"session_id": "ses_1", "watch_order": {"kind": "object", "target": "controller"},
            "focus_camera_id": "desk", "focus_box": HOME, "state": "on_surface", **fields}


def frame_reading(**fields) -> dict:
    """A frame reading from the Desk camera at BASE."""
    return {"kind": "frame", "camera_id": "desk", "camera_label": "Desk", "happened_at": BASE, "visible": True,
            "held": False, "in_place": False, "box": HOME, "person": "", **fields}


def test_tracker_settling_reading():
    """Settling while still in view only clears the missing time; settling after it left view holds back 'still
    tracking' for SETTLE_HOLD_S."""
    d = tracker.object_frame(session_with(in_view=True, missing_since=at(-1)), frame_reading(settling=True), {})
    assert d["events"] == [] and d["session"]["missing_since"] is None
    gone = session_with(in_view=False, left_at=at(-20), state="with_person", state_actor="unknown")
    d = tracker.object_frame(gone, frame_reading(settling=True), {})
    assert d["events"] == [] and d["session"]["settling_since"] == BASE and "in_view" not in d["session"]
    held_back = tracker.on_tick({**gone, **d["session"]}, at(1), [], {})
    later = tracker.on_tick({**gone, **d["session"]}, at(tracker.SETTLE_HOLD_S + 1), [], {})
    assert held_back["events"] == [] and [e["action"] for e in later["events"]] == ["still_tracking"]


def test_tracker_returned_reading_is_a_put_back():
    """Back at the step-away spot from a new spot, with no pick-up seen, is a quiet put-back and the focus box
    follows it."""
    keyboard = [0.75, 0.3, 0.85, 0.4]
    d = tracker.object_frame(session_with(in_view=True, focus_box=keyboard), frame_reading(in_place=True,
                                                                                         returned=True), {})
    (e,) = d["events"]
    assert (e["action"], e["alert"]) == ("put_back", "none") and d["session"]["focus_box"] == HOME


class FakeTracks:
    """Stands in for a camera's detector tracks: returns the scripted tracks for each frame in turn."""

    def __init__(self, script: list[list[Track]]):
        """Keep the script."""
        self.script = list(script)

    def update(self, image) -> list[Track]:
        """The next frame's tracks (none once the script runs out)."""
        return self.script.pop(0) if self.script else []


class FakeDetector:
    """Stands in for the detector: only its name is used once the follower exists."""
    name = "yoloe-test"


def start_watch(store) -> dict:
    """Desk online, the owner registered, and an object watch on the Desk camera."""
    cameras.register(store, {"label": "Desk"})
    people.add(store, {"name": "Owner", "role": "owner"})
    time.sleep(0.002)
    order = {"kind": "object", "target": "controller", "cameras": "all"}
    return store.start_session("keep an eye on my controller", {"watch_order": order, "focus_object": "controller",
                                                                 "focus_camera_id": "desk", "focus_box": HOME})


def test_follow_writes_detector_events_with_the_frame(store):
    """Live frames through follow(): resting is no change; moving writes a quiet pick-up from the detector with the
    frame kept as its picture; another camera isn't followed."""
    session = start_watch(store)
    start = parse(now()) + timedelta(seconds=1)
    script = [[Track(1, TARGET, HOME, 0.5)]] * 8
    script += [[Track(1, TARGET, box_at(0.5 + 0.04 * i, 0.75), 0.5)] for i in range(1, 5)]
    rules_ = TargetRules("desk", "Desk", TARGET, home=HOME)
    followers = {"desk": analyze.Follower(session["session_id"], FakeTracks(script), rules_)}
    image = np.zeros((90, 160, 3), np.uint8)
    outcomes = [analyze.follow(store, "desk", to_iso(start + timedelta(seconds=i * STEP)), image, followers,
                               FakeDetector()) for i in range(len(script))]
    assert outcomes[:8] == ["no change"] * 8 and outcomes[8].startswith("held -> picked_up/none")
    (e,) = store.list_events(session["session_id"])
    assert (e["action"], e["source"], e["model"], e["camera_id"]) == ("picked_up", "detector", "yoloe-test", "desk")
    assert store.media_path(e["frame_key"]).exists()
    assert "doesn't follow" in analyze.follow(store, "room", to_iso(start), image, followers, FakeDetector())


def test_uploads_on_a_followed_camera_need_no_model(store, monkeypatch):
    """With detector tracking and OBJECT_CLIP_ACTIONS off, the focus camera's frames are skipped and its clips join
    the events they cover without asking the video model."""
    monkeypatch.setattr(config, "OBJECT_CLIP_ACTIONS", False)
    session = start_watch(store)
    happened = now()
    store.add_event({"session_id": session["session_id"], "camera_id": "desk", "action": "picked_up",
                     "alert": "none", "summary": "You picked up your controller.", "happened_at": happened})

    def no_model(*_args):
        """Fail if a model is asked."""
        raise AssertionError("no model call expected")

    frame = f"frames/desk/{uploads.stamp(happened)}.jpg"
    clip = f"clips/desk/{uploads.stamp(to_iso(parse(happened) + timedelta(seconds=2)))}.mp4"
    for key in (frame, clip):
        store.write_media(key, b"not a real file")
    assert "detector follows" in analyze.handle_upload(store, frame, check_frame=no_model, tracking="detector")
    assert "clip kept" in analyze.handle_upload(store, clip, describe=no_model, tracking="detector")
    (e,) = store.list_events(session["session_id"])
    assert e["clip_key"] == clip


def test_followed_clips_tell_other_actions_on_the_object(store, monkeypatch):
    """With OBJECT_CLIP_ACTIONS on, a followed camera's clip goes to the video model: its move is left to the
    detector, what else it saw done to the object becomes an event (credited to the owner face ID named earlier in
    the session), and the clip still joins the detector's pick-up."""
    monkeypatch.setattr(config, "OBJECT_CLIP_ACTIONS", True)
    session = start_watch(store)
    naming.confirm(store, session["session_id"], "owner")
    happened = now()
    store.add_event({"session_id": session["session_id"], "camera_id": "desk", "action": "picked_up",
                     "alert": "none", "summary": "You picked up your controller.", "happened_at": happened})
    clip = f"clips/desk/{uploads.stamp(to_iso(parse(happened) + timedelta(seconds=4)))}.mp4"
    store.write_media(clip, b"not a real file")
    answer = {"relevant": True, "events": [
        {"t": 1, "action": "picked_up", "actor": "Owner", "recipient": "", "summary": "", "alert": False},
        {"t": 3, "action": "turned the controller on", "actor": "Owner", "recipient": "", "summary": "",
         "alert": False}]}
    describe = lambda *_args: (answer, {"frames": [], "times": [], "duration_s": 6.0})
    outcome = analyze.handle_upload(store, clip, describe=describe, tracking="detector")
    assert "turned the controller on/none" in outcome and "picked_up/" not in outcome.split("->")[1]
    picked, turned = store.list_events(session["session_id"])
    assert (picked["action"], picked["clip_key"]) == ("picked_up", clip)
    assert (turned["summary"], turned["source"], turned["clip_key"], turned["object_action"]) == \
        ("You turned your controller on.", "video_model", clip, True)


def test_heads_up_gets_the_clip_uploaded_before_it(store):
    """A clip that arrives before the timer writes the heads-up still becomes the heads-up's clip, with detector
    tracking (no model) and with the vision path (the clip answered by a stand-in video model)."""
    for tracking in ("detector", "vision"):
        session = start_watch(store)
        left = to_iso(parse(now()) + timedelta(seconds=1))
        store.update_session(session["session_id"], in_view=True, missing_since=left)
        clip = f"clips/desk/{uploads.stamp(to_iso(parse(left) + timedelta(seconds=2)))}.mp4"
        store.write_media(clip, b"not a real file")
        empty = lambda *_args: ({"relevant": False, "events": []}, {"frames": [], "times": [], "duration_s": 6.0})
        analyze.handle_upload(store, clip, describe=empty, tracking=tracking)
        (heads_up,) = analyze.tick(store, at=to_iso(parse(left) + timedelta(seconds=5)))
        assert (heads_up["action"], heads_up["clip_key"]) == ("left_view", clip), tracking
        store.end_session(session["session_id"])


def test_ingest_follows_online_focus_cameras_only(store):
    """The live feeds cover the online focus camera of an object watch with detector tracking, and nothing with
    vision tracking, after the session ends, or once the camera is offline."""
    from meanwhile.pipeline.ingest import Ingest

    session = start_watch(store)
    cameras.register(store, {"label": "Room"})
    lock = threading.Lock()
    assert Ingest(store, lock, tracking="detector").wanted() == {"desk"}
    assert Ingest(store, lock, tracking="vision").wanted() == set()
    store.put_camera({**store.get_camera("desk"), "last_seen": to_iso(parse(now()) - timedelta(minutes=5))})
    assert Ingest(store, lock, tracking="detector").wanted() == set()
    cameras.register(store, {"label": "Desk"})
    store.end_session(session["session_id"])
    assert Ingest(store, lock, tracking="detector").wanted() == set()


def test_ingest_prompts_the_detector_once_when_the_session_starts(store):
    """The object phrase and 'person' are encoded as soon as a session is active, once per session, and not with
    vision tracking."""
    from meanwhile.pipeline.detector import phrase_for
    from meanwhile.pipeline.ingest import Ingest

    class FakeDetector:
        """Records the prompts it is given."""
        def __init__(self):
            self.calls = []

        def prepare(self, prompts):
            """Store one prompt list."""
            self.calls.append(prompts)

    detector, lock = FakeDetector(), threading.Lock()
    ingest = Ingest(store, lock, detector=detector, tracking="detector")
    ingest.prompt_for_session()
    assert detector.calls == []
    session = start_watch(store)
    ingest.prompt_for_session()
    ingest.prompt_for_session()
    assert detector.calls == [[phrase_for(session["watch_order"]["target"]), "person"]]
    quiet = FakeDetector()
    Ingest(store, lock, detector=quiet, tracking="vision").prompt_for_session()
    assert quiet.calls == []


def weak(box: list[float], conf: float = 0.12) -> Callable[[float], list[Track]]:
    """A detection too weak to start a track, in `box`."""
    return lambda t: [Track(-1, TARGET, box, conf)]


def answers(*boxes) -> Callable:
    """A locate hook answering with the given boxes in turn (None = not found), counting its calls."""
    left = list(boxes)

    def locate(_image):
        """The next answer."""
        locate.calls += 1
        return left.pop(0) if left else None
    locate.calls = 0
    return locate


def test_locked_at_the_step_away_box():
    """The object starts locked at its step-away box: it counts as seen with no detection at all, weak detections
    there keep it in view, and when nothing is seen past the grace period it is missing (before, a weakly detected
    object that never started a track could not go missing)."""
    r = rules()
    assert r.live_box() == {"box": HOME, "held": False, "missing": False, "source": "step_away"}
    assert feed(r, 0, 3, weak(HOME)) == [] and r.trace.count("w") == r.frames
    out = feed(r, 3, 5, nothing)
    assert kinds(out) == ["missing"] and r.live_box()["missing"]


def test_weak_detections_leaving_the_spot_are_missing():
    """A weakly detected object carried away from its spot (detections never strong enough for a track) goes
    missing once it is away from the spot for the grace period."""
    r = rules()
    feed(r, 0, 1, weak(HOME))
    moved = lambda t: [Track(-1, TARGET, box_at(0.5 + 0.3 * (t - 1), 0.75), 0.12)]
    assert kinds(feed(r, 1, 3, moved)) == ["missing"]


def test_first_detector_rest_resets_the_spot_quietly():
    """A track at rest slightly off the step-away box becomes the resting spot without a reading."""
    r = rules()
    off = [0.46, 0.71, 0.56, 0.81]
    assert feed(r, 0, 2, resting(off)) == [] and r.anchor == off and r.seeded is False


def test_vision_model_locates_when_the_detector_has_no_lock():
    """No detector lock for locate_after_s: the vision model is asked (at most once per locate_gap_s); its box is a
    sighting that moves the live box (source vision), two answers at the same new spot are a move, and a new track
    at that box is taken as the object with no confirm check."""
    found = [0.6, 0.6, 0.7, 0.7]
    locate = answers(*([found] * 20))
    confirms = []
    r = rules(locate=locate, confirm=lambda image, box: confirms.append(box) or False)
    feed(r, 0, 1, resting(HOME))
    assert kinds(feed(r, 1, 4, nothing)) == ["moved"]
    assert 2 <= locate.calls <= 3 and "v" in r.trace
    assert r.live_box() == {"box": found, "held": False, "missing": False, "source": "vision"}
    assert feed(r, 4, 5, resting(found, track_id=9)) == [] and r.track_id == 9 and confirms == []
    assert r.live_box()["source"] == "detector"


def test_missing_waits_for_the_vision_model():
    """With locate on, the object is missing only once a question asked after it was last seen finds nothing; the
    reading is still timed at the first unseen frame, and the heads-up check can then be skipped."""
    locate = answers(None)
    r = rules(locate=locate)
    feed(r, 0, 1, resting(HOME))
    out = feed(r, 1, 2.05, nothing)
    assert out == [] and locate.calls == 1
    out = feed(r, 2.05, 3, nothing)
    assert kinds(out) == ["missing"] and parse(out[0][1]["happened_at"]) <= parse(at(1.01))
    assert r.gone_checked()


def test_a_slow_vision_model_counts_as_not_found(monkeypatch):
    """A locate question with no answer within locate_wait_s counts as not found."""
    from concurrent.futures import Future
    pending: Future = Future()
    r = rules(locate=lambda _image: pending, locate_wait_s=0.0)
    feed(r, 0, 1, resting(HOME))
    assert kinds(feed(r, 1, 3, nothing)) == ["missing"]


def test_locate_rate_while_missing():
    """While the object is missing, the vision model is asked once per locate_missing_gap_s, and never with 0."""
    for gap, most in ((5.0, 3), (0.0, 1)):
        locate = answers()
        r = rules(locate=locate, locate_missing_gap_s=gap)
        feed(r, 0, 1, resting(HOME))
        feed(r, 1, 12, nothing)
        assert r.phase == "missing" and locate.calls <= most, (gap, locate.calls)


def test_vision_model_brings_it_back_and_settles_it():
    """Missing, then the vision model finds it twice at the same new spot: settling, then moved."""
    spot = [0.2, 0.3, 0.3, 0.4]
    r = rules(locate=answers(None, *([spot] * 20)))
    feed(r, 0, 1, resting(HOME))
    out = feed(r, 1, 3, nothing)
    out += feed(r, 3, 12, nothing)
    assert kinds(out) == ["missing", "settling", "moved"] and out[-1][1]["box"] == spot


def test_weak_detections_follow_the_seed():
    """After the vision model places the object, weak detections near it follow it (the seed moves along), so a
    weakly detected object carried in a hand is held."""
    start = [0.6, 0.6, 0.7, 0.7]
    r = rules(locate=answers(start))
    feed(r, 0, 1, resting(HOME))
    feed(r, 1, 2.3, nothing)
    assert r.seed == start
    moving = lambda t: [Track(-1, TARGET, box_at(0.65 + 0.15 * (t - 2.3), 0.65), 0.12)]
    out = feed(r, 2.3, 3.3, moving)
    assert "held" in kinds(out) and "s" in r.trace and r.live_box()["source"] == "detector"


def test_a_sliver_at_the_frame_edge_is_not_a_sighting():
    """A box cut by the frame's edge to less than edge_part of the object's size is not the object (it is on its
    way out); a whole box at the edge still is."""
    r = rules()
    feed(r, 0, 1, resting(HOME))
    sliver = [0.6, 0.97, 0.66, 1.0]
    assert kinds(feed(r, 1, 3, lambda t: [Track(1, TARGET, sliver, 0.6)])) == ["missing"]
    r2 = rules()
    feed(r2, 0, 1, resting(HOME))
    whole_at_edge = [0.9, 0.5, 1.0, 0.6]
    feed(r2, 1, 3, lambda t: [Track(1, TARGET, whole_at_edge, 0.6)])
    assert r2.phase != "missing"


def test_taken_waits_for_a_clip_from_another_camera(store):
    """'Still tracking' and 'taken' wait while a clip from another watched camera that ends after the object left
    is still queued; a focus-camera clip or an older clip does not hold them."""
    session = start_watch(store)
    left = to_iso(parse(now()) - timedelta(seconds=20))
    store.update_session(session["session_id"], in_view=False, left_at=left, state="with_person",
                         state_actor="unknown", episode=left)
    later = uploads.stamp(to_iso(parse(left) + timedelta(seconds=5)))
    earlier = uploads.stamp(to_iso(parse(left) - timedelta(seconds=5)))
    assert analyze.tick(store, waiting=lambda: [f"clips/kitchen/{later}.mp4"]) == []
    (e,) = analyze.tick(store, waiting=lambda: [f"clips/desk/{later}.mp4", f"clips/kitchen/{earlier}.mp4"])
    assert e["action"] == "still_tracking"


def test_vision_model_at_the_resting_spot_is_quiet():
    """A resting object the detector stops seeing, found by the vision model at its spot every time: no reading,
    and the live box stays on it."""
    near_home = [0.452, 0.705, 0.548, 0.805]
    r = rules(locate=answers(*([near_home] * 20)))
    feed(r, 0, 1, resting(HOME))
    assert feed(r, 1, 8, nothing) == [] and r.phase == "rest" and r.live_box()["box"] == near_home


def test_vision_model_seeing_its_edge_is_not_a_return():
    """Missing, then the vision model finds a box cut by the frame's edge (the object's edge as it is carried just
    below the frame, desk run 25:59): not back."""
    edge = [0.584, 0.876, 0.793, 1.0]
    r = rules(locate=answers(None, *([edge] * 20)))
    feed(r, 0, 1, resting(HOME))
    assert kinds(feed(r, 1, 12, nothing)) == ["missing"] and r.located == 0


def test_vision_model_seeing_its_edge_while_in_view_lets_it_go_missing():
    """Detector lost it while in view, and the vision model finds only a box cut by the frame's edge (carried off):
    it goes missing."""
    edge = [0.3, 0.8, 0.5, 1.0]
    r = rules(locate=answers(*([edge] * 20)))
    feed(r, 0, 1, resting(HOME))
    assert kinds(feed(r, 1, 4, nothing)) == ["missing"] and r.located == 0
