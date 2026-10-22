"""A stand-in core that plays the scenario dataset (data.py) to the voice side: CONTRACT.md's API over an in-memory
store, with fixed watch-order templates. Test-only; it never runs a model."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from scenarios.data import CAMERAS, LOOK_BACK, LOOKS, PEOPLE, RECENT_WINDOW_S, SCENARIOS

Understand = Callable[[str, list[dict]], dict]

NEVER_VISIBLE = ("umbrella",)

UNCLEAR = ("Sorry, I didn't quite catch what you'd like me to watch. "
           "Could you say it again? For example, \"keep an eye on my keys\".")
QUESTION = ("That sounds like a question rather than something to watch. "
            "Ask me again and I'll answer it, or tell me what to keep an eye on while you're away.")
NO_CAMERAS = "I don't have any cameras yet. Could you open the camera page on a phone first?"


def to_iso(dt: datetime) -> str:
    """A datetime in the contract's ISO format."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def now() -> str:
    """The current UTC time in the contract's format."""
    return to_iso(datetime.now(timezone.utc))


def parse(ts: str) -> datetime:
    """A contract timestamp as an aware datetime."""
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def new_id(prefix: str) -> str:
    """A new id like 'evt_1a2b3c4d5e6f'."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class FakeStore:
    """Cameras, people, sessions, events and the look-back flag in memory, with media files under data_dir."""

    def __init__(self, data_dir: Path):
        """An empty store whose media lives in data_dir/media."""
        self.media_dir = Path(data_dir) / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.cameras: dict[str, dict] = {}
        self.offline: set[str] = set()
        self.people: dict[str, dict] = {}
        self.sessions: list[dict] = []
        self.events: list[dict] = []
        self.recent = True
        self.states: list[dict] = []

    def put_camera(self, camera_id: str, label: str) -> None:
        """Add a camera, online."""
        with self.lock:
            self.cameras[camera_id] = {"camera_id": camera_id, "label": label, "last_seen": now()}
            self.offline.discard(camera_id)

    def set_offline(self, camera_id: str) -> None:
        """Mark a camera as offline."""
        with self.lock:
            self.offline.add(camera_id)

    def get_camera(self, camera_id: str) -> dict | None:
        """A camera by id, with `online`."""
        camera = self.cameras.get(camera_id)
        return {**camera, "online": camera_id not in self.offline} if camera else None

    def list_cameras(self) -> list[dict]:
        """Every camera, with `online`."""
        return [self.get_camera(c) for c in sorted(self.cameras)]

    def put_person(self, person: dict) -> None:
        """Register a person."""
        with self.lock:
            self.people[person["person_id"]] = dict(person)

    def start_session(self, instruction: str, order: dict) -> dict:
        """Start a session from a compiled order; raises ValueError if one is active."""
        with self.lock:
            if any(s["ended_at"] is None for s in self.sessions):
                raise ValueError("a session is already active")
            session = {"session_id": new_id("ses"), "started_at": now(), "ended_at": None,
                       "instruction": instruction, "watch_order": order["watch_order"]}
            session.update({k: order[k] for k in ("focus_object", "focus_camera_id") if order.get(k)})
            if session["watch_order"].get("kind") == "object":
                session["state"] = "on_surface"
            self.sessions.append(session)
            return dict(session)

    def get_session(self, session_id: str) -> dict | None:
        """A session by id."""
        return next((dict(s) for s in self.sessions if s["session_id"] == session_id), None)

    def active_session(self) -> dict | None:
        """The active session, if any."""
        return next((dict(s) for s in reversed(self.sessions) if s["ended_at"] is None), None)

    def latest_session(self) -> dict | None:
        """The active session, else the most recently started one."""
        return self.active_session() or (dict(self.sessions[-1]) if self.sessions else None)

    def update_session(self, session_id: str, **fields: Any) -> dict:
        """Merge fields into a session."""
        with self.lock:
            session = next(s for s in self.sessions if s["session_id"] == session_id)
            session.update(fields)
            return dict(session)

    def add_event(self, event: dict) -> dict:
        """Store an event, giving it an id and ts."""
        stored = {"alert": "none", **event, "event_id": new_id("evt"), "ts": event.get("ts") or now()}
        with self.lock:
            self.events.append(stored)
        return dict(stored)

    def list_events(self, session_id: str, since: str | None = None) -> list[dict]:
        """A session's events in time order, only those after `since` if given."""
        found = [e for e in self.events if e["session_id"] == session_id and (not since or e["ts"] > since)]
        return [dict(e) for e in sorted(found, key=lambda e: e["ts"])]

    def get_event(self, event_id: str) -> dict | None:
        """An event by id."""
        return next((dict(e) for e in self.events if e["event_id"] == event_id), None)



def seed(store: FakeStore) -> None:
    """The dataset's cameras (online) and people."""
    for camera_id, label in CAMERAS:
        store.put_camera(camera_id, label)
    for person in PEOPLE:
        store.put_person(person)


def play(store: FakeStore, scenario: str, session: dict) -> list[dict]:
    """Write a scenario's events into a session at once, a millisecond apart and in order, with clip and frame keys."""
    obj = session.get("focus_object") or "watch"
    was = "were" if obj.endswith("s") and not obj.endswith("ss") else "was"
    start, written = datetime.now(timezone.utc), []
    for i, (_at_s, fields, session_fields) in enumerate(SCENARIOS[scenario]):
        ts = to_iso(start + timedelta(milliseconds=i))
        stamp = ts.replace(":", "").replace(".", "").replace("-", "")
        event = {"session_id": session["session_id"], "ts": ts, "alert": "none", "source": "tracker", **fields,
                 "summary": fields["summary"].format(obj=obj, was=was),
                 "clip_key": f"clips/{fields['camera_id']}/{stamp}.mp4",
                 "frame_key": f"frames/{fields['camera_id']}/{stamp}.jpg"}
        written.append(stored := store.add_event(event))
        if fields.get("thing") and fields.get("state"):
            store.states.insert(0, {"thing": fields["thing"], "value": fields["state"], "at": ts,
                                    "by": fields["actor_id"], "camera_id": fields["camera_id"],
                                    "event_id": stored["event_id"], "clip_key": event["clip_key"]})
        if session_fields:
            store.update_session(session["session_id"], **session_fields)
    return written


def labels(cameras: list[dict], joiner: str = "and") -> str:
    """Camera labels joined for speech: 'Desk, Door or Room'."""
    names = [c.get("label", c["camera_id"]) for c in cameras]
    return f" {joiner} ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + f" {joiner} " + names[-1]


def camera_named(cameras: list[dict], name: str) -> dict | None:
    """The camera whose label or id is `name` (also 'the desk camera'), if any."""
    name = name.removesuffix(" camera").strip()
    return next((c for c in cameras if c["label"].lower() == name or c["camera_id"] == name), None)


def build(intent: dict, cameras: list[dict]) -> dict:
    """An understood instruction as a watch order, a clarifying question, or 'not found'."""
    kind, target, named = intent["kind"], intent.get("target", ""), intent.get("camera", "")
    if kind == "question":
        return {"watch_order": {"clarify": QUESTION}}
    if kind == "unclear" or (kind == "object" and not target):
        return {"watch_order": {"clarify": UNCLEAR}}
    chosen = camera_named(cameras, named) if named else None
    if named and chosen is None:
        return {"watch_order": {"clarify": f"I don't have a {named.removesuffix(' camera')} camera. "
                                           f"I can use {labels(cameras, 'or')}. Which one would you like?"}}
    if kind == "object":
        cam = chosen or camera_named(cameras, "desk") or cameras[0]
        order = {"kind": "object", "target": target, "cameras": "all",
                 "clip_question": f"What happens to the {target}?", "report": [target],
                 "alert_when": ["picked_up", "handed_to", "put_back", "left_view"],
                 "summary_for_alexa": f"keeping an eye on your {target} on the {cam['label']} camera"}
        if target in NEVER_VISIBLE:
            return {"watch_order": order, "not_found": f"I can't see your {target} on the {labels(cameras, 'or')} camera."}
        return {"watch_order": order, "focus_object": target, "focus_camera_id": cam["camera_id"]}
    if kind == "notes":
        cam = chosen or camera_named(cameras, "room") or cameras[0]
        return {"watch_order": {"kind": "notes", "target": "who comes in and what they do",
                                "cameras": [cam["camera_id"]], "clip_question": "Who is here and what are they doing?",
                                "report": ["people arriving or leaving"], "alert_when": [],
                                "summary_for_alexa": f"taking notes on who comes into the {cam['label'].lower()}, "
                                                     "without interrupting you"}}
    return {"watch_order": {"kind": "monitor", "target": target, "cameras": [chosen["camera_id"]] if chosen else "all",
                            "clip_question": f"What happens at {target}?", "report": [target], "alert_when": [target],
                            "summary_for_alexa": f"watching {target}, and I'll tell you if anything happens"}}

def compile_order(instruction: str, cameras: list[dict], understand: Understand) -> dict:
    """Read an instruction with `understand` and build its watch order or clarifying question."""
    if not cameras:
        return {"watch_order": {"clarify": NO_CAMERAS}}
    return build(understand(instruction, cameras), cameras)


def error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    """A contract error response."""
    return JSONResponse({"error": code, "message": message, **extra}, status_code=status)


def build_app(store: FakeStore, understand: Understand) -> Starlette:
    """The fake core's app: CONTRACT.md's routes the voice side calls, and /media from the store."""

    def event_out(event: dict) -> dict:
        """An event as the contract returns it, with the actor's name and role and media URLs."""
        out = {k: v for k, v in event.items() if k not in ("clip_key", "frame_key")}
        person = store.people.get(event.get("actor_id") or "", {})
        out["actor_name"], out["actor_role"] = person.get("name"), person.get("role")
        out["clip_url"] = f"/media/{event['clip_key']}" if event.get("clip_key") else None
        out["frame_url"] = f"/media/{event['frame_key']}" if event.get("frame_key") else None
        return out

    async def watch_orders(request: Request) -> JSONResponse:
        """POST /api/watch-orders."""
        instruction = ((await request.json()).get("instruction") or "").strip()
        if active := store.active_session():
            return error(409, "already_watching", "A session is already active.", session=active)
        result = await run_in_threadpool(compile_order, instruction, store.list_cameras(), understand)
        if result["watch_order"].get("clarify"):
            return JSONResponse({"clarify": result["watch_order"]["clarify"]})
        if result.get("not_found"):
            return error(422, "not_found", result["not_found"])
        return JSONResponse({"session": store.start_session(instruction, result)}, status_code=201)

    async def latest_session(request: Request) -> JSONResponse:
        """GET /api/sessions/latest."""
        session = store.latest_session()
        return JSONResponse({"session": session, "active": bool(session and session["ended_at"] is None)})

    async def session_events(request: Request) -> JSONResponse:
        """GET /api/sessions/{id}/events."""
        session_id = request.path_params["session_id"]
        if store.get_session(session_id) is None:
            return error(404, "not_found", "No such session.")
        events = store.list_events(session_id, since=request.query_params.get("since"))
        return JSONResponse({"events": [event_out(e) for e in events]})

    async def end_session(request: Request) -> JSONResponse:
        """POST /api/sessions/{id}/end."""
        session = store.get_session(request.path_params["session_id"])
        if session is None:
            return error(404, "not_found", "No such session.")
        if session["ended_at"]:
            return error(409, "already_ended", "That session has already ended.")
        return JSONResponse({"session": store.update_session(session["session_id"], ended_at=now())})

    async def caught_up(request: Request) -> JSONResponse:
        """POST /api/sessions/{id}/caught-up: keep the later cursor and when it was set."""
        session = store.get_session(request.path_params["session_id"])
        if session is None:
            return error(404, "not_found", "No such session.")
        until = (await request.json()).get("until") or ""
        fields = {"caught_up_until": max(until, session.get("caught_up_until") or ""), "caught_up_at": now()}
        return JSONResponse({"session": store.update_session(session["session_id"], **fields)})

    async def cameras(request: Request) -> JSONResponse:
        """GET /api/cameras."""
        return JSONResponse({"cameras": [{**c, "thumb_url": None} for c in store.list_cameras()]})

    async def latest_event(request: Request) -> JSONResponse:
        """GET /api/events/latest."""
        filters = {k: v for k, v in request.query_params.items() if k in ("action", "actor_id", "actor_role", "camera_id")}
        for event in sorted(store.events, key=lambda e: e["ts"], reverse=True):
            out = event_out(event)
            if all(out.get(k) == v for k, v in filters.items()):
                return JSONResponse({"event": out})
        return JSONResponse({"event": None})

    async def look(request: Request) -> JSONResponse:
        """POST /api/look: the dataset's answer for the named camera, else the first online one, with its frame."""
        body = await request.json()
        question, named = (body.get("question") or "").strip(), (body.get("camera") or "").strip()
        if not question:
            return error(400, "bad_question", "Please ask a short question about what a camera sees.")
        cameras_ = store.list_cameras()
        if named:
            camera = camera_named(cameras_, named.lower())
            if camera is None:
                return error(404, "unknown_camera", f"I don't have a {named} camera. I can look through the "
                                                    f"{labels(cameras_, 'or')} cameras.")
        else:
            camera = next((c for c in cameras_ if c["online"]), None)
            if camera is None:
                return error(409, "no_frame", "None of the cameras is on right now.")
        answer, seen = LOOKS[camera["camera_id"]]
        at = now()
        key = f"looks/{camera['camera_id']}/{at.replace(':', '').replace('.', '').replace('-', '')}.jpg"
        (store.media_dir / key).parent.mkdir(parents=True, exist_ok=True)
        (store.media_dir / key).write_bytes(b"jpeg")
        frame = {"camera_id": camera["camera_id"], "camera": camera["label"], "online": camera["online"],
                 "frame_at": at, "frame_url": f"/media/{key}", "frame_source": "thumbnail"}
        people = [{"person_id": p, "name": store.people[p]["name"], "role": store.people[p]["role"],
                   "camera_id": camera["camera_id"]} for p in seen]
        return JSONResponse({"question": question, "answer": answer, **frame, "people": people, "looked_at": [frame],
                             "took_s": {"faces": 0.0, "model": 0.0}})

    async def look_back(request: Request) -> JSONResponse:
        """POST /api/look-back: the dataset's recent-past answer (asks_earlier when the question says "ago"), or
        no_frames when the store's window is empty."""
        question = ((await request.json()).get("question") or "").strip()
        if not question:
            return error(400, "bad_question", "Please ask a short question about what happened.")
        if not store.recent:
            return error(409, "no_frames", "I don't have any pictures from any camera in the last 5 minutes.")
        answer, seen, camera_id = LOOK_BACK
        camera, at = store.get_camera(camera_id), now()
        key = f"looks/{camera_id}/back.jpg"
        (store.media_dir / key).parent.mkdir(parents=True, exist_ok=True)
        (store.media_dir / key).write_bytes(b"jpeg")
        people = [{"person_id": p, "name": store.people[p]["name"], "role": store.people[p]["role"],
                   "camera_id": camera_id} for p in seen]
        return JSONResponse({"question": question, "answer": answer, "asks_earlier": "ago" in question,
                             "window_s": RECENT_WINDOW_S, "window": "5 minutes", "camera_id": camera_id,
                             "camera": camera["label"], "online": camera["online"], "frame_at": at,
                             "frame_url": f"/media/{key}", "frame_source": "recent", "people": people,
                             "looked_at": [], "took_s": {"faces": 0.0, "model": 0.0}})

    async def state_of(request: Request) -> JSONResponse:
        """POST /api/state-of: the newest recorded state whose thing shares a word with the one asked about, else the
        dataset's look answer for the Door camera (as the core's video check, not stored)."""
        body = await request.json()
        question, thing = (body.get("question") or "").strip(), (body.get("thing") or "").lower()
        if not question:
            return error(400, "bad_question", "Please ask a short question about how something is.")
        wanted = set(thing.split()) - {"the", "my", "a"}
        for s in store.states:
            if wanted & set(s["thing"].lower().split()):
                person = store.people.get(s["by"], {})
                return JSONResponse({"source": "state", "question": question, "thing": s["thing"], "value": s["value"],
                                     "at": s["at"], "by": s["by"], "by_name": person.get("name"),
                                     "by_role": person.get("role"), "camera_id": s["camera_id"],
                                     "camera": store.get_camera(s["camera_id"])["label"], "event_id": s["event_id"],
                                     "clip_url": f"/media/{s['clip_key']}"})
        answer, _ = LOOKS["door"]
        camera = store.get_camera("door")
        return JSONResponse({"source": "video", "stored": False, "question": question, "thing": thing, "answer": answer,
                             "camera_id": "door", "camera": camera["label"], "online": camera["online"],
                             "frame_at": now(), "frame_url": "/media/looks/door/now.jpg", "people": []})

    async def status(request: Request) -> JSONResponse:
        """GET /api/status (the fields the voice side reads)."""
        return JSONResponse({"active": store.active_session() is not None, "recent_window_s": RECENT_WINDOW_S})

    return Starlette(routes=[
        Route("/api/watch-orders", watch_orders, methods=["POST"]),
        Route("/api/sessions/latest", latest_session),
        Route("/api/sessions/{session_id}/events", session_events),
        Route("/api/sessions/{session_id}/end", end_session, methods=["POST"]),
        Route("/api/sessions/{session_id}/caught-up", caught_up, methods=["POST"]),
        Route("/api/cameras", cameras),
        Route("/api/events/latest", latest_event),
        Route("/api/look", look, methods=["POST"]),
        Route("/api/look-back", look_back, methods=["POST"]),
        Route("/api/status", status),
        Route("/api/state-of", state_of, methods=["POST"]),
        Mount("/media", StaticFiles(directory=store.media_dir, check_dir=False)),
    ])
