"""Looking through the cameras now: the people in each camera's newest frame named by face, and the vision model's
short spoken answer to the user's question about what the frames show."""

from __future__ import annotations

import re
import time
from typing import Any, TypedDict

from meanwhile import config
from meanwhile.contract import Person
from meanwhile.pipeline import models, naming, tracker, vision
from meanwhile.pipeline.faces import FaceError, Faces
from meanwhile.store import Store


class Frame(TypedDict):
    """One camera's newest frame: the camera, whether it is online, the JPEG, when it was taken and where it came
    from ('live' or 'thumbnail')."""
    camera_id: str
    label: str
    online: bool
    jpeg: bytes
    at: str
    source: str


class Seen(TypedDict):
    """A face in a frame: the person it belongs to (None when nobody recognised it) and its box."""
    person_id: str | None
    box: list[float]


# The look prompt and how people and their places are described to the model.
LOOK_PROMPT = (
    "You are looking through the user's home cameras for them, right now. {photos}\n"
    "People recognised by their faces:\n{people}\n"
    "The user asks: \"{question}\"\n"
    "Answer in one or two short sentences, spoken to the user, from what the photos show.\n"
    "- Only the faces listed above are known. Say \"you\" for a person only in a photo that lists \"you, the "
    "user\"; never assume a person is the user. Anyone else is \"someone\": never say who a person is from their "
    "clothes, hair or body. For example, if the user asks \"do you see me?\" and a person is there but \"you, the "
    "user\" isn't listed, say: \"I can see someone on the {label} camera, but {unknown_reason}, so I can't tell "
    "if it's you.\"\n"
    "- Say what people do and what happens to things. Unless the question itself is about clothing or appearance, "
    "never mention clothing, body, state of dress or appearance (\"shirtless\", \"a red t-shirt\", \"a "
    "necklace\"). When it is (\"what am I wearing?\"), describe what they wear plainly.\n"
    "- Never use he, she, him, her, his or hers.\n"
    "- If the photos don't show enough to answer, say so.\n"
    "- camera: the name of the camera your answer is about, or \"\" if none."
)
PHOTO_LINE = "Photo {n} is from the \"{label}\" camera."
PEOPLE_LINE = "- Photo {n} (\"{label}\"): {people}"
NOBODY = "no face recognised"
FACE_ID_OFF = "- None: face ID is off, so nobody can be recognised by face, even when their face is in full view."
UNSEEN_FACE = "I can't see their face"
NO_FACE_ID = "face ID is off"
OWNER = "you, the user"
STRANGER = "someone whose face I don't recognise"
SIDES = ("on the left", "in the middle", "on the right")
# Words that make a sentence about someone's clothing, body or appearance, and words that make a question ask about it
APPEARANCE = re.compile(
    r"\b(?:shirt|shirts|shirtless|t-shirt|tshirt|tee|vest|topless|bare-chested|barechested|naked|nude|"
    r"undressed|dressed|dress|skirt|pants|trousers|jeans|shorts|jacket|hoodie|sweater|sweatshirt|coat|kurta|saree|"
    r"sari|lungi|necklace|jewelry|jewellery|bracelet|earrings|wearing|wears|wore|worn|outfit|clothes|"
    r"clothing|underwear|barefoot|bald|beard|moustache|mustache|hair|hairstyle|skin|body|belly)\b", re.I)
ASKS_APPEARANCE = re.compile(
    r"\b(?:wear|wearing|wore|worn|dressed|clothes|clothing|outfit|shirt|look like|how do i look|hair)\b", re.I)
LOOK_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "camera": {"type": "string"}},
    "required": ["answer", "camera"],
}


def side(box: list[float]) -> str:
    """Where a box sits across the frame: on the left, in the middle or on the right."""
    centre = (box[0] + box[2]) / 2
    return SIDES[min(int(centre * len(SIDES)), len(SIDES) - 1)]


def face_words(seen: list[Seen], people: dict[str, Person]) -> str:
    """The faces in one frame as the prompt lists them: 'you, the user (in the middle); Ravi (on the left)'."""
    if not seen:
        return NOBODY
    words = []
    for face in seen:
        person_id = face["person_id"]
        if tracker.is_owner(person_id, people):
            name = OWNER
        else:
            name = tracker.who(person_id, people) or STRANGER
        words.append(f"{name} ({side(face['box'])})")
    return "; ".join(words)


def faces_in(faces: Faces | None, store: Store, frame: Frame) -> list[Seen]:
    """The faces in a frame, named by face ID ([] when face ID is off or Rekognition fails)."""
    if faces is None:
        return []
    try:
        return [{"person_id": p, "box": b} for p, b in naming.frame_faces(faces, store, frame["jpeg"],
                                                                         frame["camera_id"])]
    except FaceError as exc:
        print(f"Look:    face ID failed on {frame['camera_id']}, answering without names: {exc}", flush=True)
        return []


def people_lines(frames: list[Frame], seen: list[list[Seen]], people: dict[str, Person], face_id: bool) -> str:
    """The prompt's list of faces recognised per photo, or one line saying face ID is off."""
    if not face_id:
        return FACE_ID_OFF
    return "\n".join(PEOPLE_LINE.format(n=n, label=f["label"], people=face_words(s, people))
                     for n, (f, s) in enumerate(zip(frames, seen), 1))


def prompt(question: str, frames: list[Frame], seen: dict[str, list[Seen]], people: dict[str, Person],
           face_id: bool) -> str:
    """The look prompt for these frames, in order, with the faces recognised in each (or that face ID is off)."""
    photos = " ".join(PHOTO_LINE.format(n=n, label=f["label"]) for n, f in enumerate(frames, 1))
    lines = people_lines(frames, [seen[f["camera_id"]] for f in frames], people, face_id)
    return LOOK_PROMPT.format(photos=photos, people=lines, question=question.strip(), label=frames[0]["label"],
                              unknown_reason=UNSEEN_FACE if face_id else NO_FACE_ID)


def chosen_camera(said: str, frames: list[Frame], seen: dict[str, list[Seen]]) -> Frame:
    """The frame the answer is about: the camera the model named, else the only one, else the first with a
    recognised face, else the first."""
    named = said.strip().lower().removesuffix(" camera").strip()
    for frame in frames:
        if named and frame["label"].lower() == named:
            return frame
    return next((f for f in frames if any(s["person_id"] for s in seen[f["camera_id"]])), frames[0])


def plain(answer: str, question: str) -> tuple[str, int]:
    """The answer without its sentences about clothing, body or appearance, unless the question asks about those;
    returns it and how many sentences were dropped (none when every sentence would go)."""
    if ASKS_APPEARANCE.search(question):
        return answer, 0
    sentences = [s.strip() for s in re.findall(r"[^.!?]+[.!?]?", answer) if s.strip()]
    kept = [s for s in sentences if not APPEARANCE.search(s)]
    if not kept:
        return answer, 0
    return " ".join(kept), len(sentences) - len(kept)


def answer(question: str, frames: list[Frame], faces: Faces | None, store: Store,
           model: str | None = None) -> dict[str, Any]:
    """Name the faces in every frame, then ask the vision model the question about all of them at once. Returns
    the answer, the frame it is about, the faces seen per camera and the seconds each step took; raises
    ModelUnavailable when the model doesn't answer."""
    start = time.perf_counter()
    seen = {f["camera_id"]: faces_in(faces, store, f) for f in frames}
    people = {p["person_id"]: p for p in store.list_people()}
    faces_s = time.perf_counter() - start
    images = [vision.crop(f["jpeg"], [0.0, 0.0, 1.0, 1.0]) or f["jpeg"] for f in frames]
    reply = models.ask([{"role": "user", "content": prompt(question, frames, seen, people, faces is not None),
                         "images": images}],
                       LOOK_SCHEMA, model or config.VISION_MODEL)
    text, dropped = plain(" ".join(str(reply.get("answer") or "").split()), question)
    if not text:
        raise models.ModelUnavailable("The vision model gave an empty answer.")
    return {"answer": text, "dropped": dropped, "frame": chosen_camera(str(reply.get("camera") or ""), frames, seen), "seen": seen,
            "people": people, "took_s": {"faces": round(faces_s, 2),
                                         "model": round(time.perf_counter() - start - faces_s, 2)}}
