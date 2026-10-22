"""The scenario dataset: three cameras, two fictional people (the owner, spoken as "you", and Ravi, a helper) and three
scripted scenarios (object, notes, door), plus what each camera shows right now (look) and saw in
the last few minutes (look-back). Each step is (seconds after the session starts, the event as the core
would write it, the session fields it changes); "{obj}" and "{was}" are filled from the watched thing."""

from __future__ import annotations

CAMERAS = [("desk", "Desk"), ("room", "Room"), ("door", "Door")]
PEOPLE = [
    {"person_id": "owner", "name": "Owner", "role": "owner"},
    {"person_id": "ravi", "name": "Ravi", "role": "helper"},
]

OBJECT = [
    (3, dict(camera_id="room", action="person_seen", actor_id="ravi", summary="Ravi came into the room."), {}),
    (6, dict(camera_id="desk", action="picked_up", actor_id="ravi", alert="heads_up",
             summary="Your {obj} {was} picked up by Ravi."), dict(state="with_person", state_actor="ravi")),
    (16, dict(camera_id="room", action="handed_to", actor_id="ravi", recipient_id="owner", alert="verdict",
              summary="Ravi handed your {obj} back to you. Not taken."), dict(state="with_owner", state_actor="owner")),
]
NOTES = [
    (3, dict(camera_id="room", action="came in, picked up a book", actor_id="ravi",
             summary="Ravi came in and picked up a book."), {}),
    (10, dict(camera_id="room", action="looked in and left", actor_id="unknown",
              summary="Someone I don't recognise looked in and left."), {}),
    (18, dict(camera_id="room", action="left with the book", actor_id="ravi",
              summary="Ravi left the room with the book."), {}),
]
DOOR = [
    (2, dict(camera_id="door", action="left", actor_id="owner", summary="You left the house."), {}),
    (6, dict(camera_id="door", action="locked the front door", actor_id="ravi", thing="the front door",
             state="locked", summary="Ravi locked the front door."), {}),
    (12, dict(camera_id="door", action="tried_door", actor_id="unknown", alert="alert",
              summary="Someone I don't recognise tried to open the front door."), {}),
    (20, dict(camera_id="door", action="left_view", actor_id="unknown",
              summary="They left without getting in. The door stayed locked."), {}),
]
SCENARIOS = {"object": OBJECT, "notes": NOTES, "door": DOOR}
FOR_KIND = {"object": "object", "notes": "notes", "monitor": "door"}
INSTRUCTIONS = {
    "object": "keep an eye on my watch",
    "notes": "take notes of who comes into the room and what they do",
    "door": "keep an eye on the front door",
}
# What each camera shows now: the answer the vision model gives to "do you see me?" and who face ID sees there
LOOKS = {
    "desk": ("Yes, I can see you at the desk.", ["owner"]),
    "room": ("I can see Ravi in the room, but not you.", ["ravi"]),
    "door": ("Nobody is at the front door right now.", []),
}
# What the cameras saw in the last few minutes: the video model's answer, who face ID saw, and the camera it is about
LOOK_BACK = ("In the last 5 minutes, Ravi came into the room and you sat down at the desk.", ["ravi", "owner"], "room")
RECENT_WINDOW_S = 300
