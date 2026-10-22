"""The tracker: turns what the models saw into timeline events, each with a spoken summary and an alert level,
and keeps the object's state (on_surface, with_person, with_owner, taken) and where it is for object watch orders;
anything else a clip shows done to the object becomes an event in its own words. Pure functions: the analyser
passes in the session, the reading and the recent events, and writes the result.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

from meanwhile.contract import Event, Person, Session
from meanwhile.store import seconds_since

UNKNOWN = "unknown"
SIGHTINGS = ("picked_up", "put_back", "moved", "back_in_view", "handed_to")
CLIP_MOVES = ("picked_up", "put_back", "handed_to", "left_view")
MEASURED = SIGHTINGS + ("left_view", "still_tracking", "taken")
OBJECT_FIELDS = ("state", "state_actor", "state_since", "in_view", "missing_since", "left_at", "tracking_said",
                 "votes", "episode", "focus_box", "focus_held", "settling_since")


class SeenEvent(TypedDict, total=False):
    """One thing the video model reported in a clip; actor and recipient are person ids, 'unknown' or ''; what
    each wears; the thing whose state it changed and that new state ('' when none); renamed when the summary is to
    be built from the actor (the model's summary speaks of someone else, or of an unnamed person by a bare label)."""
    happened_at: str
    action: str
    actor: str
    recipient: str
    actor_wears: str
    recipient_wears: str
    thing: str
    state: str
    summary: str
    alert: bool
    renamed: bool


class Seen(TypedDict, total=False):
    """One model reading: a frame check (visible, held, in_place, box, person; settling when the object is seen
    again but not yet known to be moving or at rest; returned when it is back at its step-away spot from
    somewhere else) or a clip answer (relevant, events)."""
    kind: str
    camera_id: str
    camera_label: str
    happened_at: str
    visible: bool
    settling: bool
    returned: bool
    held: bool
    in_place: bool
    box: list[float] | None
    person: str
    relevant: bool
    events: list[SeenEvent]


class Decision(TypedDict, total=False):
    """What to write: new events (without session_id or ts), fields to merge into the session, whether this frame
    becomes the new reference, and a new actor for the current episode's events."""
    events: list[Event]
    session: dict[str, Any]
    new_reference: bool
    relabel: dict[str, str]


# The rules, timings and wording.
DEDUPE_S = 10
NOTE_DEDUPE_S = 10
LEFT_VIEW_AFTER_S = 4
STILL_TRACKING_S = 15
TAKEN_AFTER_S = 30
SETTLE_HOLD_S = 3
SOMEONE = "someone I don't recognise"
UNNAMED = "an unnamed person ({name})"
PRONOUNS = re.compile(r"\b(he|she|him|her|his|hers)\b", re.IGNORECASE)


def be(target: str) -> tuple[str, str]:
    """'was'/'were' and 'is'/'are' for the object: 'Your keys were…', 'Your watch was…'."""
    plural = target.endswith("s") and not target.endswith("ss")
    return ("were", "are") if plural else ("was", "is")


def who(person_id: str | None, people: dict[str, Person]) -> str | None:
    """A person's name, 'you' for the owner, 'an unnamed person (Person 3)' for someone face ID saw but nobody
    has named, or None if they aren't recognised."""
    person = people.get(person_id or "")
    if not person:
        return None
    if person.get("unnamed"):
        return UNNAMED.format(name=person.get("name") or person["person_id"])
    return "you" if person.get("role") == "owner" else person.get("name") or person["person_id"]


def subject(person_id: str | None, people: dict[str, Person]) -> str:
    """A person as the start of a sentence: 'Ravi', 'You', 'An unnamed person (Person 3)' or 'Someone I don't
    recognise'."""
    name = who(person_id, people)
    return name[0].upper() + name[1:] if name else "Someone I don't recognise"


def is_owner(person_id: str | None, people: dict[str, Person]) -> bool:
    """Whether the id is the registered owner's."""
    return (people.get(person_id or "") or {}).get("role") == "owner"


def line(action: str, target: str, actor: str | None, people: dict[str, Person], *, recipient: str | None = None,
         back: bool = False, camera: str = "") -> str:
    """The spoken sentence for an object event; `back` means it had left view and is seen again."""
    was, is_ = be(target)
    name, who_did = who(actor, people), subject(actor, people)
    where = f" on the {camera} camera" if camera else ""
    if action == "picked_up":
        return f"{who_did} picked up your {target}."
    if action == "left_view":
        if name == "you":
            return f"You took your {target} out of view."
        return f"{who_did} carried your {target} out of view."
    if action == "still_tracking":
        return f"I'm still tracking your {target}. It was last seen with {name or SOMEONE}."
    if action == "taken":
        return f"Your {target} {was} taken away by {name}." if name else \
            f"Someone I don't recognise took your {target} away."
    if action == "put_back":
        if back:
            return f"Your {target} {is_} back where you left {'them' if is_ == 'are' else 'it'}. Not taken."
        return f"{who_did} put your {target} back."
    if action == "moved":
        if back:
            return f"Your {target} {is_} back in view{where}, in a new spot. Not taken."
        return f"{who_did} moved your {target} to a new spot."
    if action == "back_in_view":
        return f"Your {target} {is_} back in view{where}, with {name or SOMEONE}."
    if action == "handed_to":
        if is_owner(recipient, people):
            return f"{who_did} handed your {target} back to you. Not taken."
        return f"{who_did} handed your {target} to {who(recipient, people) or SOMEONE}."
    return did(action, target, actor, people) or f"{who_did} {action.replace('_', ' ')} your {target}."


def did(action: str, target: str, actor: str | None, people: dict[str, Person]) -> str | None:
    """The sentence for something done to the object, in the clip's words with the object as 'your <target>':
    'You turned your lamp on.'; with nobody involved, 'Your lamp fell over.', or None when the words don't fit."""
    words = re.sub(rf"\b(the|my|your|a|an)\s+{re.escape(target)}\b", f"your {target}", action)
    if f"your {target}" not in words:
        words = re.sub(r"\b(it|them)\b", f"your {target}", words, count=1)
    if actor:
        if f"your {target}" not in words:
            words = f"{words} your {target}"
        return f"{subject(actor, people)} {words}."
    if f"your {target}" not in words:
        return f"Your {target} {words}."
    return words[0].upper() + words[1:] + "." if words.startswith(f"your {target}") else None


def speakable(summary: str) -> str | None:
    """The model's summary as one spoken sentence, or None if it is empty or says he/she."""
    text = " ".join(str(summary or "").split())
    text = re.split(r"(?<=[.!?])\s", text)[0] if text else ""
    if not text or PRONOUNS.search(text):
        return None
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?" else text + "."


def plain(action: Any) -> str:
    """A clip answer's action as stored: lowercase words, at most 60 characters, without a full stop or
    underscores ('turned_off' -> 'turned off'), except that the tracker's own names keep theirs ('picked up' ->
    'picked_up')."""
    words = " ".join(str(action or "").lower().replace("_", " ").split())[:60].rstrip(".")
    return words.replace(" ", "_") if words.replace(" ", "_") in MEASURED else words


def note_fallback(action: str, actor: str | None, people: dict[str, Person]) -> str:
    """A plain note sentence when the model's summary can't be spoken as it is."""
    name = who(actor, people)
    name = name[0].upper() + name[1:] if name else "Someone"
    return f"{name} {action}."


def majority(votes: dict[str, int]) -> str | None:
    """The person seen most often in this episode (a named person wins a tie with 'unknown'), or None."""
    if not votes:
        return None
    return max(votes, key=lambda p: (votes[p], p != UNKNOWN))


def near(events: list[Event], action: str, at: str, within_s: float, camera_id: str | None = None) -> bool:
    """Whether an event with this action (and camera, if given) happened within within_s of `at`."""
    for e in events:
        if e.get("action") == action and (camera_id is None or e.get("camera_id") == camera_id):
            if abs(seconds_since(e.get("happened_at") or e["ts"], at)) <= within_s:
                return True
    return False


def on_seen(session: Session, seen: Seen, recent: list[Event], people: dict[str, Person]) -> Decision:
    """Turn one reading into events and state changes for the session's watch order."""
    if session["watch_order"].get("kind") == "object":
        if seen["kind"] == "frame":
            return object_frame(session, seen, people)
        return object_clip(session, seen, recent, people)
    return general_seen(session, seen, recent, people)


def object_frame(session: Session, seen: Seen, people: dict[str, Person]) -> Decision:
    """The focus camera's frame check decides where the object is. In view: a pick-up, a put-back or a move is
    logged without an alert; once it had left view, being seen again is the verdict. Out of view: remember when it
    was first missed (on_tick decides it has left). The person is settled by majority over the episode."""
    target = session["watch_order"].get("target", "object")
    st = {k: session.get(k) for k in OBJECT_FIELDS}
    st["state"] = st["state"] or "on_surface"
    st["votes"] = dict(st["votes"] or {})
    if st["in_view"] is None:
        st["in_view"] = True
    at, label = seen["happened_at"], seen.get("camera_label", "")
    events: list[Event] = []
    decision: Decision = {"events": events, "session": {}}
    if seen.get("person") and (st["state"] != "on_surface" or not seen.get("visible")):
        st["votes"][seen["person"]] = st["votes"].get(seen["person"], 0) + 1

    def event(action: str, alert: str, actor: str, **extra: Any) -> None:
        """Add an object event for this episode."""
        events.append({"action": action, "alert": alert, "actor_id": actor, "camera_id": seen["camera_id"],
                       "happened_at": at, "episode": st["episode"], **extra,
                       "summary": line(action, target, actor, people, recipient=extra.get("recipient_id"),
                                       back=back, camera=label)})

    back = st["in_view"] is False
    if seen.get("visible") and seen.get("settling"):
        if back:
            st["settling_since"] = at
        else:
            st.update(in_view=True, missing_since=None)
    elif seen.get("visible"):
        st.update(in_view=True, missing_since=None, settling_since=None)
        holder = st["state_actor"] or seen.get("person") or UNKNOWN
        if seen.get("held"):
            st.update(focus_box=seen.get("box") or st["focus_box"], focus_held=True)
            if st["state"] == "on_surface":
                actor = seen.get("person") or UNKNOWN
                st.update(state="with_owner" if is_owner(actor, people) else "with_person", state_actor=actor,
                          state_since=at, episode=at, votes={actor: 1}, tracking_said=False, left_at=None)
                event("picked_up", "none", actor)
            elif back:
                st.update(left_at=None, tracking_said=False, state="with_owner" if is_owner(holder, people)
                          else "with_person")
                event("back_in_view", "none" if is_owner(holder, people) else "verdict", holder)
        else:
            moved = not seen.get("in_place") and seen.get("box")
            if st["state"] != "on_surface" or moved or back or seen.get("returned"):
                action = "moved" if moved else "put_back"
                alert = "verdict" if back and not is_owner(holder, people) else "none"
                event(action, alert, holder if st["state"] != "on_surface" else seen.get("person") or UNKNOWN)
                st.update(state="on_surface", state_actor=None, state_since=at, left_at=None, tracking_said=False,
                          votes={}, focus_held=False, focus_box=seen.get("box") or st["focus_box"])
                decision["new_reference"] = True
            elif st["focus_held"]:
                st["focus_held"] = False
    else:
        st["settling_since"] = None
        if st["in_view"] and not st["missing_since"]:
            st["missing_since"] = at
    if st["state"] in ("with_person", "with_owner"):
        leader = majority(st["votes"])
        if leader and leader != st["state_actor"]:
            st.update(state_actor=leader, state="with_owner" if is_owner(leader, people) else "with_person")
            decision["relabel"] = {"episode": st["episode"], "actor": leader}
    decision["session"] = {k: v for k, v in st.items() if v != session.get(k)}
    return decision


def told_in_episode(recent: list[Event], kind: str, camera_id: str, episode: str | None) -> bool:
    """Whether this camera already told this kind of event in this episode."""
    return any(e.get("action") == kind and e.get("camera_id") == camera_id and e.get("episode") == episode
               for e in recent)


def object_clip(session: Session, seen: Seen, recent: list[Event], people: dict[str, Person]) -> Decision:
    """A clip adds what it saw done to the object besides the tracker's moves (object_actions), and may bring the
    object back once it has left the focus camera's view (clip_sighting)."""
    acts = object_actions(session, seen, recent, people)
    back = clip_sighting(session, seen, recent, people)
    return {"events": acts + back["events"], "session": back["session"]}


def object_actions(session: Session, seen: Seen, recent: list[Event], people: dict[str, Person]) -> list[Event]:
    """Each thing a relevant clip from any watched camera saw done to the object, other than what the tracker
    measures itself, as an event in the clip's words (told once across cameras within NOTE_DEDUPE_S). The actor is
    the person the clip answer names; the summary is built from that actor, so naming them later rebuilds it."""
    if not seen.get("relevant"):
        return []
    target = session["watch_order"].get("target", "object")
    events: list[Event] = []
    for found in sorted(seen.get("events") or [], key=lambda e: e["happened_at"]):
        action = plain(found.get("action"))
        if not action or action == "nothing" or action in MEASURED:
            continue
        if near(recent + events, action, found["happened_at"], NOTE_DEDUPE_S):
            continue
        actor = found.get("actor") or ""
        summary = did(action, target, actor, people) if actor else None
        events.append({
            "action": action, "object_action": True, "alert": "none", "actor_id": actor or None,
            "recipient_id": found.get("recipient") or None, "camera_id": seen["camera_id"],
            "happened_at": found["happened_at"], **changed_state(found),
            "summary": summary or speakable(found.get("summary", "")) or did(action, target, "", people)
                       or f"Your {target}: {action}.",
        })
    return events


def clip_sighting(session: Session, seen: Seen, recent: list[Event], people: dict[str, Person]) -> Decision:
    """Moves in clips from the focus camera add nothing (its frames decide there). Clips from other cameras bring
    the object back once it has left the focus camera's view: handed back to the owner is the verdict, and any
    other sighting is 'back in view', told once per camera and episode."""
    if seen["camera_id"] == session.get("focus_camera_id") or session.get("in_view") is not False:
        return {"events": [], "session": {}}
    target = session["watch_order"].get("target", "object")
    holder = session.get("state_actor") or UNKNOWN
    for found in sorted(seen.get("events") or [], key=lambda e: e["happened_at"]):
        action = plain(found.get("action"))
        if action not in ("picked_up", "put_back", "handed_to") or near(recent, action, found["happened_at"], DEDUPE_S,
                                                                         seen["camera_id"]):
            continue
        actor = found.get("actor") if found.get("actor") not in ("", None, UNKNOWN) else holder
        recipient = found.get("recipient") or ""
        if action == "handed_to" and is_owner(recipient, people):
            state, alert, kind = "with_owner", "verdict", "handed_to"
        else:
            state, alert, kind = "with_person", "verdict", "back_in_view"
        if told_in_episode(recent, kind, seen["camera_id"], session.get("episode")):
            continue
        summary = line(kind, target, actor, people, recipient=recipient, camera=seen.get("camera_label", ""))
        return {"events": [{"action": kind, "alert": alert, "actor_id": actor, "recipient_id": recipient or None,
                            "camera_id": seen["camera_id"], "happened_at": found["happened_at"],
                            "episode": session.get("episode"), "summary": summary}],
                "session": {"state": state, "state_actor": recipient if state == "with_owner" else actor,
                            "left_at": None, "tracking_said": False}}
    return {"events": [], "session": {}}


def relabel(event: Event, actor: str, target: str, people: dict[str, Person], camera: str = "") -> dict[str, Any]:
    """New actor and summary for an episode event, once the majority says someone else did it."""
    return {"actor_id": actor, "summary": line(event["action"], target, actor, people,
                                               recipient=event.get("recipient_id"),
                                               back=event.get("alert") == "verdict", camera=camera)}


def changed_state(found: SeenEvent) -> dict[str, str]:
    """The thing a clip event left in a new state and that state, as event fields; {} when it changed none."""
    thing, state = " ".join(str(found.get("thing") or "").split()), " ".join(str(found.get("state") or "").split())
    return {"thing": thing, "state": state.lower()} if thing and state else {}


def general_seen(session: Session, seen: Seen, recent: list[Event], people: dict[str, Person]) -> Decision:
    """Every other order: each relevant clip event becomes a note; monitor orders alert when the model says the
    event matches alert_when; notes never alert."""
    kind = session["watch_order"].get("kind")
    events: list[Event] = []
    if seen["kind"] != "clip" or not seen.get("relevant"):
        return {"events": [], "session": {}}
    for found in sorted(seen.get("events") or [], key=lambda e: e["happened_at"]):
        action = plain(found.get("action"))
        if not action or action == "nothing":
            continue
        if near(recent + events, action, found["happened_at"], NOTE_DEDUPE_S, seen["camera_id"]):
            continue
        actor = found.get("actor") or ""
        summary = note_fallback(action, actor, people) if found.get("renamed") else speakable(found.get("summary", ""))
        events.append({
            "action": action, "actor_id": actor or UNKNOWN, "recipient_id": found.get("recipient") or None,
            "summary": summary or note_fallback(action, actor, people),
            "alert": "alert" if kind == "monitor" and found.get("alert") else "none",
            "camera_id": seen["camera_id"], "happened_at": found["happened_at"], **changed_state(found),
        })
    return {"events": events, "session": {}}


def seen_since(session: Session, recent: list[Event], left: str) -> bool:
    """Whether a camera has told a sighting of the object since it left the focus camera's view: one that happened
    at or after `left`, or any from another camera in this episode (a clip's times are only to the second)."""
    return any(e.get("action") in SIGHTINGS and ((e.get("happened_at") or e["ts"]) >= left or (
        e.get("camera_id") != session.get("focus_camera_id") and e.get("episode") == session.get("episode")))
        for e in recent)


def on_tick(session: Session, now: str, recent: list[Event], people: dict[str, Person]) -> Decision:
    """Timers for object orders: missed for LEFT_VIEW_AFTER_S means it left view (a heads-up naming who had it,
    none for the owner); then 'still tracking' once after STILL_TRACKING_S and 'taken' after TAKEN_AFTER_S,
    both held back for SETTLE_HOLD_S while a new sighting is settling, and cancelled by a sighting on any camera
    since it left."""
    nothing: Decision = {"events": [], "session": {}}
    if session["watch_order"].get("kind") != "object":
        return nothing
    target = session["watch_order"].get("target", "object")
    camera = session.get("focus_camera_id", "")
    holder = session.get("state_actor") or majority(session.get("votes") or {}) or UNKNOWN
    missing = session.get("missing_since")
    if session.get("in_view") is not False and missing and seconds_since(missing, now) >= LEFT_VIEW_AFTER_S:
        changes: dict[str, Any] = {"in_view": False, "missing_since": None, "left_at": missing, "tracking_said": False,
                                   "focus_held": False}
        if session.get("state") in (None, "on_surface"):
            changes.update(state="with_owner" if is_owner(holder, people) else "with_person", state_actor=holder,
                           state_since=missing, episode=missing, votes={})
        owner = is_owner(holder, people)
        event = {"action": "left_view", "alert": "none" if owner else "heads_up", "actor_id": holder,
                 "camera_id": camera, "happened_at": missing, "episode": changes.get("episode", session.get("episode")),
                 "summary": line("left_view", target, holder, people)}
        return {"events": [event], "session": changes}
    left = session.get("left_at")
    if session.get("in_view") is not False or not left or session.get("state") != "with_person":
        return nothing
    if seen_since(session, recent, left):
        return {"events": [], "session": {"left_at": None, "tracking_said": False}}
    settling = session.get("settling_since")
    if settling and seconds_since(settling, now) < SETTLE_HOLD_S:
        return nothing
    if seconds_since(left, now) >= TAKEN_AFTER_S:
        return {"events": [{"action": "taken", "actor_id": holder, "alert": "alert", "happened_at": now,
                            "camera_id": camera, "episode": session.get("episode"),
                            "summary": line("taken", target, holder, people)}],
                "session": {"state": "taken", "state_since": now}}
    if not session.get("tracking_said") and seconds_since(left, now) >= STILL_TRACKING_S:
        return {"events": [{"action": "still_tracking", "actor_id": holder, "alert": "heads_up", "happened_at": now,
                            "camera_id": camera, "episode": session.get("episode"),
                            "summary": line("still_tracking", target, holder, people)}],
                "session": {"tracking_said": True}}
    return nothing
