"""Session endpoints from CONTRACT.md: start a watch order, read sessions and events, end a session."""

from __future__ import annotations

from typing import Any, Callable

from meanwhile import config
from meanwhile.api import Response, error
from meanwhile.contract import Camera, Event, OrderResult
from meanwhile.pipeline import detector as detection
from meanwhile.pipeline import orders, tracker, vision
from meanwhile.pipeline.models import ModelUnavailable
from meanwhile.store import Store, StoreError, now, parse

Compiler = Callable[[str, list[Camera]], OrderResult]
EVENT_FILTERS = ("action", "actor_id", "actor_role", "camera_id")
DEFAULT_SESSIONS, MAX_SESSIONS = 20, 100


def frame_reader(store: Store) -> vision.FrameFor:
    """A function giving a camera's latest thumbnail bytes, re-reading the camera once if the file was just replaced."""
    def frame_for(camera: Camera) -> bytes | None:
        """The camera's latest frame, or None if it has none."""
        for _ in range(2):
            key = camera.get("thumb_key")
            if not key:
                return None
            try:
                return store.media_path(key).read_bytes()
            except FileNotFoundError:
                camera = store.get_camera(camera["camera_id"]) or camera
        return None
    return frame_for


def grounder(store: Store, detector: Any = None,
             log: Callable[[str], None] = lambda line: print(line, flush=True)) -> orders.Ground:
    """Grounding on the cameras' latest frames: by the detector when one is given, falling back to the vision
    model only when the detector finds the object on no camera; logs which one found it."""
    frame_for = frame_reader(store)

    def ground(target: str, cameras: list[Camera]) -> vision.Found | None:
        """Find the object's camera and box."""
        found = detection.ground(detector, target, cameras, frame_for) if detector is not None else None
        if found:
            log(f"Grounding: detector found {target} on {found['camera_id']} at {found['box']} (conf {found['conf']})")
            return {"camera_id": found["camera_id"], "box": found["box"]}
        found = vision.ground(target, cameras, frame_for)
        where = f"found {target} on {found['camera_id']} at {found['box']}" if found else f"did not find {target}"
        log(f"Grounding: vision model {where}")
        return found
    return ground


def make_compiler(store: Store, detector: Any = None) -> Compiler:
    """The real watch-order compiler: the model reads the instruction, and grounding uses the cameras' latest
    frames (see grounder)."""
    ground = grounder(store, detector)
    return lambda instruction, cameras: orders.compile_order(instruction, cameras, ground=ground)


def people_by_id(store: Store) -> dict[str, dict]:
    """Registered people by id."""
    return {p["person_id"]: p for p in store.list_people()}


def spoken_name(person: dict[str, Any]) -> str | None:
    """A person's name for events: their name, or 'an unnamed person (Person 3)' for someone nobody has named."""
    if person.get("unnamed"):
        return tracker.who(person["person_id"], {person["person_id"]: person})
    return person.get("name")


def session_out(session: dict[str, Any]) -> dict[str, Any]:
    """A session as the contract returns it, plus `id` (the app's name for session_id) and `active`."""
    return {**session, "id": session["session_id"], "active": session.get("ended_at") is None}


def event_out(store: Store, event: Event, people: dict[str, dict]) -> dict[str, Any]:
    """An event as the contract returns it, with the actor's name and role and media URLs; `id` is the app's
    name for event_id, and happened_at falls back to ts."""
    out = {k: v for k, v in event.items() if k not in ("clip_key", "frame_key")}
    out["id"], out["happened_at"] = event["event_id"], event.get("happened_at") or event["ts"]
    person = people.get(event.get("actor_id") or "", {})
    out["actor_name"], out["actor_role"] = spoken_name(person), person.get("role")
    out["actor_unnamed"] = bool(person.get("unnamed"))
    out["clip_url"] = store.media_url(event.get("clip_key"))
    out["frame_url"] = store.media_url(event.get("frame_key"))
    return out


def watch_orders(store: Store, body: dict[str, Any], compile_order: Compiler) -> Response:
    """POST /api/watch-orders {"instruction"}: 201 session, 200 clarify, 422 not_found, 409 already_watching."""
    instruction = " ".join(str(body.get("instruction") or "").split())
    if active := store.active_session():
        return error(409, "already_watching", "A session is already active.", session=session_out(active))
    cameras = [c for c in store.list_cameras() if store.is_online(c)]
    try:
        result = compile_order(instruction, cameras)
    except ModelUnavailable as exc:
        return error(503, "model_unavailable", str(exc))
    if clarify := result["watch_order"].get("clarify"):
        return 200, {"clarify": clarify}
    if result.get("not_found"):
        return error(422, "not_found", result["not_found"])
    try:
        session = store.start_session(instruction, result)
    except StoreError:
        return error(409, "already_watching", "A session is already active.", session=session_out(store.active_session()))
    return 201, {"session": session_out(keep_reference(store, session))}


def keep_reference(store: Store, session: dict[str, Any]) -> dict[str, Any]:
    """For object orders, keep the focus camera's latest frame as how the object looked at the start (thumbnails
    are replaced every few seconds), for the frame check to compare against."""
    camera = store.get_camera(session.get("focus_camera_id") or "")
    jpeg = frame_reader(store)(camera) if camera else None
    if not jpeg:
        return session
    key = f"refs/{camera['camera_id']}/{session['session_id']}.jpg"
    store.write_media(key, jpeg)
    return store.update_session(session["session_id"], ref_frame_key=key)


def latest_session(store: Store) -> Response:
    """GET /api/sessions/latest: the active session, else the newest one."""
    session = store.latest_session()
    return 200, {"session": session and session_out(session), "active": bool(session and session.get("ended_at") is None)}


def list_sessions(store: Store, limit: str | None) -> Response:
    """GET /api/sessions?limit=: past and current sessions, newest first, each with its last verdict's summary
    (also as `summary`) and its event count."""
    try:
        count = max(1, min(int(limit), MAX_SESSIONS)) if limit else DEFAULT_SESSIONS
    except ValueError:
        return error(400, "bad_limit", "The limit must be a whole number.")
    out = []
    for session in store.list_sessions(count):
        events = store.list_events(session["session_id"])
        verdict = next((e["summary"] for e in reversed(events) if e.get("alert") == "verdict"), None)
        out.append({**session_out(session), "last_verdict": verdict, "summary": verdict, "event_count": len(events)})
    return 200, {"sessions": out}


def status(store: Store, analyser: dict[str, Any] | None, push: dict[str, Any] | None) -> Response:
    """GET /api/status: how far behind live the analysis is (`lag_s`), the queue, whether a session is active,
    whether push is on, and how many seconds of the recent past look-back covers."""
    info = analyser or {}
    return 200, {"lag_s": info.get("lag_s", 0.0), "queued": info.get("queued", 0), "busy": bool(info.get("busy")),
                 "analyser": analyser is not None, "active": store.active_session() is not None,
                 "push": push or {"enabled": False, "devices": 0}, "recent_window_s": config.RECENT_WINDOW_S}


def session_events(store: Store, session_id: str, since: str | None) -> Response:
    """GET /api/sessions/{id}/events?since=: the session's events in time order."""
    if store.get_session(session_id) is None:
        return error(404, "not_found", "No such session.")
    people = people_by_id(store)
    return 200, {"events": [event_out(store, e, people) for e in store.list_events(session_id, since=since)]}


def end_session(store: Store, session_id: str) -> Response:
    """POST /api/sessions/{id}/end: end it now; 409 if it already ended."""
    session = store.get_session(session_id)
    if session is None:
        return error(404, "not_found", "No such session.")
    if session.get("ended_at"):
        return error(409, "already_ended", "That session has already ended.")
    return 200, {"session": session_out(store.end_session(session_id))}


def caught_up(store: Store, session_id: str, body: dict[str, Any]) -> Response:
    """POST /api/sessions/{id}/caught-up {"until"}: the user has been told the session's events up to `until` (the
    newest one told); keeps the later of this and any earlier cursor in `caught_up_until`, and when in `caught_up_at`."""
    session = store.get_session(session_id)
    if session is None:
        return error(404, "not_found", "No such session.")
    until = str(body.get("until") or "")
    try:
        parse(until)
    except ValueError:
        return error(400, "bad_until", "until must be a timestamp like 2026-10-10T09:32:00.123Z.")
    fields = {"caught_up_until": max(until, session.get("caught_up_until") or ""), "caught_up_at": now()}
    if session.get("ended_at") is None:
        for field, value in fields.items():
            store.set_session_field(session_id, field, value)
    else:
        store.update_session(session_id, **fields)
    return 200, {"session": session_out(store.get_session(session_id))}


def latest_event(store: Store, filters: dict[str, str]) -> Response:
    """GET /api/events/latest?action=&actor_id=&actor_role=&camera_id=: the newest matching event anywhere."""
    wanted = {k: v for k, v in filters.items() if k in EVENT_FILTERS}
    people = people_by_id(store)
    for event in store.all_events_newest_first():
        out = event_out(store, event, people)
        if all(out.get(k) == v for k, v in wanted.items()):
            return 200, {"event": out}
    return 200, {"event": None}
