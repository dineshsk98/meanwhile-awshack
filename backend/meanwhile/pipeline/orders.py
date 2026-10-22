"""Turns what the user said into a watch order (or a polite question back), once per session, with grounding for objects."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from meanwhile import config
from meanwhile.contract import Camera, OrderResult, WatchOrder
from meanwhile.pipeline import models, vision

Understanding = dict[str, Any]
Understand = Callable[[str, list[Camera]], Understanding]
Ground = Callable[[str, list[Camera]], "vision.Found | None"]

# The prompt, worked examples and fixed wording.
PROMPT = """You help Meanwhile, a home-camera assistant. The user is stepping away and tells it what to watch.
Work out what they want. The cameras are: {cameras}.

Reply with JSON only:
- "kind", one of:
  - "question": they asked a question instead of saying what to watch. Anything phrased as a question
    is a question, even when it mentions a thing: "did I lock the door?", "where is my phone?".
    A polite request ("can you keep an eye on my bag?") is not a question.
  - "object": keep an eye on, look for, find, locate, look after, mind, guard or track one thing they own that
    can be carried, or tell them what happens to it: someone touching, using, moving or taking it, or a change in
    it. "Find my wallet" is an instruction, not a question. A fixed part of the home (a door, a window, a light)
    is never an "object": watching it is "monitor".
  - "notes": take notes of, or keep a log of, who comes and goes or what happens, without interrupting them; also
    keeping an eye on or watching over a place ("keep an eye on the house", "watch the living room").
  - "monitor": tell them, warn them or alert them when something specific happens that isn't about one of
    their things being touched ("tell me if the dog gets on the sofa"), including watching a place or a fixed
    part of the home ("watch the front door").
  - "unclear": you can't tell what to watch, e.g. the thing is only "it", "that" or "this".
- "target": object: the thing as they named it, 1 to 3 words, lowercase, without my/the/our ("car keys").
  notes: who or what to note ("who comes into the room"). monitor: what to watch for ("the dog getting on
  the sofa"). Otherwise "".
- "camera": the camera named in their words ("Desk"). If their words name none, "". Never pick one for them.
- "clip_question": for notes and monitor, the one question to ask about each short video clip.
- "report": for notes and monitor, 1 to 3 short phrases saying what counts as worth noting.
- "alert_when": for monitor, 1 or 2 short phrases saying what should interrupt them. Otherwise [].
- "summary": for notes and monitor, a phrase that completes "I'm …", starting with a verb ending in -ing,
  saying "you"/"your" for the user, with no full stop ("taking notes on who comes in, without interrupting
  you"). Otherwise ""."""

EXAMPLES: list[tuple[str, Understanding]] = [
    ("is the back door shut", {"kind": "question"}),
    ("keep an eye on that", {"kind": "unclear"}),
    ("is my bag still on the desk", {"kind": "question"}),
    ("look out for my headphones on the desk camera", {"kind": "object", "target": "headphones", "camera": "desk"}),
    ("let me know who drops by", {
        "kind": "notes", "target": "who drops by",
        "clip_question": "Is anyone arriving or leaving, and who are they?",
        "report": ["someone arriving", "someone leaving"],
        "summary": "taking notes on who drops by, without interrupting you"}),
    ("tell me if the cat jumps on the table", {
        "kind": "monitor", "target": "the cat jumping on the table",
        "clip_question": "Does the cat jump onto the table?",
        "report": ["the cat on the table"], "alert_when": ["the cat jumps on the table"],
        "summary": "watching for the cat jumping on the table, and I'll tell you if it does"}),
    ("keep watch over the house while I'm gone", {
        "kind": "notes", "target": "what happens in the house",
        "clip_question": "Who is here and what are they doing?",
        "report": ["someone arriving or leaving", "anything unusual"],
        "summary": "keeping watch over the house, without interrupting you"}),
    ("warn me if someone moves my phone", {"kind": "object", "target": "phone"}),
    ("tell me when the front door is opened", {
        "kind": "monitor", "target": "the front door being opened",
        "clip_question": "Is the front door opened, and by whom?",
        "report": ["the front door opened"], "alert_when": ["the front door is opened"],
        "summary": "watching the front door, and I'll tell you if it's opened"}),
]

UNCLEAR = ("Sorry, I didn't quite catch what you'd like me to watch. "
           "Could you say it again? For example, \"keep an eye on my keys\".")
QUESTION = ("That sounds like a question rather than something to watch. "
            "Ask me again and I'll answer it, or tell me what to keep an eye on while you're away.")
NO_CAMERAS = "I don't see any cameras running right now. Could you open the camera page on a phone first?"

KINDS = ("question", "unclear", "object", "notes", "monitor")
OBJECT_REPORT = ["anything anyone does to it or with it", "any change in it",
                 "picked up, put back, handed over or taken away"]
OBJECT_ALERT_WHEN = ["picked_up", "handed_to", "put_back", "left_view"]
VAGUE_TARGETS = ("it", "that", "this", "them", "those", "these", "something", "thing", "stuff")
NOTES_TARGET = "who comes and goes"
POLITE_OPENINGS = ("alexa", "please", "can you", "could you", "would you", "will you", "hey", "ok", "okay")
QUESTION_WORDS = {"did", "do", "does", "is", "are", "was", "were", "has", "have", "who", "what", "where",
                  "when", "why", "how", "which", "whose", "am"}

FIELDS = {"type": "string"}
SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS)},
        "target": FIELDS, "camera": FIELDS, "clip_question": FIELDS, "summary": FIELDS,
        "report": {"type": "array", "items": FIELDS},
        "alert_when": {"type": "array", "items": FIELDS},
    },
    "required": ["kind", "target", "camera", "clip_question", "report", "alert_when", "summary"],
}


def labels(cameras: list[Camera], joiner: str = "and") -> str:
    """Camera labels joined for speech: 'Desk, Door or Room'."""
    names = [c.get("label", c["camera_id"]) for c in cameras]
    return f" {joiner} ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + f" {joiner} " + names[-1]


def bare(words: str) -> str:
    """Lowercase, trimmed, without a leading my/the/our/a/an."""
    words = " ".join(words.lower().strip(" .?!\"'").split())
    first, _, rest = words.partition(" ")
    return rest if first in ("my", "the", "our", "a", "an") and rest else words


def is_question(instruction: str) -> bool:
    """Whether the words read as a question once polite openings ("can you", "Alexa,") are removed."""
    words = re.sub(r"[^a-z' ]", " ", instruction.lower()).split()
    changed = True
    while changed and words:
        changed = False
        for opening in POLITE_OPENINGS:
            n = len(opening.split())
            if words[:n] == opening.split():
                words, changed = words[n:], True
    return bool(words) and words[0] in QUESTION_WORDS


def understand(instruction: str, cameras: list[Camera]) -> Understanding:
    """Ask the model what the instruction means, as SCHEMA's fields."""
    def full(meant: Understanding) -> str:
        """An example answer with every field present."""
        return json.dumps({"target": "", "camera": "", "clip_question": "", "report": [], "alert_when": [],
                           "summary": "", **meant})

    messages: list[models.Message] = [{"role": "system", "content": PROMPT.format(cameras=labels(cameras))}]
    for said, meant in EXAMPLES:
        messages += [{"role": "user", "content": said}, {"role": "assistant", "content": full(meant)}]
    messages.append({"role": "user", "content": instruction})
    return models.ask(messages, SCHEMA, config.ORDER_MODEL)


def camera_named(cameras: list[Camera], name: str) -> Camera | None:
    """The camera whose label or id is `name` (also 'the desk camera'), if any."""
    name = bare(name).removesuffix(" camera").strip()
    for cam in cameras:
        if cam.get("label", "").lower() == name or cam["camera_id"] == name:
            return cam
    return None


def labelled_in(cameras: list[Camera], instruction: str) -> Camera | None:
    """The camera whose label, as whole words, is in the instruction ("watch the front door" → the Door camera),
    longest label first; None when no label is."""
    words = " " + " ".join(re.findall(r"[a-z0-9]+", instruction.lower())) + " "
    for cam in sorted(cameras, key=lambda c: -len(c.get("label", ""))):
        label = " ".join(re.findall(r"[a-z0-9]+", cam.get("label", "").lower()))
        if label and f" {label} " in words:
            return cam
    return None


def phrase(summary: str) -> str | None:
    """A summary cleaned into a phrase completing "I'm …", or None if it doesn't start with an -ing verb."""
    text = " ".join(summary.strip().split()).rstrip(".!")
    text = re.sub(r"^(i'm|i am)\s+", "", text, flags=re.IGNORECASE)
    if not text:
        return None
    text = text[0].lower() + text[1:]
    return text if text.split()[0].endswith("ing") else None


def object_question(target: str) -> str:
    """The clip question for an object watch."""
    return f"What happens to the {target}? Who touches it, what do they do to it or with it, and where does it go?"


def build(instruction: str, meant: Understanding, cameras: list[Camera], ground: Ground) -> OrderResult:
    """Check the model's reading and turn it into a watch order, a clarifying question, or 'not found'."""
    kind = meant.get("kind") if meant.get("kind") in KINDS else "unclear"
    target = bare(str(meant.get("target") or ""))
    named = bare(str(meant.get("camera") or ""))
    if named and named.removesuffix(" camera") not in instruction.lower():
        named = ""
    if kind == "question" or is_question(instruction):
        return {"watch_order": {"clarify": QUESTION}}
    if kind == "notes" and not target:
        target = NOTES_TARGET
    if kind == "unclear" or (kind in ("object", "monitor") and (not target or target in VAGUE_TARGETS)):
        return {"watch_order": {"clarify": UNCLEAR}}
    chosen = camera_named(cameras, named) if named else None
    if not named and kind in ("notes", "monitor"):
        chosen = labelled_in(cameras, instruction)
    if named and chosen is None:
        where = named.removesuffix(" camera")
        return {"watch_order": {"clarify": f"I don't have a {where} camera. "
                                           f"I can use the {labels(cameras, 'or')} camera. Which one would you like?"}}

    if kind == "object":
        searched = [chosen] if chosen else cameras
        found = ground(target, searched)
        if found is None:
            return {"watch_order": {"kind": "object", "target": target},
                    "not_found": f"I can't see your {target} on the {labels(searched, 'or')} camera."}
        cam = next(c for c in cameras if c["camera_id"] == found["camera_id"])
        order: WatchOrder = {
            "kind": "object", "target": target, "cameras": "all",
            "clip_question": object_question(target),
            "report": OBJECT_REPORT, "alert_when": OBJECT_ALERT_WHEN,
            "summary_for_alexa": f"keeping an eye on your {target} on the {cam.get('label', cam['camera_id'])} camera",
        }
        return {"watch_order": order, "focus_object": target, "focus_camera_id": cam["camera_id"],
                "focus_box": found["box"]}

    notes = kind == "notes"
    alert_when = [] if notes else [bare(a) for a in meant.get("alert_when") or [] if bare(a)] or [target]
    fallback = (f"taking notes on {target}, without interrupting you" if notes
                else f"watching for {target}, and I'll tell you if I see it")
    return {"watch_order": {
        "kind": kind, "target": target,
        "cameras": [chosen["camera_id"]] if chosen else "all",
        "clip_question": str(meant.get("clip_question") or "").strip()
                         or (f"What is happening? Note {target}." if notes else f"Do you see {target}?"),
        "report": [r for r in (meant.get("report") or []) if str(r).strip()][:3] or [target],
        "alert_when": alert_when,
        "summary_for_alexa": phrase(str(meant.get("summary") or "")) or fallback,
    }}


def compile_order(instruction: str, cameras: list[Camera], ground: Ground,
                  understand: Understand = understand) -> OrderResult:
    """Compile one instruction for the given (online) cameras; ground(target, cameras) finds an object's box."""
    instruction = " ".join(instruction.split())
    if not cameras:
        return {"watch_order": {"clarify": NO_CAMERAS}}
    if not instruction:
        return {"watch_order": {"clarify": UNCLEAR}}
    if is_question(instruction):
        return {"watch_order": {"clarify": QUESTION}}
    return build(instruction, understand(instruction, cameras), cameras, ground)
