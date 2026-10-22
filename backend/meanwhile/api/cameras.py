"""Camera endpoints: register a labelled browser camera, take its heartbeats, rename or remove it, and list cameras
as CONTRACT.md shapes them."""

from __future__ import annotations

import re
from typing import Any

from meanwhile import config
from meanwhile.api import Response, error
from meanwhile.api.uploads import KEY_PATTERN
from meanwhile.contract import Camera
from meanwhile.pipeline import recent
from meanwhile.store import Store, now

MAX_LABEL = 24


def camera_id_for(label: str) -> str:
    """The camera id for a label: lowercase words joined by dashes ("Living Room" -> "living-room")."""
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def camera_out(store: Store, camera: Camera, at: str | None = None) -> dict[str, Any]:
    """A camera as the contract returns it: no thumb_key, thumb_at or removed flag, plus `id` (the app's name for
    camera_id), online and thumb_url."""
    out = {k: v for k, v in camera.items() if k not in ("thumb_key", "thumb_at", "removed")}
    return {**out, "id": camera["camera_id"], "online": store.is_online(camera, at), "thumb_url": store.media_url(camera.get("thumb_key"))}


def ice_servers() -> list[dict[str, Any]]:
    """The STUN/TURN servers from config in the browser's RTCIceServer shape; turn: URLs carry the username and credential."""
    return [{"urls": url, **({"username": config.TURN_USERNAME, "credential": config.TURN_CREDENTIAL}
                             if url.startswith(("turn:", "turns:")) else {})} for url in config.ICE_SERVERS]


def encodings() -> dict[str, dict[str, Any]]:
    """How the camera page encodes video, from config: a person's live view, and the detector's ingest."""
    return {"live": {"scale_down": config.LIVE_SCALE_DOWN, "max_bitrate": config.LIVE_MAX_BITRATE,
                     "max_framerate": config.LIVE_MAX_FPS},
            "ingest": {"short_side": config.INGEST_SHORT_SIDE, "max_bitrate": config.INGEST_MAX_BITRATE,
                       "max_framerate": config.INGEST_MAX_FPS}}


def watch_for(store: Store, camera_id: str) -> dict[str, Any]:
    """What this camera should do now: whether the active session watches it, its focus box if it has one, and
    whether the object is away from its resting spot (held or out of view), when the whole frame is watched."""
    session = store.active_session()
    order = (session or {}).get("watch_order", {})
    cameras = order.get("cameras", [])
    if not session or not (cameras == "all" or camera_id in cameras):
        return {"active": False, "session_id": None, "focus_box": None, "held": False}
    focus_here = session.get("focus_camera_id") == camera_id
    away = focus_here and session.get("state", "on_surface") != "on_surface"
    return {"active": True, "session_id": session["session_id"],
            "focus_box": session.get("focus_box") if focus_here else None, "held": away}


def list_cameras(store: Store, at: str | None = None) -> Response:
    """GET /api/cameras: every camera, with online judged at `at` (default now)."""
    return 200, {"cameras": [camera_out(store, c, at) for c in store.list_cameras()]}


def free_camera_id(store: Store, label: str) -> str:
    """A new camera id from the label, with -2, -3… added when a camera (perhaps renamed since) already has it."""
    base = camera_id_for(label)
    camera_id, n = base, 1
    while store.get_camera(camera_id) is not None:
        n += 1
        camera_id = f"{base}-{n}"
    return camera_id


def check_label(label: str) -> Response | None:
    """The 400 reply for a label that is empty or too long, else None."""
    if not camera_id_for(label) or len(label) > MAX_LABEL:
        return error(400, "bad_label", f"Please give the camera a short name, up to {MAX_LABEL} letters.")
    return None


def register(store: Store, body: dict[str, Any]) -> Response:
    """POST /api/cameras {"label"}: reuse the camera with that label (a renamed or removed one too, which comes back)
    or create one, and mark it seen; the reply also has the ICE servers the camera page's peer connections use and how
    it encodes its video."""
    label = str(body.get("label") or "").strip()
    if bad := check_label(label):
        return bad
    existing = store.camera_by_label(label)
    if existing:
        camera_id, label = existing["camera_id"], existing["label"]
    else:
        camera_id = free_camera_id(store, label)
    camera = store.heartbeat(camera_id, label=label, removed=False)
    return 200, {"camera": camera_out(store, camera), "watch": watch_for(store, camera_id), "ice_servers": ice_servers(),
                 "encodings": encodings()}


def heartbeat(store: Store, camera_id: str, body: dict[str, Any]) -> Response:
    """POST /api/cameras/{id}/heartbeat {"thumb_key"?}: mark the camera seen (a new thumbnail replaces the old one,
    noting when it came and keeping it in the camera's recent window) and say whether it is being watched."""
    camera = store.get_camera(camera_id)
    if camera is None:
        return error(404, "unknown_camera", "That camera isn't registered. Please open the camera page again.")
    if camera.get("removed"):
        return error(410, "camera_removed", "This camera was removed in the app. Start it again to add it back.")
    thumb_key = body.get("thumb_key")
    if thumb_key is None:
        camera = store.heartbeat(camera_id)
        return 200, {"camera": camera_out(store, camera), "watch": watch_for(store, camera_id)}
    if not (KEY_PATTERN.match(str(thumb_key)) and thumb_key.startswith(f"thumbs/{camera_id}/")):
        return error(400, "bad_thumb_key", "That isn't a thumbnail key for this camera.")
    old_key, at = camera.get("thumb_key"), now()
    camera = store.heartbeat(camera_id, thumb_key=thumb_key, thumb_at=at)
    if old_key != thumb_key:
        recent.keep(store, camera_id, thumb_key, at)
    if old_key and old_key != thumb_key:
        store.delete_media(old_key)
    return 200, {"camera": camera_out(store, camera), "watch": watch_for(store, camera_id)}


def rename(store: Store, camera_id: str, body: dict[str, Any]) -> Response:
    """PATCH /api/cameras/{id} {"label"}: give the camera a new label, keeping its id (and its past events)."""
    camera = store.get_camera(camera_id)
    if camera is None or camera.get("removed"):
        return error(404, "unknown_camera", "That camera isn't registered.")
    label = str(body.get("label") or "").strip()
    if bad := check_label(label):
        return bad
    other = store.camera_by_label(label)
    if other and other["camera_id"] != camera_id:
        return error(409, "label_taken", f"Another camera is already called {other['label']}.")
    return 200, {"camera": camera_out(store, store.put_camera({"camera_id": camera_id, "label": label}))}


def remove(store: Store, camera_id: str) -> Response:
    """DELETE /api/cameras/{id}: hide the camera from the list and stop its page (its heartbeats get 410), deleting
    its thumbnail and recent window; its past events stay. Refused while it is the focus camera of the active session."""
    camera = store.get_camera(camera_id)
    if camera is None or camera.get("removed"):
        return error(404, "unknown_camera", "That camera isn't registered.")
    session = store.active_session()
    if session and session.get("focus_camera_id") == camera_id:
        return error(409, "camera_in_use", "This camera is watching the current session. End the session first.")
    if camera.get("thumb_key"):
        store.delete_media(camera["thumb_key"])
    recent.clear(store, camera_id)
    store.put_camera({"camera_id": camera_id, "removed": True, "thumb_key": None})
    return 200, {"removed": camera_id}
