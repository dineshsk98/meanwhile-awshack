"""POST /api/look: answer a question about what one camera, or every online camera, sees right now, from each
camera's newest frame (its live ingest frame or its heartbeat thumbnail, whichever is newer). POST /api/look-back:
answer a question about what happened in the last few minutes, from the cameras' recent windows. POST /api/state-of:
answer how a thing is from its stored status, or check the video and store what it concludes."""

from __future__ import annotations

import re
from typing import Any, Callable

from meanwhile import config
from meanwhile.api import Response, error
from meanwhile.api.sessions import frame_reader, people_by_id, spoken_name
from meanwhile.api.uploads import stamp
from meanwhile.contract import Camera
from meanwhile.pipeline import look as looking
from meanwhile.pipeline import recent, states, video_model
from meanwhile.pipeline.faces import Faces
from meanwhile.pipeline.models import ModelUnavailable
from meanwhile.pipeline.orders import labelled_in
from meanwhile.store import Store, now

LiveFrame = Callable[[str], "tuple[bytes, str] | None"]
MAX_QUESTION = 300
_SKIP = {"the", "my", "a", "camera", "cam", "on", "in", "at", "by", "of", "near"}


def camera_named(store: Store, name: str) -> Camera | None:
    """The camera whose label or id matches a spoken name ('Desk', 'the desk camera', 'the camera on desk'), else
    the one whose label's words are all in the name."""
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in _SKIP]
    wanted = " ".join(words)
    if not wanted:
        return None
    cameras = store.list_cameras()
    exact = [c for c in cameras if wanted in (c.get("label", "").lower(), c["camera_id"].replace("-", " "))]
    if exact:
        return exact[0]
    return next((c for c in cameras if set(re.findall(r"[a-z0-9]+", c.get("label", "").lower())) <= set(words)), None)


def newest_frame(store: Store, camera: Camera, live: LiveFrame | None) -> looking.Frame | None:
    """A camera's newest frame: the live ingest frame when it is newer than the heartbeat thumbnail, else the
    thumbnail; None if it has neither."""
    best: tuple[bytes, str, str] | None = None
    thumb = frame_reader(store)(camera)
    if thumb:
        fresh = store.get_camera(camera["camera_id"]) or camera
        best = (thumb, fresh.get("thumb_at") or fresh["last_seen"], "thumbnail")
    now = live(camera["camera_id"]) if live else None
    if now and (best is None or now[1] > best[1]):
        best = (now[0], now[1], "live")
    if best is None:
        return None
    return {"camera_id": camera["camera_id"], "label": camera.get("label") or camera["camera_id"],
            "online": store.is_online(camera), "jpeg": best[0], "at": best[1], "source": best[2]}


def _labels(cameras: list[Camera]) -> str:
    """Camera labels joined for speech: 'Desk, Room or Door'."""
    names = [c.get("label") or c["camera_id"] for c in cameras]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def _saved(store: Store, frame: looking.Frame) -> dict[str, Any]:
    """Keep a looked-at frame as media and describe it: camera, online, when, URL and source."""
    key = f"looks/{frame['camera_id']}/{stamp(frame['at'])}.jpg"
    store.write_media(key, frame["jpeg"])
    return {"camera_id": frame["camera_id"], "camera": frame["label"], "online": frame["online"],
            "frame_at": frame["at"], "frame_url": store.media_url(key), "frame_source": frame["source"]}


def _people(seen: list[tuple[str, list[looking.Seen]]], people: dict[str, Any]) -> list[dict[str, Any]]:
    """Everyone named by face in the frames (as (camera id, faces) pairs), once each, with the camera they were first
    seen on; a face matching an id nobody registered here is left out."""
    named: dict[str, dict[str, Any]] = {}
    for camera_id, faces_seen in seen:
        for s in faces_seen:
            person = people.get(s["person_id"] or "")
            if person and s["person_id"] not in named:
                named[s["person_id"]] = {"person_id": s["person_id"], "name": spoken_name(person),
                                         "role": person.get("role"), "camera_id": camera_id}
    return list(named.values())


def look(store: Store, body: dict[str, Any], faces: Faces | None, live: LiveFrame | None = None,
         model: str | None = None) -> Response:
    """POST /api/look {"question", "camera"?}: the vision model's short spoken answer about the named camera's
    newest frame (online or not), or every online camera's, with the people in them named by face."""
    question = " ".join(str(body.get("question") or "").split())
    if not question or len(question) > MAX_QUESTION:
        return error(400, "bad_question", "Please ask a short question about what a camera sees.")
    cameras = store.list_cameras()
    name = str(body.get("camera") or "").strip()
    if name:
        camera = camera_named(store, name)
        if camera is None:
            known = f" I can look through the {_labels(cameras)} camera{'s' if len(cameras) > 1 else ''}." if cameras else ""
            return error(404, "unknown_camera", f"I don't have a {name.removesuffix(' camera')} camera.{known}")
        chosen = [camera]
    else:
        chosen = [c for c in cameras if store.is_online(c)]
        if not chosen:
            return error(409, "no_frame", "None of the cameras is on right now.")
    frames = [f for f in (newest_frame(store, c, live) for c in chosen) if f]
    if not frames:
        return error(409, "no_frame", f"The {chosen[0].get('label')} camera hasn't sent me a picture yet."
                     if name else "None of the cameras has sent me a picture yet.")
    try:
        result = looking.answer(question, frames, faces, store, model)
    except ModelUnavailable as exc:
        print(f"Look:    the vision model didn't answer: {exc}", flush=True)
        return error(503, "model_unavailable", "Sorry, I couldn't look through the cameras just now.")
    looked = {f["camera_id"]: _saved(store, f) for f in frames}
    people, seen = result["people"], result["seen"]
    named = _people(list(seen.items()), people)
    print(f"Look:    {question!r} -> {result['answer']!r} ({', '.join(looked)}; faces {result['took_s']['faces']} s, "
          f"model {result['took_s']['model']} s; appearance sentences dropped: {result['dropped']})", flush=True)
    return 200, {"question": question, "answer": result["answer"], **looked[result["frame"]["camera_id"]],
                 "people": named, "looked_at": list(looked.values()), "took_s": result["took_s"]}


def look_back(store: Store, body: dict[str, Any], faces: Faces | None, model: str | None = None) -> Response:
    """POST /api/look-back {"question", "camera"?}: the video model's short spoken answer about what happened in the
    last RECENT_WINDOW_S seconds on the named camera, or on every camera with recent frames, from at most
    RECENT_MODEL_FRAMES of their kept heartbeat thumbnails, with people named by face."""
    question = " ".join(str(body.get("question") or "").split())
    if not question or len(question) > MAX_QUESTION:
        return error(400, "bad_question", "Please ask a short question about what happened.")
    at, window = now(), recent.window_words()
    cameras = store.list_cameras()
    name = str(body.get("camera") or "").strip()
    if name:
        camera = camera_named(store, name)
        if camera is None:
            known = f" I can look through the {_labels(cameras)} camera{'s' if len(cameras) > 1 else ''}." if cameras else ""
            return error(404, "unknown_camera", f"I don't have a {name.removesuffix(' camera')} camera.{known}")
        cameras = [camera]
    kept = [(c, recent.frames(store, c["camera_id"], at)) for c in cameras]
    kept = [(c, f) for c, f in kept if f][:max(config.RECENT_MODEL_FRAMES, 1)]
    if not kept:
        where = f"the {cameras[0].get('label')} camera" if name else "any camera"
        return error(409, "no_frames", f"I don't have any pictures from {where} in the last {window}.")
    each = max(config.RECENT_MODEL_FRAMES // len(kept), 1)
    chosen: list[looking.Frame] = []
    for camera, found in kept:
        for taken, key in recent.spread(found, each):
            try:
                jpeg = store.media_path(key).read_bytes()
            except FileNotFoundError:
                continue
            chosen.append({"camera_id": camera["camera_id"], "label": camera.get("label") or camera["camera_id"],
                           "online": store.is_online(camera, at), "jpeg": jpeg, "at": taken, "source": "recent"})
    chosen.sort(key=lambda f: f["at"])
    if not chosen:
        return error(409, "no_frames", f"I don't have any pictures from the last {window}.")
    try:
        result = recent.answer(question, chosen, faces, store, at, model)
    except ModelUnavailable as exc:
        print(f"Look back: the video model didn't answer: {exc}", flush=True)
        return error(503, "model_unavailable", "Sorry, I couldn't look back through the cameras just now.")
    shown = _saved(store, result["frame"])
    looked = [{"camera_id": c["camera_id"], "camera": c.get("label") or c["camera_id"],
               "frames": sum(1 for f in chosen if f["camera_id"] == c["camera_id"]), "kept": len(found),
               "from": found[0][0], "to": found[-1][0]} for c, found in kept]
    print(f"Look back: {question!r} -> {result['answer']!r} ({len(chosen)} frames; faces {result['took_s']['faces']} s, "
          f"model {result['took_s']['model']} s; appearance sentences dropped: {result['dropped']})", flush=True)
    return 200, {"question": question, "answer": result["answer"], "asks_earlier": result["asks_earlier"],
                 "window_s": config.RECENT_WINDOW_S, "window": window, **shown,
                 "people": _people([(f["camera_id"], faces_seen) for f, faces_seen in result["seen"]],
                                   result["people"]), "looked_at": looked,
                 "took_s": result["took_s"]}


def _frames_around(store: Store, state: Any, camera: Camera) -> list[looking.Frame]:
    """Frames around a thing's last recorded change: its clip's sampled frames, else the camera's recent-window
    frames (when the change is that recent); [] when there are none."""
    label = camera.get("label") or camera["camera_id"]
    base = {"camera_id": camera["camera_id"], "label": label, "online": store.is_online(camera), "at": state["at"]}
    if state.get("clip_key"):
        try:
            clip = video_model.sample(store.media_path(state["clip_key"]))
            return [{**base, "jpeg": jpeg, "source": "clip"} for jpeg in clip["frames"]]
        except (FileNotFoundError, ValueError):
            pass
    kept = recent.frames(store, camera["camera_id"])
    chosen = []
    for taken, key in recent.spread(kept, config.RECENT_MODEL_FRAMES):
        try:
            chosen.append({**base, "at": taken, "jpeg": store.media_path(key).read_bytes(), "source": "recent"})
        except FileNotFoundError:
            continue
    return chosen


def _frames_now(store: Store, cameras: list[Camera], live: LiveFrame | None) -> list[looking.Frame]:
    """The cameras' recent-window frames, at most RECENT_MODEL_FRAMES spread over the window and the cameras, oldest
    first; when none has any, each camera's newest frame."""
    kept = [(c, recent.frames(store, c["camera_id"])) for c in cameras]
    kept = [(c, f) for c, f in kept if f][:max(config.RECENT_MODEL_FRAMES, 1)]
    chosen: list[looking.Frame] = []
    if kept:
        each = max(config.RECENT_MODEL_FRAMES // len(kept), 1)
        for camera, found in kept:
            for taken, key in recent.spread(found, each):
                try:
                    chosen.append({"camera_id": camera["camera_id"], "label": camera.get("label") or camera["camera_id"],
                                   "online": store.is_online(camera), "jpeg": store.media_path(key).read_bytes(),
                                   "at": taken, "source": "recent"})
                except FileNotFoundError:
                    continue
    if not chosen:
        chosen = [f for f in (newest_frame(store, c, live) for c in cameras) if f]
    return sorted(chosen, key=lambda f: f["at"])


def _status(store: Store, question: str, last: Any, people: dict[str, Any]) -> dict[str, Any]:
    """A stored status as the state-of reply."""
    person = people.get(last.get("by") or "")
    where = store.get_camera(last["camera_id"]) or {"camera_id": last["camera_id"]}
    return {"source": "state", "question": question, "thing": last["thing"], "value": last["value"], "at": last["at"],
            "checked_by": last.get("source") or "event", "by": last.get("by") or None,
            "by_name": spoken_name(person) if person else None, "by_role": person.get("role") if person else None,
            "camera_id": last["camera_id"], "camera": where.get("label") or last["camera_id"],
            "event_id": last.get("event_id"), "clip_url": store.media_url(last.get("clip_key"))}


def state_of(store: Store, body: dict[str, Any], faces: Faces | None, live: LiveFrame | None = None,
             model: str | None = None) -> Response:
    """POST /api/state-of {"question", "thing", "camera"?}: how a thing is. A conclusive stored status (clear, and
    no older than MEANWHILE_STATE_MAX_AGE_S) answers on its own. Otherwise the video is checked: the last change's
    clip when that change is recent but unclear, else the recent frames, else the newest frame, of the named camera,
    the camera of the last change, a camera whose label is in the thing, or every online camera; a confident
    conclusion is stored as the status, so the next question answers from it."""
    question = " ".join(str(body.get("question") or "").split())
    thing = " ".join(str(body.get("thing") or "").split()) or question
    if not question or len(question) > MAX_QUESTION:
        return error(400, "bad_question", "Please ask a short question about how something is.")
    name = str(body.get("camera") or "").strip()
    camera = camera_named(store, name) if name else None
    cameras = store.list_cameras()
    if name and camera is None:
        known = f" I can look through the {_labels(cameras)} camera{'s' if len(cameras) > 1 else ''}." if cameras else ""
        return error(404, "unknown_camera", f"I don't have a {name.removesuffix(' camera')} camera.{known}")
    found = states.matching(store, thing, camera["camera_id"] if camera else None)
    people = people_by_id(store)
    if found and states.conclusive(found):
        return 200, _status(store, question, found[0], people)
    last = found[0] if found else None
    where = store.get_camera(last["camera_id"]) if last else None
    frames: list[looking.Frame] = []
    if last and where and not states.clear(found) and states.fresh(last):
        frames = _frames_around(store, last, where)
    if not frames:
        chosen = ([camera] if camera else [where] if where else
                  [c for c in [labelled_in(cameras, thing)] if c] or [c for c in cameras if store.is_online(c)])
        frames = _frames_now(store, chosen, live)
    if not frames:
        return error(409, "no_frame", "I don't have a recent picture to check that.")
    old_frames = [f for f in frames if not states.fresh(f)]
    frames = [f for f in frames if states.fresh(f)]
    if not frames:
        newest = max(old_frames, key=lambda f: f["at"])
        return error(409, "stale_frame", f"The {newest['label']} camera hasn't sent a picture recently, so I can't "
                                         "tell right now.", camera=newest["label"], last_frame_at=newest["at"])
    try:
        result = states.check(question, last["thing"] if last else thing, frames, faces, store, last, model)
    except ModelUnavailable as exc:
        print(f"State of: the video model didn't answer: {exc}", flush=True)
        return error(503, "model_unavailable", "Sorry, I couldn't look at the video just now.")
    print(f"State of: {thing!r} checked on video -> {result['state']!r} "
          f"({'stored' if result['stored'] else 'not stored'}): {result['answer']!r}", flush=True)
    return 200, {"source": "video", "question": question, "thing": last["thing"] if last else thing,
                 "value": result["state"] or None, "stored": result["stored"] is not None, "answer": result["answer"],
                 **_saved(store, result["frame"]),
                 "people": _people([(f["camera_id"], s) for f, s in result["seen"]], result["people"]),
                 "took_s": result["took_s"]}
