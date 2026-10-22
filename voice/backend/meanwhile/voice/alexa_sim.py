"""The simulated Alexa+: hears a sentence, picks one of our MCP tools with a local model, calls it over MCP,
and words the reply (the model's version if it keeps every fact, else the tool's own `spoken` sentence).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable

import httpx
from mcp import Client

from meanwhile.voice import config, wording

log = logging.getLogger("meanwhile.voice")

OLLAMA_URL = config.OLLAMA_URL
BRAIN_MODEL = config.ALEXA_SIM_MODEL
MODEL_TIMEOUT_S = 120
KEEP_LOADED = "30m"
NUM_CTX = 8192

Message = dict[str, Any]
Choose = Callable[[str, list[dict], list[Message]], Awaitable[tuple[str, dict] | None]]
Phrase = Callable[[str, str, dict], Awaitable[str | None]]

ROUTE_SYSTEM = (
    "You are Alexa, a voice assistant. " + wording.SERVER_INSTRUCTIONS + " Always answer by calling exactly one tool. "
    "For step_away, pass the user's own words for what to watch, keeping their verb, only dropping \"Alexa\" and "
    "\"I'm stepping away\"; never make up an instruction they didn't say. Any question about how something is or "
    "was left, or who changed it (\"is the front door locked?\", \"did I lock the door?\", \"is the lamp on?\"), "
    "goes to ask_state with the thing they asked about. Any other question in the present tense about what a "
    "camera sees or what someone is doing right now (\"what am I doing?\", \"what am I wearing?\") goes to look; "
    "any question in the past tense about what just happened or what they or someone did (\"what did I do?\", "
    "\"what just happened?\"), even about longer ago, goes to look_back. Call cannot_help only if the request has "
    "nothing to do with the home, its cameras or what happened in it. If the user just says yes to your offer to "
    "tell them what happened, call what_did_i_miss; if they say yes to your offer of the details, call "
    "what_did_i_miss with detail true."
)

PHRASE_SYSTEM = (
    "You are Alexa, speaking to the user. Rephrase the tool result's `spoken` reply so it sounds natural and "
    "polite when spoken aloud. Keep every name, time and fact exactly as given; add nothing. Never use he, she, "
    "him, her, his or hers for a person: repeat their name. At most three short sentences. Reply with the "
    "sentences only."
)

NO_TOOL_REPLY = ("Sorry, I can only help with your home and its cameras. You could ask me what a camera sees, or what "
                 "you missed.")
WHAT_TO_WATCH_REPLY = "Sorry, I didn't catch what you'd like me to keep an eye on. Could you say it again?"
TOOL_FAILED_REPLY = "Sorry, something went wrong on my side. Could you try that again?"

CANNOT_HELP = {"type": "function", "function": {
    "name": "cannot_help",
    "description": "Use only when the request has nothing to do with the home, its cameras, what they see now, what "
                   "just happened or what happened while the user was away: the weather, jokes, music, general "
                   "knowledge questions. Never for a question about what a camera sees, what happened or who is at "
                   "home.",
    "parameters": {"type": "object", "properties": {}},
}}

PHRASE_BUDGET_S = 4
BUSY_ROUTE_MS = 3000
MAX_NEW_WORDS = 2
_FILLER = {"the", "and", "for", "you", "your", "my", "our", "can", "please", "while", "i'm", "away", "stepping",
           "keep", "eye", "look", "out", "on", "an", "a", "of", "to", "me", "it", "that", "this"}

_COMMON = {"the", "and", "are", "was", "were", "you", "your", "for", "with", "from", "that", "this", "now", "here",
           "there", "just", "have", "has", "had", "been", "not", "but", "also", "too", "its", "it's", "they", "them",
           "into", "onto", "while", "away", "right", "sure", "okay", "well", "all", "any", "some", "will"}

_TIME = re.compile(r"\b\d{1,2}:\d{2}\s?(?:am|pm)\b", re.I)
_PRONOUN = re.compile(r"\b(?:he|she|him|her|his|hers)\b", re.I)
_NEGATION = re.compile(r"\b(?:not|no|never|nothing)\b|n't\b", re.I)
_NUMBER = re.compile(r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")


class BrainUnavailable(Exception):
    """The local model isn't reachable or didn't answer."""
    pass


def _sentences(text: str) -> list[str]:
    """The sentences of a text, trimmed."""
    return [s.strip() for s in _SENTENCE.findall(text) if s.strip()]


def _names(text: str) -> set[str]:
    """Capitalised words that aren't the first word of a sentence: names and camera labels."""
    names = set()
    for sentence in _sentences(text):
        for word in re.findall(r"[A-Za-z']+", sentence)[1:]:
            if word[0].isupper() and word != "I" and not word.startswith("I'"):
                names.add(word)
    return names


def keeps_facts(template: str, candidate: str) -> bool:
    """Whether a reworded reply keeps the template's names, times, numbers, negations and leading Yes/No,
    uses no he/she, adds no sentences, and brings in at most MAX_NEW_WORDS meaningful words of its own."""
    if not candidate.strip() or _PRONOUN.search(candidate):
        return False
    if len(_sentences(candidate)) > max(len(_sentences(template)), 1):
        return False
    if any(name not in candidate for name in _names(template)):
        return False
    lowered = candidate.lower()
    if any(t.lower() not in lowered for t in _TIME.findall(template)):
        return False
    if any(n.lower() not in lowered for n in _NUMBER.findall(template)):
        return False
    if len(_NEGATION.findall(candidate)) < len(_NEGATION.findall(template)):
        return False
    new_words = _content_words(candidate) - _content_words(template)
    if len(new_words) > MAX_NEW_WORDS:
        return False
    first = template.strip().split(maxsplit=1)[0].strip(",.!").lower() if template.strip() else ""
    return first not in ("yes", "no") or lowered.startswith(first)


def _content_words(text: str) -> set[str]:
    """The text's words that carry meaning (contractions split, common little words dropped)."""
    words = re.findall(r"[a-z]+", text.lower().replace("'", " "))
    return {w for w in words if len(w) > 2 and w not in _COMMON}


async def _chat(messages: list[Message], tools: list[dict] | None = None) -> Message:
    """One non-streaming chat call to the local model; raise BrainUnavailable if it fails."""
    body: dict[str, Any] = {"model": BRAIN_MODEL, "messages": messages, "stream": False, "think": False,
                            "keep_alive": KEEP_LOADED, "options": {"temperature": 0, "seed": 1, "num_ctx": NUM_CTX}}
    if tools:
        body["tools"] = tools
    try:
        async with httpx.AsyncClient(timeout=MODEL_TIMEOUT_S) as http:
            reply = await http.post(f"{OLLAMA_URL}/api/chat", json=body)
            reply.raise_for_status()
            return reply.json()["message"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise BrainUnavailable(f"The model {BRAIN_MODEL} didn't answer: {exc}") from exc


async def choose_with_model(text: str, tools: list[dict], history: list[Message]) -> tuple[str, dict] | None:
    """The model's choice of tool and arguments for what the user said; None if it called no tool."""
    message = await _chat([{"role": "system", "content": ROUTE_SYSTEM}, *history, {"role": "user", "content": text}],
                          tools)
    calls = message.get("tool_calls") or []
    if not calls or calls[0]["function"]["name"] == CANNOT_HELP["function"]["name"]:
        return None
    call = calls[0]["function"]
    args = call.get("arguments") or {}
    return call["name"], json.loads(args) if isinstance(args, str) else args


async def phrase_with_model(text: str, tool: str, result: dict) -> str | None:
    """The model's spoken version of a tool result."""
    message = await _chat([
        {"role": "system", "content": PHRASE_SYSTEM},
        {"role": "user", "content": text},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": tool, "arguments": {}}}]},
        {"role": "tool", "content": json.dumps(result)},
    ])
    return (message.get("content") or "").strip() or None


def heard_it(instruction: str, heard: str) -> bool:
    """Whether a step_away instruction the model chose shares a real word with what was heard (not just filler),
    so the model can't start a watch the user never asked for."""
    instruction_words = re.findall(r"[a-z']+", instruction.lower())
    heard_words = re.findall(r"[a-z']+", heard.lower())
    if instruction_words and " ".join(instruction_words) in " ".join(heard_words):
        return True
    content = {w for w in instruction_words if len(w) > 2 and w not in _FILLER}
    return bool(content & set(heard_words))


def _ollama_tool(tool: Any) -> dict:
    """An MCP tool from tools/list in the shape the model's tool calling expects."""
    return {"type": "function", "function": {
        "name": tool.name, "description": tool.description or "", "parameters": tool.input_schema or {"type": "object"}}}


def _ms(start: float) -> int:
    """Milliseconds since `start`."""
    return round((time.perf_counter() - start) * 1000)


class AlexaSim:
    """One conversation with the simulated Alexa+, remembering the last exchange only when it ended in a question,
    for follow-ups like 'yes'."""
    def __init__(self, choose: Choose | None = None, phrase: Phrase | None = None):
        """Use the local model unless other choose/phrase functions are given."""
        self.choose = choose or choose_with_model
        self.phrase = phrase or phrase_with_model
        self.history: list[Message] = []
        self.tools: list[dict] | None = None

    async def _load_tools(self, client: Client) -> None:
        """List the MCP tools once (plus cannot_help) in the shape the model reads."""
        if self.tools is None:
            self.tools = [_ollama_tool(t) for t in (await client.list_tools()).tools] + [CANNOT_HELP]

    async def warm(self, mcp_url: str) -> None:
        """Load the tool list and the model with one routing call, so the first real question is quick."""
        async with Client(mcp_url, mode="legacy") as client:
            await self._load_tools(client)
        await self.choose("what are you watching?", self.tools, [])

    async def ask(self, text: str, mcp_url: str) -> dict:
        """Answer one sentence: pick a tool, call it over MCP at mcp_url, word the reply; returns what happened."""
        start = time.perf_counter()
        timings: dict[str, int] = {}
        tool_call = None
        async with Client(mcp_url, mode="legacy") as client:
            await self._load_tools(client)
            step = time.perf_counter()
            choice = await self.choose(text, self.tools, self.history)
            timings["route_ms"] = _ms(step)
            if choice is not None and choice[0] == "step_away" and not heard_it(choice[1].get("instruction", ""), text):
                log.info("ask heard=%r: dropped step_away%r, its words weren't heard", text, choice[1])
                choice, no_tool_reply = None, WHAT_TO_WATCH_REPLY
            else:
                no_tool_reply = NO_TOOL_REPLY
            if choice is None:
                template, result = no_tool_reply, {}
            else:
                name, args = choice
                step = time.perf_counter()
                called = await client.call_tool(name, args)
                timings["tool_ms"] = _ms(step)
                result = called.structured_content or {}
                failed = called.is_error or "spoken" not in result
                template = TOOL_FAILED_REPLY if failed else result["spoken"]
                tool_call = {"name": name, "arguments": args, "ms": timings["tool_ms"], "error": failed}
        reply, source, candidate = template, "template", None
        if tool_call and not tool_call["error"] and timings["route_ms"] <= BUSY_ROUTE_MS:
            step = time.perf_counter()
            try:
                candidate = await asyncio.wait_for(self.phrase(text, tool_call["name"], result), PHRASE_BUDGET_S)
            except (BrainUnavailable, asyncio.TimeoutError):
                candidate = None
            timings["phrase_ms"] = _ms(step)
            if candidate and keeps_facts(template, candidate):
                reply, source = candidate, "model"
        timings["total_ms"] = _ms(start)
        asked_back = reply.rstrip().endswith("?")
        self.history = [{"role": "user", "content": text}, {"role": "assistant", "content": reply}] if asked_back else []
        log.info("ask heard=%r tool=%s reply_from=%s %s", text, tool_call and tool_call["name"], source,
                 " ".join(f"{k}={v}" for k, v in timings.items()))
        return {"heard": text, "tool": tool_call, "result": result, "reply": reply, "reply_from": source,
                "template": template, "model_reply": candidate, "timings": timings}
