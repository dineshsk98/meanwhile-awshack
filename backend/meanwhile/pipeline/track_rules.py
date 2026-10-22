"""Rules that turn one camera's detector tracks into tracker readings for an object watch: held while the object
moves, moved or put back once it is at rest again, missing when neither the detector nor the vision model sees it.
The object starts locked at its step-away box; when the detector loses it, the vision model is asked where it is.
A reading comes out only when that state changes, so the tracker's events, de-duplication and timers work as they
do for frame checks.
"""

from __future__ import annotations

import math
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Callable

from meanwhile import config
from meanwhile.pipeline import tracker
from meanwhile.store import parse


@dataclass
class Track:
    """One tracked detection: its track id (-1 for a detection too weak to start a track), the prompt it matched,
    its box as [x0, y0, x1, y1] fractions of the frame, and the detector's confidence."""
    track_id: int
    label: str
    box: list[float]
    conf: float


Confirm = Callable[[Any, list[float]], "bool | Future[bool]"]
Identify = Callable[[Any, int, list[float]], str]
Locate = Callable[[Any], "list[float] | None | Future[list[float] | None]"]


def accept_all(_image: Any, _box: list[float]) -> bool:
    """A confirm check that accepts every candidate (until a model check is plugged in)."""
    return True


def nobody_known(_image: Any, _track_id: int, _box: list[float]) -> str:
    """An identify check that recognises nobody."""
    return tracker.UNKNOWN


# The thresholds.
MIN_CONF = 0.10
PERSON_CONF = 0.25
REST_S = 1.0
MOVE_FRAC = 0.3
STILL_FRAC = 0.15
FAR = (0.047, 0.75)
NEAR = (0.031, 0.5)
SAME_TRACK_RADIUS = 0.0625
MISS_GRACE_S = 0.5
CONFIRM_GAP_S = 1.0
CONFIRM_WAIT_S = 5.0
HOLDER_RADIUS = 0.25
WEAK_STEADY_S = 1.0
WEAK_STEADY_FRAMES = 5
EDGE = 0.02
TRACE_FRAMES = 600
WEAK_SPOT_ID = -1000
LOCATED_ID = -2
SEED_ID = -3
DETECTOR, VISION, STEP_AWAY = "detector", "vision", "step_away"


def overlap_of(obj: list[float], other: list[float]) -> float:
    """The share of the object box covered by the other box."""
    ix = max(0.0, min(obj[2], other[2]) - max(obj[0], other[0]))
    iy = max(0.0, min(obj[3], other[3]) - max(obj[1], other[1]))
    return ix * iy / max(1e-9, (obj[2] - obj[0]) * (obj[3] - obj[1]))


def area(box: list[float]) -> float:
    """A box's area as a share of the frame."""
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


@dataclass
class TargetRules:
    """The rules for one watched object on one camera. `home` is where it was at step-away: the object starts
    locked there, at rest. `confirm` decides whether a new track really is the object (at once, or later through a
    Future, so a model check can run off the frame loop); `identify` names a person track. With `weak_returns`, a
    steady weak detection can bring a missing object back once `confirm` accepts its spot. `locate` asks the
    vision model for the object's box in a frame (None if not found): it is asked when the detector has had no
    lock on the object for `locate_after_s`, at most once per `locate_gap_s` (`locate_missing_gap_s` while it is
    missing, 0 for never); its box moves the object and re-seeds it, and while it is on, the object goes missing
    only once an answer says it is gone (or none comes within `locate_wait_s`). Weak detections near the seed
    follow the object. A detector box cut by the frame's edge to less than `edge_part` of the object's known size
    is not a sighting, nor is any vision-model box cut by the edge. `vouch()` says a model still sees the object at `box` while the rules have it missing. `trace`
    marks the latest frames: # followed, r weak detection at a confirmed spot, w weak at its resting spot, s weak
    following the seed, v found by the vision model, + only other boxes, . nothing. Distances are in frame
    widths, so `aspect` (height / width) turns box fractions into the same units on both axes."""
    camera_id: str
    camera_label: str
    target: str
    home: list[float] | None = None
    confirm: Confirm = accept_all
    identify: Identify = nobody_known
    weak_returns: bool = False
    locate: Locate | None = None
    locate_after_s: float = field(default_factory=lambda: config.LOCATE_AFTER_S)
    locate_gap_s: float = field(default_factory=lambda: config.LOCATE_GAP_S)
    locate_missing_gap_s: float = field(default_factory=lambda: config.LOCATE_MISSING_GAP_S)
    locate_wait_s: float = field(default_factory=lambda: config.LOCATE_WAIT_S)
    edge_part: float = field(default_factory=lambda: config.EDGE_PART)
    track_id: int | None = None
    rejected: set[int] = field(default_factory=set)
    phase: str | None = None
    lost_from: str | None = None
    anchor: list[float] | None = None
    last_box: list[float] | None = None
    unseen_since: tuple[str, float] | None = None
    recent: list[tuple[float, tuple[float, float]]] = field(default_factory=list)
    names: dict[int, str] = field(default_factory=dict)
    holder: str = ""
    t0: Any = None
    last_confirm_t: float | None = None
    asking: tuple[int, Future, float] | None = None
    answers: dict[int, bool] = field(default_factory=dict)
    vouched: list[float] | None = None
    weak_seen: list[tuple[float, list[float]]] = field(default_factory=list)
    weak_asking: tuple[int, list[float]] | None = None
    weak_spot: list[float] | None = None
    refused: list[list[float]] = field(default_factory=list)
    seed: list[float] | None = None
    seeded: bool = False
    source: str = STEP_AWAY
    lock_t: float | None = None
    missed_t: float | None = None
    locating: tuple[Future, float, float] | None = None
    last_locate_t: float | None = None
    gone_t: float | None = None
    prev_vision: tuple[float, tuple[float, float]] | None = None
    confirm_calls: int = 0
    identify_calls: int = 0
    locate_calls: int = 0
    located: int = 0
    frames: int = 0
    sightings: int = 0
    trace: str = ""

    def point(self, box: list[float], aspect: float) -> tuple[float, float]:
        """The box centre in frame widths."""
        return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2 * aspect

    def diag(self, box: list[float], aspect: float) -> float:
        """The box diagonal in frame widths."""
        return math.hypot(box[2] - box[0], (box[3] - box[1]) * aspect)

    def dist(self, a: list[float], b: list[float], aspect: float) -> float:
        """Distance between two box centres in frame widths."""
        return math.dist(self.point(a, aspect), self.point(b, aspect))

    def reach(self, box: list[float], aspect: float) -> float:
        """How far from a box another box still counts as the same spot, in frame widths."""
        return max(SAME_TRACK_RADIUS, NEAR[1] * self.diag(box, aspect))

    def step(self, at: str, image: Any, tracks: list[Track], aspect: float) -> tracker.Seen | None:
        """Take one frame's tracks at time `at`; return a reading for the tracker if the object's state changed."""
        self.t0 = self.t0 or parse(at)
        t = (parse(at) - self.t0).total_seconds()
        if self.frames == 0:
            self.lock_at_home(t)
        if self.vouched is not None:
            self.resume()
        targets = [tr for tr in tracks if tr.label == self.target and tr.conf >= MIN_CONF and self.whole(tr.box)]
        weak = [tr for tr in targets if tr.track_id < 0]
        found = self.pick(image, [tr for tr in targets if tr.track_id >= 0], t, aspect)
        if found is None and self.weak_returns:
            found = self.weak_return(image, weak, t, aspect)
        people = [tr for tr in tracks if tr.label == "person" and tr.conf >= PERSON_CONF and tr.track_id >= 0]
        still = found is None and self.still_there(weak, aspect)
        if found is None and not still:
            found = self.follow_seed(weak, aspect)
        located = self.answered(t)
        if found is not None or still:
            self.lock_t, self.source, self.prev_vision = t, DETECTOR, None
        elif located is not None:
            found, self.source, self.seed = Track(LOCATED_ID, self.target, located, 0.0), VISION, located
        self.ask_locate(image, t)
        self.frames, self.sightings = self.frames + 1, self.sightings + (found is not None or still)
        self.trace = (self.trace + self.mark(found, still, targets))[-TRACE_FRAMES:]
        if still:
            self.unseen_since = None
            return None
        reading = self.unseen(at, t, image, people, aspect) if found is None else \
            self.seen(at, t, image, found.box, people, aspect, found.track_id)
        if found is not None and found.track_id >= 0:
            self.seed = None
        if self.phase == "rest" or (found is not None and found.track_id >= 0):
            self.weak_spot = None
        return reading

    def mark(self, found: Track | None, still: bool, targets: list[Track]) -> str:
        """The trace mark for this frame (see the class docstring)."""
        if found is not None:
            return {LOCATED_ID: "v", SEED_ID: "s"}.get(found.track_id, "#" if found.track_id >= 0 else "r")
        return "w" if still else "+" if targets else "."

    def lock_at_home(self, t: float) -> None:
        """Lock onto the step-away box before the first frame: the object is at rest there and counts as seen."""
        if self.home is None or self.phase is not None:
            return
        self.phase, self.anchor, self.last_box, self.seed = "rest", self.home, self.home, self.home
        self.seeded, self.lock_t, self.source = True, t, STEP_AWAY

    @staticmethod
    def cut(box: list[float]) -> bool:
        """Whether a box touches the frame's edge (within EDGE)."""
        return box[0] <= EDGE or box[1] <= EDGE or box[2] >= 1 - EDGE or box[3] >= 1 - EDGE

    def whole(self, box: list[float]) -> bool:
        """Whether a box counts as a sighting: not cut by the frame's edge, or still at least `edge_part` of the
        object's known size (its resting or step-away box)."""
        known = self.anchor or self.home
        return not self.cut(box) or known is None or area(box) >= self.edge_part * area(known)

    def follow_seed(self, weak: list[Track], aspect: float) -> Track | None:
        """A weak detection near the seed (where the vision model or the step-away box last put the object) while
        it is not missing: the nearest is the object, and the seed moves with it."""
        if self.seed is None or self.phase in (None, "missing"):
            return None
        reach = self.reach(self.seed, aspect)
        near = [tr for tr in weak if self.dist(tr.box, self.seed, aspect) <= reach]
        if not near:
            return None
        best = min(near, key=lambda tr: self.dist(tr.box, self.seed, aspect))
        self.seed = best.box
        return Track(SEED_ID, self.target, best.box, best.conf)

    def ask_locate(self, image: Any, t: float) -> None:
        """Ask the vision model where the object is when the detector has had no lock on it for locate_after_s, one
        question at a time, at most once per locate_gap_s (locate_missing_gap_s while it is missing)."""
        if self.locate is None or self.locating is not None or self.phase is None:
            return
        gap = self.locate_missing_gap_s if self.phase == "missing" else self.locate_gap_s
        if gap <= 0 or (self.lock_t is not None and t - self.lock_t < self.locate_after_s):
            return
        if self.last_locate_t is not None and t - self.last_locate_t < gap:
            return
        self.last_locate_t, self.locate_calls = t, self.locate_calls + 1
        answer = self.locate(image)
        if not isinstance(answer, Future):
            done: Future = Future()
            done.set_result(answer)
            answer = done
        self.locating = (answer, t, time.monotonic())

    def answered(self, t: float) -> list[float] | None:
        """The box from a finished locate question, if it found the object. A box cut by the frame's edge does not
        count: the vision model is asked only once the detector has lost the object, and then an edge is the object
        being carried off, not the object. A question that found nothing, failed, or has had no answer for
        locate_wait_s marks the object as gone at the time it was asked."""
        if self.locating is None:
            return None
        answer, asked_t, started = self.locating
        if not answer.done():
            if time.monotonic() - started >= self.locate_wait_s:
                self.locating, self.gone_t = None, asked_t
            return None
        self.locating = None
        box = answer.result() if answer.exception() is None else None
        if box is None or self.cut(box):
            self.gone_t = asked_t
            return None
        self.located += 1
        return box

    def gone_checked(self) -> bool:
        """Whether the vision model, asked after the object went missing, did not find it."""
        return (self.phase == "missing" and self.gone_t is not None and self.missed_t is not None
                and self.gone_t >= self.missed_t)

    def still_there(self, weak: list[Track], aspect: float) -> bool:
        """Whether a detection too weak to start a track sits where the object is resting: it keeps the object
        in view, but never moves it or takes over as the object."""
        if self.phase != "rest" or self.anchor is None:
            return False
        reach = self.reach(self.anchor, aspect)
        return any(self.dist(tr.box, self.anchor, aspect) <= reach for tr in weak)

    def weak_return(self, image: Any, weak: list[Track], t: float, aspect: float) -> Track | None:
        """A sighting from detections too weak to start a track. While the object is missing, one that has stayed
        at one spot (in WEAK_STEADY_FRAMES frames within WEAK_STEADY_S, over at least half of it) away from the
        frame's edges is asked about once with the confirm check; a refused spot is not asked about again. Once a
        spot is accepted, a weak detection near it is the object resting at that spot, until it is at rest or
        followed by a track."""
        if self.weak_spot is not None:
            spot = self.weak_spot
            near = [tr for tr in weak if self.dist(tr.box, spot, aspect) <= self.reach(spot, aspect)]
            return Track(-1, self.target, spot, max(tr.conf for tr in near)) if near else None
        if self.phase != "missing":
            self.weak_seen, self.weak_asking = [], None
            return None
        self.weak_seen = [(s, b) for s, b in self.weak_seen if t - s <= WEAK_STEADY_S] + [(t, tr.box) for tr in weak]
        if self.weak_asking is None:
            steady = self.steady_weak(weak, t, aspect)
            if steady is None:
                return None
            self.weak_asking = (WEAK_SPOT_ID - len(self.refused), steady.box)
        spot_id, spot = self.weak_asking
        answer = self.confirmed(image, Track(spot_id, self.target, spot, 0.0), t)
        if answer is None:
            return None
        self.weak_asking = None
        if not answer:
            self.refused.append(spot)
            return None
        self.weak_spot = spot
        return Track(-1, self.target, spot, 0.0)

    def steady_weak(self, weak: list[Track], t: float, aspect: float) -> Track | None:
        """The strongest weak detection this frame that has stayed at its spot long enough (see weak_return), is
        clear of the frame's edges (a sliver there is not the object back) and is not at a refused spot."""
        for tr in sorted(weak, key=lambda tr: -tr.conf):
            reach = self.reach(tr.box, aspect)
            if not (EDGE < tr.box[0] and EDGE < tr.box[1] and tr.box[2] < 1 - EDGE and tr.box[3] < 1 - EDGE):
                continue
            if any(self.dist(tr.box, spot, aspect) <= reach for spot in self.refused):
                continue
            times = [s for s, b in self.weak_seen if self.dist(b, tr.box, aspect) <= reach]
            if len(times) >= WEAK_STEADY_FRAMES and t - min(times) >= WEAK_STEADY_S / 2:
                return tr
        return None

    def vouch(self, box: list[float]) -> None:
        """A model still sees the object at `box` while the rules have it missing; applied at the next frame."""
        self.vouched = box

    def resume(self) -> None:
        """Apply a vouch: if the object is still missing, it is back where it was (held if it went missing from a
        hand, else at rest at its spot), as if it had never been lost."""
        box, self.vouched = self.vouched, None
        if box is None or self.phase != "missing":
            return
        self.phase = "held" if self.lost_from == "held" else "rest"
        self.anchor, self.last_box = self.anchor or box, self.last_box or box
        self.unseen_since, self.recent, self.gone_t = None, [], None

    def last_spot(self) -> list[float] | None:
        """Where the object was last known to be: its resting spot if it went missing from rest, else its last box."""
        if self.lost_from == "rest" and self.anchor:
            return self.anchor
        return self.last_box or self.anchor or self.home

    def live_box(self) -> dict[str, Any]:
        """Where to draw the object now: its latest box (else where it rested or was at step-away), whether it is
        moving in a hand, whether it is out of sight, and what last placed it (detector, vision or step_away)."""
        return {"box": self.last_box or self.anchor or self.home, "held": self.phase == "held",
                "missing": self.phase == "missing", "source": self.source}

    def pick(self, image: Any, candidates: list[Track], t: float, aspect: float) -> Track | None:
        """The object's track this frame: the one already followed, else a new track at the spot where the object
        last rested, at a confirmed weak spot, at the seed, or at the step-away box before any track was followed,
        else a new track the confirm check accepts (one check at a time, at most one per CONFIRM_GAP_S). Rejected
        tracks are ignored from then on."""
        candidates = sorted(candidates, key=lambda tr: -tr.conf)
        for tr in candidates:
            if tr.track_id == self.track_id:
                return tr
        for tr in candidates:
            if tr.track_id in self.rejected:
                continue
            at_rest_before = self.phase in (None, "rest") or (self.phase == "missing" and self.lost_from == "rest")
            spots = [self.anchor if at_rest_before else None, self.weak_spot, self.seed,
                     self.home if self.track_id is None else None]
            if any(spot and self.dist(tr.box, spot, aspect) <= self.reach(spot, aspect) for spot in spots):
                self.track_id = tr.track_id
                return tr
            answer = self.confirmed(image, tr, t)
            if answer is None:
                return None
            if answer:
                self.track_id = tr.track_id
                return tr
            self.rejected.add(tr.track_id)
            return None
        return None

    def confirmed(self, image: Any, tr: Track, t: float) -> bool | None:
        """Whether a new track is the object: the confirm check's answer once it has one, None while a check is
        running (one at a time) or within CONFIRM_GAP_S of the last one. A check with no answer after
        CONFIRM_WAIT_S, or one that failed, counts as a no; an answer about another track is kept for it."""
        if self.asking and (self.asking[1].done() or time.monotonic() - self.asking[2] >= CONFIRM_WAIT_S):
            track_id, answer, _ = self.asking
            self.asking = None
            self.answers[track_id] = answer.done() and answer.exception() is None and bool(answer.result())
        if tr.track_id in self.answers:
            return self.answers.pop(tr.track_id)
        if self.asking or (self.last_confirm_t is not None and t - self.last_confirm_t < CONFIRM_GAP_S):
            return None
        self.last_confirm_t = t
        self.confirm_calls += 1
        answer = self.confirm(image, tr.box)
        if not isinstance(answer, Future):
            return bool(answer)
        self.asking = (tr.track_id, answer, time.monotonic())
        return self.confirmed(image, tr, t) if answer.done() else None

    def holder_near(self, image: Any, box: list[float], people: list[Track], aspect: float) -> str:
        """The person whose track covers the object most, else the nearest within HOLDER_RADIUS, named once per
        track; '' if nobody is near."""
        best = max(people, key=lambda p: (overlap_of(box, p.box), -self.dist(box, p.box, aspect)), default=None)
        if best is None or (overlap_of(box, best.box) == 0 and self.dist(box, best.box, aspect) > HOLDER_RADIUS):
            return ""
        if best.track_id not in self.names:
            self.identify_calls += 1
            self.names[best.track_id] = self.identify(image, best.track_id, best.box) or tracker.UNKNOWN
        return self.names[best.track_id]

    def reading(self, at: str, box: list[float] | None, **fields: Any) -> tracker.Seen:
        """A frame reading for the tracker."""
        return {"kind": "frame", "camera_id": self.camera_id, "camera_label": self.camera_label, "happened_at": at,
                "visible": box is not None, "held": False, "in_place": False, "box": box, "person": "", **fields}

    def unseen(self, at: str, t: float, image: Any, people: list[Track], aspect: float) -> tracker.Seen | None:
        """No sighting this frame: after MISS_GRACE_S without one (and, with `locate`, once the vision model asked
        since then has not found it either), the object is missing (once), from the first frame it was not seen."""
        if self.phase in (None, "missing"):
            return None
        if self.unseen_since is None:
            self.unseen_since = (at, t)
        if t - self.unseen_since[1] < MISS_GRACE_S:
            return None
        if self.locate is not None and (self.gone_t is None or self.gone_t < self.unseen_since[1]):
            return None
        self.missed_t = self.unseen_since[1]
        self.lost_from, self.phase, self.recent, self.prev_vision = self.phase, "missing", [], None
        near = self.holder_near(image, self.last_box, people, aspect) if self.last_box else ""
        self.holder = near or self.holder
        return self.reading(self.unseen_since[0], None, person=self.holder)

    def motion(self, t: float, box: list[float], aspect: float, kind: int) -> tuple[bool, bool]:
        """Whether the object is moving, and whether it is at rest, from this sighting. Detector sightings look at
        the last REST_S of positions; the vision model's sparse boxes compare with its previous box (same spot is
        at rest, farther is moving)."""
        c, size = self.point(box, aspect), self.diag(box, aspect)
        if kind == LOCATED_ID:
            prev, self.prev_vision = self.prev_vision, (t, c)
            if prev is None:
                return False, False
            apart = math.dist(prev[1], c) > self.reach(box, aspect)
            return apart, not apart
        self.recent = [(s, p) for s, p in self.recent if t - s < REST_S] + [(t, c)]
        spread = max(math.dist(p, c) for _, p in self.recent)
        moving = len(self.recent) >= 2 and spread > MOVE_FRAC * size
        at_rest = (not moving and len(self.recent) >= 3 and t - self.recent[0][0] >= REST_S / 2
                   and spread <= STILL_FRAC * size)
        return moving, at_rest

    def seen(self, at: str, t: float, image: Any, box: list[float], people: list[Track],
             aspect: float, kind: int = 0) -> tracker.Seen | None:
        """A sighting (`kind` is the track id, or LOCATED_ID / SEED_ID): back after missing is 'settling' until it
        is moving or at rest; moving is held; at rest after being handled is put back (near where it rested or
        where it was at step-away) or moved (far from where it rested). The first detector rest after the
        step-away lock sets the resting spot quietly; at rest far from its spot without having been seen handled
        (found again by the vision model or the seed) counts as handled."""
        self.unseen_since, self.last_box = None, box
        size = self.diag(box, aspect)
        moving, at_rest = self.motion(t, box, aspect, kind)
        if self.phase == "missing":
            self.phase = "settling"
            return self.reading(at, box, settling=True)
        if moving and self.phase != "held":
            self.phase, self.seeded = "held", False
            self.holder = self.holder_near(image, box, people, aspect) or self.holder
            return self.reading(at, box, held=True, person=self.holder)
        if at_rest and self.phase == "rest" and self.seeded and kind >= 0:
            self.anchor, self.seeded = box, False
            return None
        unseen_move = (kind in (LOCATED_ID, SEED_ID) and self.anchor is not None
                       and self.dist(box, self.anchor, aspect) > max(FAR[0], FAR[1] * size))
        if not at_rest or (self.phase == "rest" and not unseen_move):
            return None
        first, self.phase = self.phase is None, "rest"
        anchor = self.anchor or self.home
        if first or anchor is None:
            self.anchor = box
            return None if first else self.reading(at, box, in_place=True)
        at_home = self.home is not None and self.dist(box, self.home, aspect) < max(NEAR[0], NEAR[1] * size)
        far = self.dist(box, anchor, aspect) > max(FAR[0], FAR[1] * size)
        self.anchor = box if at_home or far else anchor
        return self.reading(at, box, in_place=at_home or not far, returned=at_home and far)
