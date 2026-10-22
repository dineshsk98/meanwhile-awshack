"""Vision-model checks for the detector path, asked only when the object's state changes and run off the frame
loop: before a heads-up, whether the object still sits at its spot; for a new track away from it, whether it
really is the object; and when the detector has lost it, where it is in the frame. Each check is counted per
session.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

import cv2
import numpy as np

from meanwhile import config
from meanwhile.contract import Session
from meanwhile.pipeline import track_rules, vision

SEND, HOLD, THERE = "send", "hold", "there"
KEPT_TIMES = 200

# The overlap rule and crop margin.
SPOT_IOU = 0.3
CROP_MARGIN = 1.0


def iou(a: list[float], b: list[float]) -> float:
    """Intersection over union of two [x0, y0, x1, y1] boxes."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / union if union > 0 else 0.0


def jpeg_of(image: np.ndarray, area: list[float] | None = None) -> bytes:
    """A BGR frame, or an area of it as fractions, as a JPEG at most vision.CHECK_WIDTH wide."""
    whole = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
    return vision.crop(whole, area or [0.0, 0.0, 1.0, 1.0]) or whole


def at_spot(target: str, label: str, image: np.ndarray, spot: list[float] | None, model: str) -> list[float] | None:
    """The object's box if the vision model finds it in the whole frame at `spot` (overlapping it by at least
    SPOT_IOU; anywhere when there is no spot), else None."""
    box = vision.find_in_frame(target, label, jpeg_of(image), model)
    return box if box and (spot is None or iou(box, spot) >= SPOT_IOU) else None


def is_object(target: str, label: str, image: np.ndarray, box: list[float], model: str) -> bool:
    """Whether the vision model sees the object in a crop around a detector box (the box grown by CROP_MARGIN of
    its size on each side)."""
    return vision.find_in_frame(target, label, jpeg_of(image, vision.grown(box, CROP_MARGIN)), model) is not None


class Checks:
    """Runs checks one at a time in a worker thread (or at once with wait=True, for replays), bounds how long a
    heads-up waits for its check, and counts the checks per session."""

    def __init__(self, model: str | None = None, timeout_s: float | None = None, enabled: bool | None = None,
                 wait: bool = False, locating: bool | None = None):
        """Settings default to config: the vision model, the heads-up wait, whether checks are on, and whether
        the vision model locates the object when the detector loses it."""
        self.model = model or config.VISION_MODEL
        self.timeout_s = config.CHECK_TIMEOUT_S if timeout_s is None else timeout_s
        self.enabled = config.CHECKS if enabled is None else enabled
        self.locating = config.LOCATE if locating is None else locating
        self.wait = wait
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="checks")
        self.lock = threading.Lock()
        self.counts: dict[str, Counter] = {}
        self.pending: dict[tuple[str, str], tuple[Future, float]] = {}
        self.ms: list[float] = []

    def count(self, session_id: str, what: str) -> None:
        """Add one to a session's count."""
        with self.lock:
            self.counts.setdefault(session_id, Counter())[what] += 1

    def run(self, session_id: str, kind: str, check: Callable[..., Any], *args: Any) -> Future:
        """Start one model check (counted as `kind`); returns its Future, already answered with wait=True."""
        self.count(session_id, kind)

        def timed() -> Any:
            """Run the check and keep its time."""
            start = time.perf_counter()
            try:
                return check(*args)
            finally:
                self.ms = self.ms[-(KEPT_TIMES - 1):] + [(time.perf_counter() - start) * 1000]

        if not self.wait:
            return self.pool.submit(timed)
        done: Future = Future()
        try:
            done.set_result(timed())
        except Exception as exc:
            done.set_exception(exc)
        return done

    def confirm_for(self, session_id: str, target: str, label: str) -> track_rules.Confirm:
        """The confirm hook for one session's rules: a crop check per new track (accept-all when checks are off)."""
        if not self.enabled:
            return track_rules.accept_all

        def confirm(image: np.ndarray, box: list[float]) -> Future:
            """Start the crop check for one new track."""
            answer = self.run(session_id, "confirm", is_object, target, label, image, box, self.model)
            answer.add_done_callback(lambda f: self.count(
                session_id, "confirm_yes" if f.exception() is None and f.result() else "confirm_no"))
            return answer
        return confirm

    def locate_for(self, session_id: str, target: str, label: str) -> track_rules.Locate | None:
        """The locate hook for one session's rules: the vision model's box for the object in a whole frame (None
        when checks or locating are off)."""
        if not (self.enabled and self.locating):
            return None

        def locate(image: np.ndarray) -> Future:
            """Start one locate question on a frame."""
            answer = self.run(session_id, "locate", vision.find_in_frame, target, label, jpeg_of(image), self.model)
            answer.add_done_callback(lambda f: self.count(
                session_id, "locate_found" if f.exception() is None and f.result() else "locate_none"))
            return answer
        return locate

    def heads_up(self, session: Session, follower: Any) -> str:
        """What to do with a heads-up the timers would write now: HOLD while its check runs; THERE if the vision
        model still sees the object at its spot in the newest frame (the follower's rules are told); SEND if not,
        if the check fails or has no answer within timeout_s, or when there is nothing to check."""
        session_id = session["session_id"]
        if (not self.enabled or follower is None or follower.session_id != session_id
                or getattr(follower, "image", None) is None):
            return SEND
        if follower.rules.gone_checked():
            self.count(session_id, "heads_up_located_gone")
            return SEND
        key = (session_id, session.get("missing_since") or "")
        if key not in self.pending:
            rules = follower.rules
            self.pending = {k: v for k, v in self.pending.items() if k[0] == session_id}
            self.pending[key] = (self.run(session_id, "heads_up", at_spot, rules.target, rules.camera_label,
                                          follower.image, rules.last_spot(), self.model), time.monotonic())
        answer, started = self.pending[key]
        if not answer.done():
            if time.monotonic() - started < self.timeout_s:
                return HOLD
            del self.pending[key]
            self.count(session_id, "heads_up_timeout")
            return SEND
        del self.pending[key]
        box = answer.result() if answer.exception() is None else None
        if box is None:
            self.count(session_id, "heads_up_sent")
            return SEND
        follower.rules.vouch(box)
        self.count(session_id, "heads_up_dropped")
        return THERE

    def status(self, session_id: str | None = None) -> dict[str, Any]:
        """Whether checks are on, the model, the wait, the mean check time, and one session's counts."""
        ms = self.ms[-50:]
        return {"enabled": self.enabled, "model": self.model, "timeout_s": self.timeout_s,
                "ms": round(sum(ms) / len(ms)) if ms else None,
                "counts": dict(self.counts.get(session_id or "", {}))}
