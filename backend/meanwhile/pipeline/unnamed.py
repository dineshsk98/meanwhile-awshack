"""People nobody has named: each gets its own id and number; one face ID saw keeps one upper-body photo, so later
sightings match the same person, and one a clip showed without a face keeps what they wore. Also renaming or merging a person into another id (their faces, photos and past events
move with them), deleting people with their faces, and deleting unnamed people after the configured days.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from meanwhile import config
from meanwhile.contract import Person
from meanwhile.pipeline import tracker
from meanwhile.pipeline.faces import Faces
from meanwhile.store import Store, now as now_iso, parse

PREFIX = "unnamed-"
COUNTER = "unnamed_count"
UNNAMED_FIELDS = ("unnamed", "number", "first_seen", "last_seen", "camera_id")
# How an unnamed person is listed; tracker.UNNAMED says how they are spoken of.
LABEL = "Person {n}"


def is_unnamed(person: Person | None) -> bool:
    """Whether this is a person nobody has named."""
    return bool(person and person.get("unnamed"))


def photo_keys(person: Person) -> list[str]:
    """A person's stored photo keys, first photo first."""
    return list(person.get("photo_keys") or ([person["photo_key"]] if person.get("photo_key") else []))


def create(store: Store, faces: Faces, photo: bytes, camera_id: str = "") -> Person | None:
    """A new unnamed person from a JPEG upper-body crop whose face matched nobody: its face is indexed under a new
    id and the crop kept as its photo; None if Rekognition wouldn't index the face (too small or blurred)."""
    person_id = PREFIX + secrets.token_hex(3)
    face_ids = faces.index(person_id, photo)
    if not face_ids:
        return None
    number = next_number(store)
    key = f"people/{person_id}/1.jpg"
    store.write_media(key, photo)
    at = now_iso()
    return store.put_person({"person_id": person_id, "name": LABEL.format(n=number), "role": "", "note": "",
                             "unnamed": True, "number": number, "first_seen": at, "last_seen": at,
                             "camera_id": camera_id, "photo_key": key, "photo_keys": [key], "face_ids": face_ids})


def next_number(store: Store) -> int:
    """The next unnamed person's number."""
    number = int(store.get_meta(COUNTER, 0)) + 1
    store.put_meta(COUNTER, number)
    return number


def create_unseen(store: Store, wears: str, camera_id: str = "") -> Person:
    """A new unnamed person a clip showed without a recognisable face, noted by what they wear (no face, no photo)."""
    person_id = PREFIX + secrets.token_hex(3)
    number, at = next_number(store), now_iso()
    return store.put_person({"person_id": person_id, "name": LABEL.format(n=number), "role": "", "note": wears,
                             "unnamed": True, "number": number, "first_seen": at, "last_seen": at,
                             "camera_id": camera_id, "photo_key": None, "photo_keys": [], "face_ids": []})


def seen(store: Store, person_id: str) -> None:
    """Note that an unnamed person was seen again (their days to deletion count from the last sighting)."""
    person = store.get_person(person_id)
    if is_unnamed(person):
        store.put_person({**person, "last_seen": now_iso()})


def delete(store: Store, faces: Faces | None, person: Person) -> None:
    """Remove a person, their photos and every face indexed under their id (raises FaceError, keeping the
    person, if the faces can't be deleted)."""
    if person.get("face_ids"):
        (faces or Faces()).forget(person["person_id"])
    for key in photo_keys(person):
        store.delete_media(key)
    store.remove_person(person["person_id"])


def expire(store: Store, faces: Faces | None, days: float | None = None, at: str | None = None) -> list[str]:
    """Delete unnamed people last seen more than `days` (default MEANWHILE_FACE_UNNAMED_DAYS) ago; returns their ids."""
    days = config.FACE_UNNAMED_DAYS if days is None else days
    cutoff = parse(at or now_iso()) - timedelta(days=days)
    gone = []
    for person in store.list_people():
        if is_unnamed(person) and parse(person.get("last_seen") or person["first_seen"]) < cutoff:
            delete(store, faces, person)
            gone.append(person["person_id"])
    return gone


def move(store: Store, faces: Faces | None, source: Person, target_id: str, fields: dict[str, Any]) -> Person:
    """Make `source` into the person `target_id` (a rename, or a merge when target_id exists): their photos are
    indexed under the target id (with face ID on) and moved to its folder, their old faces deleted, `fields` (name, role, note)
    set, and past events, session states and votes credited to the target. Raises FaceError if Rekognition fails
    (nothing is moved then, though faces already indexed under the target stay)."""
    source_id = source["person_id"]
    target = store.get_person(target_id)
    before = {p["person_id"]: p for p in store.list_people()}
    kept = photo_keys(target) if target else []
    new_face_ids: list[str] = []
    for key in photo_keys(source) if faces is not None else []:
        new_face_ids += faces.index(target_id, store.media_path(key).read_bytes())
    if source.get("face_ids"):
        (faces or Faces()).forget(source_id)
    moved = []
    for n, key in enumerate(photo_keys(source), len(kept) + 1):
        new_key = f"people/{target_id}/{n}.jpg"
        store.write_media(new_key, store.media_path(key).read_bytes())
        store.delete_media(key)
        moved.append(new_key)
    base = target or {k: v for k, v in source.items() if k not in UNNAMED_FIELDS}
    keys = kept + moved
    person = {**base, **fields, "person_id": target_id, "photo_key": keys[0] if keys else None,
              "photo_keys": keys, "face_ids": list((target or {}).get("face_ids") or []) + new_face_ids}
    store.remove_person(source_id)
    store.put_person(person)
    credit(store, source_id, target_id, before)
    return store.get_person(target_id)


def credit(store: Store, source_id: str, target_id: str, before: dict[str, Person]) -> None:
    """Give a renamed or merged person's past events, session holders and votes to the target id, rewriting how
    events speak of them."""
    after = {p["person_id"]: p for p in store.list_people()}
    old = (tracker.subject(source_id, before), tracker.who(source_id, before) or "")
    new = (tracker.subject(target_id, after), tracker.who(target_id, after) or "")
    for e in store.all_events_newest_first():
        if source_id not in (e.get("actor_id"), e.get("recipient_id")):
            continue
        summary = str(e.get("summary") or "")
        for was, now in zip(old, new):
            if was:
                summary = summary.replace(was, now)
        store.update_event(e["event_id"], summary=summary,
                           **{f: target_id for f in ("actor_id", "recipient_id") if e.get(f) == source_id})
    for s in store.list_sessions(-1):
        fields: dict[str, Any] = {}
        if s.get("state_actor") == source_id:
            fields["state_actor"] = target_id
        if source_id in (s.get("votes") or {}):
            votes = dict(s["votes"])
            votes[target_id] = votes.get(target_id, 0) + votes.pop(source_id)
            fields["votes"] = votes
        if fields:
            store.update_session(s["session_id"], **fields)
