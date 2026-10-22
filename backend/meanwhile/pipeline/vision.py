"""Vision checks on single frames: grounding finds the object a watch order names and returns its box, and the
frame check (the fast path) says whether it is still in place and who is touching it."""

from __future__ import annotations

import math
from typing import Any, Callable, TypedDict

import cv2
import numpy as np

from meanwhile import config
from meanwhile.contract import Camera
from meanwhile.pipeline import models

FrameFor = Callable[[Camera], "bytes | None"]


class Found(TypedDict):
    """Where an object was found: the camera, and its box as [x0, y0, x1, y1] fractions of the frame."""
    camera_id: str
    box: list[float]


# The grounding prompt, schema and box rules.
GROUND_PROMPT = (
    "This photo is from the \"{label}\" camera. Is there a {target} in it?\n"
    "Answer found true only if you can clearly see a {target}; if you're not sure, or it's only something "
    "that looks a bit like one, answer found false.\n"
    "If found, box_2d is its bounding box as [x1, y1, x2, y2]; otherwise [0, 0, 0, 0]."
)
GROUND_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "box_2d": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
    },
    "required": ["found", "box_2d"],
}
MIN_BOX_AREA = 0.0005
MAX_BOX_AREA = 0.6


QWEN_PATCH = 28
QWEN_MIN_PIXELS = 28 * 28 * 1024
QWEN_MAX_PIXELS = 28 * 28 * 1280


def qwen_canvas(width: int, height: int) -> tuple[int, int]:
    """The size Ollama resizes an image to before qwen2.5vl sees it (sides in multiples of 28, scaled up
    to at least QWEN_MIN_PIXELS); the model's pixel coordinates are on this canvas. 640x360 -> 1204x672."""
    f = QWEN_PATCH
    w, h = max(f, round(width / f) * f), max(f, round(height / f) * f)
    if w * h > QWEN_MAX_PIXELS:
        beta = math.sqrt(width * height / QWEN_MAX_PIXELS)
        w, h = math.floor(width / beta / f) * f, math.floor(height / beta / f) * f
    elif w * h < QWEN_MIN_PIXELS:
        beta = math.sqrt(QWEN_MIN_PIXELS / (width * height))
        w, h = math.ceil(width * beta / f) * f, math.ceil(height * beta / f) * f
    return w, h


def parse_box(answer: dict[str, Any], width: int, height: int, model: str) -> list[float] | None:
    """The model's box as [x0, y0, x1, y1] fractions, or None if not found or not believable.
    Qwen3.5 gives [x1, y1, x2, y2] on 0-1000; Qwen2.5-VL gives them in pixels of its resized canvas;
    Gemini gives [ymin, xmin, ymax, xmax] on 0-1000."""
    raw = answer.get("box_2d") or []
    if not answer.get("found") or len(raw) != 4:
        return None
    if model.startswith("gemini:"):
        y0, x0, y1, x1 = (v / 1000 for v in raw)
    elif "qwen3" in model:
        x0, y0, x1, y1 = (v / 1000 for v in raw)
    else:
        cw, ch = qwen_canvas(width, height)
        x0, y0, x1, y1 = raw[0] / cw, raw[1] / ch, raw[2] / cw, raw[3] / ch
    x0, x1 = sorted(min(max(v, 0.0), 1.0) for v in (x0, x1))
    y0, y1 = sorted(min(max(v, 0.0), 1.0) for v in (y0, y1))
    area = (x1 - x0) * (y1 - y0)
    if not MIN_BOX_AREA <= area <= MAX_BOX_AREA:
        return None
    return [round(v, 4) for v in (x0, y0, x1, y1)]


# The frame-check question, thresholds and spot rule.
CHECK_PROMPT = (
    "This photo is from the \"{label}\" camera in a home. Find the user's {target}.\n"
    "- found: true only if you can clearly see the {target}.\n"
    "- box_2d: its bounding box as [x1, y1, x2, y2] if found, otherwise [0, 0, 0, 0].\n"
    "- in_hand: true if a person's hand is gripping or lifting it right now. Hands nearby but not touching it "
    "don't count.\n"
    "- person: the person holding it, or else the person nearest to it, recognised by what they wear: {people}. "
    "Use their name, \"unknown\" for anyone else, or \"\" if nobody is in the photo."
)
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "box_2d": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
        "in_hand": {"type": "boolean"},
        "person": {"type": "string"},
    },
    "required": ["found", "box_2d", "in_hand", "person"],
}
STILL_MAX = 0.12
SAME_SPOT_MARGIN = 0.5
PIXEL_DELTA = 24
GRID_WIDTH = 64
BASELINE_MARGIN = 0.5
CHECK_WIDTH = 640

def crop(jpeg: bytes, area: list[float], width: int = CHECK_WIDTH) -> bytes | None:
    """The given area of a JPEG (fractions of the frame), as a JPEG at most `width` wide, or None if unreadable."""
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    h, w = image.shape[:2]
    part = image[round(area[1] * h):round(area[3] * h), round(area[0] * w):round(area[2] * w)]
    if part.shape[1] > width:
        part = cv2.resize(part, (width, round(part.shape[0] * width / part.shape[1])), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", part, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def grown(box: list[float], margin: float = BASELINE_MARGIN) -> list[float]:
    """The box with `margin` of its size added on each side, kept inside the frame."""
    mx, my = (box[2] - box[0]) * margin, (box[3] - box[1]) * margin
    return [max(0.0, box[0] - mx), max(0.0, box[1] - my), min(1.0, box[2] + mx), min(1.0, box[3] + my)]


def area_grid(jpeg: bytes, area: list[float]) -> np.ndarray | None:
    """An area of a JPEG as a GRID_WIDTH-wide grid of brightness values, or None if unreadable."""
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    h, w = image.shape
    part = image[round(area[1] * h):round(area[3] * h), round(area[0] * w):round(area[2] * w)].astype(np.float32)
    if part.size == 0:
        return None
    return cv2.resize(part, (GRID_WIDTH, max(4, round(GRID_WIDTH * part.shape[0] / part.shape[1]))),
                      interpolation=cv2.INTER_AREA)


def box_change(jpeg: bytes, reference: bytes, box: list[float]) -> float | None:
    """Share (0-1) of the focus box that differs from the reference frame. The overall brightness shift
    (auto-exposure) is measured over the box plus a margin, so an object leaving the box isn't mistaken for
    one; None if either frame can't be read."""
    area = grown(box)
    now, before = area_grid(jpeg, area), area_grid(reference, area)
    if now is None or before is None:
        return None
    diff = now - cv2.resize(before, (now.shape[1], now.shape[0]))
    diff -= diff.mean()
    gh, gw = diff.shape
    aw, ah = area[2] - area[0], area[3] - area[1]
    x0, x1 = round((box[0] - area[0]) / aw * gw), round((box[2] - area[0]) / aw * gw)
    y0, y1 = round((box[1] - area[1]) / ah * gh), round((box[3] - area[1]) / ah * gh)
    inner = diff[y0:max(y1, y0 + 1), x0:max(x1, x0 + 1)]
    return float(np.mean(np.abs(inner) > PIXEL_DELTA))

def same_spot(new: list[float], old: list[float]) -> bool:
    """Whether a new box's centre lies within the old box grown by SAME_SPOT_MARGIN of its size on each side."""
    area = grown(old, SAME_SPOT_MARGIN)
    cx, cy = (new[0] + new[2]) / 2, (new[1] + new[3]) / 2
    return area[0] <= cx <= area[2] and area[1] <= cy <= area[3]


def check_frame(target: str, label: str, jpeg: bytes, box: list[float], people: str, reference: bytes | None = None,
                model: str | None = None) -> dict[str, Any]:
    """Where the object is now, and who has it. If the focus box looks as it did in the reference frame, it is in
    place without asking the model. Otherwise the model looks at the whole frame and gives the object's box,
    whether a hand has it and who is holding it or nearest. Returns visible, held, in_place (at rest in the
    same spot), box (where it is now, or None) and person."""
    changed = box_change(jpeg, reference, box) if reference else None
    if changed is not None and changed <= STILL_MAX:
        return {"visible": True, "held": False, "in_place": True, "box": box, "person": "", "changed": round(changed, 2)}
    image = crop(jpeg, [0.0, 0.0, 1.0, 1.0], CHECK_WIDTH)
    if image is None:
        raise ValueError("the frame can't be decoded")
    model = model or config.VISION_MODEL
    answer = models.ask([{"role": "user", "content": CHECK_PROMPT.format(label=label, target=target, people=people),
                          "images": [image]}], CHECK_SCHEMA, model)
    size = image_size(image) or (CHECK_WIDTH, CHECK_WIDTH)
    visible = bool(answer.get("found"))
    now = parse_box(answer, size[0], size[1], model) if visible else None
    held = visible and bool(answer.get("in_hand"))
    in_place = visible and not held and (now is None or same_spot(now, box))
    return {"visible": visible, "held": held, "in_place": in_place, "box": now if now else (box if in_place else None),
            "person": str(answer.get("person") or ""), "changed": None if changed is None else round(changed, 2)}

def image_size(jpeg: bytes) -> tuple[int, int] | None:
    """A JPEG's (width, height), or None if it can't be decoded."""
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    return None if image is None else (image.shape[1], image.shape[0])


def find_in_frame(target: str, label: str, jpeg: bytes, model: str | None = None) -> list[float] | None:
    """Ask the vision model for the target's box in one frame."""
    model = model or config.VISION_MODEL
    size = image_size(jpeg)
    if size is None:
        return None
    width, height = size
    prompt = GROUND_PROMPT.format(label=label, target=target)
    answer = models.ask([{"role": "user", "content": prompt, "images": [jpeg]}], GROUND_SCHEMA, model)
    return parse_box(answer, width, height, model)


def ground(target: str, cameras: list[Camera], frame_for: FrameFor) -> Found | None:
    """The first camera, in the order given, whose latest frame shows the target, with its box."""
    for camera in cameras:
        jpeg = frame_for(camera)
        if jpeg is None:
            continue
        box = find_in_frame(target, camera.get("label", camera["camera_id"]), jpeg)
        if box:
            return {"camera_id": camera["camera_id"], "box": box}
    return None
