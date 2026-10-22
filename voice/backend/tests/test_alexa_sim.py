"""The simulated Alexa+: the reply fact check, /voice/ask through the devserver's own /mcp, and tool choice on the local model."""

from __future__ import annotations

import asyncio
import functools

import httpx
import pytest
from mcp import Client

from meanwhile.voice import alexa_sim, devserver, mcp_server
from meanwhile.voice.alexa_sim import AlexaSim, keeps_facts
from meanwhile.voice.core_client import CoreClient
from scenarios import fake_core

from conftest import _serve

CATCH_UP = ("While you were away, at 4:13 pm, Ravi came into the room. Then your phone was picked up by Ravi. "
            "Then Ravi handed your phone back to you. Not taken.")


@pytest.mark.parametrize("candidate, ok", [
    (CATCH_UP, True),
    ("At 4:13 pm Ravi came into the room. Ravi picked up your phone. Ravi handed it back to you, not taken.", True),
    ("At 4:13 pm Ravi came in. He picked up your phone and handed it back. Not taken.", False),
    ("At 4:13 pm Ravi came into the room, picked up your phone and handed it back to you.", False),
    ("At 4:15 pm Ravi came into the room. Ravi picked up your phone. Ravi handed it back. Not taken.", False),
    ("At 4:13 pm someone came into the room. Your phone was picked up. It came back. Not taken.", False),
    ("", False),
])
def test_fact_check_on_a_catch_up(candidate, ok):
    """A reworded catch-up must keep names, times and negations, use no he/she and add no sentences."""
    assert keeps_facts(CATCH_UP, candidate) is ok


def test_fact_check_refuses_invented_claims():
    """A rewording that adds its own promise is refused; a small rewording is kept."""
    template = "Sure. I'm keeping an eye on your watch on the Desk camera."
    assert not keeps_facts(template, "I'm keeping an eye on your watch on the Desk camera. "
                                     "I'll let you know when it's time to come back.")
    assert keeps_facts("Here's the clip from 3:41 pm. It's on the screen now.",
                       "Here is the clip from 3:41 pm. It is showing on the screen now.")


def test_fact_check_keeps_yes_and_adds_nothing():
    """The door answer keeps its leading Yes and gains no extra sentence; 'I'm' isn't a name."""
    door = "Yes, it's locked. Ravi locked it at 9:20 am."
    assert keeps_facts(door, "Yes, the door is locked. Ravi locked it at 9:20 am.")
    assert not keeps_facts(door, "The door is locked. Ravi locked it at 9:20 am.")
    assert not keeps_facts(door, "Yes, it's locked. Ravi locked it at 9:20 am. You are safe.")
    assert keeps_facts("Sure. I'm keeping an eye on your phone on the Desk camera.",
                       "Sure, I am keeping an eye on your phone on the Desk camera.")
    assert keeps_facts("Welcome back! Three things happened.", "Welcome back! Three things happened while you were out.")
    assert not keeps_facts("Welcome back! Three things happened.", "Welcome back! Some things happened.")


class Brain:
    """A scripted stand-in for the model: fixed tool choices and rewordings, recording what it was shown."""
    def __init__(self, choices: dict[str, tuple[str, dict] | None], wording: str | None = None):
        """Map heard sentences to tool choices; reword every reply as `wording` (None keeps the template)."""
        self.choices = choices
        self.wording = wording
        self.seen: list[tuple[str, list]] = []

    async def choose(self, text, tools, history):
        """Return the scripted choice and remember the tools and history it saw."""
        self.seen.append((text, [t["function"]["name"] for t in tools], list(history)))
        return self.choices[text]

    async def phrase(self, text, tool, result):
        """Return the scripted rewording."""
        return self.wording


@pytest.fixture
def ask_url(core_url, screen, request):
    """A devserver whose simulated Alexa uses the Brain given by the test's `brain` marker."""
    brain = request.node.get_closest_marker("brain").args[0]
    sim = AlexaSim(choose=brain.choose, phrase=brain.phrase)
    server, thread, url = _serve(devserver.http_app(CoreClient(core_url), screen, alexa=sim))
    yield url
    server.should_exit = True
    thread.join(timeout=5)


def _ask(url: str, text: str) -> dict:
    """POST /voice/ask and return its JSON."""
    reply = httpx.post(f"{url}/voice/ask", json={"text": text}, timeout=30)
    assert reply.status_code == 200, reply.text
    return reply.json()


OBJECT_BRAIN = Brain({
    "Alexa, I'm stepping away, look for my phone": ("step_away", {"instruction": "look for my phone"}),
    "do you see me on the desk camera?": ("look", {"question": "do you see me?", "camera": "desk"}),
    "what did I do?": ("look_back", {"question": "what did I do?"}),
    "what did I miss?": ("what_did_i_miss", {}),
    "I'm back": ("im_back", {}),
    "yes": ("what_did_i_miss", {}),
    "tell me a joke": None,
})


@pytest.mark.brain(OBJECT_BRAIN)
def test_ask_calls_the_tool_over_mcp_and_speaks_its_reply(ask_url, core_store, screen):
    """A sentence goes through tools/list and tools/call on /mcp; the reply is the tool's spoken sentence."""
    fake_core.seed(core_store)
    away = _ask(ask_url, "Alexa, I'm stepping away, look for my phone")
    assert away["tool"]["name"] == "step_away" and away["tool"]["arguments"] == {"instruction": "look for my phone"}
    assert away["result"]["status"] == "watching" and core_store.active_session()["focus_object"] == "phone"
    assert away["reply"] == away["template"] == away["result"]["spoken"] and away["reply_from"] == "template"
    assert {"route_ms", "tool_ms", "phrase_ms", "total_ms"} <= away["timings"].keys()
    assert "cannot_help" in OBJECT_BRAIN.seen[-1][1] and "step_away" in OBJECT_BRAIN.seen[-1][1]


@pytest.mark.brain(OBJECT_BRAIN)
def test_a_question_about_a_camera_is_answered_by_look(ask_url, core_store, screen):
    """"Do you see me on the desk camera?" reaches look over /mcp and is answered from the camera, with or without
    a session, and the frame goes on screen."""
    fake_core.seed(core_store)
    seen = _ask(ask_url, "do you see me on the desk camera?")
    assert seen["tool"]["name"] == "look" and not seen["tool"]["error"]
    assert seen["reply"] == "Yes, I can see you at the desk." and seen["result"]["camera"] == "Desk"
    assert screen.display["clips"][0]["frame_url"] == seen["result"]["frame_url"]
    assert "look" in OBJECT_BRAIN.seen[-1][1]


@pytest.mark.brain(OBJECT_BRAIN)
def test_what_did_i_do_is_answered_from_the_recent_window(ask_url, core_store):
    """"What did I do?" reaches look_back over /mcp and the reply says it covers the last 5 minutes."""
    fake_core.seed(core_store)
    did = _ask(ask_url, "what did I do?")
    assert did["tool"]["name"] == "look_back" and not did["tool"]["error"]
    assert did["reply"].startswith("In the last 5 minutes,")


@pytest.mark.brain(OBJECT_BRAIN)
def test_off_topic_gets_the_cannot_help_reply(ask_url, core_store):
    """When the brain picks no tool, the reply says what Meanwhile can do, and no tool is called."""
    fake_core.seed(core_store)
    joke = _ask(ask_url, "tell me a joke")
    assert joke["tool"] is None and joke["reply"] == alexa_sim.NO_TOOL_REPLY


@pytest.mark.brain(OBJECT_BRAIN)
def test_context_is_kept_only_after_a_question(ask_url, core_store):
    """'I'm back' ends with an offer, so 'yes' is routed with that exchange; an answer without a question leaves no context."""
    fake_core.seed(core_store)
    _ask(ask_url, "Alexa, I'm stepping away, look for my phone")
    fake_core.play(core_store, "object", core_store.active_session())
    back = _ask(ask_url, "I'm back")
    assert back["reply"].endswith("?")
    caught_up = _ask(ask_url, "yes")
    text, _, history = OBJECT_BRAIN.seen[-1]
    assert text == "yes" and history[-1]["content"] == back["reply"]
    assert caught_up["reply"].endswith("Want the details?")
    joke = _ask(ask_url, "tell me a joke")
    assert OBJECT_BRAIN.seen[-1][2][-1]["content"] == caught_up["reply"]
    assert not joke["reply"].endswith("?")
    _ask(ask_url, "what did I miss?")
    assert OBJECT_BRAIN.seen[-1][2] == []


@pytest.mark.brain(Brain({"what did I miss?": ("what_did_i_miss", {})},
                         wording="While you were away, he picked it up. Not taken."))
def test_a_reworded_reply_that_breaks_the_rules_falls_back(ask_url, core_store):
    """A model rewording with 'he' is replaced by the tool's own sentence, and both are returned."""
    fake_core.seed(core_store)
    core_store.start_session("keep an eye on my watch", {"watch_order": {"kind": "object"}})
    missed = _ask(ask_url, "what did I miss?")
    assert missed["reply_from"] == "template" and missed["reply"] == missed["template"]
    assert missed["model_reply"] == "While you were away, he picked it up. Not taken."


def test_ask_needs_text(devserver_url):
    """An empty sentence is a 400."""
    assert httpx.post(f"{devserver_url}/voice/ask", json={"text": " "}).status_code == 400


@functools.cache
def _brain_problem() -> str | None:
    """Why the local model can't be used, or None if it's ready."""
    try:
        httpx.get(f"{alexa_sim.OLLAMA_URL}/api/tags", timeout=3).raise_for_status()
    except httpx.HTTPError as exc:
        return f"Ollama isn't answering: {exc}"
    return None


@functools.cache
def _tools() -> list[dict]:
    """The MCP tools as the brain sees them, listed from an in-process server, plus cannot_help."""
    async def go():
        async with Client(mcp_server.build_server(CoreClient("http://127.0.0.1:9"))) as client:
            return [alexa_sim._ollama_tool(t) for t in (await client.list_tools()).tools] + [alexa_sim.CANNOT_HELP]
    return asyncio.run(go())


def chosen(text: str, history: list | None = None) -> tuple[str, dict] | None:
    """The local model's choice for a sentence; skip if the model isn't available."""
    if problem := _brain_problem():
        pytest.skip(problem)
    return asyncio.run(alexa_sim.choose_with_model(text, _tools(), history or []))


@pytest.mark.model
@pytest.mark.parametrize("text, tool, check", [
    ("Alexa, I'm stepping away, look out for my smart watch", "step_away", lambda a: "look out for" in a["instruction"].lower()),
    ("I'm heading out, keep an eye on the front door", "step_away", lambda a: "front door" in a["instruction"].lower()),
    ("take notes of who comes into the room while I'm away", "step_away", lambda a: "notes" in a["instruction"].lower()),
    ("I'm stepping out for a bit, can you watch my laptop?", "step_away", lambda a: "laptop" in a["instruction"].lower()),
    ("Alexa, keep an eye on my keys, I'm popping out", "step_away", lambda a: "keys" in a["instruction"].lower()),
    ("Alexa, I'm stepping away, look for my phone", "step_away", lambda a: "phone" in a["instruction"].lower()),
    ("what did I miss?", "what_did_i_miss", None),
    ("did anything happen while I was gone?", "what_did_i_miss", None),
    ("catch me up", "what_did_i_miss", None),
    ("anything new?", "what_did_i_miss", None),
    ("show me", "show_me", None),
    ("show me the hand-over", "show_me", lambda a: "hand" in a.get("event", "").lower()),
    ("let me see who picked it up", "show_me", None),
    ("I'm back", "im_back", None),
    ("I'm home now", "im_back", None),
    ("you can stop watching", "im_back", None),
    ("what are you watching?", "status", None),
    ("are the cameras working?", "status", None),
    ("is the door locked?", "ask_state", lambda a: "door" in a.get("thing", "").lower()),
    ("is the front door locked?", "ask_state", lambda a: "door" in a.get("thing", "").lower()),
    ("is the front door open?", "ask_state", None),
    ("who locked the door?", "ask_state", None),
    ("did I lock the door?", "ask_state", None),
    ("do you see me on the desk camera?", "look", lambda a: "desk" in (a.get("camera") or "").lower()),
    ("do you see me on screen with the camera on desk?", "look", lambda a: "desk" in (a.get("camera") or "").lower()),
    ("do you see me?", "look", None),
    ("is the lamp on?", "ask_state", lambda a: "lamp" in a.get("thing", "").lower()),
    ("who's at the desk?", "look", None),
    ("what's on the table?", "look", None),
    ("is anyone in the room right now?", "look", None),
    ("is my laptop still on the desk?", "look|ask_state", None),
    ("what did I do?", "look_back", None),
    ("what just happened?", "look_back", None),
    ("what just happened at the desk?", "look_back", None),
    ("did anyone come in?", "look_back", None),
    ("what did I do 10 minutes ago?", "look_back", None),
    ("what was I doing a minute ago?", "look_back", None),
    ("what am I wearing?", "look", None),
    ("what am I doing?", "look", None),
])
def test_model_picks_the_right_tool(text, tool, check):
    """Spoken sentences from the scenarios reach the right MCP tool with the right arguments."""
    choice = chosen(text)
    assert choice is not None and choice[0] in tool.split("|"), choice
    assert check is None or check(choice[1]), choice


@pytest.mark.model
@pytest.mark.parametrize("text", ["what's the weather like?", "tell me a joke", "play some music",
                                  "what's the capital of France?"])
def test_model_declines_off_topic(text):
    """Requests unrelated to the home pick no tool."""
    assert chosen(text) is None


@pytest.mark.model
def test_model_follows_yes_after_the_offer():
    """'Yes' right after "Would you like to hear them?" asks for the catch-up."""
    history = [{"role": "user", "content": "I'm back"},
               {"role": "assistant", "content": "Welcome back! Three things happened while you were away. "
                                                "Would you like to hear them?"}]
    assert chosen("yes", history)[0] == "what_did_i_miss"


def test_every_ollama_call_sends_the_shared_options(monkeypatch):
    """The panel's model call sends the core's options (num_ctx 8192, think off, kept loaded), so Ollama never
    reloads the shared model between the core's calls and ours."""
    sent = []

    class Reply:
        """A canned Ollama reply."""
        def __init__(self, content):
            """Hold the message content."""
            self.content = content

        def raise_for_status(self):
            """Always fine."""

        def json(self):
            """The chat reply shape."""
            return {"message": {"content": self.content}}

    class FakeAsync:
        """Records the body of an async call."""
        def __init__(self, timeout):
            """Ignore the timeout."""

        async def __aenter__(self):
            """Enter."""
            return self

        async def __aexit__(self, *exc):
            """Exit."""

        async def post(self, url, json):
            """Record and answer."""
            sent.append(json)
            return Reply("")

    monkeypatch.setattr(alexa_sim.httpx, "AsyncClient", FakeAsync)
    asyncio.run(alexa_sim.choose_with_model("what did I miss?", [alexa_sim.CANNOT_HELP], []))
    assert len(sent) == 1
    for body in sent:
        assert body["options"]["num_ctx"] == 8192 and body["think"] is False and body["keep_alive"] == "30m"


@pytest.mark.parametrize("instruction, heard, ok", [
    ("keep an eye on my watch", "Alexa, I'm stepping away, keep an eye on my watch", True),
    ("watch it", "Alexa I'm stepping away watch it", True),
    ("watch my laptop", "I'm stepping out for a bit, can you watch my laptop?", True),
    ("look out for my smart watch", "uh", False),
    ("watch my smart watch", "keep an eye", False),
    ("look out for my smart watch", "what", False),
])
def test_step_away_must_use_words_that_were_heard(instruction, heard, ok):
    """An instruction the model made up (sharing no real word with what was heard) is refused."""
    assert alexa_sim.heard_it(instruction, heard) is ok


@pytest.mark.brain(Brain({"uh": ("step_away", {"instruction": "watch my smart watch"})}))
def test_a_made_up_step_away_starts_nothing(ask_url, core_store):
    """If the brain invents a step_away from noise, no watch starts and Alexa asks what to watch."""
    fake_core.seed(core_store)
    answer = _ask(ask_url, "uh")
    assert answer["tool"] is None and answer["reply"] == alexa_sim.WHAT_TO_WATCH_REPLY
    assert core_store.active_session() is None


class SlowWords(Brain):
    """A brain whose rewording never finishes in time."""
    async def phrase(self, text, tool, result):
        """Take longer than the rewording budget."""
        await asyncio.sleep(alexa_sim.PHRASE_BUDGET_S + 2)
        return "too late"


@pytest.mark.brain(SlowWords({"what did I miss?": ("what_did_i_miss", {})}))
def test_slow_rewording_falls_back_within_budget(ask_url, core_store):
    """A rewording slower than the budget is dropped and the tool's own sentence is spoken."""
    fake_core.seed(core_store)
    answer = _ask(ask_url, "what did I miss?")
    assert answer["reply_from"] == "template"
    assert answer["timings"]["phrase_ms"] < (alexa_sim.PHRASE_BUDGET_S + 1) * 1000


class SlowChoice(Brain):
    """A brain that takes long to choose, as when the GPU is busy with the core."""
    async def choose(self, text, tools, history):
        """Choose after a delay longer than the busy threshold."""
        await asyncio.sleep(alexa_sim.BUSY_ROUTE_MS / 1000 + 0.3)
        return await super().choose(text, tools, history)


@pytest.mark.brain(SlowChoice({"what did I miss?": ("what_did_i_miss", {})}, wording="Reworded."))
def test_rewording_is_skipped_when_the_model_is_busy(ask_url, core_store):
    """When choosing the tool already took longer than the busy threshold, no rewording is attempted."""
    fake_core.seed(core_store)
    answer = _ask(ask_url, "what did I miss?")
    assert answer["reply_from"] == "template" and "phrase_ms" not in answer["timings"]


@pytest.mark.model
@pytest.mark.parametrize("text, history", [
    ("tell me everything", []),
    ("give me the details", []),
    ("yes", [{"role": "user", "content": "what did I miss?"},
             {"role": "assistant", "content": "Your watch is back with you. Ravi picked it up and handed it back to "
                                              "you. Want the details?"}]),
])
def test_model_asks_for_the_details(text, history):
    """Asking for everything, or yes after the offer of details, calls what_did_i_miss with detail true."""
    choice = chosen(text, history)
    assert choice is not None and choice[0] == "what_did_i_miss" and choice[1].get("detail") is True, choice
