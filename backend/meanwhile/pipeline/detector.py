"""The object detector for live frames: one open-vocabulary YOLOE model prompted with the watched object's phrase
and "person" (the phrase can also carry how the object looked at step-away, a visual prompt), and one ByteTrack per
camera so each feed keeps its own steady track ids. At step-away it also grounds the object on the cameras' latest
frames. ultralytics and torch load on first use, so the rest of the core runs without them.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from meanwhile import config
from meanwhile.contract import Camera
from meanwhile.pipeline.track_rules import Track

PERSON = "person"
PREPARE_WIDTH, PREPARE_HEIGHT = 640, 360
KEPT_TIMES = 1000

# The phrases and tracker settings.
PHRASES = {
    "controller": "game controller",
    "game controller": "game controller",
    "remote": "remote control",
    "tv remote": "remote control",
}
TRACKER_SETTINGS = {
    "tracker_type": "bytetrack",
    "track_high_thresh": 0.25,
    "track_low_thresh": 0.05,
    "new_track_thresh": 0.25,
    "track_buffer": 60,
    "match_thresh": 0.9,
    "fuse_score": False,
}
GROUND_MIN_CONF = 0.12


def phrase_for(target: str) -> str:
    """The detector prompt for the object the user named: a more specific phrase where a bare word misleads
    the detector ("controller" -> "game controller"), else the words as said."""
    said = " ".join(re.sub(r"[^a-z0-9 ]", " ", target.lower()).split())
    return PHRASES.get(said, said)


def best_box(boxes: Any, width: int, height: int, cls: int = 0,
             min_conf: float = GROUND_MIN_CONF) -> tuple[list[float], float] | None:
    """The box to ground on among one frame's detections of prompt `cls`: the highest confidence at or above
    min_conf, as [x0, y0, x1, y1] fractions with its confidence. Confidences that round to the same hundredth
    are a tie, won by the larger box (a box around part of the object is the smaller one), then by the
    detector's order."""
    found = [(float(boxes.conf[i]), [float(v) for v in boxes.xyxy[i]]) for i in range(len(boxes))
             if int(boxes.cls[i]) == cls and float(boxes.conf[i]) >= min_conf]
    if not found:
        return None
    conf, (x0, y0, x1, y1) = max(found, key=lambda f: (round(f[0], 2), (f[1][2] - f[1][0]) * (f[1][3] - f[1][1])))
    box = [round(min(max(v, 0.0), 1.0), 4) for v in (x0 / width, y0 / height, x1 / width, y1 / height)]
    return box, round(conf, 3)


def ground(detector: "Detector", target: str, cameras: list[Camera],
           frame_for: Callable[[Camera], "bytes | None"]) -> dict[str, Any] | None:
    """The first camera, in the order given, whose latest frame shows the object to the detector (prompted with
    the object's phrase and 'person', as the session will be), with its box and confidence; None if none does."""
    detector.set_prompts([phrase_for(target), PERSON])
    for camera in cameras:
        jpeg = frame_for(camera)
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR) if jpeg else None
        if image is None:
            continue
        best = best_box(detector.detect(image), image.shape[1], image.shape[0])
        if best:
            return {"camera_id": camera["camera_id"], "box": best[0], "conf": best[1]}
    return None


class Detector:
    """One YOLOE model shared by every camera. Prompts are set per session; detect() returns raw boxes for a
    camera's tracker. Calls are serialised, since the model and its prompts are shared."""

    def __init__(self, model: str | None = None, weights: Path | None = None, imgsz: int | None = None,
                 conf: float | None = None, device: str | None = None):
        """Remember the settings (defaults from config); the model loads on first use."""
        self.name = model or config.DETECTOR_MODEL
        self.weights = Path(weights or config.DETECTOR_WEIGHTS)
        self.imgsz = imgsz or config.DETECTOR_IMGSZ
        self.conf = config.DETECTOR_CONF if conf is None else conf
        self.device = device if device is not None else config.DETECTOR_DEVICE
        self.model: Any = None
        self.encoder: Any = None
        self.prompts: list[str] = []
        self.text_pe: Any = None
        self.look: str | None = None
        self.lock = threading.Lock()
        self.calls = 0
        self.ms: list[float] = []

    def load(self) -> None:
        """Load the weights once."""
        if self.model is None:
            from ultralytics import YOLOE
            self.model = YOLOE(str(self.weights / self.name))
            if self.device:
                self.model.to(self.device)

    def warm_up(self) -> None:
        """Load the weights and the text encoder ahead of the first session, printing how long it took."""
        start = time.perf_counter()
        self.set_prompts([PERSON])
        print(f"Detector: {self.name} ready ({time.perf_counter() - start:.1f} s)", flush=True)

    def set_prompts(self, prompts: list[str]) -> None:
        """Prompt the model with these phrases (class i of every box is prompts[i]). The text encoder runs on
        the CPU, so it costs no GPU memory; it is kept for the next session's prompts."""
        if prompts == self.prompts:
            return
        import torch
        from ultralytics.nn.text_model import MobileCLIPTS

        with self.lock:
            self.load()
            net = self.model.model
            param = next(net.parameters())
            self.encoder = self.encoder or MobileCLIPTS(torch.device("cpu"),
                                                        weight=str(self.weights / config.DETECTOR_TEXT_ENCODER))
            with torch.inference_mode():
                feats = self.encoder.encode_text(self.encoder.tokenize(prompts)).reshape(1, len(prompts), -1)
                embeddings = net.model[-1].get_tpe(feats.to(param.device, param.dtype))
            self.model.set_classes(prompts, embeddings)
            self.prompts, self.text_pe, self.look = list(prompts), embeddings, None

    def add_look(self, key: str, image: np.ndarray, box: list[float], weight: float) -> None:
        """Add how the object looks in `box` (fractions) of a BGR frame to the first prompt (the object's phrase),
        with `weight` against the words, for the session `key`; the other prompts stay as words."""
        import torch
        from ultralytics.models.yolo.yoloe import YOLOEVPDetectPredictor, YOLOEVPSegPredictor

        with self.lock:
            self.load()
            h, w = image.shape[:2]
            kind = YOLOEVPSegPredictor if self.model.task == "segment" else YOLOEVPDetectPredictor
            predictor = kind(overrides={"task": self.model.task, "mode": "predict", "save": False, "batch": 1,
                                        "imgsz": self.imgsz, "conf": self.conf, "verbose": False,
                                        **({"device": self.device} if self.device else {})})
            predictor.set_prompts({"bboxes": np.array([[box[0] * w, box[1] * h, box[2] * w, box[3] * h]], np.float32),
                                   "cls": np.array([0])})
            predictor.setup_model(model=self.model.model, verbose=False)
            with torch.inference_mode():
                visual = predictor.get_vpe(image).to(self.text_pe.device, self.text_pe.dtype)
                unit = torch.nn.functional.normalize
                target = unit(unit(self.text_pe[:, :1], dim=-1) + weight * unit(visual, dim=-1), dim=-1)
                self.model.set_classes(self.prompts, torch.cat([target, self.text_pe[:, 1:]], dim=1))
            self.look = key

    def drop_look(self) -> None:
        """Go back to the prompts' words alone."""
        with self.lock:
            if self.look is not None:
                self.model.set_classes(self.prompts, self.text_pe)
                self.look = None

    def prepare(self, prompts: list[str]) -> None:
        """Prompt the model and run one blank frame through it, so the first real frame doesn't pay for the change."""
        self.set_prompts(prompts)
        self.detect(np.zeros((PREPARE_HEIGHT, PREPARE_WIDTH, 3), np.uint8))

    def detect(self, image: np.ndarray) -> Any:
        """The boxes in one BGR frame, as ultralytics Boxes on the CPU (xyxy pixels, conf, cls)."""
        with self.lock:
            self.load()
            start = time.perf_counter()
            result = self.model.predict(image, imgsz=self.imgsz, conf=self.conf, verbose=False,
                                        **({"device": self.device} if self.device else {}))[0]
            self.ms = self.ms[-(KEPT_TIMES - 1):] + [(time.perf_counter() - start) * 1000]
            self.calls += 1
        return result.boxes.cpu().numpy()


class CameraTracks:
    """One camera's ByteTrack over the shared detector: frames in, tracks with steady ids out."""

    def __init__(self, detector: Detector):
        """A fresh tracker for one camera feed."""
        from ultralytics.trackers.byte_tracker import BYTETracker
        from ultralytics.utils import IterableSimpleNamespace

        self.detector = detector
        self.tracker = BYTETracker(IterableSimpleNamespace(**TRACKER_SETTINGS))

    def update(self, image: np.ndarray) -> list[Track]:
        """Detect and track in one BGR frame; boxes come back as fractions of the frame. Detections no track took
        (too weak to start one) come back too, with track id -1."""
        boxes = self.detector.detect(image)
        rows = self.tracker.update(boxes, image)
        h, w = image.shape[:2]
        prompts = self.detector.prompts

        def track(tid: int, xyxy: Any, score: float, cls: float) -> Track:
            """One output row as a Track."""
            x0, y0, x1, y1 = xyxy
            box = [round(float(min(max(v, 0.0), 1.0)), 4) for v in (x0 / w, y0 / h, x1 / w, y1 / h)]
            return Track(tid, prompts[int(cls)], box, round(float(score), 3))

        out = [track(int(tid), (x0, y0, x1, y1), score, cls) for x0, y0, x1, y1, tid, score, cls, _ in rows]
        taken = {int(i) for i in rows[:, -1]} if len(rows) else set()
        out += [track(-1, boxes.xyxy[i], boxes.conf[i], boxes.cls[i]) for i in range(len(boxes)) if i not in taken]
        return out
