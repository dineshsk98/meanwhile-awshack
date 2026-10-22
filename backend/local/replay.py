"""Replay a recorded object-watch run through the core and count the model calls it needs.
detector mode feeds the clips' frames (or, with --live, frames captured from the live path) to analyze.follow()
as live frames, with the vision-model checks answered at once; vision mode sends the run's uploaded frames and
clips through analyze.handle_upload with the real vision and video models. Both run the tracker's timers on the
recording's clock and write into a fresh store, so no live data is touched.
Run from backend/:
  python -m local.replay --data <new folder> --clips <dir> --frames <dir> [--list <file>] [--mode detector|vision]
  python -m local.replay --data <new folder> --live <dir of <epoch seconds>.jpg> [--checks on|off]
Detector mode also takes --rotate (frames turned a quarter clockwise, as from an upright phone), --visual on|off
(the step-away box as a visual prompt) and --locate on|off (the vision model locating the object); each frame's live
box is kept in replay.json.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import cv2
import numpy as np

from meanwhile import config
from meanwhile.api import cameras, people
from meanwhile.pipeline import analyze, checks as model_checks, detector as detection, models, orders, video_model
from meanwhile.pipeline import vision
from meanwhile.store import DEFAULT_DATA_DIR, Store, parse, to_iso

CAMERA = "Desk"
TICK_S = 1.0
TAIL_S = 6.0


def parse_args() -> argparse.Namespace:
    """Read command-line options."""
    ap = argparse.ArgumentParser(description="Replay a recorded run through the core.")
    ap.add_argument("--data", required=True, help="a new, empty folder for the replay's store")
    ap.add_argument("--clips", help="the run's clips, named <stamp>.mp4 (stamp = clip end)")
    ap.add_argument("--frames", help="the run's uploaded frames, named <stamp>.jpg")
    ap.add_argument("--live", help="detector mode: frames captured from the live path, named <epoch seconds>.jpg, "
                                   "fed instead of the clips' frames")
    ap.add_argument("--checks", choices=("on", "off"), default="on" if config.CHECKS else "off",
                    help="detector mode: vision-model checks before a heads-up and for new tracks")
    ap.add_argument("--list", help="detector mode: a file naming the clips whose frames are fed live, in order")
    ap.add_argument("--mode", choices=("detector", "vision"), default="detector")
    ap.add_argument("--target", default="controller", help="the object as the user named it")
    ap.add_argument("--size", help="detector mode: resize live frames to WxH first (e.g. 320x180)")
    ap.add_argument("--stride", type=int, default=config.DETECTOR_STRIDE, help="detector mode: every Nth frame")
    ap.add_argument("--rotate", action="store_true", help="detector mode: turn frames a quarter clockwise after --size")
    ap.add_argument("--visual", choices=("on", "off"), default="on" if config.VISUAL_PROMPT else "off",
                    help="detector mode: the step-away box as a visual prompt when the words are weak")
    ap.add_argument("--locate", choices=("on", "off"), default="on" if config.LOCATE else "off",
                    help="detector mode: the vision model locating the object when the detector loses it")
    a = ap.parse_args()
    if not a.live and not (a.clips and a.frames):
        ap.error("give --clips and --frames, or --live")
    return a


def gpu_used_mib() -> int | None:
    """GPU memory in use across all processes, from nvidia-smi, or None without one."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def fresh_store(data: Path, clips: Path | None, frames: Path | None) -> tuple[Store, Path]:
    """A store in a new, empty folder, with copies of the inputs under input/ (empty folders for inputs not
    given); refuses the dev server's data."""
    if data.resolve() == Path(DEFAULT_DATA_DIR).resolve():
        raise SystemExit("--data must not be the dev server's data folder")
    if data.exists() and any(data.iterdir()):
        raise SystemExit(f"--data {data} is not empty; give a new folder")
    copy = data / "input"
    for source, name in ((clips, "clips"), (frames, "frames")):
        if source:
            shutil.copytree(source, copy / name)
        else:
            (copy / name).mkdir(parents=True)
    return Store(data / "store"), copy


def stamp_of(path: Path) -> str:
    """A media file's stamp as an ISO time."""
    return analyze.stamp_time(path.stem)


def live_frames(clips: list[Path], stride: int, size: tuple[int, int] | None,
                rotate: bool = False) -> Iterator[tuple[str, np.ndarray]]:
    """Every `stride`-th frame of the clips in order with its time (a clip's name is its end time), resized and
    turned a quarter clockwise if asked."""
    index = 0
    for clip in clips:
        video = cv2.VideoCapture(str(clip))
        count, fps = int(video.get(cv2.CAP_PROP_FRAME_COUNT)), video.get(cv2.CAP_PROP_FPS) or 15.0
        end = parse(stamp_of(clip))
        for k in range(count):
            ok, image = video.read()
            if not ok:
                break
            if index % stride == 0:
                if size:
                    image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
                if rotate:
                    image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
                yield to_iso(end - timedelta(seconds=(count - 1 - k) / fps)), image
            index += 1
        video.release()


def captured_frames(folder: Path) -> Iterator[tuple[str, np.ndarray]]:
    """Frames captured from the live path, in time order, each with the time in its name (epoch seconds)."""
    for path in sorted(folder.glob("*.jpg"), key=lambda p: float(p.stem)):
        image = cv2.imread(str(path))
        if image is not None:
            yield to_iso(datetime.fromtimestamp(float(path.stem), timezone.utc)), image


def start_session(store: Store, target: str, box: list[float] | None, at: str) -> dict[str, Any]:
    """Desk online, the two people from the run registered, and an object watch on the Desk camera that started
    just before the recording."""
    cameras.register(store, {"label": CAMERA})
    people.add(store, {"name": "Owner", "role": "owner", "note": "no shirt"})
    people.add(store, {"name": "Helper", "role": "helper", "note": "a white shirt"})
    order = {"kind": "object", "target": target, "cameras": "all",
             "clip_question": orders.object_question(target),
             "report": orders.OBJECT_REPORT, "alert_when": orders.OBJECT_ALERT_WHEN,
             "summary_for_alexa": f"keeping an eye on your {target} on the {CAMERA} camera"}
    session = store.start_session(f"keep an eye on my {target}", {"watch_order": order, "focus_object": target,
                                                                  "focus_camera_id": "desk", "focus_box": box})
    return store.update_session(session["session_id"], started_at=at, state_since=at)


def counted(calls: Counter) -> None:
    """Count every model call by the schema's first field (found = grounding or frame check, relevant = clip)."""
    real = models.ask

    def ask(messages, schema, model):
        """Count the call, then ask the real model."""
        fields = schema.get("required", [])
        calls["frame check" if "in_hand" in fields else "grounding" if "found" in fields else "clip"] += 1
        return real(messages, schema, model)
    models.ask = ask


class Clock:
    """The tracker's timers on the recording's clock: a tick every TICK_S of recording time."""

    def __init__(self, store: Store, start: str, gate: analyze.Gate | None = None):
        """Start ticking from `start`; heads-ups go through `gate` if given."""
        self.store, self.next, self.gate = store, parse(start), gate

    def until(self, at: str, log: list[str]) -> None:
        """Run every tick due up to `at`."""
        while self.next <= parse(at):
            for e in analyze.tick(self.store, at=to_iso(self.next), gate=self.gate):
                log.append(f"{to_iso(self.next)[11:23]} tick: {e['action']}/{e['alert']}")
            self.next += timedelta(seconds=TICK_S)


def uploads_due(pending: list[Path], at: str) -> list[Path]:
    """Pop the uploads whose stamp is at or before `at`, oldest first."""
    due = [p for p in pending if stamp_of(p) <= at]
    for p in due:
        pending.remove(p)
    return due


def upload(store: Store, path: Path, tracking: str) -> str:
    """Put one recorded frame or clip in the store as the camera page would, and analyse it."""
    key = f"{'clips' if path.suffix == '.mp4' else 'frames'}/desk/{path.stem}{path.suffix}"
    store.write_media(key, path.read_bytes())
    return f"{key}: {analyze.handle_upload(store, key, tracking=tracking)}"


def run_detector(store: Store, copy: Path, a: argparse.Namespace, log: list[str]) -> dict[str, Any]:
    """Feed the listed clips' frames (or the captured live frames) as live frames, grounding on the first one as
    the core does; the run's uploads go through the core as well."""
    names = [Path(n.strip()).name for n in open(a.list)] if a.list else None
    clips = [copy / "clips" / n for n in names] if names else sorted((copy / "clips").glob("*.mp4"))
    size = tuple(int(v) for v in a.size.split("x")) if a.size else None
    frames = captured_frames(Path(a.live)) if a.live else live_frames(clips, a.stride, size, a.rotate)
    first_at, first = next(frames)
    det = detection.Detector()
    gpu_before = gpu_used_mib()
    jpeg = cv2.imencode(".jpg", first, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
    found = detection.ground(det, a.target, [{"camera_id": "desk", "label": CAMERA}], lambda _camera: jpeg)
    box = found["box"] if found else vision.find_in_frame(a.target, CAMERA, jpeg)
    det.calls, det.ms = 0, []
    session = start_session(store, a.target, box, to_iso(parse(first_at) - timedelta(seconds=1)))
    log.append(f"grounded by the {'detector' if found else 'vision model'}: {box} (conf {found and found['conf']})")
    config.VISUAL_PROMPT = a.visual == "on"
    checks = model_checks.Checks(enabled=a.checks == "on", wait=True, locating=a.locate == "on")
    followers: dict[str, analyze.Follower] = {}

    def gate(s: dict[str, Any]) -> str:
        """The heads-up check on the desk follower's newest frame, logged."""
        verdict = checks.heads_up(s, followers.get("desk"))
        log.append(f"{(s.get('missing_since') or '')[11:23]} heads-up check: {verdict}")
        return verdict

    pending = sorted([*(copy / "clips").glob("*.mp4"), *(copy / "frames").glob("*.jpg")], key=stamp_of)
    clock, peak, at = Clock(store, session["started_at"], gate), gpu_before or 0, first_at
    per_frame = []
    for n, (at, image) in enumerate([(first_at, first), *frames]):
        clock.until(at, log)
        for path in uploads_due(pending, at):
            log.append(f"{at[11:23]} upload {upload(store, path, 'detector')}")
        outcome = analyze.follow(store, "desk", at, image, followers, det, checks=checks)
        if outcome != "no change":
            log.append(f"{at[11:23]} follow: {outcome}")
        rules = followers["desk"].rules
        per_frame.append({"i": n * a.stride, "at": at, "mark": rules.trace[-1:], **rules.live_box()})
        if det.calls % 30 == 0:
            peak = max(peak, gpu_used_mib() or 0)
    end = to_iso(parse(at) + timedelta(seconds=TAIL_S))
    clock.until(end, log)
    for path in pending:
        log.append(f"after upload {upload(store, path, 'detector')}")
    rules = followers["desk"].rules
    steady = det.ms[10:] or det.ms
    return {"detector_frames": det.calls, "detector_ms_mean": round(float(np.mean(steady)), 1),
            "detector_ms_p95": round(float(np.percentile(steady, 95)), 1), "frame_size": list(first.shape[1::-1]),
            "target_seen": f"{rules.sightings}/{rules.frames}",
            "confirm_calls": rules.confirm_calls, "identify_calls": rules.identify_calls,
            "locate_calls": rules.locate_calls, "located": rules.located, "visual_prompt": det.look is not None,
            "marks": dict(Counter(rules.trace)), "rotate": a.rotate, "per_frame": per_frame,
            "checks": checks.status(store.latest_session()["session_id"])["counts"], "checks_ms": checks.status()["ms"],
            "gpu_mib_before": gpu_before, "gpu_mib_peak": peak}


def run_vision(store: Store, copy: Path, a: argparse.Namespace, log: list[str]) -> dict[str, Any]:
    """Send the run's uploaded frames and clips through the core in time order, with the real models."""
    pending = sorted([*(copy / "clips").glob("*.mp4"), *(copy / "frames").glob("*.jpg")], key=stamp_of)
    first_frame = next(p for p in pending if p.suffix == ".jpg")
    box = vision.find_in_frame(a.target, CAMERA, first_frame.read_bytes())
    log.append(f"grounded by the vision model: {box}")
    clips = [p for p in pending if p.suffix == ".mp4"]
    start = min(stamp_of(first_frame), to_iso(parse(stamp_of(clips[0])) - timedelta(
        seconds=video_model.clip_length_s(clips[0])))) if clips else stamp_of(first_frame)
    session = start_session(store, a.target, box, to_iso(parse(start) - timedelta(seconds=1)))
    clock, at = Clock(store, session["started_at"]), session["started_at"]
    for path in pending:
        at = stamp_of(path)
        clock.until(at, log)
        log.append(f"{at[11:23]} upload {upload(store, path, 'vision')}")
    clock.until(to_iso(parse(at) + timedelta(seconds=TAIL_S)), log)
    return {}


def main() -> None:
    """Replay, then print the outcome log, the events and the counts (also saved as replay.json)."""
    a = parse_args()
    store, copy = fresh_store(Path(a.data), a.clips and Path(a.clips), a.frames and Path(a.frames))
    calls: Counter = Counter()
    counted(calls)
    log: list[str] = []
    started = time.perf_counter()
    extra = run_detector(store, copy, a, log) if a.mode == "detector" else run_vision(store, copy, a, log)
    session = store.latest_session()
    events = store.list_events(session["session_id"])
    summary = {"mode": a.mode, "target": a.target, "phrase": detection.phrase_for(a.target), "size": a.size,
               "stride": a.stride if a.mode == "detector" else None, "model_calls": dict(calls),
               "model_calls_total": sum(calls.values()), "wall_s": round(time.perf_counter() - started, 1),
               **extra, "final_state": session.get("state"),
               "events": [{"at": e.get("happened_at", "")[11:23], "action": e["action"], "alert": e["alert"],
                           "source": e.get("source"), "clip": bool(e.get("clip_key")),
                           "frame": bool(e.get("frame_key")), "summary": e["summary"]} for e in events]}
    print("\n".join(log))
    for e in summary["events"]:
        print(f"EVENT {e['at']} {e['action']}/{e['alert']} [{e['source']}] clip={e['clip']} frame={e['frame']}: "
              f"{e['summary']}")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("events", "per_frame")}))
    (Path(a.data) / "replay.json").write_text(json.dumps({**summary, "log": log}, indent=2))


if __name__ == "__main__":
    main()
