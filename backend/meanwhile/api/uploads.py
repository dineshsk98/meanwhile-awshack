"""Upload endpoints: hand a camera page a key and URL to PUT a thumbnail, frame or clip to, and accept local PUTs."""

from __future__ import annotations

import re
from typing import Any, Callable

from meanwhile.api import Response, error
from meanwhile.store import Store, now

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

CONTENT_TYPES = {"jpg": "image/jpeg", "webm": "video/webm", "mp4": "video/mp4"}
EXTENSIONS = {"thumb": ("jpg",), "frame": ("jpg",), "clip": ("webm", "mp4")}
KEY_PATTERN = re.compile(r"^(thumbs|frames|clips)/[a-z0-9-]+/\d{8}T\d{9}Z\.(jpg|webm|mp4)$")


def stamp(ts: str) -> str:
    """A timestamp as a file-name-safe stamp: 2026-10-11T09:32:00.123Z -> 20261011T093200123Z."""
    return re.sub(r"[-:.]", "", ts)


def presign(store: Store, body: dict[str, Any]) -> Response:
    """POST /api/uploads {"camera_id", "kind", "ext"}: a new key plus where and how to PUT its bytes."""
    camera_id, kind, ext = body.get("camera_id"), body.get("kind"), body.get("ext")
    if not camera_id or (store.get_camera(camera_id) or {"removed": True}).get("removed"):
        return error(404, "unknown_camera", "That camera isn't registered. Please open the camera page again.")
    if ext not in EXTENSIONS.get(kind, ()):
        return error(400, "bad_upload", "Uploads are thumb or frame (jpg) or clip (webm or mp4).")
    key = f"{kind}s/{camera_id}/{stamp(now())}.{ext}"
    return 200, {"key": key, "url": store.upload_url(key), "method": "PUT",
                 "headers": {"Content-Type": CONTENT_TYPES[ext]}}


def put_local(store: Store, key: str, data: bytes, on_upload: Callable[[str], None] | None = None) -> Response:
    """PUT /api/media/{key}: save an upload under the data dir, then tell on_upload (the analyser's queue)."""
    if not KEY_PATTERN.match(key):
        return error(400, "bad_key", "That isn't an upload key the core handed out.")
    if not data or len(data) > MAX_UPLOAD_BYTES:
        return error(413, "bad_size", f"Uploads must be between 1 byte and {MAX_UPLOAD_BYTES // 2**20} MB.")
    store.write_media(key, data)
    if on_upload:
        on_upload(key)
    return 200, {"key": key, "bytes": len(data)}
