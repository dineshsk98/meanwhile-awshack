"""Test setup: settings from .env.example plus a fake core key, then fixtures: the fake core, the voice MCP server and
the home-screen devserver, each on a real local port."""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn


def _load_example_settings() -> None:
    """Set every setting from the repo's .env.example, so tests never depend on a personal .env."""
    example = Path(__file__).resolve().parents[2] / ".env.example"
    for line in example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ[name.strip()] = value.strip()


_load_example_settings()
os.environ["MEANWHILE_CORE_KEY"] = "test-key-not-real"

from scenarios import fake_core  # noqa: E402
from meanwhile.voice import devserver, mcp_server  # noqa: E402
from meanwhile.voice.core_client import CoreClient  # noqa: E402
from meanwhile.voice.screen import Screen  # noqa: E402


def _free_port() -> int:
    """A free local TCP port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app):
    """Run an ASGI app on a free port in a thread; return (server, thread, base URL)."""
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    return server, thread, f"http://127.0.0.1:{port}"


def _intent(kind: str, target: str = "", camera: str = "") -> dict:
    """An understood instruction, as the model returns it."""
    return {"kind": kind, "target": target, "camera": camera}


TEST_INTENTS = {
    "keep an eye on my watch": _intent("object", "watch"),
    "Can you keep an eye on my watch?": _intent("object", "watch"),
    "look for my phone": _intent("object", "phone"),
    "keep an eye on my umbrella": _intent("object", "umbrella"),
    "keep an eye on my keys on the garage camera": _intent("object", "keys", "garage"),
    "keep an eye on the front door": _intent("monitor", "the front door"),
    "watch the front door": _intent("monitor", "the front door"),
    "take notes": _intent("notes"),
    "take notes of who comes into the room": _intent("notes", camera="room"),
    "take notes of who comes into the room and what they do": _intent("notes", camera="room"),
    "watch it": _intent("unclear"),
    "Did I lock the door when leaving?": _intent("question"),
    "is the front door locked": _intent("question"),
    "who took my watch?": _intent("question"),
}


def fixed_understand(instruction: str, cameras: list[dict]) -> dict:
    """Stand-in for the model in plumbing tests: a fixed reading of each test sentence."""
    if instruction not in TEST_INTENTS:
        raise AssertionError(f"Add {instruction!r} to TEST_INTENTS in conftest.py")
    return TEST_INTENTS[instruction]


@pytest.fixture
def core_store(tmp_path) -> fake_core.FakeStore:
    """A fresh fake-core store with its cameras and people, media in a temp dir."""
    store = fake_core.FakeStore(tmp_path / "data")
    fake_core.seed(store)
    return store


@pytest.fixture
def core_url(core_store):
    """The fake core's base URL."""
    server, thread, url = _serve(fake_core.build_app(core_store, understand=fixed_understand))
    yield url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def screen() -> Screen:
    """A fresh home-screen state."""
    return Screen()


@pytest.fixture
def mcp_url(core_url, screen):
    """The voice MCP server's URL, talking to the fake core."""
    server, thread, url = _serve(mcp_server.http_app(CoreClient(core_url), screen))
    yield f"{url}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def devserver_url(core_url, screen):
    """The home-screen devserver's base URL (/mcp, /voice, /media, web/), talking to the fake core."""
    server, thread, url = _serve(devserver.http_app(CoreClient(core_url), screen))
    yield url
    server.should_exit = True
    thread.join(timeout=5)
