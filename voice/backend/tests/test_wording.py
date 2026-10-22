"""What the spoken replies must do."""

from __future__ import annotations

from meanwhile.voice import wording

SESSION = {
    "session_id": "ses_1", "started_at": "2026-10-10T09:30:00.000Z", "ended_at": None,
    "instruction": "keep an eye on my watch", "state": "with_owner",
    "watch_order": {"kind": "object", "summary_for_alexa": "keeping an eye on your watch on the Desk camera"},
}
EVENTS = [
    {"event_id": "evt_1", "ts": "2026-10-10T09:32:00.000Z", "camera_id": "desk", "alert": "heads_up",
     "summary": "Your watch was picked up by Ravi."},
    {"event_id": "evt_2", "ts": "2026-10-10T09:33:00.000Z", "camera_id": "room", "alert": "verdict",
     "summary": "Ravi handed your watch back to you. Not taken."},
]


def said(fn, *args, **kwargs) -> str:
    """Call a wording function and check it returns a non-empty sentence."""
    text = fn(*args, **kwargs)
    assert isinstance(text, str) and text.strip()
    return text


def test_descriptions_are_written():
    """The server instructions and all eight tool descriptions are filled in."""
    assert wording.SERVER_INSTRUCTIONS.strip() and all(d.strip() for d in wording.TOOL_DESCRIPTIONS.values())
    assert set(wording.TOOL_DESCRIPTIONS) == {"step_away", "what_did_i_miss", "show_me", "im_back", "status",
                                                "ask_state", "look", "look_back"}


def test_step_away_reads_back_what_it_understood():
    """step_away replies for watching, clarify, not found and already watching."""
    text = said(wording.say_step_away, "watching", summary_for_alexa="keeping an eye on your watch on the Desk camera")
    assert text == "Sure. I'm keeping an eye on your watch on the Desk camera."
    assert said(wording.say_step_away, "watching", summary_for_alexa="Watching the front door.") == "Sure. I'm watching the front door."
    assert "watch" in text.lower() and "desk" in text.lower()
    assert "watch what" in said(wording.say_step_away, "clarify", question="Watch what?").lower()
    assert "umbrella" in said(wording.say_step_away, "not_found", not_found="I can't see your umbrella on Desk.")
    busy = said(wording.say_step_away, "already_watching", summary_for_alexa="watching the front door",
                current_instruction="Did I lock the door when leaving?")
    assert busy.startswith("I'm still watching the front door.") and "?." not in busy and "Did I lock" not in busy
    bare = said(wording.say_step_away, "already_watching", current_instruction="watch my keys?")
    assert "?." not in bare


def test_spoken_time_uses_the_home_time_zone_not_the_servers():
    """A UTC contract time is spoken in MEANWHILE_TIME_ZONE (Asia/Kolkata in .env.example), whatever the server's zone."""
    assert wording.spoken_time("2026-10-21T13:20:23.981Z") == "6:50 pm"
    assert wording.spoken_time("2026-10-21T00:05:00.000Z") == "5:35 am"


def test_catch_up_reads_events_in_order_with_times():
    """Catch-up reads events in order with their times."""
    text = said(wording.say_catch_up, SESSION, EVENTS, [], detail=True)
    first, second = text.find("picked up by Ravi"), text.find("handed your watch back")
    assert 0 <= first < second, "both summaries, in order"
    assert wording.spoken_time(EVENTS[0]["ts"]) in text


def test_catch_up_when_nothing_happened_or_nothing_watched():
    """Nothing happened differs from nothing watched."""
    assert said(wording.say_catch_up, SESSION, [], []) != said(wording.say_catch_up, None, [], [])


def test_long_catch_up_keeps_every_alert_and_stays_short():
    """A long catch-up keeps every alert and reads at most MAX_READ events."""
    notes = [{"event_id": f"n{i}", "ts": f"2026-10-10T09:{10 + i:02d}:00.000Z", "camera_id": "room",
              "alert": "none", "summary": f"Note {i}."} for i in range(10)]
    events = sorted(notes + EVENTS, key=lambda e: e["ts"])
    text = said(wording.say_catch_up, SESSION, events, [], detail=True)
    assert "picked up by Ravi" in text and "handed your watch back" in text
    assert text.count("At ") <= wording.MAX_READ
    assert "Note 0." not in text and "Note 9." in text


def test_catch_up_mentions_offline_cameras():
    """Catch-up names offline cameras."""
    assert "Room" in said(wording.say_catch_up, SESSION, EVENTS, ["Room"])


def test_show_me_im_back_and_status():
    """show_me, im_back and status replies."""
    assert said(wording.say_show_me, EVENTS[:1])
    assert said(wording.say_show_me, []) != said(wording.say_show_me, EVENTS[:1])
    assert said(wording.say_im_back, SESSION, EVENTS)
    assert "Door" in said(wording.say_status, SESSION, ["Desk", "Room"], ["Door"])


def test_banner_only_for_alerts_and_speaks_the_summary():
    """Each alert kind has its own tone; the spoken line is the event's summary; ordinary events get none."""
    assert wording.banner(EVENTS[0]) == {"tone": "amber", "title": "Heads-up", "say": EVENTS[0]["summary"]}
    assert wording.banner(EVENTS[1])["tone"] == "green"
    assert wording.banner({**EVENTS[0], "alert": "alert"})["tone"] == "red"
    assert wording.banner({**EVENTS[0], "alert": "none"}) is None


def test_times_use_happened_at_when_the_core_sends_it():
    """A late-analysed event is spoken at the time it happened, not when it was written."""
    late = {**EVENTS[0], "ts": "2026-10-10T09:40:00.000Z", "happened_at": "2026-10-10T09:32:00.000Z"}
    assert wording.happened_at(late) == "2026-10-10T09:32:00.000Z"
    assert wording.happened_at(EVENTS[0]) == EVENTS[0]["ts"]
    catch_up = said(wording.say_catch_up, SESSION, [late], [], detail=True)
    assert wording.spoken_time("2026-10-10T09:32:00.000Z") in catch_up
    assert wording.spoken_time("2026-10-10T09:40:00.000Z") not in catch_up


def test_card_agrees_with_plural_things():
    """'Your keys are back with you', 'Your watch is back with you'."""
    keys = {**SESSION, "focus_object": "keys", "state": "with_owner"}
    assert wording.screen_card(keys, True, EVENTS, {})["state"] == "Your keys are back with you."
    glasses = {**SESSION, "focus_object": "glasses", "state": "on_surface", "focus_camera_id": "desk"}
    assert wording.screen_card(glasses, True, [], {"desk": "Desk"})["state"] ==         "Your glasses are where you left them, on the Desk."
    assert wording.verb_for("watch", "was") == "was" and wording.verb_for("car keys", "was") == "were"
    assert wording.verb_for("glass", "is") == "is"


def test_card_names_the_holder():
    """The card's state line names a stranger holding the object; an order without an object has none."""
    with_stranger = {**SESSION, "focus_object": "watch", "state": "with_person", "state_actor": "unknown"}
    card = wording.screen_card(with_stranger, True, EVENTS, {"desk": "Desk"})
    assert card["state"] == "Your watch is with someone I don't recognise."
    door_watch = {**SESSION, "watch_order": {"kind": "monitor", "summary_for_alexa": "watching the front door"}}
    assert wording.screen_card(door_watch, True, [], {})["state"] is None


def _ev(action, actor="ravi", name="Ravi", role="helper", alert="none", summary="", ts="2026-10-15T04:26:10.000Z"):
    """An object event as the core writes it."""
    return {"ts": ts, "camera_id": "desk", "action": action, "actor_id": actor, "actor_name": name, "actor_role": role,
            "alert": alert, "summary": summary or f"{name} {action}."}


OWNER = dict(actor="owner", name="Owner", role="owner")


def _object_session(state, actor=None, obj="controller"):
    """An object session in a given state."""
    return {**SESSION, "focus_object": obj, "state": state, "state_actor": actor,
            "watch_order": {"kind": "object", "target": obj}}


def test_brief_catch_up_when_a_helper_still_has_it():
    """Owner handling, then a helper pick-up and still tracking: one line on where it is, one on who, an offer."""
    events = [_ev("picked_up", **OWNER), _ev("put_back", **OWNER),
              _ev("picked_up", "helper", "helper", alert="heads_up", ts="2026-10-15T04:26:10.720Z"),
              _ev("still_tracking", "helper", "helper", alert="heads_up")]
    text = said(wording.say_catch_up, _object_session("with_person", "helper"), events, [])
    assert text == (f"Your controller is with helper right now. Helper picked it up at "
                    f"{wording.spoken_time('2026-10-15T04:26:10.720Z')}. Want the details?")


def test_brief_catch_up_after_a_hand_back():
    """A helper hand-back: it's back with you, who handled it, an offer of details."""
    events = [_ev("person_seen"), _ev("picked_up", alert="heads_up"), _ev("handed_to", alert="verdict")]
    assert said(wording.say_catch_up, _object_session("with_owner", "owner", "watch"), events, []) == \
        "Your watch is back with you. Ravi picked it up and handed it back to you. Want the details?"


def test_the_owners_own_handling_is_not_news():
    """Only the owner moved it: one sentence, no offer of details; nobody touched it: says so."""
    owner_only = [_ev("picked_up", **OWNER), _ev("put_back", **OWNER)]
    assert said(wording.say_catch_up, _object_session("on_surface", "owner"), owner_only, []) == \
        "Your controller is still where you left it; only you moved it."
    seen_only = [_ev("person_seen")]
    assert said(wording.say_catch_up, _object_session("on_surface"), seen_only, []).startswith(
        "Nobody touched your controller. It is still where you left it.")


def test_brief_catch_up_copes_with_new_and_unknown_actions():
    """moved, left_view and taken lead the answer; an unknown action never crashes and still offers details."""
    moved = said(wording.say_catch_up, _object_session("unknown"), [_ev("moved", alert="heads_up")], [])
    assert moved.startswith("Your controller was moved by Ravi.") and moved.endswith("Want the details?")
    gone = said(wording.say_catch_up, _object_session("unknown"), [_ev("left_view", alert="heads_up")], [])
    assert gone.startswith("Your controller went out of view with Ravi.")
    taken = said(wording.say_catch_up, _object_session("taken", "unknown"), [_ev("taken", "unknown", None, None, "alert")], [])
    assert taken.startswith("I'm afraid your controller was taken.")
    odd = said(wording.say_catch_up, _object_session("unknown"), [_ev("juggled")], [])
    assert odd.startswith("I'm not sure where your controller is right now.") and odd.endswith("Want the details?")
    keys = said(wording.say_catch_up, _object_session("on_surface", obj="keys"), [_ev("picked_up"), _ev("put_back")], [])
    assert keys.startswith("Your keys are back where you left them. Ravi picked them up and put them back.")


def _act(summary, ts, actor="owner", name="Owner", role="owner"):
    """An open action on the watched thing as the core writes it."""
    return {**_ev("turned", actor, name, role, summary=summary, ts=ts), "object_action": True}


def test_brief_catch_up_reads_what_was_done_to_the_thing_in_time_order():
    """Watching a lamp: it stayed put but was turned on, then off; both are read with their times, and nothing is
    left over to offer."""
    on, off = "2026-10-21T09:32:00.000Z", "2026-10-21T09:35:00.000Z"
    events = [_act("You turned your lamp on.", on), _act("You turned your lamp off.", off)]
    text = said(wording.say_catch_up, _object_session("on_surface", obj="lamp"), events, [])
    assert text == (f"Your lamp is still where you left it. At {wording.spoken_time(on)}, you turned your lamp on, "
                    f"then at {wording.spoken_time(off)}, you turned your lamp off.")
    same = said(wording.say_catch_up, _object_session("on_surface", obj="lamp"),
                [_act("You turned your lamp on.", on), _act("Your lamp fell over.", on, None, None, None)], [])
    assert same.endswith(f"At {wording.spoken_time(on)}, you turned your lamp on, then your lamp fell over.")


def test_brief_catch_up_when_the_thing_was_used_and_carried_away():
    """Ravi unplugs the lamp and carries it off: the verdict leads, then what Ravi did to it, then the moves."""
    at = "2026-10-21T09:32:00.000Z"
    events = [_ev("picked_up", ts=at), _act("Ravi unplugged your lamp.", at, "ravi", "Ravi", "helper"),
              _ev("left_view", alert="heads_up", ts=at), _ev("taken", alert="alert", ts=at)]
    text = said(wording.say_catch_up, _object_session("taken", "ravi", "lamp"), events, [])
    assert text == (f"I'm afraid your lamp was taken by Ravi. At {wording.spoken_time(at)}, Ravi unplugged your lamp. "
                    "Ravi picked it up, took it out of view and took it away. Want the details?")


def test_brief_catch_up_names_offline_cameras_in_one_extra_sentence():
    """An offline camera joins the 'what mattered' sentence instead of adding another."""
    events = [_ev("picked_up", alert="heads_up"), _ev("handed_to", alert="verdict")]
    text = said(wording.say_catch_up, _object_session("with_owner", "owner", "watch"), events, ["Room"])
    assert text == ("Your watch is back with you. Ravi picked it up and handed it back to you; also, the Room camera "
                    "is offline, so I might have missed something there. Want the details?")


def test_brief_notes_lead_with_a_count_and_cap_the_list():
    """Notes: a one-line count, at most three things, and how many more."""
    notes = {**SESSION, "watch_order": {"kind": "notes"}}
    events = [_ev("person_seen", summary=f"Visitor {i} came in.") for i in range(5)]
    text = said(wording.say_catch_up, notes, events, [])
    assert text.startswith("I noted five things while you were away. Visitor 2 came in.")
    assert "Visitor 0" not in text and text.endswith("There are two more things. Want the details?")


def test_look_answers_as_the_core_said_and_flags_an_offline_camera():
    """An online camera's answer is spoken as given; an offline one's starts with its last picture's time; a failed
    look speaks the core's sentence, else an apology."""
    assert wording.say_look("Yes, I can see you.", "Desk", True, "2026-10-10T09:32:00.000Z") == "Yes, I can see you."
    offline = wording.say_look("The lamp is off.", "Room", False, "2026-10-10T09:32:00.000Z")
    assert offline == (f"The Room camera is offline, so this is from its last picture, at "
                       f"{wording.spoken_time('2026-10-10T09:32:00.000Z')}. The lamp is off.")
    assert wording.say_look_failed("I don't have a garage camera.") == "I don't have a garage camera."
    assert wording.say_look_failed("").startswith("Sorry")


def test_look_back_always_says_how_far_back_it_covers():
    """The answer keeps its own "In the last 5 minutes"; one without it gets it added (a leading "You" lowercased, a
    name kept); a question about earlier first says it can only look back that far; the catch-up fallback says it
    wasn't watching."""
    answer = "In the last 5 minutes, you sat down at the desk."
    assert wording.say_look_back(answer, "5 minutes", False) == answer
    assert wording.say_look_back("You sat down.", "5 minutes", False) == "In the last 5 minutes, you sat down."
    assert wording.say_look_back("Ravi came in.", "minute", False) == "In the last minute, Ravi came in."
    assert wording.say_look_back(answer, "5 minutes", True) == f"I can only look back over the last 5 minutes. {answer}"
    assert wording.say_catch_up_recent(answer, "5 minutes", False).endswith(answer)


def test_state_answers_say_yes_only_for_the_state_asked_about():
    """'Is it locked?' with locked says yes; with unlocked it states the state; the time and who follow; 'my' is
    spoken as 'your'."""
    at = "2026-10-10T09:20:00.000Z"
    when = wording.spoken_time(at)
    assert wording.say_state("is the front door locked?", "the front door", "locked", at, "Ravi") == \
        f"Yes, the front door is locked. The last change I saw was at {when}, by Ravi."
    assert wording.say_state("is the front door locked?", "front door", "unlocked", at, None) == \
        f"The front door is unlocked. The last change I saw was at {when}."
    assert wording.say_state("is my lamp on?", "my lamp", "on", at, "you").startswith("Yes, your lamp is on.")


def test_a_video_checked_status_says_when_the_camera_was_checked():
    """A status stored from a video check says when the camera was checked, not who changed it."""
    at = "2026-10-10T09:25:00.000Z"
    assert wording.say_state("is the front door locked?", "the front door", "open", at, None, checked=True) == \
        f"The front door is open. I checked the camera at {wording.spoken_time(at)}."


def test_an_old_picture_is_named_with_its_time():
    """When the camera's last picture is too old, the reply names the camera and the picture's time."""
    at = "2026-10-23T04:56:46.491Z"
    assert wording.say_no_fresh_picture("Door", at) ==         f"The Door camera's last picture is from {wording.spoken_time(at)}, so I can't tell right now."
