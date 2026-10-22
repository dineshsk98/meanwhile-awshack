"""What Meanwhile says: the server instructions and tool descriptions Alexa's model reads,
the spoken replies built from the core's facts, and the home screen's card and banner text.
"""

from __future__ import annotations

from datetime import datetime, timezone

from meanwhile.voice import config

Event = Session = dict


def parse(ts: str) -> datetime:
    """Parse a contract timestamp ('2026-10-10T09:32:00.123Z') to an aware datetime."""
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


SERVER_INSTRUCTIONS = (
    "Meanwhile watches the user's home cameras (phones and laptops labelled Desk, Room, Door) while they "
    "step away, and does whatever they asked: keep an eye on an object, take notes of who comes and goes, "
    "watch the front door. It works out what happened on every camera and who did it, and answers from that "
    "when the user asks. It can also look through any camera right now, and look back over the last few minutes "
    "of every camera, session or not. Use step_away when they leave with an instruction, what_did_i_miss and "
    "show_me when they want to catch up after being away, im_back when they return, status for what it's doing "
    "now, ask_state for how something is or was left and who changed it (whether a door is locked or open, a "
    "light is on), look for any other question about what a camera sees right now (whether the user or someone "
    "is there, what they are doing, where something is), and "
    "look_back for any question about what just happened or what they or someone did in the last few minutes, "
    "or longer ago. Each tool returns a `spoken` reply: say it as it is, keeping names and times."
)

TOOL_DESCRIPTIONS: dict[str, str] = {
    "step_away": (
        "Start watching the home cameras while the user is away, following their instruction. Call it when "
        "they say they're leaving or stepping away and what to watch, e.g. \"I'm stepping away, keep an eye on "
        "my watch\", \"look out for my keys\", \"take notes of who comes into the room\", \"watch the front door "
        "while I'm out\". `instruction` is what they asked for, in their words, without the \"I'm stepping "
        "away\" part. Don't call it for questions (\"did I lock the door?\", \"is my watch on the desk?\"): those "
        "go to ask_state, look, look_back or what_did_i_miss. If they only say they're leaving, ask what to watch for first."
    ),
    "what_did_i_miss": (
        "Tell the user what happened while they were away (their away session), and put the clips on the home "
        "screen. Call it for \"what did I miss?\", \"anything new?\", \"did anything happen while I was gone?\", "
        "\"catch me up\", or "
        "\"yes\" after im_back offers to "
        "tell them. The answer is short: how the thing they asked about ended up, and anything that mattered. "
        "Set `detail` to true only when they ask for everything (\"tell me everything\", \"give me the details\", "
        "\"what happened exactly?\") or say yes after you offered the details; then it reads every event in "
        "order. Works during the away session and right after they come back."
    ),
    "show_me": (
        "Play clips of what happened in the home screen's corner window. Call it for \"show me\", \"show me "
        "the hand-over\", \"let me see who picked it up\". `event`: when they name a moment, always pass their few "
        "words for it (\"show me the hand-over\" -> \"the hand-over\", \"the pick-up\", \"the door\"), or an "
        "event_id from an earlier answer. Leave it out only for a plain \"show me\", to show what the last answer "
        "was about."
    ),
    "im_back": (
        "The user is back: stop watching and end the away session. Call it for \"I'm back\", \"I'm home\", "
        "\"you can stop watching\". It says how much happened and offers to tell them; if they say yes, "
        "call what_did_i_miss."
    ),
    "status": (
        "Say what Meanwhile is watching right now and which cameras are live. Call it for \"what are you "
        "watching?\", \"are the cameras on?\", \"is everything working?\"."
    ),
    "ask_state": (
        "Say how something is, or was last left, and who changed it: whether a door is locked or open, a light is "
        "on or off, a window is closed. Call it for \"is the front door locked?\", \"did I lock the door?\", \"who "
        "locked the door?\", \"is the lamp on?\", \"is the window open?\". `question` is what they asked, in their "
        "words; `thing` is the thing they asked about (\"the front door\", \"the lamp\"); `camera` is the camera "
        "they named, if any. It answers from the last change the cameras recorded, and looks at the video when "
        "nothing was recorded or it isn't clear."
    ),
    "look": (
        "Look through the home cameras right now and answer any question about what they see, with the picture "
        "on the home screen, for questions in the present tense. Call it for \"do you see me?\", \"what am I "
        "doing?\", \"what am I wearing?\", \"can you see me on the desk camera?\", \"who's at the desk?\", \"what's "
        "on the table?\", \"is anyone in the room?\", \"is my laptop still on the desk?\", \"what does the door "
        "camera show?\". `question` is what they asked, in "
        "their words. `camera` is the camera they named, e.g. \"desk\" for \"the camera on the desk\"; leave it "
        "out to look through every camera. Works whether or not Meanwhile is watching anything."
    ),
    "look_back": (
        "Look back over the last few minutes of the home cameras and answer what happened, with a picture on the "
        "home screen. Call it for questions in the past tense about the recent past: \"what did I do?\", \"what "
        "just happened?\", \"what just happened at the desk?\", \"did anyone come in?\", \"did someone touch my "
        "laptop?\", \"what was I doing a minute ago?\", and also \"what did I do 10 minutes ago?\" (it says how far "
        "back it can see). Not for catching up after being away (\"what did I miss?\", \"anything new?\"): that is "
        "what_did_i_miss. `question` is what they asked, in their words. `camera` is the camera they named; leave "
        "it out for every camera. Works whether or not Meanwhile is watching anything."
    ),
}

CATCH_UP_QUESTION = "What happened?"

MAX_READ = 6

_NUMBERS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
_LOWER_FIRST = {"Your", "You", "Someone", "They", "The", "A", "An", "It"}


def happened_at(event: Event) -> str:
    """When an event happened: its `happened_at` if the core sent one, else `ts` (when it was written)."""
    return event.get("happened_at") or event["ts"]


def spoken_time(ts: str) -> str:
    """A contract timestamp as '3:02 pm' in the home's time zone (MEANWHILE_TIME_ZONE)."""
    local = parse(ts).astimezone(config.TIME_ZONE)
    return local.strftime("%I:%M %p").lstrip("0").lower()


def _count(n: int, noun: str) -> str:
    """'three things', 'one clip'."""
    word = _NUMBERS[n] if n < len(_NUMBERS) else str(n)
    return f"{word} {noun}{'' if n == 1 else 's'}"


def _join(names: list[str], joiner: str = "and") -> str:
    """'Desk, Room and Door'."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" {joiner} " + names[-1]


def _is_are(names: list[str]) -> str:
    """'is' for one name, 'are' for several."""
    return "are" if len(names) > 1 else "is"


def _after_time(summary: str) -> str:
    """Lowercase a summary's first word when it reads better after 'At 3:02 pm, '."""
    first, _, rest = summary.partition(" ")
    return f"{first.lower()} {rest}" if first in _LOWER_FIRST else summary


def _doing(summary: str) -> str:
    """A watch order's summary as a phrase that follows "I'm"."""
    return _lower_first(summary.strip().rstrip(".!"))


def _lower_first(text: str) -> str:
    """Lowercase the first letter, except the word 'I'."""
    return text if text[:2] in ("I ", "I'") else text[:1].lower() + text[1:]


def say_step_away(
    status: str,
    *,
    summary_for_alexa: str = "",
    question: str = "",
    not_found: str = "",
    current_instruction: str = "",
) -> str:
    """The reply to step_away for status watching / clarify / not_found / already_watching."""
    if status == "watching":
        return f"Sure. I'm {_doing(summary_for_alexa)}." if summary_for_alexa else "Sure, I'm on it."
    if status == "clarify":
        return question or "Sorry, I didn't quite catch what you'd like me to watch. Could you say it again?"
    if status == "not_found":
        return f"Sorry, {_lower_first(not_found)} Could you put it where a camera can see it, and ask me again?"
    if status == "already_watching":
        if summary_for_alexa:
            doing = f"I'm still {_doing(summary_for_alexa)}."
        elif current_instruction:
            doing = f"I'm still on your last request: {current_instruction.strip().rstrip('?.!')}."
        else:
            doing = "I'm still watching from before."
        return f"{doing} When you're back, just say \"I'm back\", and then I can watch something new."
    return "Sorry, I couldn't start watching just now. Could you try again?"


_OBJECT_ACTIONS = {"picked_up", "put_back", "handed_to", "moved", "left_view", "taken", "still_tracking"}
_DID = {
    "picked_up": "picked {it} up",
    "put_back": "put {it} back",
    "handed_to": "handed {it} back to you",
    "moved": "moved {it}",
    "left_view": "took {it} out of view",
    "taken": "took {it} away",
}
MAX_BRIEF_NOTES = 3


def _it(thing: str) -> str:
    """'it', or 'them' for a thing whose name reads as plural."""
    return "them" if verb_for(thing, "is") == "are" else "it"


def _capital(text: str) -> str:
    """Capitalise the first letter."""
    return text[:1].upper() + text[1:]


def _offline_note(offline_cameras: list[str]) -> str:
    """'the Room camera is offline, so I might have missed something there'."""
    noun = "cameras" if len(offline_cameras) > 1 else "camera"
    return f"the {_join(offline_cameras)} {noun} {_is_are(offline_cameras)} offline, so I might have missed something there"


def _object_lead(session: Session, obj: str, handled: list[Event], events: list[Event], only_owner: bool,
                 acted: bool = False) -> str:
    """One sentence on how the watched thing ended up, from the session's state or else the latest object event;
    `acted` means something else was done to it, so it can't be called untouched."""
    is_, was, it = verb_for(obj, "is"), verb_for(obj, "was"), _it(obj)
    state = session.get("state") or ""
    if state == "on_surface":
        if not handled and acted:
            return f"Your {obj} {is_} still where you left {it}."
        if not handled:
            return f"Nobody touched your {obj}. {_capital(it)} {is_} still where you left {it}."
        if only_owner:
            return f"Your {obj} {is_} still where you left {it}; only you moved {it}."
        return f"Your {obj} {is_} back where you left {it}."
    if state == "with_owner":
        return f"Your {obj} {is_} back with you."
    if state == "with_person":
        return f"Your {obj} {is_} with {_who(session.get('state_actor'), events)} right now."
    if state == "taken":
        actor = session.get("state_actor")
        by = "" if not actor or actor == "unknown" else f" by {_who(actor, events)}"
        return f"I'm afraid your {obj} {was} taken{by}."
    last = handled[-1] if handled else None
    action = last.get("action") if last else ""
    who = _who(last.get("actor_id"), events) if last else ""
    if action == "moved":
        return f"Your {obj} {was} moved by {who}."
    if action == "left_view":
        return f"Your {obj} went out of view with {who}."
    if action == "taken":
        return f"I'm afraid your {obj} {was} taken by {who}."
    if action in ("still_tracking", "picked_up"):
        return f"I'm still tracking your {obj}; {it} {was} last seen with {who}."
    if action in ("put_back", "handed_to"):
        return f"Your {obj} {is_} back."
    return f"I'm not sure where your {obj} {is_} right now."


def _acts_line(acts: list[Event]) -> str:
    """What else was done to the thing, in time order: 'At 3:02 pm, you turned your lamp on, then at 3:05 pm, you
    turned your lamp off.'"""
    parts, last = [], None
    for e in acts:
        when = spoken_time(happened_at(e))
        said = _after_time((e.get("summary") or "something happened").strip().rstrip(".!?"))
        parts.append(said if when == last else f"at {when}, {said}")
        last = when
    return _capital(", then ".join(parts)) + "."


def _brief_object(session: Session, events: list[Event], offline_cameras: list[str]) -> str:
    """The short catch-up for "keep an eye on my …": how it ended up, the latest things done to it in time order,
    who else handled it, an offer of details."""
    obj = session.get("focus_object") or session.get("watch_order", {}).get("target")
    it = _it(obj)
    handled = [e for e in events if e.get("action") in _OBJECT_ACTIONS]
    acts = [e for e in events if e.get("object_action")][-MAX_BRIEF_NOTES:]
    others = [e for e in handled if e.get("actor_role") != "owner"]
    only_owner = bool(handled) and not others
    sentences = [_object_lead(session, obj, handled, events, only_owner, bool(acts))]
    if acts:
        sentences.append(_acts_line(acts))
    holder = _who(session.get("state_actor"), events) if session.get("state") == "with_person" else None
    deeds: dict[str, list[str]] = {}
    first_seen: dict[str, Event] = {}
    for e in others:
        person = _who(e.get("actor_id"), events)
        first_seen.setdefault(person, e)
        did = _DID.get(e.get("action", ""))
        if did and did.format(it=it) not in deeds.setdefault(person, []):
            deeds[person].append(did.format(it=it))
    notes = []
    for person, done in deeds.items():
        if not done:
            continue
        if person == holder and done == [_DID["picked_up"].format(it=it)]:
            notes.append(f"{_capital(person)} picked {it} up at {spoken_time(happened_at(first_seen[person]))}")
        else:
            notes.append(f"{_capital(person)} {_join(done)}")
    second = "; ".join(notes[:2])
    if offline_cameras:
        second = f"{second}; also, {_offline_note(offline_cameras)}" if second else _capital(_offline_note(offline_cameras))
    if second:
        sentences.append(second + ".")
    alerts = any(e.get("alert", "none") != "none" for e in events)
    more = others or alerts or len(events) > len(handled) + len(acts)
    if more:
        sentences.append("Want the details?")
    return " ".join(sentences)


def _brief_list(session: Session, events: list[Event], offline_cameras: list[str]) -> str:
    """The short catch-up for notes and other orders: a one-line count, the main few things, an offer of details."""
    alerts = [e for e in events if e.get("alert", "none") != "none"]
    if session.get("watch_order", {}).get("kind") == "notes":
        picked = events[-MAX_BRIEF_NOTES:]
        sentences = [f"I noted {_count(len(events), 'thing')} while you were away."]
    else:
        picked = (alerts or events)[-1:]
        sentences = []
    sentences += [_capital((e.get("summary") or "Something happened.").strip()) for e in picked]
    if offline_cameras:
        sentences.append(_capital(_offline_note(offline_cameras)) + ".")
    if len(events) > len(picked):
        rest = len(events) - len(picked)
        sentences.append(f"There {'is' if rest == 1 else 'are'} {_count(rest, 'more thing')}. Want the details?")
    return " ".join(sentences)


def say_catch_up(session: Session | None, events: list[Event], offline_cameras: list[str], detail: bool = False) -> str:
    """The answer to 'what did I miss?': short by default (how it ended up, what mattered, an offer of details);
    every event in order when `detail` is asked for."""
    if session is None:
        return "I haven't been watching anything yet. Whenever you step away, just tell me what to keep an eye on."
    if events and not detail:
        order = session.get("watch_order", {})
        if order.get("kind") == "object" and (session.get("focus_object") or order.get("target")):
            return _brief_object(session, events, offline_cameras)
        return _brief_list(session, events, offline_cameras)
    return _narrate(session, events, offline_cameras)


def _narrate(session: Session, events: list[Event], offline_cameras: list[str]) -> str:
    """Every event in order with its time (the detailed catch-up), plus offline cameras and the clips note."""
    offline = ""
    if offline_cameras:
        noun = "cameras" if len(offline_cameras) > 1 else "camera"
        offline = (f" Just so you know, the {_join(offline_cameras)} {noun} {_is_are(offline_cameras)} offline, "
                   "so I might have missed something there.")
    if not events:
        quiet = "It's all quiet so far. Nothing's happened yet." if session.get("ended_at") is None \
            else "It was all quiet while you were away. Nothing happened."
        return quiet + offline
    read, intro = events, "While you were away,"
    if len(events) > MAX_READ:
        alerts = [i for i, e in enumerate(events) if e.get("alert", "none") != "none"][:MAX_READ]
        others = [i for i in range(len(events)) if i not in alerts]
        room = MAX_READ - len(alerts)
        keep = set(alerts) | set(others[len(others) - room:] if room else [])
        read = [e for i, e in enumerate(events) if i in keep]
        intro = f"Quite a bit happened: {_count(len(events), 'thing')}. Here are the main ones."
    lines, last_time = [], None
    for e in read:
        when, said = spoken_time(happened_at(e)), _after_time(e.get("summary") or "something happened.")
        lines.append(f"Then {said}" if when == last_time else f"At {when}, {said}")
        last_time = when
    if intro.endswith(","):
        lines[0] = lines[0][0].lower() + lines[0][1:]
    clips = " I've put the clips on the screen for you." if any(e.get("clip_url") for e in read) else ""
    return " ".join([intro, *lines]) + offline + clips


def say_show_me(events: list[Event]) -> str:
    """What to say while the screen plays these clips."""
    if not events:
        return "Sorry, I don't have any clips to show you yet."
    first = spoken_time(happened_at(events[0]))
    if len(events) == 1:
        return f"Here's the clip from {first}. It's on the screen now."
    return f"Here you go: {_count(len(events), 'clip')}, starting from {first}. They're on the screen now."


def say_im_back(session: Session | None, events: list[Event]) -> str:
    """The welcome back after the session ends."""
    if session is None:
        return "Welcome back! I wasn't watching anything, though."
    if not events:
        return "Welcome back! It was all quiet while you were away."
    return (f"Welcome back! {_count(len(events), 'thing').capitalize()} happened while you were away. "
            "Would you like to hear them?")


def say_status(session: Session | None, online: list[str], offline: list[str]) -> str:
    """What is being watched now, and which cameras are live or offline."""
    if session is None:
        parts = ["I'm not watching anything right now."]
    else:
        summary = session.get("watch_order", {}).get("summary_for_alexa")
        parts = [f"Right now I'm {_doing(summary)}." if summary else f"Right now I'm watching for: {session['instruction']}."]
        state = {
            "on_surface": "It's still where you left it.",
            "with_person": "Someone has it at the moment.",
            "with_owner": "It's with you.",
            "taken": "I'm afraid it looks like it was taken.",
        }.get(session.get("state", ""))
        if state:
            parts.append(state)
    if online and offline:
        parts.append(f"{_join(online)} {_is_are(online)} live, but {_join(offline)} {_is_are(offline)} offline.")
    elif online:
        parts.append(f"All {_count(len(online), 'camera')} are live." if len(online) > 1 else f"The {online[0]} camera is live.")
    elif offline:
        parts.append(f"I'm afraid all the cameras are offline: {_join(offline)}.")
    else:
        parts.append("No cameras are connected yet.")
    return " ".join(parts)


def say_state(question: str, thing: str, value: str, at: str, by_name: str | None, checked: bool = False) -> str:
    """How a thing is, from its stored status: 'Yes, the front door is locked. The last change I saw was at 3:02 pm,
    by Ravi.', or with `checked` (a video check, not a change seen during a watch) '… I checked the camera at
    3:05 pm.' ('Yes' only when the question asks about that very state)."""
    words = set(question.lower().replace("?", " ").split())
    yes = "Yes, " if value and set(value.lower().split()) <= words else ""
    name = thing.strip()
    if name.split()[0].lower() not in ("the", "my", "your", "our", "a", "an"):
        name = f"the {name}"
    if name.split()[0].lower() == "my":
        name = "your " + name.split(" ", 1)[1]
    sentence = f"{name} {verb_for(name, 'is')} {value}."
    who = f", by {by_name}" if by_name else ""
    tail = f"I checked the camera at {spoken_time(at)}." if checked else \
        f"The last change I saw was at {spoken_time(at)}{who}."
    return f"{yes}{sentence if yes else _capital(sentence)} {tail}"


def say_look(answer: str, camera: str, online: bool, frame_at: str) -> str:
    """The answer to a question about what a camera sees now; from an offline camera, its last picture's time first."""
    if online:
        return answer
    return f"The {camera} camera is offline, so this is from its last picture, at {spoken_time(frame_at)}. {answer}"


def say_look_back(answer: str, window: str, asks_earlier: bool) -> str:
    """The answer about the recent past, always saying it covers only the last `window` ('5 minutes'); first that
    it can't see further back when the question reached earlier."""
    covered = answer if f"last {window}" in answer else f"In the last {window}, {_after_time(answer)}"
    if asks_earlier:
        return f"I can only look back over the last {window}. {covered}"
    return covered


def say_catch_up_recent(answer: str, window: str, asks_earlier: bool, watching: bool = False,
                        offline_cameras: list[str] | None = None) -> str:
    """'What did I miss?' with nothing untold from a session: what the cameras saw in the last `window` instead,
    and which watched cameras are offline."""
    lead = ("Nothing new from what I'm watching, but I can look back." if watching
            else "I wasn't watching anything for you, but I can look back.")
    offline = f" {_capital(_offline_note(offline_cameras))}." if offline_cameras else ""
    return f"{lead} {say_look_back(answer, window, asks_earlier)}{offline}"


def say_nothing_new(watching: bool, offline_cameras: list[str] | None = None) -> str:
    """'What did I miss?' when everything was already told and the cameras can't be looked back over."""
    said = "Nothing new since I last told you. I'm still watching." if watching else "Nothing new since I last told you."
    return f"{said} {_capital(_offline_note(offline_cameras))}." if offline_cameras else said


def say_no_fresh_picture(camera: str, at: str) -> str:
    """The reply when the only picture of a thing is too old to tell how it is now."""
    return f"The {camera} camera's last picture is from {spoken_time(at)}, so I can't tell right now."


def say_look_failed(message: str) -> str:
    """The reply when the core couldn't look: its own sentence, else a plain apology."""
    return message or "Sorry, I couldn't look through the cameras just now."


BANNERS = {
    "heads_up": {"tone": "amber", "title": "Heads-up"},
    "verdict": {"tone": "green", "title": "Update"},
    "alert": {"tone": "red", "title": "Alert"},
}


def banner(event: Event) -> dict | None:
    """The home-screen banner for an alert event: tone, title and the line to speak; None if not an alert."""
    style = BANNERS.get(event.get("alert", "none"))
    if style is None:
        return None
    return {**style, "say": event.get("summary") or "Something happened."}


def verb_for(thing: str, verb: str) -> str:
    """'is'/'was' as 'are'/'were' when the thing's name reads as plural ('keys', 'headphones'), else as given."""
    last = thing.split()[-1].lower() if thing.split() else ""
    plural = last.endswith("s") and not last.endswith(("ss", "us", "is"))
    return {"is": "are", "was": "were"}.get(verb, verb) if plural else verb


def _who(actor_id: str | None, events: list[Event]) -> str:
    """A person's name for the card, from the events that mention them."""
    if not actor_id or actor_id == "unknown":
        return "someone I don't recognise"
    for e in events:
        if e.get("actor_id") == actor_id:
            if e.get("actor_role") == "owner":
                return "you"
            if e.get("actor_name"):
                return e["actor_name"]
    return actor_id.capitalize()


def _object_line(session: Session, events: list[Event], camera_labels: dict[str, str]) -> str | None:
    """Where the focus object is now, e.g. 'Your watch is with Ravi.'"""
    obj = session.get("focus_object")
    if not obj:
        return None
    where = camera_labels.get(session.get("focus_camera_id") or "", "")
    is_, was = verb_for(obj, "is"), verb_for(obj, "was")
    return {
        "on_surface": f"Your {obj} {is_} where you left {'them' if is_ == 'are' else 'it'}"
                      + (f", on the {where}." if where else "."),
        "with_person": f"Your {obj} {is_} with {_who(session.get('state_actor'), events)}.",
        "with_owner": f"Your {obj} {is_} back with you.",
        "taken": f"Your {obj} {was} taken away.",
    }.get(session.get("state") or "", f"I'm not sure where your {obj} {is_} right now.")


def screen_card(
    session: Session | None,
    active: bool,
    events: list[Event],
    camera_labels: dict[str, str],
) -> dict:
    """The session card's text: heading, what was said, what Meanwhile understood, the state line, the empty note."""
    if session is None:
        return {"heading": "Not watching anything right now.", "said": None, "understood": None, "state": None,
                "empty": "When you step away, tell Alexa what to keep an eye on."}
    summary = session.get("watch_order", {}).get("summary_for_alexa")
    state = _object_line(session, events, camera_labels)
    ended = session.get("ended_at")
    return {
        "heading": "Watching now" if active else (f"Last time, until {spoken_time(ended)}" if ended else "Last time"),
        "said": f"“{session.get('instruction', '').strip()}”",
        "understood": f"I'm {_doing(summary)}." if summary else None,
        "state": state,
        "empty": "Nothing's happened yet." if active else "Nothing happened.",
    }
