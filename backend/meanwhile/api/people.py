"""People endpoints: register a person by name, role, what they wear and up to a few photos (stored under the data
dir and, with face ID on, indexed so the tracker can name them by face); list them with the unnamed people face ID
found; name or merge a person (their faces, photos and past events move with them); remove one, or every unnamed
person (their faces too)."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from meanwhile import config
from meanwhile.api import Response, error
from meanwhile.api.cameras import camera_id_for
from meanwhile.pipeline import unnamed
from meanwhile.pipeline.faces import Faces, FaceError
from meanwhile.store import Store

ROLES = ("owner", "family", "helper", "relative", "friend", "guest")
MAX_NAME = 24
MAX_NOTE = 60
MAX_PHOTOS = 3
MAX_PHOTO_BYTES = 15 * 1024 * 1024
HIDDEN = ("face_ids", "photo_keys")


def person_out(store: Store, person: dict[str, Any]) -> dict[str, Any]:
    """A person as the contract returns it, plus `id` (the app's name for person_id), `photo_url` (the first
    photo), `photo_urls`, `faces` (how many faces are indexed for face ID) and `unnamed` (face ID saw them but
    nobody has named them yet)."""
    keys = unnamed.photo_keys(person)
    return {**{k: v for k, v in person.items() if k not in HIDDEN}, "id": person["person_id"],
            "photo_url": store.media_url(person.get("photo_key")), "photo_urls": [store.media_url(k) for k in keys],
            "faces": len(person.get("face_ids") or []), "unnamed": unnamed.is_unnamed(person)}


def list_people(store: Store, faces: Faces | None = None) -> Response:
    """GET /api/people: named people first, then unnamed ones newest first (unnamed people past their days are
    deleted first)."""
    try:
        unnamed.expire(store, faces)
    except FaceError:
        pass
    people = store.list_people()
    named = [p for p in people if not unnamed.is_unnamed(p)]
    found = sorted((p for p in people if unnamed.is_unnamed(p)), key=lambda p: p.get("number", 0), reverse=True)
    return 200, {"people": [person_out(store, p) for p in named + found]}


def check_fields(store: Store, person_id: str, name: str, role: str, note: str) -> Response | None:
    """The error for a bad name, role or note, or a second owner; None when they are fine."""
    if not person_id or len(name) > MAX_NAME or person_id.split("-")[0] == unnamed.PREFIX.rstrip("-"):
        return error(400, "bad_name", f"Please give a name, up to {MAX_NAME} letters.")
    if role not in ROLES:
        return error(400, "bad_role", f"The role must be one of: {', '.join(ROLES)}.")
    if len(note) > MAX_NOTE:
        return error(400, "bad_note", f"Please describe what they wear in up to {MAX_NOTE} letters.")
    if role == "owner" and config.OWNER_ID and person_id != config.OWNER_ID:
        return error(409, "owner_exists", "The owner is set in the core's settings. Pick another role.")
    if role == "owner" and any(p.get("role") == "owner" and p["person_id"] != person_id for p in store.list_people()):
        return error(409, "owner_exists", "Someone is already registered as the owner. Remove them first.")
    return None


def as_jpeg(photo: bytes) -> bytes | None:
    """A photo as JPEG bytes with its longest side at most MEANWHILE_FACE_PHOTO_MAX_SIDE, or None if it isn't
    an image."""
    image = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR) if photo else None
    if image is None:
        return None
    scale = config.FACE_PHOTO_MAX_SIDE / max(image.shape[:2])
    if scale < 1:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()


def add(store: Store, body: dict[str, Any], photos: list[bytes] | None = None, faces: Faces | None = None) -> Response:
    """POST /api/people {"name", "role", "note"} (JSON, or multipart with the same fields and up to MAX_PHOTOS
    `photo` files): register or update a person; note says what they wear ("a red shirt"). Photos replace the
    person's earlier ones; with `faces` (face ID on), each must show a face, which is indexed under the person id.
    There is at most one owner; with MEANWHILE_OWNER_ID set, that person is always the owner and nobody else can be."""
    name = " ".join(str(body.get("name") or "").split())
    role = str(body.get("role") or "family").lower()
    note = " ".join(str(body.get("note") or "").split())
    person_id = camera_id_for(name)
    if config.OWNER_ID and person_id == config.OWNER_ID:
        role = "owner"
    if refused := check_fields(store, person_id, name, role, note):
        return refused
    person: dict[str, Any] = {"person_id": person_id, "name": name, "role": role, "note": note}
    if photos:
        if len(photos) > MAX_PHOTOS:
            return error(400, "bad_photo", f"Please send up to {MAX_PHOTOS} photos.")
        if any(len(p) > MAX_PHOTO_BYTES for p in photos):
            return error(413, "bad_size", "That photo is too big.")
        jpegs = [as_jpeg(p) for p in photos]
        if any(j is None for j in jpegs):
            return error(400, "bad_photo", "Please send photos as JPEG or PNG images.")
        face_ids = []
        if faces is not None:
            try:
                for n, jpeg in enumerate(jpegs, 1):
                    found = faces.index(person_id, jpeg)
                    if not found:
                        faces.delete(face_ids)
                        return error(400, "no_face", f"I couldn't find a face in photo {n}. Please send one where "
                                                     "their face is clear.")
                    face_ids += found
                old = (store.get_person(person_id) or {}).get("face_ids") or []
                faces.delete([f for f in old if f not in face_ids])
            except FaceError:
                return error(503, "face_id_unavailable", "Face ID isn't reachable right now. Please try again.")
        for key in (store.get_person(person_id) or {}).get("photo_keys") or []:
            store.delete_media(key)
        keys = [f"people/{person_id}/{n}.jpg" for n in range(1, len(jpegs) + 1)]
        for key, jpeg in zip(keys, jpegs):
            store.write_media(key, jpeg)
        person.update(photo_key=keys[0], photo_keys=keys, face_ids=face_ids)
    return 200, {"person": person_out(store, store.put_person(person))}


def rename(store: Store, person_id: str, body: dict[str, Any], faces: Faces | None = None) -> Response:
    """PATCH /api/people/{id} {"name", "role", "note"} (each optional, but an unnamed person needs a name): name
    or change a person. A new name gives them the id that name makes, moving their faces, photos and past events
    (409 name_taken if someone already has it: merge instead)."""
    person = store.get_person(person_id)
    if person is None:
        return error(404, "not_found", "No such person.")
    was_unnamed = unnamed.is_unnamed(person)
    if was_unnamed and not str(body.get("name") or "").strip():
        return error(400, "bad_name", "Please give them a name.")
    name = " ".join(str(body.get("name") or person.get("name") or "").split())
    role = str(body.get("role") or ("family" if was_unnamed else person.get("role")) or "family").lower()
    note = " ".join(str(body["note"] if "note" in body else person.get("note") or "").split())
    new_id = camera_id_for(name)
    if config.OWNER_ID and new_id == config.OWNER_ID:
        role = "owner"
    if refused := check_fields(store, new_id, name, role, note):
        return refused
    fields = {"name": name, "role": role, "note": note}
    if new_id == person_id:
        return 200, {"person": person_out(store, store.put_person({**person, **fields}))}
    if store.get_person(new_id) is not None:
        return error(409, "name_taken", f"{name} is already registered. Merge them instead.", into=new_id)
    try:
        moved = unnamed.move(store, faces, person, new_id, fields)
    except FaceError:
        return error(503, "face_id_unavailable", "Face ID isn't reachable right now. Please try again.")
    return 200, {"person": person_out(store, moved), "renamed_from": person_id}


def merge(store: Store, person_id: str, body: dict[str, Any], faces: Faces | None = None) -> Response:
    """POST /api/people/{id}/merge {"into": other id}: the same person seen twice; this one's faces, photos and
    past events go to the other, and this id is removed."""
    person, into = store.get_person(person_id), str(body.get("into") or "")
    target = store.get_person(into)
    if person is None or target is None:
        return error(404, "not_found", "No such person.")
    if into == person_id:
        return error(400, "bad_merge", "Pick two different people to merge.")
    try:
        merged = unnamed.move(store, faces, person, into, {})
    except FaceError:
        return error(503, "face_id_unavailable", "Face ID isn't reachable right now. Please try again.")
    return 200, {"person": person_out(store, merged), "merged": person_id}


def remove(store: Store, person_id: str, faces: Faces | None = None) -> Response:
    """DELETE /api/people/{id}: the person, their photos and every face indexed under their id."""
    person = store.get_person(person_id)
    if person is None:
        return error(404, "not_found", "No such person.")
    try:
        unnamed.delete(store, faces, person)
    except FaceError:
        return error(503, "face_id_unavailable", "Face ID isn't reachable right now, so their face is still "
                                                 "stored. Please try again.")
    return 200, {"removed": person_id}


def remove_unnamed(store: Store, faces: Faces | None = None) -> Response:
    """DELETE /api/people/unnamed: every unnamed person, with their faces and photos."""
    removed = []
    try:
        for person in store.list_people():
            if unnamed.is_unnamed(person):
                unnamed.delete(store, faces, person)
                removed.append(person["person_id"])
    except FaceError:
        return error(503, "face_id_unavailable", "Face ID isn't reachable right now, so some faces are still "
                                                 "stored. Please try again.", removed=removed)
    return 200, {"removed": removed}
