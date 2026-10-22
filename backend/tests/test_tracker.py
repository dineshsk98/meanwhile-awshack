"""The tracker's rules: notes and monitor events, and the object's states: in view or not, heads-up when it leaves
view, verdicts when it is seen again, timers, and who did it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meanwhile.pipeline import tracker

T0 = datetime(2026, 10, 13, 10, 0, 0, tzinfo=timezone.utc)
BOX = [0.4, 0.7, 0.6, 0.9]
KEYBOARD = [0.7, 0.5, 0.95, 0.7]
PEOPLE = {
    "owner": {"person_id": "owner", "name": "Owner", "role": "owner", "note": "a blue shirt"},
    "ravi": {"person_id": "ravi", "name": "Ravi", "role": "helper", "note": "a red shirt"},
}


def at(seconds: float) -> str:
    """A time `seconds` after T0."""
    return (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(seconds * 1000) % 1000:03d}Z"


def session(kind: str = "object", target: str = "watch", alert_when=None) -> dict:
    """A session started at T0 with the given kind of watch order."""
    order = {"kind": kind, "target": target, "cameras": "all", "alert_when": alert_when or []}
    s = {"session_id": "ses_1", "started_at": at(0), "watch_order": order, "focus_camera_id": "desk"}
    if kind == "object":
        s.update(state="on_surface", state_since=at(0), focus_box=BOX)
    return s


class Timeline:
    """Feeds readings to the tracker one at a time, keeping the written events and the session as the analyser would."""

    def __init__(self, s: dict):
        """Start from session s with no events."""
        self.session, self.events = s, []

    def apply(self, decision: tracker.Decision) -> list[dict]:
        """Keep a decision's events, session changes, reference and relabelling; returns the new events."""
        new = [{**e, "ts": e["happened_at"], "camera_id": e.get("camera_id", "desk")} for e in decision["events"]]
        self.events += new
        self.session = {**self.session, **decision["session"]}
        self.new_reference = decision.get("new_reference", False)
        if change := decision.get("relabel"):
            for e in self.events:
                if e.get("episode") == change["episode"]:
                    e.update(tracker.relabel(e, change["actor"], self.session["watch_order"]["target"], PEOPLE))
        return new

    def frame(self, seconds: float, visible: bool = True, held: bool = False, in_place: bool = False,
              box: list | None = None, person: str = "") -> list[dict]:
        """A frame check at T0 + seconds."""
        seen = {"kind": "frame", "camera_id": "desk", "camera_label": "Desk", "happened_at": at(seconds),
                "visible": visible, "held": held, "in_place": in_place, "box": box, "person": person}
        return self.apply(tracker.on_seen(self.session, seen, self.events, PEOPLE))

    def clip(self, *found: tuple, camera: str = "desk", relevant: bool = True) -> list[dict]:
        """A clip answer with events given as (seconds, action, actor, recipient, summary, alert)."""
        events = [{"happened_at": at(s), "action": a, "actor": actor, "recipient": rec, "summary": summary, "alert": alert}
                  for s, a, actor, rec, summary, alert in found]
        seen = {"kind": "clip", "camera_id": camera, "happened_at": at(found[0][0] if found else 0),
                "relevant": relevant, "events": events}
        return self.apply(tracker.on_seen(self.session, seen, self.events, PEOPLE))

    def tick(self, seconds: float) -> list[dict]:
        """The once-a-second timer at T0 + seconds."""
        return self.apply(tracker.on_tick(self.session, at(seconds), self.events, PEOPLE))


def test_notes_become_events_without_alerts():
    """Each relevant clip event is a note with the model's sentence; notes never alert; 'nothing' is skipped."""
    t = Timeline(session("notes"))
    new = t.clip((2, "came into the room", "ravi", "", "Ravi came into the room.", True),
                 (4, "nothing", "", "", "", False), camera="room")
    assert [(e["action"], e["actor_id"], e["alert"], e["summary"]) for e in new] == \
        [("came into the room", "ravi", "none", "Ravi came into the room.")]
    assert t.clip((9, "left", "", "", "", False), camera="room", relevant=False) == []


def test_overlapping_clips_note_once():
    """The same action on the same camera within NOTE_DEDUPE_S (pre-roll and change clips) is noted once."""
    t = Timeline(session("notes"))
    t.clip((2, "came into the room", "ravi", "", "Ravi came into the room.", False), camera="room")
    assert t.clip((5, "Came into the room.", "ravi", "", "Ravi walked in.", False), camera="room") == []
    assert len(t.clip((5, "came into the room", "ravi", "", "Ravi came in.", False), camera="door")) == 1


def test_monitor_alerts_only_on_a_match():
    """A monitor order alerts when the model says the event matches alert_when."""
    t = Timeline(session("monitor", "the cat on the table", ["the cat jumps on the table"]))
    (walk,) = t.clip((1, "walked past", "", "", "The cat walked past the table.", False))
    (jump,) = t.clip((20, "jumped on the table", "", "", "The cat jumped on the table.", True))
    assert walk["alert"] == "none" and jump["alert"] == "alert"


def test_unspeakable_summary_falls_back():
    """A summary saying he/she, or an empty one, is replaced by a plain sentence."""
    t = Timeline(session("notes"))
    (e,) = t.clip((1, "sat down", "ravi", "", "He sat down on the sofa.", False))
    assert e["summary"] == "Ravi sat down."
    (e,) = t.clip((30, "came in", "unknown", "", "", False))
    assert e["summary"] == "Someone came in."


def test_a_pick_up_in_view_is_logged_without_an_alert():
    """Held in view: picked up, no alert; the box follows the object and is shown as held."""
    t = Timeline(session())
    (e,) = t.frame(3, held=True, box=[0.45, 0.5, 0.6, 0.7], person="ravi")
    assert (e["action"], e["alert"], e["actor_id"], e["summary"]) == ("picked_up", "none", "ravi", "Ravi picked up your watch.")
    assert t.session["state"] == "with_person" and t.session["focus_held"] is True
    assert t.session["focus_box"] == [0.45, 0.5, 0.6, 0.7]
    assert t.frame(6, held=True, person="ravi") == [] and t.tick(60) == []


def test_put_back_in_view_is_quiet_and_resets_the_reference():
    """Put back in the same spot without ever leaving view: no alert, on_surface, a new reference frame."""
    t = Timeline(session())
    t.frame(3, held=True, person="ravi")
    (e,) = t.frame(8, in_place=True, box=BOX)
    assert (e["action"], e["alert"], e["summary"]) == ("put_back", "none", "Ravi put your watch back.")
    assert t.session["state"] == "on_surface" and t.session["focus_held"] is False and t.new_reference


def test_set_down_somewhere_else_moves_the_box():
    """Set down elsewhere in view (the keyboard): a quiet 'moved', at rest at the new place with a new box."""
    t = Timeline(session())
    t.frame(3, held=True, person="ravi")
    (e,) = t.frame(9, box=KEYBOARD)
    assert (e["action"], e["alert"], e["summary"]) == ("moved", "none", "Ravi moved your watch to a new spot.")
    assert t.session["state"] == "on_surface" and t.session["focus_box"] == KEYBOARD and t.new_reference
    assert t.tick(100) == []


def test_the_person_is_settled_by_majority():
    """One stray reading names the wrong person; the majority over the episode corrects the events and the state."""
    t = Timeline(session())
    (e,) = t.frame(3, held=True, person="ravi")
    t.frame(5, held=True, person="owner")
    t.frame(7, held=True, person="owner")
    assert t.session["state_actor"] == "owner" and t.session["state"] == "with_owner"
    assert (t.events[0]["actor_id"], t.events[0]["summary"]) == ("owner", "You picked up your watch.")


def test_leaving_view_is_the_heads_up_naming_who_had_it():
    """Missed for LEFT_VIEW_AFTER_S: heads-up dated from the first miss; still tracking at 15 s; taken at 30 s."""
    t = Timeline(session())
    t.frame(3, held=True, person="ravi")
    assert t.frame(6, visible=False, person="ravi") == [] and t.tick(9) == []
    (e,) = t.tick(10)
    assert (e["action"], e["alert"], e["happened_at"], e["summary"]) == \
        ("left_view", "heads_up", at(6), "Ravi carried your watch out of view.")
    assert t.session["in_view"] is False and t.session["focus_held"] is False
    assert t.tick(20) == []
    (still,) = t.tick(21)
    assert (still["action"], still["alert"]) == ("still_tracking", "heads_up")
    assert still["summary"] == "I'm still tracking your watch. It was last seen with Ravi."
    (taken,) = t.tick(36)
    assert (taken["action"], taken["alert"], taken["summary"]) == ("taken", "alert", "Your watch was taken away by Ravi.")


def test_a_moment_out_of_sight_is_not_leaving():
    """Hidden by a hand for one frame, seen again within LEFT_VIEW_AFTER_S: no heads-up."""
    t = Timeline(session())
    t.frame(3, held=True, person="ravi")
    t.frame(5, visible=False)
    t.frame(7, held=True, person="ravi")
    assert t.tick(20) == [] and t.session["in_view"] is True


def test_vanishing_from_its_spot_without_a_held_reading():
    """Gone between frames: once missed for long enough it left view, credited to whoever the frames saw."""
    t = Timeline(session())
    t.frame(2, visible=False, person="ravi")
    (e,) = t.tick(6)
    assert (e["action"], e["alert"], e["actor_id"]) == ("left_view", "heads_up", "ravi")
    assert t.session["state"] == "with_person"


def test_seen_again_is_the_verdict():
    """After leaving view: back in the same spot, in a new spot, or in a hand, each is the green verdict."""
    for reading, action, summary in [
        (dict(in_place=True, box=BOX), "put_back", "Your watch is back where you left it. Not taken."),
        (dict(box=KEYBOARD), "moved", "Your watch is back in view on the Desk camera, in a new spot. Not taken."),
        (dict(held=True, person="ravi"), "back_in_view", "Your watch is back in view on the Desk camera, with Ravi."),
    ]:
        t = Timeline(session())
        t.frame(3, held=True, person="ravi")
        t.frame(6, visible=False)
        t.tick(10)
        (e,) = t.frame(15, **reading)
        assert (e["action"], e["alert"], e["summary"]) == (action, "verdict", summary)
        assert t.session["in_view"] is True and t.tick(100) == []


def test_the_owner_taking_it_out_of_view_is_not_an_alert():
    """The owner picking it up and carrying it off: noted, no alerts, never 'taken'."""
    t = Timeline(session())
    (e,) = t.frame(3, held=True, person="owner")
    assert e["summary"] == "You picked up your watch." and t.session["state"] == "with_owner"
    t.frame(6, visible=False)
    (left,) = t.tick(10)
    assert (left["alert"], left["summary"]) == ("none", "You took your watch out of view.")
    assert t.tick(100) == []


def test_another_camera_brings_it_back():
    """Out of the focus camera's view, a hand-back to the owner on the Room camera is the verdict; focus-camera clips
    never change the state."""
    t = Timeline(session())
    t.frame(3, held=True, person="ravi")
    assert t.clip((4, "left_view", "ravi", "", "", True)) == []
    t.frame(6, visible=False)
    t.tick(10)
    (e,) = t.clip((20, "handed_to", "ravi", "owner", "", True), camera="room")
    assert (e["action"], e["alert"], e["camera_id"]) == ("handed_to", "verdict", "room")
    assert e["summary"] == "Ravi handed your watch back to you. Not taken."
    assert t.session["state"] == "with_owner" and t.tick(100) == []


def test_anything_done_to_the_object_is_told_in_the_clips_words():
    """Watching a lamp: clips from any camera (the focus camera too) that see it turned on and later off each write a
    quiet event in the clip's words with a sentence built from the actor; overlapping clips tell it once; moves,
    'nothing' and irrelevant clips add nothing; the state and timers are untouched."""
    t = Timeline(session(target="lamp"))
    (on,) = t.clip((2, "turned the lamp on", "owner", "", "Someone in blue turned on the lamp.", False),
                   (3, "picked up", "owner", "", "", False), (4, "nothing", "", "", "", False))
    assert (on["action"], on["alert"], on["actor_id"], on["camera_id"], on["object_action"]) == \
        ("turned the lamp on", "none", "owner", "desk", True)
    assert on["summary"] == "You turned your lamp on."
    assert t.clip((5, "Turned the lamp on.", "owner", "", "", False), camera="room") == []
    assert t.clip((30, "turned the lamp off", "owner", "", "", False), relevant=False) == []
    (off,) = t.clip((40, "turned it off", "unknown", "", "", False))
    assert off["summary"] == "Someone I don't recognise turned your lamp off."
    (fell,) = t.clip((50, "fell over", "", "", "", False), camera="room")
    assert (fell["actor_id"], fell["summary"]) == (None, "Your lamp fell over.")
    (dim,) = t.clip((60, "the lamp flickered", "", "", "The lamp flickered twice.", False))
    assert dim["summary"] == "The lamp flickered twice."
    (dark,) = t.clip((80, "turned_off", "", "", "The lamp was turned off.", False))
    assert (dark["action"], dark["summary"]) == ("turned off", "The lamp was turned off.")
    assert [e["action"] for e in t.events] == \
        ["turned the lamp on", "turned it off", "fell over", "the lamp flickered", "turned off"]
    assert t.session["state"] == "on_surface" and t.tick(100) == []
    assert tracker.relabel(off, "ravi", "lamp", PEOPLE)["summary"] == "Ravi turned your lamp off."


def test_carried_away_with_other_actions_keeps_the_verdicts():
    """Ravi picks the lamp up, unplugs it and carries it off: the unplugging is told, and the heads-up, 'still
    tracking' and 'taken' come as before; an action seen on another camera doesn't cancel them."""
    t = Timeline(session(target="lamp"))
    (picked,) = t.frame(3, held=True, person="ravi")
    (unplugged,) = t.clip((4, "unplugged the lamp", "ravi", "", "", False), (4, "picked_up", "ravi", "", "", False))
    assert unplugged["summary"] == "Ravi unplugged your lamp." and picked["action"] == "picked_up"
    t.frame(6, visible=False, person="ravi")
    (left,) = t.tick(10)
    assert (left["action"], left["alert"]) == ("left_view", "heads_up")
    (shaken,) = t.clip((12, "shook the lamp", "ravi", "", "", False), camera="room")
    assert shaken["alert"] == "none" and t.session["in_view"] is False
    (still,) = t.tick(21)
    (taken,) = t.tick(36)
    assert (still["action"], taken["action"], taken["summary"]) == \
        ("still_tracking", "taken", "Your lamp was taken away by Ravi.")
    assert [e["action"] for e in t.events] == \
        ["picked_up", "unplugged the lamp", "left_view", "shook the lamp", "still_tracking", "taken"]


def test_plural_objects_read_naturally():
    """'Your keys are…', 'were…'."""
    t = Timeline(session(target="keys"))
    t.frame(1, held=True, person="ravi")
    t.frame(2, visible=False)
    t.tick(6)
    (e,) = t.tick(40)
    assert e["summary"] == "Your keys were taken away by Ravi."


def test_the_owner_moving_the_object_about_is_quiet():
    """The owner moves the controller about in view, and one reading names the helper.
    Expect quiet events credited to the owner, the box on the keyboard, and no alerts at all."""
    t = Timeline(session(target="controller"))
    t.frame(55, held=True, person="owner")
    t.frame(59, visible=False, person="owner")
    t.frame(61, held=True, person="owner")
    t.frame(70, held=True, person="helper")
    t.frame(73, box=KEYBOARD, person="owner")
    t.frame(86, held=True, person="owner")
    t.frame(93, in_place=False, box=[0.42, 0.72, 0.62, 0.93], person="owner")
    for s in range(94, 160):
        t.tick(s)
    assert all(e["alert"] == "none" for e in t.events)
    assert {e["actor_id"] for e in t.events} == {"owner"}
    assert [e["action"] for e in t.events] == ["picked_up", "moved", "picked_up", "moved"]
    assert t.session["state"] == "on_surface"

def test_overlapping_clips_on_another_camera_tell_back_in_view_once():
    """Two overlapping clips from the Kitchen camera both show the object after it left the focus camera's view:
    one 'back in view' per camera and episode; another camera still tells its own."""
    t = Timeline(session())
    t.frame(3, held=True, person="ravi")
    t.frame(6, visible=False)
    t.tick(10)
    (e,) = t.clip((10, "picked_up", "ravi", "", "", False), camera="kitchen")
    assert (e["action"], e["alert"], e["camera_id"]) == ("back_in_view", "verdict", "kitchen")
    assert t.clip((13, "picked_up", "ravi", "", "", False), camera="kitchen") == []
    assert t.clip((25, "put_back", "ravi", "", "", False), camera="kitchen") == []
    (room,) = t.clip((30, "picked_up", "ravi", "", "", False), camera="room")
    assert room["camera_id"] == "room"


def test_a_pick_up_on_the_focus_camera_does_not_hide_one_on_another():
    """A clip's pick-up on another camera within DEDUPE_S of the focus camera's own pick-up still brings it back."""
    t = Timeline(session())
    t.frame(3, held=True, person="ravi")
    t.frame(5, visible=False)
    t.tick(9)
    (e,) = t.clip((8, "picked_up", "ravi", "", "", False), camera="kitchen")
    assert e["action"] == "back_in_view"


def test_a_sighting_on_another_camera_cancels_the_timers():
    """Seen on the Kitchen camera after leaving the Desk camera's view: no 'still tracking' and no 'taken', even if
    the session still carries left_at."""
    t = Timeline(session())
    t.frame(3, held=True, person="ravi")
    t.frame(6, visible=False)
    t.tick(10)
    t.clip((6, "picked_up", "ravi", "", "", False), camera="kitchen")
    t.session["left_at"] = at(6)
    assert t.tick(22) == [] and t.tick(37) == []
    assert t.session["left_at"] is None and t.session["state"] == "with_person"


def test_timers_run_when_nothing_else_saw_it():
    """Without a sighting since it left, 'still tracking' and 'taken' still come."""
    t = Timeline(session())
    t.frame(3, held=True, person="ravi")
    t.frame(6, visible=False)
    t.tick(10)
    assert [e["action"] for e in t.tick(22) + t.tick(37)] == ["still_tracking", "taken"]
