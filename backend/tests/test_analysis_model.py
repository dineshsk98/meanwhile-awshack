"""Asks the real local model about a recorded pick-up and put-back (videos/clip1.mp4, not in git): the frame
check over time and the clip answer. Skipped when the clip or the model isn't available."""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from meanwhile import config
from meanwhile.pipeline import identity, models, video_model, vision

CLIP = Path(__file__).resolve().parents[2] / "videos" / "clip1.mp4"
TARGET = "game controller"
BOX = [0.439, 0.826, 0.657, 1.0]
PEOPLE = [{"person_id": "ravi", "name": "Ravi", "role": "helper", "note": "a navy blue t-shirt"},
          {"person_id": "owner", "name": "Owner", "role": "owner", "note": "a red shirt"}]
SESSION = {"instruction": f"keep an eye on my {TARGET}",
           "watch_order": {"kind": "object", "target": TARGET, "cameras": "all", "alert_when": ["picked_up"],
                           "clip_question": f"What happens to the {TARGET}? Who touches it, and where does it go?",
                           "report": ["picked up", "put back", "handed to someone", "taken away"]}}

pytestmark = pytest.mark.model


@pytest.fixture(scope="module", autouse=True)
def ready():
    """Skip unless the clip exists and the model answers."""
    if not CLIP.exists():
        pytest.skip(f"{CLIP} isn't here")
    try:
        models.check(config.VISION_MODEL)
    except models.ModelUnavailable as exc:
        pytest.skip(str(exc))


def frame_at(seconds: float) -> bytes:
    """The clip's frame at `seconds`, as a JPEG."""
    video = cv2.VideoCapture(str(CLIP))
    video.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
    ok, image = video.read()
    video.release()
    return cv2.imencode(".jpg", image)[1].tobytes()


@pytest.mark.parametrize("seconds, in_place, held", [(0.5, True, False), (3.5, False, True), (4.5, False, None)])
def test_frame_check_follows_the_controller(seconds, in_place, held):
    """Resting at 0.5 s (same as the start, so no model call); lifted at 3.5 s by the person in the navy t-shirt;
    not in place at 4.5 s (held or not, the state is already "with a person" by then)."""
    answer = vision.check_frame(TARGET, "Desk", frame_at(seconds), BOX, identity.describe_people(PEOPLE), frame_at(0.1))
    assert answer["in_place"] is in_place and held in (None, answer["held"])
    if held:
        assert identity.person_id_for(answer["person"], PEOPLE) == "ravi"


def test_clip_says_picked_up_then_put_back():
    """The clip answer has a pick-up before a put-back."""
    answer, clip = video_model.describe(CLIP, SESSION, "Desk", PEOPLE)
    actions = [e["action"] for e in sorted(answer["events"], key=lambda e: e["t"])]
    assert "picked_up" in actions and "put_back" in actions
    assert actions.index("picked_up") < actions.index("put_back")
    assert clip["duration_s"] > 7
