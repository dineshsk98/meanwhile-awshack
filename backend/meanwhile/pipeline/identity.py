"""Who is who: registered people described by what they wear, for the models to name, and names mapped back to ids."""

from __future__ import annotations

import re

from meanwhile.contract import Person

UNKNOWN = "unknown"
NOBODY = ""


def owner_of(people: list[Person]) -> Person | None:
    """The registered person whose role is owner, if any."""
    return next((p for p in people if p.get("role") == "owner"), None)


def describe_people(people: list[Person]) -> str:
    """The people list a model reads, each with what they wear as typed:
    'Ravi: a red shirt; Owner [the user, call them "you"]: a blue shirt'."""
    parts = []
    for p in people:
        who = p.get("name") or p["person_id"]
        if p.get("role") == "owner":
            who += ' [the user, call them "you"]'
        note = " ".join(str(p.get("note") or "").split())
        parts.append(f"{who}: {note[0].lower() + note[1:]}" if note else who)
    return "; ".join(parts) or "nobody is known yet, so call everyone \"unknown\""


def person_id_for(name: str | None, people: list[Person]) -> str:
    """The person id a model's answer names: a registered name or id, 'you' for the owner, 'unknown' for anyone
    else, or '' when the answer says nobody is there."""
    said = re.sub(r"[^a-z0-9 ]", "", str(name or "").lower()).strip()
    if said in ("", "none", "nobody", "no one", "no person", "n a", "na"):
        return NOBODY
    if said in ("you", "the user", "user", "owner", "the owner"):
        owner = owner_of(people)
        return owner["person_id"] if owner else UNKNOWN
    for p in people:
        names = {p["person_id"].lower(), str(p.get("name") or "").lower()}
        if said in names or said.split(" ")[0] in names:
            return p["person_id"]
    return UNKNOWN
