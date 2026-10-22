"""Tests for the watch-order compiler and the box parsing; the `model` tests ask the real local model."""

from __future__ import annotations

import functools

import pytest

from meanwhile import config
from meanwhile.pipeline import models, orders, vision

CAMERAS = [{"camera_id": "desk", "label": "Desk"}, {"camera_id": "room", "label": "Room"}, {"camera_id": "door", "label": "Door"}]
DESK_BOX = [0.4, 0.5, 0.6, 0.7]


def ground_on_desk(target, cameras):
    """A stand-in for grounding that sees everything except an umbrella, on the Desk camera if searched."""
    if target == "umbrella" or "desk" not in [c["camera_id"] for c in cameras]:
        return None
    return {"camera_id": "desk", "box": DESK_BOX}


def compiled(instruction, meant, cameras=CAMERAS):
    """Compile with a fixed model reading instead of the model."""
    return orders.compile_order(instruction, cameras, ground_on_desk, understand=lambda *_: meant)


@pytest.mark.parametrize("instruction", [
    "did I lock the door?", "Alexa, is the front door locked", "Who took my watch", "where is my phone?",
])
def test_questions_never_start_a_watch_even_if_the_model_disagrees(instruction):
    """A question gets the polite question reply before the model is asked."""
    result = compiled(instruction, {"kind": "monitor", "target": "the front door"})
    assert result == {"watch_order": {"clarify": orders.QUESTION}}


def test_polite_requests_are_not_questions():
    """'Can you keep an eye on…' is an instruction."""
    assert not orders.is_question("can you please keep an eye on my laptop?")
    assert not orders.is_question("Alexa, I'm stepping away. Keep an eye on my keys")


def test_object_order_is_grounded_and_uses_the_object_vocabulary():
    """An object order gets the fixed object fields and the camera and box grounding found."""
    result = compiled("keep an eye on my game controller", {"kind": "object", "target": "the Game Controller"})
    order = result["watch_order"]
    assert order["kind"] == "object" and order["target"] == "game controller" and order["cameras"] == "all"
    assert order["alert_when"] == orders.OBJECT_ALERT_WHEN
    assert order["summary_for_alexa"] == "keeping an eye on your game controller on the Desk camera"
    assert result["focus_camera_id"] == "desk" and result["focus_box"] == DESK_BOX


def test_object_not_seen_names_the_cameras_searched():
    """Not found names every camera searched, or only the one named."""
    result = compiled("keep an eye on my umbrella", {"kind": "object", "target": "umbrella"})
    assert result["not_found"] == "I can't see your umbrella on the Desk, Room or Door camera."
    named = compiled("watch my keys on the room camera", {"kind": "object", "target": "keys", "camera": "Room"})
    assert named["not_found"] == "I can't see your keys on the Room camera."


def test_cameras_are_only_used_when_named_and_must_exist():
    """A camera the words don't name is dropped; a named one that doesn't exist gets a question."""
    invented = compiled("find my keys", {"kind": "object", "target": "keys", "camera": "Room"})
    assert invented["focus_camera_id"] == "desk"
    garage = compiled("keep an eye on my keys on the garage camera", {"kind": "object", "target": "keys", "camera": "garage"})
    assert garage["watch_order"]["clarify"].startswith("I don't have a garage camera.")


def test_unclear_doors_and_no_cameras():
    """Unclear words; a door or the house is an ordinary order whose question and report come from the user's
    words, with or without a Door camera; and no cameras."""
    assert compiled("watch it", {"kind": "unclear"})["watch_order"]["clarify"] == orders.UNCLEAR
    assert compiled("watch my", {"kind": "object", "target": ""})["watch_order"]["clarify"] == orders.UNCLEAR
    house = compiled("keep watch over the house", {
        "kind": "notes", "target": "what happens in the house", "clip_question": "Who is here and what are they doing?",
        "report": ["someone arriving or leaving"], "summary": "keeping watch over the house"})["watch_order"]
    assert house["kind"] == "notes" and house["cameras"] == "all"
    assert house["clip_question"] == "Who is here and what are they doing?"
    locked = compiled("let me know if the door gets locked", {
        "kind": "monitor", "target": "the door getting locked", "clip_question": "Does someone lock the door?",
        "report": ["the door locked"], "alert_when": ["the door is locked"]})["watch_order"]
    assert locked["kind"] == "monitor" and locked["clip_question"] == "Does someone lock the door?"
    assert locked["report"] == ["the door locked"] and locked["alert_when"] == ["door is locked"]
    fridge = compiled("tell me if the fridge door opens", {"kind": "monitor", "target": "fridge door opening"})
    assert fridge["watch_order"]["kind"] == "monitor"
    no_door = compiled("watch the front door", {"kind": "monitor", "target": "the front door"}, CAMERAS[:2])
    assert no_door["watch_order"]["kind"] == "monitor" and no_door["watch_order"]["cameras"] == "all"
    assert compiled("anything", {}, [])["watch_order"]["clarify"] == orders.NO_CAMERAS


def test_notes_and_monitor_orders_are_cleaned():
    """Notes never alert; summaries become an "I'm …" phrase, or a template when the model's isn't one."""
    notes = compiled("take notes of who comes into the room", {
        "kind": "notes", "target": "who comes into the room", "camera": "room", "clip_question": "Who is here?",
        "report": ["people"], "alert_when": ["someone"], "summary": "I'm taking notes on who comes in."})["watch_order"]
    assert notes["cameras"] == ["room"] and notes["alert_when"] == []
    assert notes["summary_for_alexa"] == "taking notes on who comes in"
    monitor = compiled("tell me if the dog gets on the sofa", {
        "kind": "monitor", "target": "the dog getting on the sofa", "summary": "The dog will be watched.",
        "clip_question": "", "report": [], "alert_when": []})["watch_order"]
    assert monitor["cameras"] == "all" and monitor["alert_when"] == ["dog getting on the sofa"]
    assert monitor["summary_for_alexa"] == "watching for dog getting on the sofa, and I'll tell you if I see it"
    assert monitor["clip_question"] and monitor["report"]


def test_phrase_completes_im():
    """phrase() strips 'I'm' and the full stop, and rejects non -ing openings."""
    assert orders.phrase("I'm Watching the door.") == "watching the door"
    assert orders.phrase("keeping an eye on your bag") == "keeping an eye on your bag"
    assert orders.phrase("I will watch it") is None and orders.phrase("") is None


def test_qwen_canvas_matches_the_model_canvas():
    """The canvas sizes Qwen resizes frames to (its box coordinates are on these)."""
    assert vision.qwen_canvas(640, 360) == (1204, 672)
    assert vision.qwen_canvas(960, 540) == (1204, 672)
    assert vision.qwen_canvas(320, 180) == (1204, 672)
    assert vision.qwen_canvas(1280, 720) == (1288, 728)


def test_parse_box_for_qwen_and_gemini():
    """Boxes become 0-1 fractions; missing, tiny or huge boxes are refused."""
    qwen = vision.parse_box({"found": True, "box_2d": [517, 546, 746, 672]}, 640, 360, "ollama:qwen2.5vl:3b")
    assert qwen == pytest.approx([0.4294, 0.8125, 0.6196, 1.0], abs=1e-3)
    gemini = vision.parse_box({"found": True, "box_2d": [500, 250, 750, 500]}, 640, 360, "gemini:x")
    assert gemini == [0.25, 0.5, 0.5, 0.75]
    qwen35 = vision.parse_box({"found": True, "box_2d": [458, 836, 719, 1000]}, 640, 360, "ollama:qwen3.5:4b")
    assert qwen35 == [0.458, 0.836, 0.719, 1.0]
    assert vision.parse_box({"found": False, "box_2d": [1, 2, 3, 4]}, 640, 360, "ollama:x") is None
    assert vision.parse_box({"found": True, "box_2d": [0, 0, 1204, 672]}, 640, 360, "ollama:x") is None
    assert vision.parse_box({"found": True, "box_2d": [10, 10, 12, 12]}, 640, 360, "ollama:x") is None


@functools.cache
def _model_problem() -> str | None:
    """Why the local model can't be used, or None if it's ready."""
    try:
        models.check(config.ORDER_MODEL)
        orders.understand("keep an eye on my keys", CAMERAS)
    except models.ModelUnavailable as exc:
        return str(exc)
    return None


def read_by_model(instruction: str) -> dict:
    """Compile with the real model (grounding stubbed); skip if the model isn't available."""
    if problem := _model_problem():
        pytest.skip(problem)
    return orders.compile_order(instruction, CAMERAS, ground_on_desk)


@pytest.mark.model
@pytest.mark.parametrize("instruction, target", [
    ("look for my watch", "watch"), ("look for my phone", "phone"), ("find my keys", "keys"),
    ("tell me if anyone touches my laptop", "laptop"),
    ("look out for my smart watch", "smart watch"), ("Keep an eye on my watch.", "watch"),
    ("can you please keep an eye on my laptop while I'm out?", "laptop"),
    ("keep track of my car keys until I'm back", "car keys"), ("mind my bag", "bag"),
    ("look after the game controller", "game controller"),
])
def test_model_reads_object_phrasings(instruction, target):
    """Everyday ways of asking to watch a thing give an object order."""
    order = read_by_model(instruction)["watch_order"]
    assert order.get("kind") == "object" and order["target"] == target, order


@pytest.mark.model
@pytest.mark.parametrize("instruction, kind", [
    ("watch the front door while I'm out", "monitor|notes"), ("keep an eye on the front door", "monitor|notes"),
    ("keep an eye on the house", "monitor|notes"),
    ("let me know if the door gets locked", "monitor"),
    ("take notes of who comes into the room", "notes"), ("note down who visits", "notes"),
    ("keep a log of what happens at my desk", "notes"),
    ("tell me if the dog gets on the sofa", "monitor"), ("let me know if anyone opens the fridge", "monitor"),
    ("alert me if the baby wakes up", "monitor"),
])
def test_model_reads_door_notes_and_monitor(instruction, kind):
    """Door, notes and monitor instructions get their kinds, with an "I'm …" summary."""
    order = read_by_model(instruction)["watch_order"]
    assert order.get("kind") in kind.split("|"), order
    assert orders.phrase(order["summary_for_alexa"]) == order["summary_for_alexa"]
    if order["kind"] == "monitor":
        assert order["alert_when"] and order["clip_question"]


@pytest.mark.model
@pytest.mark.parametrize("instruction, reply", [
    ("watch it", orders.UNCLEAR), ("hmm", orders.UNCLEAR), ("did I lock the house before I left?", orders.QUESTION),
    ("can you tell me where my phone is", orders.QUESTION), ("did I lock the door?", orders.QUESTION),
    ("Did I lock the door when leaving?", orders.QUESTION), ("is the front door locked", orders.QUESTION),
    ("who took my watch", orders.QUESTION), ("where is my phone?", orders.QUESTION),
])
def test_model_asks_back_when_unclear(instruction, reply):
    """No thing to watch, or a question, gets the polite reply."""
    assert read_by_model(instruction)["watch_order"].get("clarify") == reply


@pytest.mark.model
def test_model_keeps_only_cameras_that_were_named():
    """A camera that doesn't exist gets a question back; with none named, the object is searched on every camera."""
    garage = read_by_model("keep an eye on my keys on the garage camera")["watch_order"]
    assert garage.get("clarify", "").startswith("I don't have a garage camera."), garage
    assert read_by_model("find my keys")["focus_camera_id"] == "desk"


@pytest.mark.model
def test_model_makes_the_door_lock_watch_from_the_users_words():
    """The tester's door-lock scenario: an ordinary order on the camera labelled Door, whose question is about the
    lock, made only from the instruction."""
    order = read_by_model("watch the front door and tell me when it's locked or unlocked")["watch_order"]
    assert order.get("kind") in ("monitor", "notes") and order["cameras"] == ["door"], order
    assert "lock" in order["clip_question"].lower(), order
