"""The analyser: checks each uploaded frame or clip against the active session's watch order and writes the
tracker's events. handle_upload analyses one upload; Analyser feeds
it from a background queue (frames before clips) and runs the tracker's timers every second. With detector
tracking, follow() takes live frames from the focus camera instead (keeping the object's live box in the session),
that camera's frames need no model call, and a heads-up that the object left view can wait for one check.
"Still tracking" and "taken" wait for clips from other cameras that are still queued.
"""

from __future__ import annotations

import re
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

import cv2
import numpy as np

from meanwhile import config
from meanwhile.api import uploads
from meanwhile.contract import Event, Session
from meanwhile.pipeline import detector as detection
from meanwhile.pipeline import checks as model_checks
from meanwhile.pipeline import faces as face_id
from meanwhile.pipeline import identity, naming, states, track_rules, tracker, unnamed, video_model, vision
from meanwhile.pipeline.models import ModelUnavailable
from meanwhile.store import Store, now as now_iso, parse, seconds_since, to_iso

KEY = re.compile(r"^(frames|clips)/([a-z0-9-]+)/(\d{8}T\d{9}Z)\.(jpg|webm|mp4)$")
CLIP_MATCH_SLACK_S = 3
RECENT_RESULTS = 20
KEPT_CLIPS = 30
CLIP_MAX_WAIT_S = 10
LIVE_BOX_GAP_S = 0.5

CheckFrame = Callable[..., dict[str, Any]]
Describe = Callable[..., "tuple[dict[str, Any], video_model.Clip]"]
Gate = Callable[[Session], str]


def stamp_time(stamp: str) -> str:
    """An upload key's stamp as an ISO time: 20261011T093200123Z -> 2026-10-11T09:32:00.123Z."""
    return f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}.{stamp[15:18]}Z"


def watches(session: Session, camera_id: str) -> bool:
    """Whether the session's watch order covers this camera."""
    cameras = session["watch_order"].get("cameras", [])
    return cameras == "all" or camera_id in cameras


def covers(clip: dict[str, str], camera_id: str | None, at: str) -> bool:
    """Whether a kept clip is from this camera and covers `at` (with CLIP_MATCH_SLACK_S either side)."""
    lo = parse(clip["start"]) - timedelta(seconds=CLIP_MATCH_SLACK_S)
    hi = parse(clip["end"]) + timedelta(seconds=CLIP_MATCH_SLACK_S)
    return clip["camera_id"] == camera_id and lo <= parse(at) <= hi


def keep_clip(store: Store, session_id: str, camera_id: str, key: str, start: str, end: str) -> None:
    """Remember an uploaded clip's span for the session (the newest KEPT_CLIPS), so an event written later,
    like a heads-up from the timers, still gets the clip that covers it."""
    clips = store.get_meta(f"clips:{session_id}", [])[-(KEPT_CLIPS - 1):]
    store.put_meta(f"clips:{session_id}", clips + [{"camera_id": camera_id, "key": key, "start": start, "end": end}])


def clip_for(store: Store, session_id: str, camera_id: str | None, at: str) -> str | None:
    """The newest kept clip from this camera that covers `at`, or None."""
    found = [c for c in store.get_meta(f"clips:{session_id}", []) if covers(c, camera_id, at)]
    return found[-1]["key"] if found else None


def write(store: Store, session: Session, decision: tracker.Decision, defaults: dict[str, Any]) -> list[Event]:
    """Store the tracker's events (defaults filled in, ts = now, and a kept clip that covers them if they have
    none), record the new state of anything an event changed, and store the session changes."""
    written = []
    for e in decision["events"]:
        event = {**defaults, "session_id": session["session_id"], **e}
        clip = None if event.get("clip_key") else clip_for(store, session["session_id"], event.get("camera_id"),
                                                             event.get("happened_at") or now_iso())
        if clip:
            event["clip_key"] = clip
        written.append(stored := store.add_event(event))
        states.record(store, stored)
    if decision["session"]:
        store.update_session(session["session_id"], **decision["session"])
    return written


def attach_clip(store: Store, events: list[Event], camera_id: str, clip_key: str, start: str, end: str) -> None:
    """Give events from this camera that have no clip yet (e.g. a heads-up from a frame) this clip, if it covers them."""
    lo, hi = parse(start) - timedelta(seconds=CLIP_MATCH_SLACK_S), parse(end) + timedelta(seconds=CLIP_MATCH_SLACK_S)
    for e in events:
        at = parse(e.get("happened_at") or e["ts"])
        if e.get("camera_id") == camera_id and not e.get("clip_key") and lo <= at <= hi:
            store.update_event(e["event_id"], clip_key=clip_key)


def keep_reference(store: Store, session_id: str, frame_key: str) -> None:
    """Make this frame the session's reference (how the object looks at rest now), as a copy that outlives cleanup."""
    camera_id, stamp = KEY.match(frame_key).group(2, 3)
    ref_key = f"refs/{camera_id}/{session_id}-{stamp}.jpg"
    store.write_media(ref_key, store.media_path(frame_key).read_bytes())
    store.update_session(session_id, ref_frame_key=ref_key)


def relabel_episode(store: Store, session: Session, change: dict[str, str], people: list) -> None:
    """Credit the episode's events to the person the majority of readings now says did it."""
    by_id = {p["person_id"]: p for p in people}
    labels = {c["camera_id"]: c.get("label", c["camera_id"]) for c in store.list_cameras()}
    target = session["watch_order"].get("target", "object")
    for e in store.list_events(session["session_id"]):
        if e.get("episode") == change["episode"] and e.get("actor_id") != change["actor"]:
            store.update_event(e["event_id"], **tracker.relabel(e, change["actor"], target, by_id,
                                                                 labels.get(e.get("camera_id"), "")))


def read_frame(store: Store, session: Session, key: str, check_frame: CheckFrame,
               people: list) -> tuple[tracker.Seen, str] | str:
    """Run the frame check on a focus-camera frame; returns the reading and a short note, or why it was skipped."""
    order = session["watch_order"]
    camera_id = KEY.match(key).group(2)
    if order.get("kind") != "object" or camera_id != session.get("focus_camera_id") or not session.get("focus_box"):
        return "skipped: only the focus camera's frames are checked"
    reference = None
    if session.get("ref_frame_key"):
        try:
            reference = store.media_path(session["ref_frame_key"]).read_bytes()
        except FileNotFoundError:
            pass
    label = (store.get_camera(camera_id) or {}).get("label", camera_id)
    answer = check_frame(order.get("target", "object"), label, store.media_path(key).read_bytes(), session["focus_box"],
                         identity.describe_people(people), reference)
    seen: tracker.Seen = {"kind": "frame", "camera_id": camera_id, "camera_label": label,
                          "happened_at": stamp_time(KEY.match(key).group(3)), "visible": bool(answer.get("visible")),
                          "held": bool(answer.get("held")), "in_place": bool(answer.get("in_place")),
                          "box": answer.get("box"), "person": identity.person_id_for(answer.get("person"), people)}
    return seen, (f"changed={answer.get('changed')} visible={seen['visible']} held={seen['held']} "
                  f"in_place={seen['in_place']} person={seen['person'] or '-'}")


def read_clip(store: Store, session: Session, key: str, describe: Describe,
              people: list) -> tuple[tracker.Seen, str]:
    """Ask the video model about a clip, listing only the session's known people; event times are placed from the
    clip's start (its key time minus its length). In a relevant clip, people are named by the faces in its frames
    (with face ID on); on an object watch, an actor nobody recognised is the person the session saw holding the
    object, and anyone else not known to the session becomes an unnamed person."""
    _, camera_id, stamp, _ = KEY.match(key).groups()
    camera = store.get_camera(camera_id) or {}
    answer, clip = describe(store.media_path(key), session, camera.get("label", camera_id),
                            naming.session_people(store, session["session_id"], people))
    end = parse(stamp_time(stamp))
    start = end - timedelta(seconds=clip["duration_s"])
    found: list[tracker.SeenEvent] = []
    offsets: list[float] = []
    for e in answer.get("events") or []:
        t = min(max(float(e.get("t") or 0), 0.0), clip["duration_s"])
        offsets.append(t)
        found.append({"happened_at": to_iso(start + timedelta(seconds=t)), "action": str(e.get("action") or ""),
                      "actor": identity.person_id_for(e.get("actor"), people),
                      "recipient": identity.person_id_for(e.get("recipient"), people),
                      "actor_wears": str(e.get("actor_wears") or ""),
                      "recipient_wears": str(e.get("recipient_wears") or ""),
                      "thing": str(e.get("thing") or ""), "state": str(e.get("state") or ""),
                      "summary": str(e.get("summary") or ""), "alert": bool(e.get("alert"))})
    said = [e["actor"] for e in found]
    faces = face_id.get_faces()
    took: list[float] = []
    if answer.get("relevant"):
        if faces is not None:
            took = naming.name_clip_events(faces, store, session["session_id"], clip["frames"], clip["times"], found,
                                           offsets, camera_id)
        holder = session.get("state_actor") if session["watch_order"].get("kind") == "object" else None
        naming.only_known(store, session["session_id"], found, camera_id, holder)
    for e, was in zip(found, said):
        if e["actor"] != was or e["actor"].startswith(unnamed.PREFIX):
            e["renamed"] = True
    seen: tracker.Seen = {"kind": "clip", "camera_id": camera_id, "camera_label": camera.get("label", camera_id),
                          "happened_at": to_iso(start),
                          "relevant": bool(answer.get("relevant")), "events": found}
    actions = ", ".join(f"{e['action']}@{e['happened_at'][11:19]}" for e in found) or "nothing"
    named = [f"{e['actor']} {s:.2f} s" for e, s in zip([e for e in found if e["actor"]], took)]
    named_note = f" faces={', '.join(named)}" if named else ""
    return seen, f"relevant={seen['relevant']} {actions}{named_note}"


def followed(session: Session, camera_id: str, tracking: str) -> bool:
    """Whether the detector follows this camera for the session (with detector tracking: the focus camera of an
    object watch)."""
    return (tracking == "detector" and session["watch_order"].get("kind") == "object"
            and camera_id == session.get("focus_camera_id"))


def handle_upload(store: Store, key: str, lock: threading.Lock | None = None,
                  check_frame: CheckFrame = vision.check_frame, describe: Describe = video_model.describe,
                  tracking: str | None = None) -> str:
    """Analyse one uploaded frame or clip for the active session and write what the tracker decides;
    returns a one-line outcome. On a camera the detector follows, frames are skipped; its clips go to the video
    model for anything else done to the object when OBJECT_CLIP_ACTIONS is on, else they only join the events they
    cover. Raises ModelUnavailable if the model can't answer."""
    match = KEY.match(key)
    if not match:
        return "skipped: not a frame or clip"
    folder, camera_id, stamp, _ = match.groups()
    session = store.active_session()
    if session is None:
        return "skipped: no active session"
    if stamp_time(stamp) < session["started_at"]:
        return "skipped: from before the session started"
    if not watches(session, camera_id):
        return "skipped: the session doesn't watch this camera"
    if followed(session, camera_id, tracking or config.TRACKING):
        if folder == "frames":
            return "skipped: the detector follows this camera"
        if config.OBJECT_CLIP_ACTIONS:
            return read_and_write(store, session, key, lock, check_frame, describe)
        end = stamp_time(stamp)
        start = to_iso(parse(end) - timedelta(seconds=video_model.clip_length_s(store.media_path(key))))
        with lock or threading.Lock():
            keep_clip(store, session["session_id"], camera_id, key, start, end)
            attach_clip(store, store.list_events(session["session_id"]), camera_id, key, start, end)
        return "clip kept: the detector follows this camera"
    return read_and_write(store, session, key, lock, check_frame, describe)


def read_and_write(store: Store, session: Session, key: str, lock: threading.Lock | None, check_frame: CheckFrame,
                   describe: Describe) -> str:
    """Read one frame or clip with its model, write what the tracker decides, and keep a clip for the events it
    covers; returns a one-line outcome."""
    folder, camera_id, stamp, _ = KEY.match(key).groups()
    people = store.list_people()
    if folder == "frames":
        read = read_frame(store, session, key, check_frame, people)
        if isinstance(read, str):
            return read
        media, source, model = {"frame_key": key}, "vision", config.VISION_MODEL
    else:
        read = read_clip(store, session, key, describe, people)
        people = store.list_people()
        media, source, model = {"clip_key": key}, "video_model", config.VIDEO_MODEL
    seen, note = read
    with lock or threading.Lock():
        session = store.get_session(session["session_id"])
        if session.get("ended_at"):
            return f"{note} -> skipped: the session ended"
        recent = store.list_events(session["session_id"])
        decision = tracker.on_seen(session, seen, recent, {p["person_id"]: p for p in people})
        written = write(store, session, decision, {"camera_id": camera_id, "source": source, "model": model, **media})
        if decision.get("new_reference"):
            keep_reference(store, session["session_id"], key)
        if decision.get("relabel"):
            relabel_episode(store, store.get_session(session["session_id"]), decision["relabel"], people)
        if folder == "clips":
            keep_clip(store, session["session_id"], camera_id, key, seen["happened_at"], stamp_time(stamp))
            attach_clip(store, recent, camera_id, key, seen["happened_at"], stamp_time(stamp))
    outcome = ", ".join(f"{e['action']}/{e['alert']}" for e in written) or "no event"
    state = f" state={decision['session']['state']}" if "state" in decision["session"] else ""
    return f"{note} -> {outcome}{state}"


def clip_due(session: Session, keys: list[str]) -> bool:
    """Whether one of these upload keys is a clip from another watched camera that ends after the object left the
    focus camera's view (it may still show the object somewhere else)."""
    for key in keys:
        match = KEY.match(key)
        if (match and match.group(1) == "clips" and match.group(2) != session.get("focus_camera_id")
                and watches(session, match.group(2)) and stamp_time(match.group(3)) >= (session.get("left_at") or "")):
            return True
    return False


def tick(store: Store, lock: threading.Lock | None = None, at: str | None = None,
         gate: Gate | None = None, waiting: Callable[[], list[str]] | None = None) -> list[Event]:
    """Run the tracker's timers for the active session once; returns any events written. When they would say the
    object left view, `gate` decides first: hold it while a check runs, drop it (the object is still there, so it
    is no longer missing), or send it. 'Still tracking' and 'taken' wait while a clip that may show the object on
    another camera is still `waiting` to be analysed."""
    with lock or threading.Lock():
        session = store.active_session()
        if session is None:
            return []
        people = {p["person_id"]: p for p in store.list_people()}
        decision = tracker.on_tick(session, at or now_iso(), store.list_events(session["session_id"]), people)
        if waiting and any(e["action"] in ("still_tracking", "taken") for e in decision["events"]) \
                and clip_due(session, waiting()):
            return []
        if gate and any(e["action"] == "left_view" for e in decision["events"]):
            verdict = gate(session)
            if verdict == model_checks.THERE:
                store.update_session(session["session_id"], missing_since=None)
            if verdict != model_checks.SEND:
                return []
        return write(store, session, decision, {"camera_id": session.get("focus_camera_id", ""), "source": "tracker"})


@dataclass
class Follower:
    """One camera's tracks and object rules for one session, its newest frame (for checks), the live box last
    written to the session (when, by the frame clock, and what), and whether its first frame has armed the
    vision-model locate hook and the detector's visual prompt."""
    session_id: str
    tracks: Any
    rules: track_rules.TargetRules
    image: Any = None
    live_at: str | None = None
    live: dict[str, Any] | None = None
    armed: bool = False


def arm(follower: Follower, session: Session, detector: Any, checks: model_checks.Checks | None,
        image: np.ndarray) -> None:
    """On a follower's first frame: give its rules the vision-model locate hook (with checks on), and, with visual
    prompts on, add the object's look to the detector's phrase when the phrase alone finds it at its step-away box
    too weakly to start a track (a look from an earlier session is dropped first). The look is taken from the best
    weak detection there, which fits the object more tightly than a grounding box, else from the step-away box."""
    if follower.armed:
        return
    follower.armed, rules = True, follower.rules
    if checks is not None:
        rules.locate = checks.locate_for(session["session_id"], rules.target, rules.camera_label)
    if not config.VISUAL_PROMPT or rules.home is None \
            or not callable(getattr(detector, "add_look", None)):
        return
    if detector.look not in (None, session["session_id"]):
        detector.drop_look()
    if detector.look == session["session_id"]:
        return
    aspect = image.shape[0] / image.shape[1]
    boxes, (h, w) = detector.detect(image), image.shape[:2]
    found = [(float(boxes.conf[i]), [float(v) / d for v, d in zip(boxes.xyxy[i], (w, h, w, h))])
             for i in range(len(boxes)) if int(boxes.cls[i]) == 0]
    reach = rules.reach(rules.home, aspect)
    at_home = [(conf, box) for conf, box in found if rules.dist(box, rules.home, aspect) <= reach]
    conf, box = max(at_home, default=(0.0, rules.home), key=lambda f: f[0])
    if conf < detection.TRACKER_SETTINGS["new_track_thresh"]:
        detector.add_look(session["session_id"], image, box, config.VISUAL_PROMPT_WEIGHT)


def follower_for(store: Store, session: Session, camera_id: str, followers: dict[str, Follower], detector: Any,
                 confirm: track_rules.Confirm, identify: track_rules.Identify,
                 checks: model_checks.Checks | None = None) -> Follower:
    """The camera's follower for this session, made on its first frame (prompting the detector with the
    object's phrase and 'person'; with `checks` on, new tracks and steady weak detections of a missing object are
    confirmed by a crop check; with face ID on and no identify check given, person tracks are named by face);
    followers of earlier sessions are dropped."""
    follower = followers.get(camera_id)
    if follower and follower.session_id == session["session_id"]:
        return follower
    phrase = detection.phrase_for(session["watch_order"].get("target", "object"))
    detector.set_prompts([phrase, detection.PERSON])
    label = (store.get_camera(camera_id) or {}).get("label", camera_id)
    if checks is not None:
        confirm = checks.confirm_for(session["session_id"], phrase, label)
    faces = face_id.get_faces() if identify is track_rules.nobody_known else None
    if faces is not None:
        identify = naming.TrackNamer(faces, store, session["session_id"])
    rules = track_rules.TargetRules(camera_id, label, phrase, home=session.get("focus_box"), confirm=confirm,
                                    identify=identify, weak_returns=checks is not None and checks.enabled)
    if isinstance(identify, naming.TrackNamer):
        identify.rules = rules
    followers[camera_id] = Follower(session["session_id"], detection.CameraTracks(detector), rules)
    return followers[camera_id]


def keep_frame(store: Store, camera_id: str, at: str, image: np.ndarray) -> str:
    """Save a live frame as the evidence picture for an event; returns its media key."""
    key = f"frames/{camera_id}/{uploads.stamp(at)}.jpg"
    store.write_media(key, cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes())
    return key


def keep_live_box(store: Store, session_id: str, camera_id: str, at: str, follower: Follower,
                  lock: threading.Lock | None = None) -> bool:
    """Write the object's live box to the session when it changed, at most once per LIVE_BOX_GAP_S of frame time;
    returns whether it was written."""
    live = {"camera_id": camera_id, **follower.rules.live_box()}
    if live == follower.live or (follower.live_at and seconds_since(follower.live_at, at) < LIVE_BOX_GAP_S):
        return False
    with lock or threading.Lock():
        store.set_session_field(session_id, "live_box", {**live, "at": at})
    follower.live, follower.live_at = live, at
    return True


def follow(store: Store, camera_id: str, at: str, image: np.ndarray, followers: dict[str, Follower], detector: Any,
           lock: threading.Lock | None = None, confirm: track_rules.Confirm = track_rules.accept_all,
           identify: track_rules.Identify = track_rules.nobody_known,
           checks: model_checks.Checks | None = None) -> str:
    """Track the object in one live BGR frame taken at `at` from a camera the detector follows, keep its live box,
    and write what the tracker decides (the frame is kept as the events' picture); returns a one-line outcome.
    With `checks`, new tracks away from the object's spot are confirmed by a vision-model crop check."""
    session = store.active_session()
    if session is None:
        return "skipped: no active session"
    if at < session["started_at"]:
        return "skipped: from before the session started"
    if not watches(session, camera_id) or not followed(session, camera_id, "detector"):
        return "skipped: the detector doesn't follow this camera"
    follower = follower_for(store, session, camera_id, followers, detector, confirm, identify, checks)
    arm(follower, session, detector, checks, image)
    follower.image = image
    tracks = follower.tracks.update(image)
    if isinstance(follower.rules.identify, naming.TrackNamer):
        follower.rules.identify.observe(image, tracks)
    seen = follower.rules.step(at, image, tracks, image.shape[0] / image.shape[1])
    keep_live_box(store, session["session_id"], camera_id, at, follower, lock)
    if seen is None:
        return "no change"
    people = store.list_people()
    with lock or threading.Lock():
        session = store.get_session(session["session_id"])
        if session.get("ended_at"):
            return "skipped: the session ended"
        recent = store.list_events(session["session_id"])
        decision = tracker.on_seen(session, seen, recent, {p["person_id"]: p for p in people})
        media = {"frame_key": keep_frame(store, camera_id, at, image)} if decision["events"] else {}
        written = write(store, session, decision, {"camera_id": camera_id, "source": "detector",
                                                   "model": detector.name, **media})
        if decision.get("relabel"):
            relabel_episode(store, store.get_session(session["session_id"]), decision["relabel"], people)
    note = ("settling" if seen.get("settling") else "held" if seen.get("held") else
            "missing" if not seen["visible"] else "in place" if seen.get("in_place") else "moved")
    outcome = ", ".join(f"{e['action']}/{e['alert']}" for e in written) or "no event"
    state = f" state={decision['session']['state']}" if "state" in decision["session"] else ""
    return f"{note} -> {outcome}{state}"


class Analyser:
    """A background worker that analyses uploads one at a time, plus a once-a-second timer tick (whose heads-ups
    go through `gate` when one is set); for the local dev server. Only each camera's latest frame waits (the frame
    check needs only the newest picture), frames go before clips, and a clip that has waited CLIP_MAX_WAIT_S goes
    next, so constant motion can't starve clips."""

    def __init__(self, store: Store, tick_s: float = 1.0, check_frame: CheckFrame = vision.check_frame,
                 describe: Describe = video_model.describe,
                 log: Callable[[str], None] = lambda line: print(line, flush=True)):
        """Set up the queues over the store; start() begins work."""
        self.store, self.tick_s, self.log = store, tick_s, log
        self.check_frame, self.describe = check_frame, describe
        self.lock = threading.Lock()
        self.ready = threading.Condition()
        self.frames: dict[str, tuple[str, float]] = {}
        self.clips: deque[tuple[str, float]] = deque()
        self.results: deque[dict[str, Any]] = deque(maxlen=RECENT_RESULTS)
        self.busy: str | None = None
        self.busy_landed = 0.0
        self.stopping = threading.Event()
        self.threads: list[threading.Thread] = []
        self.gate: Gate | None = None

    def submit(self, key: str) -> None:
        """Queue an uploaded frame or clip (thumbnails and other keys are ignored); a frame replaces the same
        camera's waiting one."""
        match = KEY.match(key)
        if not match:
            return
        with self.ready:
            if match.group(1) == "clips":
                self.clips.append((key, time.time()))
            else:
                replaced = self.frames.get(match.group(2))
                if replaced:
                    self.record(replaced[0], "skipped: a newer frame from this camera arrived", replaced[1], 0.0)
                self.frames[match.group(2)] = (key, time.time())
            self.ready.notify()

    def next_item(self) -> tuple[str, float] | None:
        """The next upload to analyse, waiting for one; None once stopping."""
        with self.ready:
            while not (self.frames or self.clips or self.stopping.is_set()):
                self.ready.wait()
            if self.stopping.is_set():
                return None
            clip_due = self.clips and time.time() - self.clips[0][1] >= CLIP_MAX_WAIT_S
            if self.frames and not clip_due:
                camera = min(self.frames, key=lambda c: self.frames[c][1])
                item = self.frames.pop(camera)
            else:
                item = self.clips.popleft()
            self.busy, self.busy_landed = item
            return item

    def start(self) -> "Analyser":
        """Start the worker and the timer threads."""
        self.threads = [threading.Thread(target=self._work, daemon=True, name="analyser"),
                        threading.Thread(target=self._ticks, daemon=True, name="tracker-ticks")]
        for t in self.threads:
            t.start()
        return self

    def stop(self) -> None:
        """Stop after the current item."""
        with self.ready:
            self.stopping.set()
            self.ready.notify_all()

    def drain(self, timeout: float = 60) -> None:
        """Wait until every queued item has been analysed (for tests and scripts)."""
        until = time.time() + timeout
        while (self.frames or self.clips or self.busy) and time.time() < until:
            time.sleep(0.02)

    def lag_s(self) -> float:
        """Seconds the analysis is behind live: the age of the oldest upload still waiting or being analysed, or when
        idle, the total delay (queue plus analysis) of the latest item that was actually analysed."""
        landed = [t for _, t in self.frames.values()] + [t for _, t in self.clips] + ([self.busy_landed] if self.busy else [])
        if landed:
            return round(time.time() - min(landed), 2)
        done = [r for r in self.results if not r["outcome"].startswith("skipped")]
        return round(done[-1]["waited_s"] + done[-1]["took_s"], 2) if done else 0.0

    def status(self) -> dict[str, Any]:
        """Queue length, lag, the item in progress and the latest outcomes, newest first."""
        return {"queued": len(self.frames) + len(self.clips), "lag_s": self.lag_s(), "busy": self.busy,
                "recent": list(reversed(self.results))}

    def record(self, key: str, outcome: str, landed: float, took: float) -> None:
        """Keep an outcome for status(), and log it unless it was skipped."""
        waited = time.time() - landed - took
        self.results.append({"key": key, "outcome": outcome, "waited_s": round(waited, 2), "took_s": round(took, 2)})
        if not outcome.startswith("skipped"):
            self.log(f"Analysed {key} in {took:.1f} s (queued {waited:.1f} s): {outcome}")

    def _work(self) -> None:
        """Analyse queued items until stopped."""
        while (item := self.next_item()) is not None:
            key, landed = item
            started = time.time()
            try:
                outcome = handle_upload(self.store, key, self.lock, self.check_frame, self.describe)
            except ModelUnavailable as exc:
                outcome = f"model unavailable: {exc}"
            except Exception as exc:
                outcome = f"failed: {exc!r}"
                traceback.print_exc()
            self.record(key, outcome, landed, time.time() - started)
            self.busy = None
    def waiting_clips(self) -> list[str]:
        """The clips queued or being analysed."""
        with self.ready:
            return [key for key, _ in self.clips] + ([self.busy] if self.busy else [])

    def _ticks(self) -> None:
        """Run the tracker's timers every tick_s seconds until stopped."""
        while not self.stopping.wait(self.tick_s):
            try:
                for e in tick(self.store, self.lock, gate=self.gate, waiting=self.waiting_clips):
                    self.log(f"Tracker: {e['action']}/{e['alert']}: {e['summary']}")
            except Exception:
                traceback.print_exc()
