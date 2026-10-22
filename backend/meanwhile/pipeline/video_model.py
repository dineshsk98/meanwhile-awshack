"""The slow path: asks the video model what happened in one clip, against the watch order's question.
Locally the "video model" is the vision model shown evenly spaced frames from the clip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

import cv2

from meanwhile import config
from meanwhile.contract import Person, Session
from meanwhile.pipeline import identity, models, tracker

FRAMES = 8
FRAME_WIDTH = 640
JPEG_QUALITY = 85


class Clip(TypedDict):
    """Frames sampled from a clip: JPEGs, their times in seconds from the clip's start, and its length."""
    frames: list[bytes]
    times: list[float]
    duration_s: float


# The clip prompt and the object wording.
CLIP_PROMPT = """These {n} frames come from the "{label}" camera in a home, oldest first, over about {duration:.0f} seconds.
Frame times in seconds: {times}.

The user is away and asked: "{instruction}".
What to look for: {question}
Worth noting: {report}.
{object_rules}
People you may see, recognised by their clothes: {people}. Name a person only when their clothes match;
anyone else is "unknown".

Reply with JSON:
- "relevant": true only if something worth noting for this request happens in these frames.
- "events": what happened, in time order (empty if nothing relevant). Each one has:
  - "t": seconds from the first frame when it happened;
  - "action": {action_rule};
  - "actor": who did it (a name from the list, "unknown", or "" if no person is involved);
  - "actor_wears": what the actor wears, in a few words (e.g. "a navy t-shirt and white shorts"), else "";
  - "recipient": who received something from the actor, else "";
  - "recipient_wears": what the recipient wears, in a few words, else "";
  - "thing": if the event leaves something in a new state (a door locked or unlocked, a light turned on or off, a
    window opened or closed), that thing in the user's words ("the front door"), else "";
  - "state": that thing's new state in one or two words ("locked", "off", "open"), else "";
  - "summary": one short sentence Alexa can read aloud, naming people by name or as "someone", calling the
    user "you", and never using "he" or "she";
  - "alert": true only if it matches one of these: {alert_when}."""

OBJECT_RULES = """The user's {target} is the thing to follow. Report everything that happens to it, in time order:
what anyone does to it or with it, and any change in it. A change in the {target} itself counts even when nobody is
in view: compare how it looks in the first and the last frames. When it is moved, use these names: {moves}."""
OBJECT_ACTION_RULE = ("the move's name for a move; otherwise a few lowercase words in the past tense saying what "
                      "was done to it or how it changed, calling it \"the {target}\" and leaving out who did it; "
                      "\"nothing\" if nothing happened to it")
MOVE_RULES = {
    "picked_up": "someone lifts it from where it rests",
    "put_back": "it is set down again where it was",
    "handed_to": "one person gives it to another",
    "left_view": "someone carries it out of the camera's view",
}
FREE_ACTION_RULE = ("a few lowercase words in the past tense saying what happened, leaving out who did it, e.g. "
                    "\"came into the room\" or \"picked up the remote\"")


def schema() -> dict[str, Any]:
    """The clip answer schema; the action is open text for every kind of order."""
    event = {
        "type": "object",
        "properties": {"t": {"type": "number"}, "action": {"type": "string"}, "actor": {"type": "string"},
                       "actor_wears": {"type": "string"}, "recipient": {"type": "string"},
                       "recipient_wears": {"type": "string"}, "thing": {"type": "string"},
                       "state": {"type": "string"}, "summary": {"type": "string"}, "alert": {"type": "boolean"}},
        "required": ["t", "action", "actor", "actor_wears", "recipient", "recipient_wears", "thing", "state",
                     "summary", "alert"],
    }
    return {"type": "object", "properties": {"relevant": {"type": "boolean"}, "events": {"type": "array", "items": event}},
            "required": ["relevant", "events"]}


def clip_length_s(path: Path) -> float:
    """A clip's length in seconds from its frame times, or 0 if it can't be read."""
    video = cv2.VideoCapture(str(path))
    times = []
    while video.grab():
        times.append(video.get(cv2.CAP_PROP_POS_MSEC) / 1000)
    video.release()
    if not times:
        return 0.0
    return round(times[-1] - times[0] + (times[-1] - times[0]) / max(len(times) - 1, 1), 2)


def sample(path: Path, n: int = FRAMES, width: int = FRAME_WIDTH) -> Clip:
    """n evenly spaced frames from a clip as JPEGs `width` wide, with their times; counts frames first because
    browser WebM files often don't say how many they have."""
    video = cv2.VideoCapture(str(path))
    times = []
    while video.grab():
        times.append(video.get(cv2.CAP_PROP_POS_MSEC) / 1000)
    video.release()
    if not times:
        raise ValueError(f"no frames in {path.name}")
    picks = sorted({round(i * (len(times) - 1) / max(n - 1, 1)) for i in range(n)})
    frames = []
    video = cv2.VideoCapture(str(path))
    for index in range(picks[-1] + 1):
        if not video.grab():
            break
        if index in picks:
            ok, image = video.retrieve()
            if ok:
                height = round(image.shape[0] * width / image.shape[1])
                image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
                frames.append(cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])[1].tobytes())
    video.release()
    start = times[0]
    fps_gap = (times[-1] - start) / max(len(times) - 1, 1)
    return {"frames": frames, "times": [round(times[i] - start, 1) for i in picks][:len(frames)],
            "duration_s": round(times[-1] - start + fps_gap, 2)}


def prompt(session: Session, label: str, clip: Clip, people: list[Person]) -> str:
    """The clip prompt for this session's watch order and this clip."""
    order = session["watch_order"]
    is_object = order.get("kind") == "object"
    target = order.get("target", "object")
    moves = "; ".join(f"{name} when {MOVE_RULES[name]}" for name in tracker.CLIP_MOVES)
    return CLIP_PROMPT.format(
        n=len(clip["frames"]), label=label, duration=clip["duration_s"],
        times=", ".join(f"{t:.1f}" for t in clip["times"]),
        instruction=session.get("instruction", ""), question=order.get("clip_question", ""),
        report=", ".join(order.get("report") or []) or "anything relevant",
        object_rules=OBJECT_RULES.format(target=target, moves=moves) if is_object else "",
        people=identity.describe_people(people),
        action_rule=OBJECT_ACTION_RULE.format(target=target) if is_object else FREE_ACTION_RULE,
        alert_when=", ".join(order.get("alert_when") or []) or "nothing (never alert)",
    )


def describe(path: Path, session: Session, label: str, people: list[Person],
             model: str | None = None) -> tuple[dict[str, Any], Clip]:
    """Ask the video model about one clip; returns its answer (shaped by schema()) and the sampled clip."""
    clip = sample(path)
    message: models.Message = {"role": "user", "content": prompt(session, label, clip, people), "images": clip["frames"]}
    answer = models.ask([message], schema(), model or config.VIDEO_MODEL)
    return answer, clip
