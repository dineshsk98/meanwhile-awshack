"""The home-screen devserver: /voice/state, the /media proxy and the page, with MCP calls in the same process."""

from __future__ import annotations

import asyncio

import httpx
from mcp import Client

from meanwhile.voice import devserver
from meanwhile.voice.core_client import CoreClient
from scenarios import fake_core

from conftest import _free_port, _serve


def _state(url: str) -> dict:
    """GET /voice/state and return its JSON."""
    reply = httpx.get(f"{url}/voice/state")
    assert reply.status_code == 200, reply.text
    return reply.json()


def _call(url: str, *calls: tuple[str, dict]) -> list[dict]:
    """Call MCP tools on the devserver's /mcp in order; return their structured content."""
    async def go():
        async with Client(f"{url}/mcp", mode="legacy") as client:
            out = []
            for tool, args in calls:
                result = await client.call_tool(tool, args)
                assert not result.is_error, result.content
                out.append(result.structured_content)
            return out
    return asyncio.run(go())


def test_state_with_no_session(devserver_url, core_store):
    """Before anyone steps away: no session, the empty card, the cameras, nothing on display."""
    fake_core.seed(core_store)
    state = _state(devserver_url)
    assert state["active"] is False and state["session"] is None
    assert state["card"]["heading"] == "Not watching anything right now."
    assert state["card"]["said"] is None and state["card"]["empty"]
    assert state["events"] == []
    assert {c["label"] for c in state["cameras"]} == {"Desk", "Room", "Door"}
    assert state["display"]["cmd"] == "clear" and state["display"]["clips"] == []


def test_state_follows_the_object_scenario(devserver_url, core_store):
    """A step_away over the same process's /mcp fills the card; the events carry amber and green banners."""
    fake_core.seed(core_store)
    [away] = _call(devserver_url, ("step_away", {"instruction": "keep an eye on my watch"}))
    assert away["status"] == "watching"
    state = _state(devserver_url)
    assert state["active"] is True
    assert state["card"]["heading"] == "Watching now"
    assert state["card"]["said"] == "“keep an eye on my watch”"
    assert state["card"]["understood"].startswith("I'm keeping an eye on your watch")
    assert state["card"]["state"] == "Your watch is where you left it, on the Desk."

    fake_core.play(core_store, "object", core_store.active_session())
    state = _state(devserver_url)
    tones = [e["banner"]["tone"] if e["banner"] else None for e in state["events"]]
    assert tones == [None, "amber", "green"]
    heads_up = state["events"][1]
    assert heads_up["banner"] == {"tone": "amber", "title": "Heads-up", "say": heads_up["summary"]}
    assert heads_up["camera"] == "Desk" and heads_up["time"]
    assert state["card"]["state"] == "Your watch is back with you."


def test_show_me_changes_the_display_the_page_reads(devserver_url, core_store):
    """show_me over /mcp changes /voice/state's display at once, with playable clip URLs."""
    fake_core.seed(core_store)
    _call(devserver_url, ("step_away", {"instruction": "keep an eye on my watch"}))
    fake_core.play(core_store, "object", core_store.active_session())
    before = _state(devserver_url)["display"]
    [shown] = _call(devserver_url, ("show_me", {"event": "the hand-over"}))
    after = _state(devserver_url)["display"]
    assert after["at"] != before["at"]
    assert after["cmd"] == "show"
    assert [c["event_id"] for c in after["clips"]] == shown["showing"]
    assert all(c["clip_url"].startswith("/media/") for c in after["clips"])


def test_look_puts_the_frame_on_the_page_without_a_session(devserver_url, core_store):
    """look over /mcp, with no session, shows its frame in /voice/state's display, loadable through /media."""
    fake_core.seed(core_store)
    [seen] = _call(devserver_url, ("look", {"question": "do you see me?", "camera": "desk"}))
    state = _state(devserver_url)
    assert state["session"] is None and state["display"]["cmd"] == "show"
    [frame] = state["display"]["clips"]
    assert frame["frame_url"] == seen["frame_url"] and frame["camera_id"] == "desk"
    assert httpx.get(f"{devserver_url}{frame['frame_url']}").content == b"jpeg"


def test_door_watch_card_and_red_alert(devserver_url, core_store):
    """A door watch is an ordinary order: the card says what was understood, and the stranger is a red banner."""
    fake_core.seed(core_store)
    _call(devserver_url, ("step_away", {"instruction": "watch the front door"}))
    fake_core.play(core_store, "door", core_store.active_session())
    state = _state(devserver_url)
    assert state["card"]["understood"].startswith("I'm watching the front door")
    reds = [e for e in state["events"] if e["banner"] and e["banner"]["tone"] == "red"]
    assert [e["summary"] for e in reds] == ["Someone I don't recognise tried to open the front door."]


def test_ended_session_stays_on_the_card(devserver_url, core_store):
    """After im_back the card shows the last session as ended."""
    fake_core.seed(core_store)
    _call(devserver_url, ("step_away", {"instruction": "take notes of who comes into the room"}), ("im_back", {}))
    state = _state(devserver_url)
    assert state["active"] is False
    assert state["card"]["heading"].startswith("Last time")
    assert state["card"]["state"] is None
    assert state["card"]["empty"] == "Nothing happened."


def test_media_is_proxied_with_range(devserver_url, core_store):
    """/media/* streams the core's file, honouring Range so <video> can seek."""
    clip = core_store.media_dir / "clips" / "desk" / "test.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(bytes(range(256)) * 4)
    whole = httpx.get(f"{devserver_url}/media/clips/desk/test.mp4")
    assert whole.status_code == 200 and whole.content == clip.read_bytes()
    assert whole.headers["content-type"] == "video/mp4"
    part = httpx.get(f"{devserver_url}/media/clips/desk/test.mp4", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and part.content == clip.read_bytes()[:100]
    assert part.headers["content-range"].startswith("bytes 0-99/")
    assert httpx.get(f"{devserver_url}/media/clips/desk/missing.mp4").status_code == 404


def test_serves_the_home_page(devserver_url):
    """/ serves the page with the Alexa panel slot, and its scripts and styles."""
    page = httpx.get(f"{devserver_url}/")
    assert page.status_code == 200
    assert 'id="alexa-panel"' in page.text and 'id="replay-video"' in page.text
    for path in ("/js/home.js", "/js/api.js", "/css/style.css"):
        assert httpx.get(f"{devserver_url}{path}").status_code == 200, path


def test_core_down_is_a_503_not_a_crash(screen):
    """With no core listening, /voice/state answers 503 core_unreachable."""
    nobody = f"http://127.0.0.1:{_free_port()}"
    server, thread, url = _serve(devserver.http_app(CoreClient(nobody), screen))
    try:
        reply = httpx.get(f"{url}/voice/state")
        assert reply.status_code == 503 and reply.json()["error"] == "core_unreachable"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_a_clip_shared_by_several_events_plays_once(screen):
    """Events that share one clip file put it on screen once, in order."""
    screen.show([{"event_id": "e1", "clip_url": "/media/a.mp4"}, {"event_id": "e2", "clip_url": "/media/a.mp4"},
                 {"event_id": "e3", "clip_url": "/media/b.mp4"}])
    assert [c["event_id"] for c in screen.display["clips"]] == ["e1", "e3"]


class _RecordingAlexa:
    """A stand-in simulated Alexa that records the MCP address it was asked to use."""

    def __init__(self):
        """Start with no recorded address."""
        self.mcp_url = None

    async def ask(self, text: str, mcp_url: str) -> dict:
        """Remember the address and answer with a fixed reply."""
        self.mcp_url = mcp_url
        return {"heard": text, "reply": "ok"}


def test_ask_uses_the_servers_own_address_behind_an_https_proxy():
    """Behind an https proxy, /voice/ask still calls this server's own plain-http /mcp, not the public https URL."""
    from starlette.testclient import TestClient

    alexa = _RecordingAlexa()
    app = devserver.http_app(core=CoreClient("http://127.0.0.1:1"), alexa=alexa, self_url="http://127.0.0.1:8001")
    with TestClient(app, base_url="https://alexa.example.test") as client:
        reply = client.post("/voice/ask", json={"text": "what are you watching?"})
    assert reply.status_code == 200
    assert alexa.mcp_url == "http://127.0.0.1:8001/mcp"
