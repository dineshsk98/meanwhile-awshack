"""The eight voice tools, called by a real MCP client over HTTP against the fake core."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import httpx
from mcp import Client

from meanwhile.voice import mcp_server
from scenarios import fake_core
from scenarios.fake_core import parse, to_iso

from conftest import fixed_understand

TOOLS = {"step_away", "what_did_i_miss", "show_me", "im_back", "status", "ask_state", "look", "look_back"}
READ_TOOLS = {"what_did_i_miss", "show_me", "status"}
LIMIT_MS = 500


class Alexa:
    """A minimal MCP client, like Alexa+ or the panel, that times each call."""
    def __init__(self, url: str):
        """Remember the server URL."""
        self.url = url
        self.client_ms: dict[str, float] = {}

    async def __aenter__(self):
        """Connect."""
        self.client = Client(self.url, mode="legacy")
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *exc):
        """Disconnect."""
        await self.client.__aexit__(*exc)

    async def call(self, tool: str, **args) -> dict:
        """Call a tool and return its structured content."""
        start = time.perf_counter()
        result = await self.client.call_tool(tool, args)
        self.client_ms[tool] = (time.perf_counter() - start) * 1000
        assert not result.is_error, result.content
        return result.structured_content


def assert_fast(alexa: Alexa) -> None:
    """Every read tool called answered within LIMIT_MS, server-side and end to end."""
    called = READ_TOOLS & alexa.client_ms.keys()
    assert called
    for tool in called:
        assert mcp_server.last_call_ms[tool] < LIMIT_MS, (tool, mcp_server.last_call_ms[tool])
        assert alexa.client_ms[tool] < LIMIT_MS, (tool, alexa.client_ms[tool])


def test_lists_eight_tools_with_descriptions_and_output_schemas(mcp_url):
    """tools/list returns the eight tools, described, with output schemas."""
    async def go():
        async with Client(mcp_url, mode="legacy") as client:
            return (await client.list_tools()).tools

    tools = {t.name: t for t in asyncio.run(go())}
    assert set(tools) == TOOLS
    for tool in tools.values():
        assert tool.description and tool.output_schema, tool.name
    assert tools["status"].annotations.read_only_hint is True
    assert tools["step_away"].input_schema["required"] == ["instruction"]
    assert tools["look"].input_schema["required"] == ["question"]
    assert tools["ask_state"].input_schema["required"] == ["question", "thing"]
    assert tools["look_back"].input_schema["required"] == ["question"]


def test_accepts_alexa_initialize_2025_03_26(mcp_url):
    """Alexa+'s exact initialize request is accepted."""
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {"roots": {"listChanged": True}},
            "clientInfo": {"name": "Alexa+ MCP Client", "version": "1.0.0"},
        },
    }
    reply = httpx.post(mcp_url, json=body, headers=headers)
    assert reply.status_code == 200
    result = reply.json()["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert "tools" in result["capabilities"]


def test_nothing_watched_yet(mcp_url, core_store):
    """Every tool answers sensibly before any session."""
    async def go():
        async with Alexa(mcp_url) as alexa:
            return [await alexa.call(t) for t in ("what_did_i_miss", "im_back", "status", "show_me")]

    missed, back, status, show = asyncio.run(go())
    assert missed["session_id"] is None and missed["events"] == []
    assert back["ended"] is False
    assert status["session_active"] is False
    assert show["showing"] == []
    assert all(r["spoken"] for r in (missed, back, status, show))


def test_unclear_instruction_asks_one_question(mcp_url, core_store):
    """Unclear, unknown-camera and not-found instructions start no session."""
    fake_core.seed(core_store)

    async def go():
        async with Alexa(mcp_url) as alexa:
            return (
                await alexa.call("step_away", instruction="watch it"),
                await alexa.call("step_away", instruction="keep an eye on my keys on the garage camera"),
                await alexa.call("step_away", instruction="keep an eye on my umbrella"),
            )

    unclear, no_camera, not_found = asyncio.run(go())
    assert unclear["status"] == "clarify" and unclear["question"]
    assert no_camera["status"] == "clarify" and "garage" in no_camera["question"]
    assert not_found["status"] == "not_found"
    assert core_store.active_session() is None


def test_a_question_never_starts_a_session(mcp_url, core_store):
    """Questions get a clarifying reply; polite requests still start one."""
    fake_core.seed(core_store)

    async def go():
        async with Alexa(mcp_url) as alexa:
            asked = [await alexa.call("step_away", instruction=q) for q in (
                "Did I lock the door when leaving?", "is the front door locked", "who took my watch?")]
            polite = await alexa.call("step_away", instruction="Can you keep an eye on my watch?")
            return asked, polite

    asked, polite = asyncio.run(go())
    assert [a["status"] for a in asked] == ["clarify"] * 3
    assert "question" in asked[0]["question"]
    assert polite["status"] == "watching"


def test_already_watching_says_what_it_is_doing(mcp_url, core_store):
    """A second step_away says, and returns, what is being watched, not the raw words."""
    fake_core.seed(core_store)

    async def go():
        async with Alexa(mcp_url) as alexa:
            await alexa.call("step_away", instruction="keep an eye on the front door")
            return await alexa.call("step_away", instruction="keep an eye on my watch")

    again = asyncio.run(go())
    assert again["status"] == "already_watching"
    assert again["spoken"].startswith("I'm still watching the front door")
    assert "keep an eye on the front door" not in again["spoken"]
    assert again["kind"] == "monitor" and again["watching"].startswith("watching the front door")
    assert again["cameras"] == ["Desk", "Door", "Room"]


def test_object_scenario_end_to_end(mcp_url, core_store, screen):
    """The object scenario: step away, events, catch-up, show me, I'm back."""
    fake_core.seed(core_store)

    async def go():
        async with Alexa(mcp_url) as alexa:
            away = await alexa.call("step_away", instruction="keep an eye on my watch")
            again = await alexa.call("step_away", instruction="take notes")
            fake_core.play(core_store, "object", core_store.active_session())
            status = await alexa.call("status")
            missed = await alexa.call("what_did_i_miss")
            shown_after_miss = dict(screen.display)
            handover = await alexa.call("show_me", event="the hand-over")
            shown_after_show_me = dict(screen.display)
            back = await alexa.call("im_back")
            missed_after_back = await alexa.call("what_did_i_miss")
            return alexa, away, again, status, missed, shown_after_miss, handover, shown_after_show_me, back, missed_after_back

    alexa, away, again, status, missed, shown, handover, shown_handover, back, after = asyncio.run(go())

    assert away["status"] == "watching" and away["kind"] == "object"
    assert away["watching"] == "keeping an eye on your watch on the Desk camera"
    assert away["spoken"] == "Sure. I'm keeping an eye on your watch on the Desk camera."
    assert again["status"] == "already_watching"

    assert status["session_active"] and status["object_state"] == "with_owner"
    assert all(c["online"] for c in status["cameras"])

    summaries = [e["summary"] for e in missed["events"]]
    assert summaries == [
        "Ravi came into the room.",
        "Your watch was picked up by Ravi.",
        "Ravi handed your watch back to you. Not taken.",
    ]
    assert [e["alert"] for e in missed["events"]] == ["none", "heads_up", "verdict"]
    assert shown["cmd"] == "show" and len(shown["clips"]) == 3
    assert all(c["clip_url"].startswith("/media/clips/") for c in shown["clips"])

    assert len(handover["showing"]) == 1
    assert core_store.get_event(handover["showing"][0])["action"] == "handed_to"
    assert [c["event_id"] for c in shown_handover["clips"]] == handover["showing"]

    assert back["ended"] and back["event_count"] == 3 and back["alert_count"] == 2
    assert core_store.active_session() is None
    assert after["session_active"] is False and after["events"] == [] and after["looked_back"] is True

    assert_fast(alexa)


def test_notes_scenario_never_alerts(mcp_url, core_store):
    """The notes scenario produces events without alerts."""
    fake_core.seed(core_store)

    async def go():
        async with Alexa(mcp_url) as alexa:
            away = await alexa.call("step_away", instruction="take notes of who comes into the room and what they do")
            fake_core.play(core_store, "notes", core_store.active_session())
            return away, await alexa.call("what_did_i_miss")

    away, missed = asyncio.run(go())
    assert away["kind"] == "notes" and away["cameras"] == ["Room"]
    assert len(missed["events"]) == 3
    assert {e["alert"] for e in missed["events"]} == {"none"}


def test_door_is_an_ordinary_watch_and_its_lock_is_a_state_lookup(mcp_url, core_store):
    """"Keep an eye on the front door" is an ordinary order; the catch-up reads its events; the lock event recorded
    the door's state, so "is the front door locked?" answers from it (who and when), and "show me" plays its clip."""
    fake_core.seed(core_store)

    async def go():
        async with Alexa(mcp_url) as alexa:
            away = await alexa.call("step_away", instruction="keep an eye on the front door")
            fake_core.play(core_store, "door", core_store.active_session())
            await alexa.call("im_back")
            missed = await alexa.call("what_did_i_miss", detail=True)
            state = await alexa.call("ask_state", question="is the front door locked?", thing="the front door")
            show = await alexa.call("show_me")
            return alexa, away, missed, state, show

    alexa, away, missed, state, show = asyncio.run(go())
    assert_fast(alexa)
    assert away["kind"] == "monitor" and away["watching"].startswith("watching the front door")
    assert "Ravi locked the front door." in missed["spoken"]
    assert state["source"] == "state" and state["value"] == "locked" and state["by_name"] == "Ravi"
    assert state["spoken"] == f"Yes, the front door is locked. The last change I saw was at {state['time']}, by Ravi."
    assert len(show["showing"]) == 1 and core_store.get_event(show["showing"][0])["state"] == "locked"


def test_ask_state_with_nothing_recorded_looks_at_the_camera(mcp_url, core_store, screen):
    """With no stored status, ask_state speaks the core's video check and shows its frame."""
    fake_core.seed(core_store)

    async def go():
        async with Alexa(mcp_url) as alexa:
            return await alexa.call("ask_state", question="is the lamp on?", thing="the lamp")

    answer = asyncio.run(go())
    assert answer["source"] == "video" and answer["spoken"] == "Nobody is at the front door right now."
    assert screen.display["clips"][0]["frame_url"] == answer["frame_url"]


def test_catch_up_names_offline_cameras(mcp_url, core_url, core_store):
    """Catch-up and status name an offline camera."""
    fake_core.seed(core_store)
    _start(core_store, "keep an eye on my watch")
    core_store.set_offline("room")

    async def go():
        async with Alexa(mcp_url) as alexa:
            return await alexa.call("what_did_i_miss"), await alexa.call("status")

    missed, status = asyncio.run(go())
    assert missed["offline_cameras"] == ["Room"]
    assert [c["online"] for c in status["cameras"] if c["label"] == "Room"] == [False]


def _start(core_store, instruction):
    """Start a session directly in the fake core's store."""
    return core_store.start_session(instruction, fake_core.compile_order(instruction, core_store.list_cameras(), fixed_understand))


def test_catch_up_is_brief_unless_details_are_asked(mcp_url, core_store):
    """what_did_i_miss answers in a line or two with an offer; detail=true reads every event with times."""
    fake_core.seed(core_store)
    session = _start(core_store, "keep an eye on my watch")
    fake_core.play(core_store, "object", session)

    async def go():
        async with Alexa(mcp_url) as alexa:
            return await alexa.call("what_did_i_miss"), await alexa.call("what_did_i_miss", detail=True)

    brief, full = asyncio.run(go())
    assert brief["spoken"] == "Your watch is back with you. Ravi picked it up and handed it back to you. Want the details?"
    assert full["spoken"].startswith("While you were away, at ") and "Not taken." in full["spoken"]
    assert len(brief["events"]) == len(full["events"]) == 3


def test_look_answers_from_the_camera_and_shows_its_frame(mcp_url, core_store, screen):
    """"Do you see me?" on the Desk camera: the core's answer is spoken, the frame goes on screen, and "show me"
    still means the last clips talked about, not the frame."""
    fake_core.seed(core_store)
    screen.remember([{"event_id": "evt_1", "clip_url": "/media/clips/desk/1.mp4"}])

    async def go():
        async with Alexa(mcp_url) as alexa:
            return await alexa.call("look", question="do you see me?", camera="desk")

    seen = asyncio.run(go())
    assert seen["answered"] is True and seen["spoken"] == "Yes, I can see you at the desk."
    assert seen["camera"] == "Desk" and seen["people"] == ["Owner"] and seen["online"] is True
    shown = screen.display["clips"]
    assert screen.display["cmd"] == "show" and len(shown) == 1 and "clip_url" not in shown[0]
    assert shown[0]["frame_url"] == seen["frame_url"] and shown[0]["summary"] == seen["answer"]
    assert screen.last_referenced == [{"event_id": "evt_1", "clip_url": "/media/clips/desk/1.mp4"}]


def test_look_without_a_camera_and_through_an_offline_one(mcp_url, core_store):
    """No camera named: the first online camera answers; a named offline camera says it is offline and when its last
    picture was taken; an unknown camera gets the core's sentence."""
    fake_core.seed(core_store)
    core_store.set_offline("desk")

    async def go():
        async with Alexa(mcp_url) as alexa:
            return (await alexa.call("look", question="who's home?"),
                    await alexa.call("look", question="is the lamp on?", camera="desk"),
                    await alexa.call("look", question="is the lamp on?", camera="garage"))

    anywhere, offline, unknown = asyncio.run(go())
    assert anywhere["camera"] == "Door" and anywhere["spoken"] == "Nobody is at the front door right now."
    assert offline["online"] is False
    assert offline["spoken"].startswith(f"The Desk camera is offline, so this is from its last picture, at {offline['time']}.")
    assert unknown["answered"] is False and unknown["spoken"].startswith("I don't have a garage camera.")


def test_look_back_says_what_happened_in_the_window_and_shows_a_frame(mcp_url, core_store, screen):
    """"What did I do?" with no session: the recent window's answer, saying it covers the last 5 minutes, with its
    frame on screen; a question about earlier first says how far back it can see; no pictures speaks the core's
    sentence."""
    fake_core.seed(core_store)

    async def go():
        async with Alexa(mcp_url) as alexa:
            now_ = await alexa.call("look_back", question="what did I do?")
            earlier = await alexa.call("look_back", question="what did I do 10 minutes ago?")
            core_store.recent = False
            empty = await alexa.call("look_back", question="what did I do?")
            return now_, earlier, empty

    now_, earlier, empty = asyncio.run(go())
    assert now_["spoken"] == "In the last 5 minutes, Ravi came into the room and you sat down at the desk."
    assert now_["people"] == ["Ravi", "Owner"] and now_["window"] == "5 minutes"
    assert screen.display["clips"][0]["frame_url"] == now_["frame_url"]
    assert earlier["asks_earlier"] is True
    assert earlier["spoken"].startswith("I can only look back over the last 5 minutes. In the last 5 minutes, Ravi")
    assert empty["answered"] is False and empty["spoken"].startswith("I don't have any pictures")


def _miss(mcp_url, detail=False):
    """Call what_did_i_miss once."""
    async def go():
        async with Alexa(mcp_url) as alexa:
            return await alexa.call("what_did_i_miss", detail=detail)

    return asyncio.run(go())


def test_what_did_i_miss_without_a_session_to_read_looks_back(mcp_url, core_store):
    """No session, an active one with nothing new, or an ended one with no events: the recent window instead (saying
    whether a watch is running); with no recent pictures either, the plain "nothing" reply."""
    fake_core.seed(core_store)
    first = _miss(mcp_url)
    assert first["looked_back"] is True and first["spoken"].startswith("I wasn't watching anything for you, but I can")
    assert "In the last 5 minutes, Ravi came into the room" in first["spoken"]
    session = core_store.start_session("keep an eye on my watch", {"watch_order": {"kind": "object"}})
    quiet = _miss(mcp_url)
    assert quiet["looked_back"] is True and quiet["spoken"].startswith("Nothing new from what I'm watching")
    core_store.update_session(session["session_id"], ended_at=fake_core.now())
    assert _miss(mcp_url)["looked_back"] is True
    core_store.recent = False
    assert _miss(mcp_url)["spoken"] == "Nothing new since I last told you."


def test_a_catch_up_is_told_once(mcp_url, core_store):
    """An ended session's events are told once and marked in the core; asking again gives nothing new, never the
    old session, even when the look-back can't run."""
    fake_core.seed(core_store)
    session = _start(core_store, "keep an eye on my watch")
    fake_core.play(core_store, "object", session)
    core_store.update_session(session["session_id"], ended_at=fake_core.now())
    told = _miss(mcp_url)
    assert len(told["events"]) == 3 and told["looked_back"] is False
    assert core_store.get_session(session["session_id"])["caught_up_until"] == max(e["ts"] for e in
                                                                                  core_store.list_events(session["session_id"]))
    again = _miss(mcp_url)
    assert again["events"] == [] and again["looked_back"] is True
    core_store.recent = False
    nothing = _miss(mcp_url)
    assert nothing["events"] == [] and nothing["spoken"] == "Nothing new since I last told you."


def test_an_old_untold_session_is_not_read_out(mcp_url, core_store):
    """A session that ended before the look-back window counts as told: no session result, even with the look-back
    failing."""
    fake_core.seed(core_store)
    session = _start(core_store, "keep an eye on my watch")
    fake_core.play(core_store, "object", session)
    core_store.update_session(session["session_id"],
                              ended_at=to_iso(parse(fake_core.now()) - timedelta(hours=10)))
    core_store.recent = False
    stale = _miss(mcp_url)
    assert stale["events"] == [] and stale["spoken"] == "Nothing new since I last told you."


def test_an_active_session_tells_only_new_events_and_the_details_repeat(mcp_url, core_store):
    """During a watch, each catch-up tells only what is new; "yes" to the details right after reads the whole
    session again."""
    fake_core.seed(core_store)
    session = _start(core_store, "take notes of who comes into the room")
    played = fake_core.play(core_store, "notes", session)
    first = _miss(mcp_url)
    assert len(first["events"]) == 3
    details = _miss(mcp_url, detail=True)
    assert len(details["events"]) == 3
    later = core_store.add_event({"session_id": session["session_id"], "camera_id": "room", "action": "came back",
                                  "actor_id": "ravi", "summary": "Ravi came back into the room.",
                                  "ts": to_iso(parse(played[-1]["ts"]) + timedelta(seconds=5))})
    core_store.update_session(session["session_id"], caught_up_at=to_iso(parse(fake_core.now()) - timedelta(hours=1)))
    new = _miss(mcp_url)
    assert [e["event_id"] for e in new["events"]] == [later["event_id"]]
    assert "Ravi came back into the room." in new["spoken"]


