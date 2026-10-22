"""The core's settings, read from the environment and the repo root's .env. Every name must be set (values and their
meaning are in .env.example); names marked optional below may be set to an empty value."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_DIR = Path(__file__).resolve().parents[2]
load_dotenv(REPO_DIR / ".env")


def _need(name: str) -> str:
    """A required setting's value; stops with a message naming it when it is missing or empty."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set. Add it to .env at the repo root (see .env.example).")
    return value


def _optional(name: str) -> str:
    """A setting that must be present but may be empty (an off switch or a secret not used here)."""
    if name not in os.environ:
        raise RuntimeError(f"{name} is missing. Add it to .env at the repo root, empty if unused (see .env.example).")
    return os.environ[name].strip()


def _path(name: str) -> Path:
    """A required path setting; a relative path is taken from the repo root."""
    path = Path(_need(name))
    return path if path.is_absolute() else REPO_DIR / path


ORDER_MODEL = _need("MEANWHILE_ORDER_MODEL")
VISION_MODEL = _need("MEANWHILE_VISION_MODEL")
VIDEO_MODEL = _need("MEANWHILE_VIDEO_MODEL")
OLLAMA_URL = _need("OLLAMA_URL")
API_KEY = _need("MEANWHILE_KEY")
# Device pairing: a one-time code's lifetime, the pairing attempts allowed per client address in a window, and how
# long a paired browser's cookie lasts
PAIR_CODE_TTL_S = float(_need("MEANWHILE_PAIR_CODE_TTL_S"))
PAIR_TRIES = int(_need("MEANWHILE_PAIR_TRIES"))
PAIR_TRIES_WINDOW_S = float(_need("MEANWHILE_PAIR_TRIES_WINDOW_S"))
PAIR_COOKIE_DAYS = float(_need("MEANWHILE_PAIR_COOKIE_DAYS"))
# Requests that present a wrong key or token: at most AUTH_FAIL_TRIES per client address in AUTH_FAIL_WINDOW_S
# seconds, after which that client gets 429 until the window has passed
AUTH_FAIL_TRIES = int(_need("MEANWHILE_AUTH_FAIL_TRIES"))
AUTH_FAIL_WINDOW_S = float(_need("MEANWHILE_AUTH_FAIL_WINDOW_S"))
# Media links the core hands out are signed and last at least this many seconds (signed with a secret derived from
# MEANWHILE_KEY)
MEDIA_URL_TTL_S = float(_need("MEANWHILE_MEDIA_URL_TTL_S"))
# Optional: the domain the browser cookie covers with its subdomains (empty: only the host that paired)
PAIR_COOKIE_DOMAIN = _optional("MEANWHILE_PAIR_COOKIE_DOMAIN")
# Optional: the public base URL used in pairing links (empty: the request's own scheme and host)
PUBLIC_URL = _optional("MEANWHILE_PUBLIC_URL").rstrip("/")
FCM_SERVICE_ACCOUNT = _optional("MEANWHILE_FCM_SERVICE_ACCOUNT")
FCM_SCOPE = _need("MEANWHILE_FCM_SCOPE")
FCM_SEND_URL = _need("MEANWHILE_FCM_SEND_URL")
TRACKING = _need("MEANWHILE_TRACKING")
DETECTOR_MODEL = _need("MEANWHILE_DETECTOR_MODEL")
DETECTOR_WEIGHTS = _path("MEANWHILE_DETECTOR_WEIGHTS")
DETECTOR_TEXT_ENCODER = _need("MEANWHILE_DETECTOR_TEXT_ENCODER")
DETECTOR_IMGSZ = int(_need("MEANWHILE_DETECTOR_IMGSZ"))
DETECTOR_CONF = float(_need("MEANWHILE_DETECTOR_CONF"))
DETECTOR_STRIDE = int(_need("MEANWHILE_DETECTOR_STRIDE"))
# Optional: empty means cuda when available
DETECTOR_DEVICE = _optional("MEANWHILE_DETECTOR_DEVICE")
# Vision-model checks on the detector path (before a heads-up, and for a new track): on or off, and how long a
# heads-up waits for its check before it is sent anyway
CHECKS = _need("MEANWHILE_CHECKS").lower() != "off"
CHECK_TIMEOUT_S = float(_need("MEANWHILE_CHECK_TIMEOUT_S"))
# The vision model locating the object on the followed camera (on or off; also needs checks on): asked once the
# detector has had no lock on the object for LOCATE_AFTER_S, at most once per LOCATE_GAP_S while it should be in
# view and once per LOCATE_MISSING_GAP_S while it is missing (0 stops asking then); an answer not back within
# LOCATE_WAIT_S counts as not found
LOCATE = _need("MEANWHILE_LOCATE").lower() != "off"
LOCATE_AFTER_S = float(_need("MEANWHILE_LOCATE_AFTER_S"))
LOCATE_GAP_S = float(_need("MEANWHILE_LOCATE_GAP_S"))
LOCATE_MISSING_GAP_S = float(_need("MEANWHILE_LOCATE_MISSING_GAP_S"))
LOCATE_WAIT_S = float(_need("MEANWHILE_LOCATE_WAIT_S"))
# A box cut by the frame's edge counts as the object only if its area is at least this share of the object's
# known size
EDGE_PART = float(_need("MEANWHILE_EDGE_PART"))
# The step-away box as a visual prompt for the detector (on or off): added to the object's phrase, with this
# weight, when the phrase alone detects the object at its step-away box too weakly to start a track
VISUAL_PROMPT = _need("MEANWHILE_VISUAL_PROMPT").lower() != "off"
VISUAL_PROMPT_WEIGHT = float(_need("MEANWHILE_VISUAL_PROMPT_WEIGHT"))
# With detector tracking, the followed camera's clips also go to the video model for anything else done to the
# object (on or off; off: they only join the events they cover)
OBJECT_CLIP_ACTIONS = _need("MEANWHILE_OBJECT_CLIP_ACTIONS").lower() != "off"
ICE_SERVERS = [u.strip() for u in _need("MEANWHILE_ICE_SERVERS").split(",") if u.strip()]
# How camera pages encode their video: for a person's live view (scaled down by a factor) and for the detector's
# ingest (the frame's short side in pixels); bitrate in bits per second, frame rate in frames per second
LIVE_SCALE_DOWN = float(_need("MEANWHILE_LIVE_SCALE_DOWN"))
LIVE_MAX_BITRATE = int(_need("MEANWHILE_LIVE_MAX_BITRATE"))
LIVE_MAX_FPS = int(_need("MEANWHILE_LIVE_MAX_FPS"))
INGEST_SHORT_SIDE = int(_need("MEANWHILE_INGEST_SHORT_SIDE"))
INGEST_MAX_BITRATE = int(_need("MEANWHILE_INGEST_MAX_BITRATE"))
INGEST_MAX_FPS = int(_need("MEANWHILE_INGEST_MAX_FPS"))
TURN_USERNAME = _optional("MEANWHILE_TURN_USERNAME")
TURN_CREDENTIAL = _optional("MEANWHILE_TURN_CREDENTIAL")
GEMINI_API_KEY = _optional("GEMINI_API_KEY")
# Optional: an empty profile means the default credential chain (an instance role on EC2)
AWS_PROFILE = _optional("MEANWHILE_AWS_PROFILE")
AWS_REGION = _need("MEANWHILE_AWS_REGION")
# Face ID with Amazon Rekognition (on or off; off names people by their clothes note): the collection, the lowest
# match similarity in percent, the margin added around a person's upper body as a share of its box, the share of
# a person box (from the top) that is the upper body, how many tries a person track gets and the seconds between
# them, and the longest side a registered photo is scaled to
FACES = _need("MEANWHILE_FACES").lower() == "on"
FACE_COLLECTION = _need("MEANWHILE_FACE_COLLECTION")
FACE_MATCH_THRESHOLD = float(_need("MEANWHILE_FACE_MATCH_THRESHOLD"))
FACE_CROP_MARGIN = float(_need("MEANWHILE_FACE_CROP_MARGIN"))
FACE_UPPER_BODY = float(_need("MEANWHILE_FACE_UPPER_BODY"))
FACE_RETRIES = int(_need("MEANWHILE_FACE_RETRIES"))
FACE_RETRY_GAP_S = float(_need("MEANWHILE_FACE_RETRY_GAP_S"))
FACE_PHOTO_MAX_SIDE = int(_need("MEANWHILE_FACE_PHOTO_MAX_SIDE"))
# Unnamed people: whether a face that matches nobody is kept (on: indexed under a new unnamed person with one
# photo, so the same person is recognised again), and after how many days without a sighting they are deleted
FACE_KEEP_UNNAMED = _need("MEANWHILE_FACE_KEEP_UNNAMED").lower() == "on"
FACE_UNNAMED_DAYS = float(_need("MEANWHILE_FACE_UNNAMED_DAYS"))
# Face ID on clip events: how many of the clip's sampled frames nearest an event are tried until one shows a named
# face, and the margin around each face found in a frame (share of the face box) for its search
FACE_CLIP_FRAMES = int(_need("MEANWHILE_FACE_CLIP_FRAMES"))
FACE_CLIP_MARGIN = float(_need("MEANWHILE_FACE_CLIP_MARGIN"))
# The recent past: how many seconds of each camera's heartbeat thumbnails are kept (session or not), at most how
# many per camera, and at most how many of them one question sends to the video model
RECENT_WINDOW_S = float(_need("MEANWHILE_RECENT_WINDOW_S"))
RECENT_MAX_FRAMES = int(_need("MEANWHILE_RECENT_MAX_FRAMES"))
RECENT_MODEL_FRAMES = int(_need("MEANWHILE_RECENT_MODEL_FRAMES"))
# A thing's stored status (from a watch's event or a video check) is trusted for this many seconds; after that a
# question about it checks the video again
STATE_MAX_AGE_S = float(_need("MEANWHILE_STATE_MAX_AGE_S"))
# Optional: the person id of the user (the one Alexa talks to, called "you"); registering that id always makes
# them the owner. Empty: whoever is registered with the owner role
OWNER_ID = _optional("MEANWHILE_OWNER_ID")
# Where the store keeps its database and media
DATA_DIR = _path("MEANWHILE_DATA")
# Ports of the local dev server: HTTP, and HTTPS for phone cameras with --lan
PORT = int(_need("MEANWHILE_PORT"))
LAN_PORT = int(_need("MEANWHILE_LAN_PORT"))
