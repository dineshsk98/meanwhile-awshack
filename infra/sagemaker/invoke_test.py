"""Time the backend's own frame check (one 640 px image) and 8-frame clip question against a model, and print
the answers. Works for any provider, so the endpoint and the local model can be compared on the same frames.
Usage: python invoke_test.py <folder of JPEGs> [--model sagemaker:meanwhile-qwen3vl] [--target "game controller"]
       [--frames 5] [--clip <folder whose JPEGs make the 8-frame clip; default: the same folder>]"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from meanwhile.pipeline import models, video_model, vision  # noqa: E402

import common  # noqa: E402

PEOPLE = [{"person_id": "owner", "name": "Owner", "role": "owner", "note": "a dark t-shirt"}]


def frame_check(model: str, target: str, jpeg: bytes) -> tuple[float, dict]:
    """One frame check exactly as the analyser asks it; returns (seconds, answer)."""
    image = vision.crop(jpeg, [0.0, 0.0, 1.0, 1.0], vision.CHECK_WIDTH)
    started = time.perf_counter()
    answer = models.ask([{"role": "user", "content": vision.CHECK_PROMPT.format(
        label="Desk", target=target, people="Owner: a dark t-shirt"), "images": [image]}], vision.CHECK_SCHEMA, model)
    return time.perf_counter() - started, answer


def clip_check(model: str, target: str, jpegs: list[bytes]) -> tuple[float, dict]:
    """The clip question over 8 frames (640 px wide) as the video model gets it; returns (seconds, answer)."""
    frames = [vision.crop(j, [0.0, 0.0, 1.0, 1.0], video_model.FRAME_WIDTH) for j in jpegs]
    clip = {"frames": frames, "times": [float(i) for i in range(len(frames))], "duration_s": float(len(frames))}
    session = {"instruction": f"keep an eye on my {target}",
               "watch_order": {"kind": "object", "target": target, "cameras": "all", "alert_when": ["picked_up"],
                               "clip_question": f"What happens to the {target}? Who touches it, and where does it go?",
                               "report": ["picked up", "put back", "handed to someone", "taken away"]}}
    message = {"role": "user", "content": video_model.prompt(session, "Desk", clip, PEOPLE), "images": frames}
    started = time.perf_counter()
    answer = models.ask([message], video_model.schema("object"), model)
    return time.perf_counter() - started, answer


def main() -> int:
    """Warm the model up, then time frame checks on the first N images and one 8-frame clip call."""
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--model", default=f"sagemaker:{common.NAME}")
    parser.add_argument("--target", default="game controller")
    parser.add_argument("--frames", type=int, default=5)
    parser.add_argument("--clip", type=Path)
    args = parser.parse_args()
    paths = sorted(args.folder.glob("*.jpg"))
    if not paths:
        print(f"no .jpg files in {args.folder}")
        return 1
    jpegs = [p.read_bytes() for p in paths]
    warm, _ = frame_check(args.model, args.target, jpegs[0])
    print(f"model {args.model}  first call (warm-up) {warm:.2f} s")
    times = []
    for path, jpeg in list(zip(paths, jpegs))[:args.frames]:
        seconds, answer = frame_check(args.model, args.target, jpeg)
        times.append(seconds)
        print(f"frame {path.name}: {seconds:.2f} s  {json.dumps(answer)}")
    print(f"frame check: mean {sum(times) / len(times):.2f} s, max {max(times):.2f} s over {len(times)}")
    if args.clip:
        jpegs = [p.read_bytes() for p in sorted(args.clip.glob("*.jpg"))]
    picks = [jpegs[round(i * (len(jpegs) - 1) / 7)] for i in range(8)] if len(jpegs) >= 8 else jpegs
    seconds, answer = clip_check(args.model, args.target, picks)
    print(f"clip ({len(picks)} frames): {seconds:.2f} s  {json.dumps(answer)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
