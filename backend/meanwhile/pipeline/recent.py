"""The recent past: every camera's heartbeat thumbnails of the last few minutes, kept whether or not a session is
active and pruned by age and count, and the video model's answer to a question about what happened in them, with
people named by face as in look."""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from meanwhile import config
from meanwhile.api.uploads import stamp
from meanwhile.pipeline import look as looking
from meanwhile.pipeline import models, vision
from meanwhile.store import Store, now, seconds_since, to_iso

FOLDER = "recent"
_STAMP = re.compile(r"^(\d{8}T\d{9}Z)\.jpg$")


def unstamp(name: str) -> str:
    """A file-name stamp back to a timestamp: 20261011T093200123Z -> 2026-10-11T09:32:00.123Z."""
    return to_iso(datetime.strptime(name, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc))


def window_words(seconds: float | None = None) -> str:
    """The window as it is spoken: '5 minutes', 'minute', '90 seconds'."""
    seconds = config.RECENT_WINDOW_S if seconds is None else seconds
    if seconds % 60 == 0:
        minutes = int(seconds // 60)
        return "minute" if minutes == 1 else f"{minutes} minutes"
    return f"{seconds:g} seconds"


def _folder(store: Store, camera_id: str):
    """The folder holding a camera's recent frames."""
    return store.media_path(f"{FOLDER}/{camera_id}")


def frames(store: Store, camera_id: str, at: str | None = None, window_s: float | None = None) -> list[tuple[str, str]]:
    """A camera's kept frames within the window before `at` (default now), oldest first, as (time, media key)."""
    window_s = config.RECENT_WINDOW_S if window_s is None else window_s
    folder = _folder(store, camera_id)
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        match = _STAMP.match(path.name)
        if match:
            taken = unstamp(match.group(1))
            if 0 <= seconds_since(taken, at or now()) <= window_s:
                found.append((taken, f"{FOLDER}/{camera_id}/{path.name}"))
    return sorted(found)


def prune(store: Store, camera_id: str, at: str | None = None) -> int:
    """Delete a camera's frames older than the window, then the oldest beyond RECENT_MAX_FRAMES; returns how many."""
    folder = _folder(store, camera_id)
    if not folder.is_dir():
        return 0
    kept = {key for _, key in frames(store, camera_id, at)}
    newest = sorted(kept)[-config.RECENT_MAX_FRAMES:] if config.RECENT_MAX_FRAMES > 0 else []
    gone = 0
    for path in folder.iterdir():
        if f"{FOLDER}/{camera_id}/{path.name}" not in newest:
            path.unlink(missing_ok=True)
            gone += 1
    return gone


def keep(store: Store, camera_id: str, thumb_key: str, at: str) -> None:
    """Keep a new heartbeat thumbnail in the camera's window (as taken at `at`) and prune the window."""
    try:
        data = store.media_path(thumb_key).read_bytes()
    except FileNotFoundError:
        return
    store.write_media(f"{FOLDER}/{camera_id}/{stamp(at)}.jpg", data)
    prune(store, camera_id, at)


def clear(store: Store, camera_id: str) -> None:
    """Delete every kept frame of a camera."""
    folder = _folder(store, camera_id)
    if folder.is_dir():
        for path in folder.iterdir():
            path.unlink(missing_ok=True)


def spread(items: list[Any], n: int) -> list[Any]:
    """At most n items evenly spaced over the list, always including the first and the last."""
    if n <= 0:
        return []
    if len(items) <= n:
        return list(items)
    if n == 1:
        return [items[-1]]
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


def ago(taken: str, at: str) -> str:
    """How long before `at` a frame was taken: '2 min 40 s ago', '12 s ago'."""
    seconds = max(0, round(seconds_since(taken, at)))
    return f"{seconds // 60} min {seconds % 60} s ago" if seconds >= 60 else f"{seconds} s ago"


# The look-back prompt.
LOOK_BACK_PROMPT = (
    "These {n} photos come from the user's home cameras, taken over the last {window} and shown oldest first; they "
    "are a few seconds apart, so what happened is the change from one photo to the next.\n{photos}\n"
    "People recognised by their faces:\n{people}\n"
    "The user asks: \"{question}\"\n"
    "Answer in one to three short sentences, spoken to the user, in time order, starting with \"In the last "
    "{window},\".\n"
    "- You can only see the last {window}. If the user asks about anything earlier (\"10 minutes ago\", \"this "
    "morning\"), set asks_earlier to true and answer for the last {window} only; never guess about earlier.\n"
    "- Only the faces listed above are known. Say \"you\" for a person only in a photo that lists \"you, the "
    "user\"; never assume a person is the user. Anyone else is \"someone\": never say who a person is from their "
    "clothes, hair or body. If nobody's face is recognised, say \"someone\", e.g. \"In the last {window}, someone "
    "sat down at the desk on the {label} camera, but {unknown_reason}.\"\n"
    "- Say what people do and what happens to things. Unless the question itself is about clothing or appearance, "
    "never mention clothing, body, state of dress or appearance (\"shirtless\", \"a red t-shirt\", \"a "
    "necklace\"). When it is (\"what am I wearing?\"), describe what they wear plainly.\n"
    "- Never use he, she, him, her, his or hers. If nothing changed, say so.\n"
    "- frame: the number of the photo that best shows your answer. Put it only in this field; the answer never "
    "mentions photos, frames or their numbers."
)
PHOTO_LINE = "Photo {n}: the \"{label}\" camera, {ago}."
UNSEEN_FACE = "I couldn't see their face"
NO_FACE_ID = "face ID is off, so I can't tell who it was"
LOOK_BACK_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "asks_earlier": {"type": "boolean"}, "frame": {"type": "integer"}},
    "required": ["answer", "asks_earlier", "frame"],
}


def answer(question: str, chosen: list[looking.Frame], faces: Any, store: Store, at: str,
           model: str | None = None) -> dict[str, Any]:
    """Name the faces in every chosen frame (in parallel), then ask the video model the question about all of them.
    Returns the answer, whether the question reached before the window, the frame it is about, the faces seen per
    frame and the seconds each step took; raises ModelUnavailable when the model doesn't answer."""
    start = time.perf_counter()
    if faces is not None and chosen:
        with ThreadPoolExecutor(max_workers=len(chosen), thread_name_prefix="look-back-faces") as pool:
            seen = list(pool.map(lambda f: looking.faces_in(faces, store, f), chosen))
    else:
        seen = [[] for _ in chosen]
    people = {p["person_id"]: p for p in store.list_people()}
    faces_s = time.perf_counter() - start
    window = window_words()
    photos = "\n".join(PHOTO_LINE.format(n=n, label=f["label"], ago=ago(f["at"], at)) for n, f in enumerate(chosen, 1))
    lines = looking.people_lines(chosen, seen, people, faces is not None)
    text = LOOK_BACK_PROMPT.format(n=len(chosen), window=window, photos=photos, people=lines,
                                   question=question.strip(), label=chosen[-1]["label"],
                                   unknown_reason=UNSEEN_FACE if faces is not None else NO_FACE_ID)
    images = [vision.crop(f["jpeg"], [0.0, 0.0, 1.0, 1.0]) or f["jpeg"] for f in chosen]
    reply = models.ask([{"role": "user", "content": text, "images": images}], LOOK_BACK_SCHEMA,
                       model or config.VIDEO_MODEL)
    said, dropped = looking.plain(" ".join(str(reply.get("answer") or "").split()), question)
    if not said:
        raise models.ModelUnavailable("The video model gave an empty answer.")
    index = reply.get("frame")
    pick = chosen[index - 1] if isinstance(index, int) and 1 <= index <= len(chosen) else chosen[-1]
    return {"answer": said, "dropped": dropped, "asks_earlier": bool(reply.get("asks_earlier")), "frame": pick,
            "seen": list(zip(chosen, seen)), "people": people,
            "took_s": {"faces": round(faces_s, 2), "model": round(time.perf_counter() - start - faces_s, 2)}}
