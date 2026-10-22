"""Things' states: a watch order's clip event that leaves something in a new state ("the front door" → "locked")
records it per camera; a question about a thing answers from its stored status when that is conclusive (clear and
fresh), and otherwise checks the video, storing a confident conclusion as the status."""

from __future__ import annotations

import re
import time
from typing import Any

from meanwhile import config
from meanwhile.contract import Event, ThingState
from meanwhile.pipeline import look as looking
from meanwhile.pipeline import models, tracker, vision
from meanwhile.pipeline.recent import ago
from meanwhile.store import Store, now, seconds_since

_ARTICLES = {"my", "the", "our", "a", "an", "your"}
# What counts as a state that says nothing, and the video check's prompt.
UNSURE = {"", "unknown", "unclear", "not sure", "uncertain"}
CHECK_PROMPT = (
    "These {n} photos come from the user's home cameras, oldest first.{last}\n{photos}\n"
    "People recognised by their faces:\n{people}\n"
    "The user asks: \"{question}\" (about {thing}).\n"
    "Answer only from what is visible in these photos; never guess. Judge {thing} itself, not light, shadows, "
    "gaps or reflections around it; if the photo is blurred, dark or only shows part of it, its state is "
    "unknown.\n"
    "- answer: one or two short sentences, spoken to the user, saying what you can see of {thing} at the end of "
    "these photos (\"the door is open\"). Never say who changed it. If its state can't be seen (the lock or "
    "switch isn't visible, or it is too dark), say so, and say what you do see. An open door is not locked.\n"
    "- state: {thing}'s state as the last photo that shows it shows it, in one or two words (\"locked\", "
    "\"open\", \"off\"); \"unknown\" when it can't be seen.\n"
    "- visible: true only if that state itself can be seen in a photo, not guessed.\n"
    "- evidence: the number of the photo that shows that state, or 0 if none does.\n"
    "- matches: how many separate things in one photo could be {thing} (two doors in view → 2). When more than one "
    "could be and the question doesn't say which, don't pick one: in the answer describe each briefly by where it "
    "is (\"the door on the left is open, the one in front is closed\"), and set state to \"unknown\".\n"
    "- changed_by: {who_rule}\n"
    "- Only the faces listed above are known; never say who a person is from their clothes, hair or body.\n"
    "- Say what people do and what happens to things; never mention clothing, body or appearance.\n"
    "- Never use he, she, him, her, his or hers.\n"
    "- frame: the number of the photo that best shows the answer. Put it only in this field; the answer never "
    "mentions photos, frames or their numbers."
)
WHO_IN_CLIP = ("a person listed above for a photo who is seen in these photos making the change (as listed, e.g. "
               "\"you\" or \"Ravi\"), or \"\" if nobody listed is seen doing it.")
WHO_NOT_SEEN = "always \"\" (these photos don't show a change being made)."
SAW_CHANGE = "I saw {who} change it."
LAST_LINE = " The last recorded change was {thing} becoming \"{value}\", {ago}."
PHOTO_LINE = "Photo {n}: the \"{label}\" camera, {ago}."
PERSON_WORDS = ("you", "someone", "somebody", "they")
CHECK_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "state": {"type": "string"}, "visible": {"type": "boolean"},
                   "evidence": {"type": "integer"}, "matches": {"type": "integer"}, "changed_by": {"type": "string"},
                   "frame": {"type": "integer"}},
    "required": ["answer", "state", "visible", "evidence", "matches", "changed_by", "frame"],
}


def key_for(thing: str) -> str:
    """A thing's key: its words in lowercase without my/the/our/a/an ('The Front Door' → 'front door')."""
    return " ".join(w for w in re.findall(r"[a-z0-9]+", thing.lower()) if w not in _ARTICLES)


def record(store: Store, event: Event) -> ThingState | None:
    """Record the state an event left its thing in (when it names both), as seen on its camera."""
    thing, value = str(event.get("thing") or "").strip(), str(event.get("state") or "").strip().lower()
    if not thing or not value or not key_for(thing):
        return None
    return store.set_state({"camera_id": event.get("camera_id", ""), "key": key_for(thing), "thing": thing,
                            "value": value, "at": event.get("happened_at") or event["ts"],
                            "by": event.get("actor_id") or "", "event_id": event["event_id"],
                            "session_id": event.get("session_id", ""), "clip_key": event.get("clip_key"),
                            "source": "event"})


def matching(store: Store, thing: str, camera_id: str | None = None) -> list[ThingState]:
    """The recorded states of the thing asked about, newest first: same key, or one key's words all within the
    other's ('door' finds 'front door'); only the given camera's when one is given."""
    wanted = set(key_for(thing).split())
    if not wanted:
        return []
    found = []
    for state in store.list_states():
        words = set(state["key"].split())
        if (wanted <= words or words <= wanted) and (camera_id is None or state["camera_id"] == camera_id):
            found.append(state)
    return found


def clear(found: list[ThingState]) -> bool:
    """Whether the recorded states give one clear answer: the newest says something, and every camera's newest
    state of the same thing agrees with it."""
    if not found or found[0]["value"] in UNSURE:
        return False
    newest_per_camera: dict[tuple[str, str], str] = {}
    for state in found:
        newest_per_camera.setdefault((state["camera_id"], state["key"]), state["value"])
    return all(value == found[0]["value"] for value in newest_per_camera.values())


def fresh(state: ThingState | looking.Frame, at: str | None = None) -> bool:
    """Whether a stored status (or a frame) is no older than MEANWHILE_STATE_MAX_AGE_S."""
    return seconds_since(state["at"], at) <= config.STATE_MAX_AGE_S


def conclusive(found: list[ThingState], at: str | None = None) -> bool:
    """Whether the stored statuses answer on their own: clear (see clear()) and the newest fresh."""
    return clear(found) and fresh(found[0], at)


def unattributed(answer: str, value: str, names: list[str]) -> str:
    """The answer without sentences that say a person changed the thing (a person word or name together with the
    state word: "You locked it."), keeping the rest; unchanged when every sentence would go."""
    words = set(value.lower().split()) - UNSURE
    if not words:
        return answer
    people = {w.lower() for w in PERSON_WORDS} | {n.lower() for n in names if n}
    sentences = [x.strip() for x in re.findall(r"[^.!?]+[.!?]?", answer) if x.strip()]
    kept = []
    for sentence in sentences:
        low = sentence.lower()
        tokens = set(re.findall(r"[a-z']+", low))
        if tokens & words and (tokens & people or any(n in low for n in people if " " in n)):
            continue
        kept.append(sentence)
    return " ".join(kept) if kept else answer


def check(question: str, thing: str, frames: list[looking.Frame], faces: Any, store: Store,
          last: ThingState | None = None, model: str | None = None) -> dict[str, Any]:
    """Ask the video model what can be seen of a thing's state in these frames (any cameras, oldest first), with
    people named by face and the last recorded change when there is one. The answer never says who changed it,
    unless the frames are a change clip and a person face ID recognised in them is seen making the change. A state
    is stored as the thing's status (camera of the evidence photo, at its time, never with an actor) only when it
    is a real state, said to be visible, backed by an evidence photo, and about the only thing in view that matches
    (with several, nothing is stored and the answer describes each). Returns the answer, the state, whether it
    was stored, the frame, the people and the seconds each step took; raises ModelUnavailable."""
    start = time.perf_counter()
    seen = [looking.faces_in(faces, store, f) for f in frames]
    people = {p["person_id"]: p for p in store.list_people()}
    faces_s = time.perf_counter() - start
    at = now()
    clip = all(f.get("source") == "clip" for f in frames)
    photos = "\n".join(PHOTO_LINE.format(n=n, label=f["label"], ago=ago(f["at"], at)) for n, f in enumerate(frames, 1))
    lines = "\n".join(looking.PEOPLE_LINE.format(n=n, label=f["label"], people=looking.face_words(s, people))
                      for n, (f, s) in enumerate(zip(frames, seen), 1))
    before = LAST_LINE.format(thing=last["thing"], value=last["value"], ago=ago(last["at"], at)) if last else ""
    text = CHECK_PROMPT.format(n=len(frames), last=before, photos=photos, people=lines, question=question.strip(),
                               thing=thing, who_rule=WHO_IN_CLIP if clip else WHO_NOT_SEEN)
    images = [vision.crop(f["jpeg"], [0.0, 0.0, 1.0, 1.0]) or f["jpeg"] for f in frames]
    reply = models.ask([{"role": "user", "content": text, "images": images}], CHECK_SCHEMA,
                       model or config.VIDEO_MODEL)
    value = " ".join(str(reply.get("state") or "").lower().split())
    recognised = {looking.OWNER if tracker.is_owner(s["person_id"], people) else tracker.who(s["person_id"], people)
                  for faces_seen in seen for s in faces_seen if s["person_id"]}
    spoken = {"you" if name == looking.OWNER else name for name in recognised if name}
    names = [tracker.who(p, people) or "" for p in people]
    said, _ = looking.plain(" ".join(str(reply.get("answer") or "").split()), question)
    said = unattributed(said, value, names)
    if not said:
        raise models.ModelUnavailable("The video model gave an empty answer.")
    said = said[0].upper() + said[1:]
    who = " ".join(str(reply.get("changed_by") or "").split())
    who = who if clip and who in spoken else ""
    if who and value not in UNSURE:
        said = f"{said} {SAW_CHANGE.format(who=who)}"
    index = reply.get("frame")
    pick = frames[index - 1] if isinstance(index, int) and 1 <= index <= len(frames) else frames[-1]
    evidence = reply.get("evidence")
    shown = frames[evidence - 1] if isinstance(evidence, int) and 1 <= evidence <= len(frames) else None
    stored = None
    several = isinstance(reply.get("matches"), int) and reply["matches"] > 1
    if several:
        value = "unknown"
    if shown is not None and reply.get("visible") is True and value not in UNSURE and key_for(thing):
        stored = store.set_state({"camera_id": shown["camera_id"], "key": key_for(thing), "thing": thing,
                                  "value": value, "at": shown["at"], "by": "", "event_id": None, "session_id": "",
                                  "clip_key": None, "source": "video"})
    return {"answer": said, "state": value, "stored": stored, "frame": shown or pick, "seen": list(zip(frames, seen)),
            "people": people,
            "took_s": {"faces": round(faces_s, 2), "model": round(time.perf_counter() - start - faces_s, 2)}}
