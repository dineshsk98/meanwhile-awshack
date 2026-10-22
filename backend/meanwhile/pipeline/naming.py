"""Names each person track once with face ID: a person track is searched when it appears (off the frame loop),
retried a few times on its best crop while no face is visible, and otherwise given the episode's majority name
across cameras. A face that is visible but matches nobody becomes a new unnamed person (when keeping them is on),
or else stays unknown. Clip events name only people face ID recognised in the session (clothes may carry such a
person to frames without their face); anyone else is an unnamed person, numbered once per look in the session.
"""

from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from typing import Any, Callable

import cv2
import numpy as np

from meanwhile import config
from meanwhile.contract import Person
from meanwhile.pipeline import track_rules, tracker, unnamed
from meanwhile.pipeline.faces import Faces, FaceError, jpeg, upper_body
from meanwhile.store import Store

_pool: Executor | None = None


def pool() -> Executor:
    """The worker that runs face searches one at a time, off the frame loop."""
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="faces")
    return _pool


def episode_name(store: Store, session_id: str) -> str:
    """The person most readings named in the session's current episode, from any camera, ignoring 'unknown';
    'unknown' when nobody is named."""
    votes = {p: n for p, n in ((store.get_session(session_id) or {}).get("votes") or {}).items()
             if p and p != tracker.UNKNOWN}
    return max(votes, key=lambda p: votes[p]) if votes else tracker.UNKNOWN


class TrackNamer:
    """The identify hook for one camera's tracks in one session. Call observe() with every frame's tracks;
    calling the namer itself (as track_rules' identify) returns the track's name so far ('' while unknown).
    Once a name is settled it is written into `rules.names`, where the rules keep each track's holder name."""

    def __init__(self, faces: Faces, store: Store, session_id: str, retries: int | None = None,
                 gap_s: float | None = None, executor: Executor | None = None,
                 clock: Callable[[], float] = time.monotonic, keep_unnamed: bool | None = None):
        """Name tracks with `faces`, falling back to the session's episode vote in `store`; with `keep_unnamed`
        (default MEANWHILE_FACE_KEEP_UNNAMED), a face that matches nobody is kept as a new unnamed person."""
        self.faces, self.store, self.session_id = faces, store, session_id
        self.retries = config.FACE_RETRIES if retries is None else retries
        self.gap_s = config.FACE_RETRY_GAP_S if gap_s is None else gap_s
        self.executor, self.clock = executor, clock
        self.keep_unnamed = config.FACE_KEEP_UNNAMED if keep_unnamed is None else keep_unnamed
        self.face_views: dict[int, tuple[Any, list[float]]] = {}
        self.rules: track_rules.TargetRules | None = None
        self.names: dict[int, str] = {}
        self.by_vote: set[int] = set()
        self.saw_face: set[int] = set()
        self.tries: dict[int, int] = {}
        self.next_at: dict[int, float] = {}
        self.best: dict[int, tuple[float, Any, list[float]]] = {}
        self.pending: set[int] = set()
        self.searches = 0
        self.lock = threading.Lock()

    def __call__(self, image: Any, track_id: int, box: list[float]) -> str:
        """The track's name: a registered person id, 'unknown', or '' while it is still being named."""
        self.offer(image, track_id, box)
        return self.names.get(track_id, "")

    def observe(self, image: Any, tracks: list[track_rules.Track]) -> None:
        """Take one frame's tracks: start or retry naming each person track that has no name yet, and refresh
        the vote-based names."""
        for tr in tracks:
            if tr.label == "person" and tr.track_id >= 0 and tr.conf >= track_rules.PERSON_CONF:
                self.offer(image, tr.track_id, tr.box)
        if self.by_vote:
            name = episode_name(self.store, self.session_id)
            for track_id in list(self.by_vote):
                if self.names.get(track_id) != name:
                    self.settle(track_id, name, by_vote=True)

    def offer(self, image: Any, track_id: int, box: list[float]) -> None:
        """Keep the largest view of a track since its last try, and send a search when one is due."""
        with self.lock:
            if track_id in self.names or track_id in self.pending:
                if track_id in self.pending:
                    self.keep_best(image, track_id, box)
                return
            self.keep_best(image, track_id, box)
            if self.clock() < self.next_at.get(track_id, 0.0):
                return
            _, best_image, best_box = self.best.pop(track_id)
            self.pending.add(track_id)
            self.searches += 1
        future = (self.executor or pool()).submit(self.search, track_id, best_image, best_box)
        future.add_done_callback(lambda f: self.answered(track_id, f))

    def search(self, track_id: int, image: Any, box: list[float]) -> tuple[str | None, bool]:
        """One face search on a person box: the matched person id or None, and whether a face was found (that
        view is kept in case the track becomes a new unnamed person)."""
        person_id = self.faces.identify(image, box)
        saw_face = bool(self.faces.last.get("face"))
        if saw_face:
            self.face_views[track_id] = (image, box)
        if person_id:
            unnamed.seen(self.store, person_id)
        return person_id, saw_face

    def add_unnamed(self, track_id: int) -> str:
        """Make the track's last face view into a new unnamed person, after one more search in case another
        camera's track just did; returns the person id, or 'unknown' if the face couldn't be kept."""
        image, box = self.face_views[track_id]
        crop = jpeg(upper_body(image, box, self.faces.margin, self.faces.upper))
        person_id = self.faces.search(crop)
        if person_id:
            return person_id
        person = unnamed.create(self.store, self.faces, crop, self.rules.camera_id if self.rules else "")
        return person["person_id"] if person else tracker.UNKNOWN

    def keep_best(self, image: Any, track_id: int, box: list[float]) -> None:
        """Remember this view of the track if its box is the largest since the last try."""
        area = (box[2] - box[0]) * (box[3] - box[1])
        if track_id not in self.best or area > self.best[track_id][0]:
            self.best[track_id] = (area, image, box)

    def answered(self, track_id: int, future: Future) -> None:
        """Settle a track from a search, or schedule its next try; out of tries, a track whose face was never
        seen takes the episode's name and one whose face matched nobody becomes a new unnamed person (or stays
        unknown when they aren't kept)."""
        try:
            person_id, saw_face = future.result()
        except FaceError:
            traceback.print_exc()
            person_id, saw_face = None, False
        with self.lock:
            self.pending.discard(track_id)
            if saw_face:
                self.saw_face.add(track_id)
            self.tries[track_id] = self.tries.get(track_id, 0) + 1
            out_of_tries = self.tries[track_id] >= self.retries
            self.next_at[track_id] = self.clock() + self.gap_s
        if person_id:
            self.settle(track_id, person_id)
        elif out_of_tries and track_id in self.saw_face and self.keep_unnamed:
            future = (self.executor or pool()).submit(self.add_unnamed, track_id)
            future.add_done_callback(lambda f: self.added(track_id, f))
        elif out_of_tries and track_id in self.saw_face:
            self.settle(track_id, tracker.UNKNOWN)
        elif out_of_tries:
            self.settle(track_id, episode_name(self.store, self.session_id), by_vote=True)

    def added(self, track_id: int, future: Future) -> None:
        """Settle a track with the unnamed person made for it (or 'unknown' if that failed)."""
        try:
            person_id = future.result()
        except FaceError:
            traceback.print_exc()
            person_id = tracker.UNKNOWN
        self.settle(track_id, person_id)

    def settle(self, track_id: int, name: str, by_vote: bool = False) -> None:
        """Give a track its name and hand it to the rules (a vote-based name is refreshed as the vote changes); a
        name from a face joins the session's known people."""
        with self.lock:
            self.names[track_id] = name
            (self.by_vote.add if by_vote else self.by_vote.discard)(track_id)
        if not by_vote and name not in (tracker.UNKNOWN, ""):
            confirm(self.store, self.session_id, name)
        if self.rules is not None:
            self.rules.names[track_id] = name


KNOWN = "known_people"
_known_lock = threading.Lock()


def known_people(store: Store, session_id: str) -> dict[str, str]:
    """The people a session may name, by person id, with what they were seen wearing ('' when not noted): everyone
    face ID recognised during the session, and the unnamed people its clips saw without a face."""
    return dict((store.get_session(session_id) or {}).get(KNOWN) or {})


def confirm(store: Store, session_id: str, person_id: str, wears: str = "") -> None:
    """Add a person to the session's known people (face ID recognised them, or they are a new unnamed person),
    noting what they wear when given; an earlier note is kept when none is given."""
    wears = " ".join(str(wears or "").split())
    with _known_lock:
        known = known_people(store, session_id)
        if person_id in known and (not wears or known[person_id] == wears):
            return
        known[person_id] = wears or known.get(person_id, "")
        store.set_session_field(session_id, KNOWN, known)


def session_people(store: Store, session_id: str, people: list[Person]) -> list[Person]:
    """The registered people a session's clip prompt lists: only its known people, each described by what they
    were seen wearing in this session, else by their registered note."""
    known = known_people(store, session_id)
    return [{**p, "note": known[p["person_id"]] or p.get("note") or ""} for p in people if p["person_id"] in known]


def frame_faces(faces: Faces, store: Store, image: bytes, camera_id: str, margin: float | None = None,
                keep_unnamed: bool | None = None) -> list[tuple[str | None, list[float]]]:
    """Every face in one JPEG frame, largest first, as (person id or None, face box): each face is searched once on
    a crop widened by `margin` (default MEANWHILE_FACE_CLIP_MARGIN) of its size; a face that matches nobody becomes a
    new unnamed person when they are kept, else its id is None."""
    margin = config.FACE_CLIP_MARGIN if margin is None else margin
    keep = config.FACE_KEEP_UNNAMED if keep_unnamed is None else keep_unnamed
    frame = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    found: list[tuple[str | None, list[float]]] = []
    for box in faces.find(image):
        crop = jpeg(upper_body(frame, box, margin, 1.0))
        person_id = faces.search(crop)
        if person_id:
            unnamed.seen(store, person_id)
        elif faces.last.get("face") and keep:
            person = unnamed.create(store, faces, crop, camera_id)
            person_id = person["person_id"] if person else None
        found.append((person_id, box))
    return found


def frame_people(faces: Faces, store: Store, image: bytes, camera_id: str, margin: float | None = None,
                 keep_unnamed: bool | None = None) -> list[str]:
    """The people whose faces show in one JPEG frame, largest face first (see frame_faces); faces that can't be
    named are left out."""
    found: list[str] = []
    for person_id, _ in frame_faces(faces, store, image, camera_id, margin, keep_unnamed):
        if person_id and person_id not in found:
            found.append(person_id)
    return found


def face_choice(said: str, found: list[str], other: str = "") -> str:
    """Who a clip event names once faces are known: the model's pick `said` when nobody is involved, no face was
    named or that person's face is there; otherwise the largest named face other than `other`."""
    if not said or not found or said in found:
        return said
    rest = [p for p in found if p != other]
    return rest[0] if rest else said


def name_clip_events(faces: Faces, store: Store, session_id: str, frames: list[bytes], times: list[float],
                     events: list[tracker.SeenEvent], offsets: list[float], camera_id: str,
                     tries: int | None = None) -> list[float]:
    """Name the actor and recipient of each clip event that involves a person from the faces in the clip's frames:
    the frames nearest the event's offset (seconds into the clip) are tried, up to `tries` (default
    MEANWHILE_FACE_CLIP_FRAMES), until one shows a named face, and each frame is searched at most once per clip.
    Everyone named by face joins the session's known people, with what the model says they wear. Returns the
    seconds face ID took for each event that was named."""
    tries = config.FACE_CLIP_FRAMES if tries is None else tries
    cache: dict[int, list[str]] = {}
    took: list[float] = []
    for event, offset in zip(events, offsets):
        if not event.get("actor") or not frames:
            continue
        start = time.perf_counter()
        found: list[str] = []
        try:
            for i in sorted(range(len(frames)), key=lambda i: abs(times[i] - offset))[:tries]:
                if i not in cache:
                    cache[i] = frame_people(faces, store, frames[i], camera_id)
                if cache[i]:
                    found = cache[i]
                    break
        except FaceError:
            traceback.print_exc()
        event["actor"] = face_choice(event["actor"], found)
        if event.get("recipient"):
            event["recipient"] = face_choice(event["recipient"], found, other=event["actor"])
        for role in ("actor", "recipient"):
            if event.get(role) in found:
                confirm(store, session_id, event[role], event.get(f"{role}_wears", ""))
        took.append(round(time.perf_counter() - start, 3))
    return took


def only_known(store: Store, session_id: str, events: list[tracker.SeenEvent], camera_id: str,
               holder: str | None = None) -> None:
    """Keep a clip event's actor and recipient only when they are among the session's known people. An actor
    nobody recognised is the object's `holder` when one is given (the person the session saw holding it, by name or
    unnamed label); anyone else is an unnamed person: the session's unnamed person seen wearing the same, or a new
    one (one per look per clip)."""
    known = known_people(store, session_id)
    looks = {" ".join(w.lower().split()): p for p, w in known.items() if p.startswith(unnamed.PREFIX)}
    for event in events:
        for role in ("actor", "recipient"):
            person_id = event.get(role) or ""
            if not person_id or person_id in known:
                continue
            if role == "actor" and holder and holder != tracker.UNKNOWN:
                event[role] = holder
                continue
            wears = " ".join(str(event.get(f"{role}_wears") or "").split())
            look = wears.lower()
            if look not in looks:
                looks[look] = unnamed.create_unseen(store, wears, camera_id)["person_id"]
                confirm(store, session_id, looks[look], wears)
                known[looks[look]] = wears
            event[role] = looks[look]
