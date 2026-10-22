"""The detector path's model checks and live box: the heads-up gate (hold, still there, send, timeout), crop checks
for new tracks answered later, a steady weak detection bringing a missing object back, grounding by the detector
(which box wins, and when the vision model is asked instead), and the throttled live box in the session and API."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from datetime import timedelta
from types import SimpleNamespace

import numpy as np
from starlette.testclient import TestClient

from local.devserver import build_app
from meanwhile import config
from meanwhile.api import cameras, people, sessions
from meanwhile.pipeline import analyze, checks as model_checks, detector as detection, track_rules
from meanwhile.pipeline.checks import Checks
from meanwhile.pipeline.track_rules import TargetRules, Track
from meanwhile.store import now, parse, to_iso

KEY = {"X-Meanwhile-Key": config.API_KEY}

STEP = 2 / 15
ASPECT = 9 / 16
TARGET = "game controller"
HOME = [0.45, 0.7, 0.55, 0.8]
KEYBOARD = [0.75, 0.3, 0.85, 0.4]
IMAGE = np.zeros((90, 160, 3), np.uint8)


def at(t: float) -> str:
    """A fixed start plus t seconds."""
    return to_iso(parse("2026-10-15T04:25:50.000Z") + timedelta(seconds=t))


def feed(r: TargetRules, t0: float, t1: float, tracks) -> list[dict]:
    """Step the rules every STEP from t0 up to t1 with tracks(t); returns the readings."""
    out, t = [], t0
    while t < t1 - 1e-9:
        seen = r.step(at(t), IMAGE, tracks(t), ASPECT)
        if seen is not None:
            out.append(seen)
        t += STEP
    return out


def kind(s: dict) -> str:
    """A reading as one word."""
    if s.get("settling"):
        return "settling"
    if s.get("held"):
        return "held"
    if not s["visible"]:
        return "missing"
    return "in_place" if s["in_place"] else "moved"


def resting(box, track_id=1, conf=0.5):
    """The object still in `box` as a track."""
    return lambda t: [Track(track_id, TARGET, box, conf)]


def weak_at(box, conf=0.15):
    """Only a detection too weak to start a track, at `box`."""
    return lambda t: [Track(-1, TARGET, box, conf)]


def nothing(_t):
    """No sighting."""
    return []


def answered(value) -> Future:
    """A Future that already holds `value`."""
    f: Future = Future()
    f.set_result(value)
    return f


def test_confirm_answered_later_takes_over_only_once_it_says_yes():
    """A crop check that runs off the frame loop: no takeover while it runs, the track is taken once it says yes,
    and only one check is asked for it."""
    pending: Future = Future()
    asked = []
    r = TargetRules("desk", "Desk", TARGET, home=HOME,
                    confirm=lambda image, box: asked.append(box) or pending)
    feed(r, 0, 2, resting(HOME))
    feed(r, 2, 3, nothing)
    out = feed(r, 3, 4, resting(KEYBOARD, track_id=5))
    assert out == [] and r.track_id == 1 and len(asked) == 1
    pending.set_result(True)
    out = feed(r, 4, 6, resting(KEYBOARD, track_id=5))
    assert r.track_id == 5 and [kind(s) for s in out] == ["settling", "moved"] and len(asked) == 1


def test_confirm_with_no_answer_in_time_is_a_no(monkeypatch):
    """A crop check still running after CONFIRM_WAIT_S counts as a no: the track is rejected."""
    monkeypatch.setattr(track_rules, "CONFIRM_WAIT_S", 0.0)
    r = TargetRules("desk", "Desk", TARGET, home=HOME, confirm=lambda image, box: Future())
    feed(r, 0, 2, resting(HOME))
    feed(r, 2, 3, nothing)
    feed(r, 3, 4, resting(KEYBOARD, track_id=5))
    assert 5 in r.rejected and r.track_id == 1


def test_steady_weak_detection_brings_a_missing_object_back_once_confirmed():
    """While missing, a weak detection that stays at one spot is checked once; accepted, it is the object back
    (settling, then moved to that spot), with no track ever started."""
    asked = []
    r = TargetRules("desk", "Desk", TARGET, home=HOME, weak_returns=True,
                    confirm=lambda image, box: asked.append(box) or True)
    feed(r, 0, 2, resting(HOME))
    out = feed(r, 2, 3, nothing) + feed(r, 3, 6, weak_at(KEYBOARD))
    assert [kind(s) for s in out] == ["missing", "settling", "moved"] and out[-1]["box"] == KEYBOARD
    assert len(asked) == 1 and r.weak_spot is None and r.anchor == KEYBOARD


def test_weak_returns_need_a_yes_a_clear_spot_and_the_setting():
    """A refused spot is not asked about again; a sliver at the frame's edge is never asked about; without
    weak_returns (checks off) a steady weak detection doesn't bring the object back."""
    asked = []
    r = TargetRules("desk", "Desk", TARGET, home=HOME, weak_returns=True,
                    confirm=lambda image, box: asked.append(box) or False)
    feed(r, 0, 2, resting(HOME))
    out = feed(r, 2, 3, nothing) + feed(r, 3, 8, weak_at(KEYBOARD))
    assert [kind(s) for s in out] == ["missing"] and len(asked) == 1
    sliver = [0.58, 0.89, 0.79, 1.0]
    r = TargetRules("desk", "Desk", TARGET, home=HOME, weak_returns=True,
                    confirm=lambda image, box: asked.append(box) or True)
    feed(r, 0, 2, resting(HOME))
    assert [kind(s) for s in feed(r, 2, 3, nothing) + feed(r, 3, 6, weak_at(sliver))] == ["missing"]
    r = TargetRules("desk", "Desk", TARGET, home=HOME)
    feed(r, 0, 2, resting(HOME))
    assert [kind(s) for s in feed(r, 2, 3, nothing) + feed(r, 3, 6, weak_at(KEYBOARD))] == ["missing"]
    assert len(asked) == 1


def test_at_spot_needs_the_model_box_on_the_spot(monkeypatch):
    """The heads-up check counts the object as still there only when the vision model's box overlaps its spot:
    a sliver at the bottom edge next to the spot is not it."""
    spot = [0.47, 0.73, 0.69, 0.96]
    answers = iter([[0.483, 0.756, 0.692, 1.0], [0.698, 0.934, 0.795, 1.0], None])
    monkeypatch.setattr(model_checks.vision, "find_in_frame", lambda *args: next(answers))
    assert model_checks.at_spot(TARGET, "Desk", IMAGE, spot, "m") == [0.483, 0.756, 0.692, 1.0]
    assert model_checks.at_spot(TARGET, "Desk", IMAGE, spot, "m") is None
    assert model_checks.at_spot(TARGET, "Desk", IMAGE, spot, "m") is None


def start_watch(store) -> dict:
    """Desk online, the owner registered, and an object watch on the Desk camera."""
    cameras.register(store, {"label": "Desk"})
    people.add(store, {"name": "Owner", "role": "owner"})
    time.sleep(0.002)
    order = {"kind": "object", "target": "controller", "cameras": "all"}
    return store.start_session("keep an eye on my controller", {"watch_order": order, "focus_object": "controller",
                                                                 "focus_camera_id": "desk", "focus_box": HOME})


def missing_follower(session: dict) -> analyze.Follower:
    """A Desk follower whose rules have the object missing from its resting spot, with a newest frame."""
    rules = TargetRules("desk", "Desk", TARGET, home=HOME)
    feed(rules, 0, 2, resting(HOME))
    feed(rules, 2, 3, nothing)
    assert rules.phase == "missing"
    return analyze.Follower(session["session_id"], None, rules, image=IMAGE)


def test_heads_up_waits_for_the_check_and_is_dropped_when_still_there(store, monkeypatch):
    """The timers' heads-up is held while the check runs; when the model still sees the object at its spot, no
    event is written, the object is no longer missing, and the rules take it back at its spot."""
    session = start_watch(store)
    gone = to_iso(parse(now()) - timedelta(seconds=10))
    store.update_session(session["session_id"], in_view=True, missing_since=gone)
    release = threading.Event()

    def slow_find(*_args):
        """Answer 'at the spot' once released."""
        release.wait(5)
        return [0.46, 0.71, 0.56, 0.81]

    monkeypatch.setattr(model_checks.vision, "find_in_frame", slow_find)
    checks = Checks(model="test", timeout_s=5, enabled=True)
    follower = missing_follower(session)
    gate = lambda s: checks.heads_up(s, follower)
    assert analyze.tick(store, gate=gate) == [] and store.list_events(session["session_id"]) == []
    release.set()
    checks.pool.shutdown(wait=True)
    assert analyze.tick(store, gate=gate) == []
    assert store.get_session(session["session_id"])["missing_since"] is None
    assert store.list_events(session["session_id"]) == []
    follower.rules.step(at(3.5), IMAGE, [], ASPECT)
    assert follower.rules.phase == "rest" and follower.rules.anchor == HOME
    assert checks.status(session["session_id"])["counts"] == {"heads_up": 1, "heads_up_dropped": 1}


def test_heads_up_is_sent_when_gone_on_failure_or_after_the_wait(store, monkeypatch):
    """The heads-up goes out when the model doesn't see the object at its spot, when the check fails, and when it
    has no answer within the wait; with checks off it goes out at once, with no model call."""
    for find, timeout, counted in ((lambda *a: None, 5, "heads_up_sent"),
                                   (lambda *a: (_ for _ in ()).throw(RuntimeError("down")), 5, "heads_up_sent"),
                                   (lambda *a: time.sleep(0.5), 0.05, "heads_up_timeout")):
        session = start_watch(store)
        store.update_session(session["session_id"], in_view=True,
                             missing_since=to_iso(parse(now()) - timedelta(seconds=10)))
        monkeypatch.setattr(model_checks.vision, "find_in_frame", find)
        checks = Checks(model="test", timeout_s=timeout, enabled=True)
        follower = missing_follower(session)
        gate = lambda s: checks.heads_up(s, follower)
        written = analyze.tick(store, gate=gate)
        if not written:
            time.sleep(0.2)
            written = analyze.tick(store, gate=gate)
        assert [(e["action"], e["alert"]) for e in written] == [("left_view", "heads_up")]
        assert checks.status(session["session_id"])["counts"][counted] == 1
        store.end_session(session["session_id"])
    session = start_watch(store)
    store.update_session(session["session_id"], in_view=True,
                         missing_since=to_iso(parse(now()) - timedelta(seconds=10)))
    off = Checks(model="test", enabled=False)
    assert len(analyze.tick(store, gate=lambda s: off.heads_up(s, missing_follower(session)))) == 1
    assert off.status(session["session_id"])["counts"] == {}


class FakeBoxes:
    """Stands in for ultralytics Boxes: pixel xyxy, confidences and classes."""

    def __init__(self, rows):
        """Rows of (x0, y0, x1, y1, conf, cls)."""
        self.xyxy = [r[:4] for r in rows]
        self.conf = [r[4] for r in rows]
        self.cls = [r[5] for r in rows]

    def __len__(self):
        """How many boxes."""
        return len(self.conf)


def test_best_box_takes_the_highest_confidence_and_breaks_ties_by_size():
    """Grounding takes the most confident box of the object's prompt above the floor; confidences equal to the
    hundredth go to the larger box; people and weak boxes never win."""
    boxes = FakeBoxes([(0, 0, 10, 10, 0.40, 0), (20, 20, 60, 60, 0.404, 0), (0, 0, 100, 100, 0.9, 1),
                       (50, 50, 90, 90, 0.38, 0)])
    box, conf = detection.best_box(boxes, 100, 100)
    assert box == [0.2, 0.2, 0.6, 0.6] and conf == 0.404
    assert detection.best_box(FakeBoxes([(0, 0, 10, 10, detection.GROUND_MIN_CONF - 0.01, 0)]), 100, 100) is None


def test_grounding_uses_the_detector_and_asks_the_vision_model_only_when_it_finds_nothing(store, monkeypatch):
    """With a detector, grounding takes its box on the first camera that shows the object (no model call); the
    vision model is asked only when the detector finds the object on no camera."""
    import cv2

    cameras.register(store, {"label": "Desk"})
    jpeg = cv2.imencode(".jpg", np.zeros((360, 640, 3), np.uint8))[1].tobytes()
    monkeypatch.setattr(sessions, "frame_reader", lambda _store: lambda camera: jpeg)

    class FakeDetector:
        """Finds the object at a fixed box, or nothing."""
        def __init__(self, rows):
            self.rows, self.prompts = rows, []

        def set_prompts(self, prompts):
            """Keep the prompts."""
            self.prompts = prompts

        def detect(self, image):
            """The scripted boxes."""
            return FakeBoxes(self.rows)

    asked = []
    monkeypatch.setattr(sessions.vision, "ground", lambda target, cams, frame_for: asked.append(target) or
                        {"camera_id": "desk", "box": [0.1, 0.1, 0.2, 0.2]})
    log = []
    found = sessions.grounder(store, FakeDetector([(320, 252, 448, 324, 0.5, 0)]), log.append)("controller",
                                                                                            store.list_cameras())
    assert found == {"camera_id": "desk", "box": [0.5, 0.7, 0.7, 0.9]} and asked == []
    det = FakeDetector([])
    found = sessions.grounder(store, det, log.append)("controller", store.list_cameras())
    assert found["box"] == [0.1, 0.1, 0.2, 0.2] and asked == ["controller"]
    assert det.prompts == ["game controller", "person"] and "detector found" in log[0]


class FakeTracks:
    """Stands in for a camera's detector tracks: the scripted tracks for each frame in turn."""

    def __init__(self, script):
        """Keep the script."""
        self.script = list(script)

    def update(self, image):
        """The next frame's tracks (none once the script runs out)."""
        return self.script.pop(0) if self.script else []


def test_live_box_is_written_at_most_twice_a_second_and_served(store):
    """follow() keeps the object's live box in the session (where, held, missing), written only when it changes and
    at most once per LIVE_BOX_GAP_S of frame time; GET /api/sessions/latest returns it, and an ended session is
    never written again."""
    session = start_watch(store)
    start = parse(now()) + timedelta(seconds=1)
    script = [[Track(1, TARGET, HOME, 0.5)]] * 4
    script += [[Track(1, TARGET, [0.45 + 0.04 * i, 0.7, 0.55 + 0.04 * i, 0.8], 0.5)] for i in range(1, 9)]
    script += [[]] * 8
    rules = TargetRules("desk", "Desk", TARGET, home=HOME)
    followers = {"desk": analyze.Follower(session["session_id"], FakeTracks(script), rules)}
    writes, seen = 0, []
    real = store.set_session_field

    def counting(*args):
        """Count the live-box writes."""
        nonlocal writes
        writes += 1
        return real(*args)

    store.set_session_field = counting
    for i in range(len(script)):
        analyze.follow(store, "desk", to_iso(start + timedelta(seconds=i * STEP)), IMAGE, followers,
                       SimpleNamespace(name="yoloe-test"))
        live = store.get_session(session["session_id"]).get("live_box")
        seen.append((live["held"], live["missing"]) if live else None)
    frames_s = len(script) * STEP
    assert writes <= frames_s / analyze.LIVE_BOX_GAP_S + 1
    assert (True, False) in seen and seen[-1] == (False, True)
    live = TestClient(build_app(store, compile_order=lambda *_: {}), headers=KEY).get("/api/sessions/latest").json()["session"]
    assert live["live_box"]["camera_id"] == "desk" and live["live_box"]["missing"] is True
    assert len(live["live_box"]["box"]) == 4 and live["live_box"]["at"]
    store.end_session(session["session_id"])
    assert store.set_session_field(session["session_id"], "live_box", None) is False
    assert store.get_session(session["session_id"])["ended_at"]


def test_locate_hook_asks_the_vision_model_off_the_frame_loop(monkeypatch):
    """locate_for gives a hook that asks the vision model for the object's box in a whole frame and counts the
    answers; with checks or locating off there is no hook."""
    boxes = iter([[0.1, 0.1, 0.2, 0.2], None])
    monkeypatch.setattr(model_checks.vision, "find_in_frame", lambda target, label, jpeg, model: next(boxes))
    checks = Checks(model="test", enabled=True, wait=True, locating=True)
    locate = checks.locate_for("ses_1", TARGET, "Desk")
    assert locate(IMAGE).result() == [0.1, 0.1, 0.2, 0.2] and locate(IMAGE).result() is None
    assert checks.status("ses_1")["counts"] == {"locate": 2, "locate_found": 1, "locate_none": 1}
    assert Checks(model="test", enabled=True, locating=False).locate_for("ses_1", TARGET, "Desk") is None
    assert Checks(model="test", enabled=False, locating=True).locate_for("ses_1", TARGET, "Desk") is None


def test_heads_up_needs_no_check_once_the_vision_model_found_it_gone(store, monkeypatch):
    """When a locate question asked after the object went missing found nothing, the heads-up goes out with no
    second model call."""
    session = start_watch(store)
    store.update_session(session["session_id"], in_view=True,
                         missing_since=to_iso(parse(now()) - timedelta(seconds=10)))
    monkeypatch.setattr(model_checks.vision, "find_in_frame", lambda *a: (_ for _ in ()).throw(AssertionError()))
    rules = TargetRules("desk", "Desk", TARGET, home=HOME, locate=lambda image: None)
    feed(rules, 0, 2, resting(HOME))
    feed(rules, 2, 5, nothing)
    assert rules.gone_checked()
    checks = Checks(model="test", enabled=True)
    follower = analyze.Follower(session["session_id"], None, rules, image=IMAGE)
    (e,) = analyze.tick(store, gate=lambda s: checks.heads_up(s, follower))
    assert e["action"] == "left_view"
    assert checks.status(session["session_id"])["counts"] == {"heads_up_located_gone": 1}


class LookDetector:
    """Stands in for the detector when arming a follower: one detection of the object at a given confidence, and a
    record of the looks added and dropped."""

    def __init__(self, conf: float, look: str | None = None, box: list[float] | None = None):
        """Detect the object at `box` (HOME by default) with this confidence; `look` is a look already in place."""
        self.conf, self.look, self.added, self.dropped, self.box = conf, look, [], 0, box or HOME
        self.name = "yoloe-test"

    def detect(self, image):
        """One box (in pixels of the frame)."""
        h, w = image.shape[:2]
        b = self.box
        return FakeBoxes([(b[0] * w, b[1] * h, b[2] * w, b[3] * h, self.conf, 0)])

    def add_look(self, key, image, box, weight):
        """Record the look."""
        self.added.append((key, box, weight))
        self.look = key

    def drop_look(self):
        """Record the drop."""
        self.dropped += 1
        self.look = None


def test_arm_adds_the_look_only_when_the_words_are_weak(store, monkeypatch):
    """On a follower's first frame: the rules get the locate hook (checks on); the step-away box becomes a visual
    prompt only when the phrase alone finds the object there below the tracker's start threshold, once, built from
    the weak detection's tighter box when there is one, and a look left by an earlier session is dropped first.
    With visual prompts off nothing is added."""
    session = start_watch(store)
    monkeypatch.setattr(analyze.config, "VISUAL_PROMPT", True)
    for conf, older, added in ((0.12, None, 1), (0.6, None, 0), (0.6, "ses_old", 0), (0.12, "ses_old", 1)):
        det = LookDetector(conf, older)
        follower = analyze.Follower(session["session_id"], None, TargetRules("desk", "Desk", TARGET, home=HOME))
        analyze.arm(follower, session, det, Checks(model="test", enabled=True, locating=True), IMAGE)
        analyze.arm(follower, session, det, None, IMAGE)
        assert len(det.added) == added and det.dropped == (older is not None), (conf, older)
        assert follower.rules.locate is not None
    tighter = [0.46, 0.71, 0.54, 0.79]
    det = LookDetector(0.08, box=tighter)
    analyze.arm(analyze.Follower(session["session_id"], None, TargetRules("desk", "Desk", TARGET, home=HOME)),
                session, det, None, IMAGE)
    assert [round(v, 4) for v in det.added[0][1]] == tighter
    monkeypatch.setattr(analyze.config, "VISUAL_PROMPT", False)
    det = LookDetector(0.12)
    analyze.arm(analyze.Follower(session["session_id"], None, TargetRules("desk", "Desk", TARGET, home=HOME)),
                session, det, None, IMAGE)
    assert det.added == []


def test_live_box_says_what_placed_it(store):
    """The live box carries its source: step_away before any sighting, detector once tracked, vision when the
    vision model placed it."""
    session = start_watch(store)
    rules = TargetRules("desk", "Desk", TARGET, home=HOME, locate=lambda image: [0.6, 0.6, 0.7, 0.7])
    follower = analyze.Follower(session["session_id"], None, rules)
    start = parse(now()) + timedelta(seconds=1)
    rules.lock_at_home(0.0)
    sources = []
    for i, tracks in enumerate([[Track(1, TARGET, HOME, 0.5)]] * 4 + [[]] * 20):
        at_i = to_iso(start + timedelta(seconds=i * STEP))
        follower.live_at = None
        analyze.keep_live_box(store, session["session_id"], "desk", at_i, follower)
        sources.append(store.get_session(session["session_id"])["live_box"]["source"])
        rules.step(at_i, IMAGE, tracks, ASPECT)
    assert sources[0] == "step_away" and "detector" in sources and sources[-1] == "vision"
