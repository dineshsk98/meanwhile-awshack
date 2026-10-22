"""The MCP server Alexa+ (or the simulated panel) calls: eight voice tools over Streamable HTTP.
Run: python -m meanwhile.voice.mcp_server (http://127.0.0.1:MEANWHILE_VOICE_PORT/mcp).
"""

from __future__ import annotations

import functools
from datetime import datetime, timezone
import logging
import re
import time
from typing import Any, Callable, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import BaseModel

from meanwhile.voice import config, wording
from meanwhile.voice.core_client import CoreClient, CoreError
from meanwhile.voice.screen import Screen

log = logging.getLogger("meanwhile.voice")

last_call_ms: dict[str, float] = {}

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
SCREEN_ONLY = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


class EventItem(BaseModel):
    """One event as a tool returns it."""
    event_id: str
    time: str
    camera: str
    summary: str
    alert: str
    clip_url: str | None = None


class StepAwayResult(BaseModel):
    """What step_away returns."""
    status: Literal["watching", "clarify", "not_found", "already_watching"]
    spoken: str
    session_id: str | None = None
    kind: str | None = None
    watching: str | None = None
    cameras: list[str] = []
    question: str | None = None


class CatchUpResult(BaseModel):
    """What what_did_i_miss returns; `looked_back` when, with no session to read, it answered from the cameras'
    recent window instead."""
    spoken: str
    session_id: str | None = None
    session_active: bool = False
    events: list[EventItem] = []
    offline_cameras: list[str] = []
    looked_back: bool = False


class ShowMeResult(BaseModel):
    """What show_me returns: the event ids now on screen."""
    spoken: str
    showing: list[str] = []


class ImBackResult(BaseModel):
    """What im_back returns."""
    spoken: str
    session_id: str | None = None
    ended: bool = False
    event_count: int = 0
    alert_count: int = 0


class CameraItem(BaseModel):
    """One camera as status returns it."""
    camera_id: str
    label: str
    online: bool


class StatusResult(BaseModel):
    """What status returns."""
    spoken: str
    session_active: bool
    instruction: str | None = None
    watching: str | None = None
    object_state: str | None = None
    cameras: list[CameraItem] = []


class StateResult(BaseModel):
    """What ask_state returns: where the answer came from (the last recorded state, or the video), the thing, its
    state, when and by whom when recorded, and the video's answer otherwise."""
    spoken: str
    answered: bool
    source: str | None = None
    thing: str | None = None
    value: str | None = None
    time: str | None = None
    by_name: str | None = None
    camera: str | None = None
    answer: str | None = None
    frame_url: str | None = None


class LookResult(BaseModel):
    """What look returns: the answer about what a camera sees now, and the frame it is about (shown on screen)."""
    spoken: str
    answered: bool
    answer: str | None = None
    camera: str | None = None
    camera_id: str | None = None
    online: bool | None = None
    time: str | None = None
    frame_url: str | None = None
    people: list[str] = []


class LookBackResult(BaseModel):
    """What look_back returns: the answer about the recent past, the window it covers, and the frame shown."""
    spoken: str
    answered: bool
    answer: str | None = None
    asks_earlier: bool = False
    window: str | None = None
    camera: str | None = None
    camera_id: str | None = None
    time: str | None = None
    frame_url: str | None = None
    people: list[str] = []


def _timed(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Record and log each tool call's server-side duration in ms."""
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            ms = (time.perf_counter() - start) * 1000
            last_call_ms[fn.__name__] = ms
            log.info("tool=%s ms=%.1f", fn.__name__, ms)

    return wrapper


def _labels(cameras: list[dict]) -> dict[str, str]:
    """Map camera ids to their labels."""
    return {c["camera_id"]: c.get("label") or c["camera_id"] for c in cameras}


def _session_camera_ids(session: dict, cameras: list[dict]) -> list[str]:
    """The ids of the cameras a session's watch order uses."""
    wanted = session.get("watch_order", {}).get("cameras", "all")
    ids = [c["camera_id"] for c in cameras]
    return ids if wanted == "all" else [c for c in wanted if c in ids]


def _event_item(event: dict, labels: dict[str, str]) -> EventItem:
    """Turn a core event into an EventItem."""
    return EventItem(
        event_id=event["event_id"],
        time=wording.spoken_time(wording.happened_at(event)),
        camera=labels.get(event.get("camera_id", ""), event.get("camera_id", "")),
        summary=event.get("summary", ""),
        alert=event.get("alert", "none"),
        clip_url=event.get("clip_url"),
    )


def _clip(event: dict) -> dict:
    """The fields of an event the home screen needs to play its clip."""
    keep = ("event_id", "ts", "camera_id", "summary", "alert", "clip_url")
    return {k: event[k] for k in keep if k in event}


def _match_events(events: list[dict], query: str) -> list[dict]:
    """Events whose summary or action contains a word of the query ('the hand-over' finds 'handed')."""
    skip = {"the", "and", "show", "clip", "clips", "over", "when", "what", "that", "who", "me"}
    words = [w for w in re.findall(r"[a-z]+", query.lower()) if len(w) > 2 and w not in skip]
    hits = []
    for e in events:
        text = f'{e.get("summary", "")} {e.get("action", "")}'.lower().replace("_", " ")
        if any(w in text for w in words):
            hits.append(e)
    return hits


def _frame(body: dict) -> dict:
    """A look or look-back answer's frame as the home screen shows it."""
    return {"event_id": f'look:{body["camera_id"]}:{body["frame_at"]}', "ts": body["frame_at"],
            "camera_id": body["camera_id"], "summary": body["answer"], "frame_url": body["frame_url"]}


def _older(ts: str, window_s: float) -> bool:
    """Whether a timestamp is further back than the look-back window."""
    return (datetime.now(timezone.utc) - wording.parse(ts)).total_seconds() > window_s


def _stale(session: dict, window_s: float) -> bool:
    """Whether an ended session ended longer ago than the look-back window."""
    return bool(session.get("ended_at")) and _older(session["ended_at"], window_s)


def build_server(core: CoreClient | None = None, screen: Screen | None = None) -> MCPServer:
    """Build the MCP server with its eight tools, reading the core through `core`."""
    core = core or CoreClient()
    screen = screen or Screen()
    mcp = MCPServer(name="meanwhile", title="Meanwhile", instructions=wording.SERVER_INSTRUCTIONS, version="0.1.0")
    mcp.screen = screen
    desc = wording.TOOL_DESCRIPTIONS

    @mcp.tool(description=desc["step_away"], annotations=WRITES)
    @_timed
    def step_away(instruction: str) -> StepAwayResult:
        """Start an away session from the user's instruction."""
        reply = core.start_watch(instruction)
        if reply.already is not None:
            current = reply.already.get("watch_order", {})
            cameras = core.cameras()
            labels = _labels(cameras)
            return StepAwayResult(
                status="already_watching",
                session_id=reply.already.get("session_id"),
                kind=current.get("kind"),
                watching=current.get("summary_for_alexa"),
                cameras=[labels[c] for c in _session_camera_ids(reply.already, cameras)] if reply.already else [],
                spoken=wording.say_step_away(
                    "already_watching",
                    summary_for_alexa=current.get("summary_for_alexa", ""),
                    current_instruction=reply.already.get("instruction", ""),
                ),
            )
        if reply.clarify:
            return StepAwayResult(
                status="clarify", question=reply.clarify,
                spoken=wording.say_step_away("clarify", question=reply.clarify),
            )
        if reply.not_found:
            return StepAwayResult(
                status="not_found",
                spoken=wording.say_step_away("not_found", not_found=reply.not_found),
            )
        session = reply.session
        order = session["watch_order"]
        cameras = core.cameras()
        labels = _labels(cameras)
        screen.clear()
        return StepAwayResult(
            status="watching",
            session_id=session["session_id"],
            kind=order.get("kind"),
            watching=order.get("summary_for_alexa"),
            cameras=[labels[c] for c in _session_camera_ids(session, cameras)],
            spoken=wording.say_step_away("watching", summary_for_alexa=order.get("summary_for_alexa", "")),
        )

    @mcp.tool(description=desc["what_did_i_miss"], annotations=SCREEN_ONLY)
    @_timed
    def what_did_i_miss(detail: bool = False) -> CatchUpResult:
        """Say what the user hasn't been told yet (every such event if `detail`) and put their clips on screen, then
        tell the core they were told. Each session's events are told once: a session that ended before the
        look-back window counts as told, and "yes" to the details right after a catch-up reads the session again.
        With nothing untold, say what the cameras saw in the look-back window, else that nothing is new."""
        session, active = core.latest_session()
        events = core.events(session["session_id"]) if session else []
        told = (session or {}).get("caught_up_until") or ""
        tell = [e for e in events if e["ts"] > told]
        if session and (detail and session.get("caught_up_at") or not active and tell):
            window_s = core.recent_window_s()
            if detail and session.get("caught_up_at") and not _older(session["caught_up_at"], window_s):
                tell = events
            elif not active and _stale(session, window_s):
                tell = []
        offline: list[str] = []
        if session and (tell or active):
            cameras = core.cameras()
            labels = _labels(cameras)
            online = {c["camera_id"]: c.get("online", False) for c in cameras}
            offline = [labels[c] for c in _session_camera_ids(session, cameras) if not online[c]]
        if session and tell:
            screen.show([_clip(e) for e in tell if e.get("clip_url")])
            spoken = wording.say_catch_up(session, tell, offline, detail=detail)
            try:
                core.caught_up(session["session_id"], max(e["ts"] for e in tell))
            except (CoreError, httpx.HTTPError) as exc:
                log.warning("what_did_i_miss: couldn't mark the catch-up as told: %s", exc)
            return CatchUpResult(session_id=session["session_id"], session_active=active,
                                 events=[_event_item(e, labels) for e in tell], offline_cameras=offline, spoken=spoken)
        status, body = core.look_back(wording.CATCH_UP_QUESTION)
        if status == 200:
            screen.show_frame(_frame(body))
            return CatchUpResult(looked_back=True, session_active=active, offline_cameras=offline,
                                 spoken=wording.say_catch_up_recent(body["answer"], body["window"],
                                                                    body["asks_earlier"], watching=active,
                                                                    offline_cameras=offline))
        if session is None:
            return CatchUpResult(spoken=wording.say_catch_up(None, [], []))
        return CatchUpResult(session_id=session["session_id"], session_active=active, offline_cameras=offline,
                             spoken=wording.say_nothing_new(active, offline))

    @mcp.tool(description=desc["show_me"], annotations=SCREEN_ONLY)
    @_timed
    def show_me(event: str | None = None) -> ShowMeResult:
        """Put clips on screen: a given event, events matching words, or the last thing talked about."""
        session, _ = core.latest_session()
        events = core.events(session["session_id"]) if session else []
        chosen: list[dict] = []
        if event and (by_id := [e for e in events if e["event_id"] == event]):
            chosen = by_id
        elif event and event.strip().lower() not in {"it", "that", "them", "this", "those"}:
            chosen = _match_events(events, event)
        if not chosen:
            chosen = screen.last_referenced or events
        chosen = [_clip(e) for e in chosen if e.get("clip_url")]
        screen.show(chosen)
        return ShowMeResult(showing=[e["event_id"] for e in chosen], spoken=wording.say_show_me(chosen))

    @mcp.tool(description=desc["im_back"], annotations=WRITES)
    @_timed
    def im_back() -> ImBackResult:
        """End the active session and say how much happened."""
        session, active = core.latest_session()
        ended = core.end_session(session["session_id"]) if session and active else None
        if ended is None:
            return ImBackResult(spoken=wording.say_im_back(None, []))
        events = core.events(ended["session_id"])
        return ImBackResult(
            session_id=ended["session_id"],
            ended=True,
            event_count=len(events),
            alert_count=sum(1 for e in events if e.get("alert", "none") != "none"),
            spoken=wording.say_im_back(ended, events),
        )

    @mcp.tool(description=desc["status"], annotations=READ_ONLY)
    @_timed
    def status() -> StatusResult:
        """Say what is being watched and which cameras are live."""
        session, active = core.latest_session()
        session = session if active else None
        items = [
            CameraItem(camera_id=c["camera_id"], label=c.get("label") or c["camera_id"], online=bool(c.get("online")))
            for c in core.cameras()
        ]
        return StatusResult(
            session_active=session is not None,
            instruction=session["instruction"] if session else None,
            watching=session["watch_order"].get("summary_for_alexa") if session else None,
            object_state=session.get("state") if session else None,
            cameras=items,
            spoken=wording.say_status(
                session, [i.label for i in items if i.online], [i.label for i in items if not i.online]
            ),
        )

    @mcp.tool(description=desc["ask_state"], annotations=SCREEN_ONLY)
    @_timed
    def ask_state(question: str, thing: str, camera: str | None = None) -> StateResult:
        """Answer how a thing is from its stored status, else from the core's video check (showing its frame)."""
        status, body = core.state_of(question, thing, camera)
        if body.get("error") == "stale_frame":
            return StateResult(answered=False, camera=body.get("camera"),
                               spoken=wording.say_no_fresh_picture(body["camera"], body["last_frame_at"]))
        if status != 200:
            return StateResult(answered=False, spoken=wording.say_look_failed(body.get("message", "")))
        source = body.get("source")
        if source == "state":
            by_name = "you" if body.get("by_role") == "owner" else body.get("by_name")
            if body.get("clip_url"):
                screen.remember([{"event_id": body.get("event_id"), "ts": body["at"], "camera_id": body["camera_id"],
                                  "summary": f'{body["thing"]} {body["value"]}', "clip_url": body["clip_url"]}])
            return StateResult(
                answered=True, source=source, thing=body["thing"], value=body["value"],
                time=wording.spoken_time(body["at"]), by_name=by_name, camera=body.get("camera"),
                spoken=wording.say_state(question, body["thing"], body["value"], body["at"], by_name,
                                         body.get("checked_by") == "video"),
            )
        screen.show_frame(_frame(body))
        return StateResult(answered=True, source=source, thing=body.get("thing"), value=body.get("value"),
                           camera=body.get("camera"), answer=body["answer"], frame_url=body.get("frame_url"),
                           spoken=body["answer"])

    @mcp.tool(description=desc["look"], annotations=SCREEN_ONLY)
    @_timed
    def look(question: str, camera: str | None = None) -> LookResult:
        """Answer a question about what a camera (or every online camera) sees now, and show that frame."""
        status, body = core.look(question, camera)
        if status != 200:
            return LookResult(answered=False, spoken=wording.say_look_failed(body.get("message", "")))
        when = wording.spoken_time(body["frame_at"])
        screen.show_frame(_frame(body))
        return LookResult(
            answered=True, answer=body["answer"], camera=body["camera"], camera_id=body["camera_id"],
            online=body["online"], time=when, frame_url=body["frame_url"],
            people=[p["name"] for p in body.get("people", []) if p.get("name")],
            spoken=wording.say_look(body["answer"], body["camera"], body["online"], body["frame_at"]),
        )

    @mcp.tool(description=desc["look_back"], annotations=SCREEN_ONLY)
    @_timed
    def look_back(question: str, camera: str | None = None) -> LookBackResult:
        """Answer a question about what happened in the last few minutes on a camera (or every camera), and show
        the frame that best shows it."""
        status, body = core.look_back(question, camera)
        if status != 200:
            return LookBackResult(answered=False, spoken=wording.say_look_failed(body.get("message", "")))
        screen.show_frame(_frame(body))
        return LookBackResult(
            answered=True, answer=body["answer"], asks_earlier=body["asks_earlier"], window=body["window"],
            camera=body["camera"], camera_id=body["camera_id"], time=wording.spoken_time(body["frame_at"]),
            frame_url=body["frame_url"], people=[p["name"] for p in body.get("people", []) if p.get("name")],
            spoken=wording.say_look_back(body["answer"], body["window"], body["asks_earlier"]),
        )

    return mcp


def http_app(core: CoreClient | None = None, screen: Screen | None = None, extra_hosts: list[str] | None = None):
    """The Starlette app serving /mcp, stateless with JSON responses. Only localhost Host headers are accepted,
    plus extra_hosts (e.g. the laptop's Wi-Fi address, so a phone can connect)."""
    hosts = ["127.0.0.1", "localhost", "[::1]", *(extra_hosts or [])]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{h}:*" for h in hosts],
        allowed_origins=[f"http://{h}:*" for h in hosts],
    )
    return build_server(core, screen).streamable_http_app(
        stateless_http=True, json_response=True, transport_security=security)


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    uvicorn.run(http_app(), host="127.0.0.1", port=config.PORT)
