"""WebRTC connection setup for live tiles: the core relays a viewer's offer to a camera page and the answer back."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from meanwhile.api import Response, cameras, error
from meanwhile.store import Store, StoreError, now, parse, to_iso

OFFER_TTL_S = 30
MAX_SDP = 20_000
PURPOSES = ("live", "ingest")


def ice_servers() -> Response:
    """GET /api/rtc/ice-servers: the STUN/TURN servers a viewer (the phone app) connects with, the same list camera
    pages get when they register."""
    return 200, {"ice_servers": cameras.ice_servers()}


def _cutoff() -> str:
    """The creation time before which offers are stale."""
    return to_iso(parse(now()) - timedelta(seconds=OFFER_TTL_S))


def _sdp(body: dict[str, Any]) -> str | None:
    """The session description from a request body, or None if missing or too long."""
    sdp = body.get("sdp")
    return sdp if isinstance(sdp, str) and 0 < len(sdp) <= MAX_SDP else None


def offer(store: Store, camera_id: str, body: dict[str, Any]) -> Response:
    """POST /api/rtc/{camera_id}/offers {"sdp", "purpose"?}: a viewer asks a camera for live video; purpose is
    live (default, a person watching) or ingest (the detector, which gets larger frames)."""
    if (store.get_camera(camera_id) or {"removed": True}).get("removed"):
        return error(404, "unknown_camera", "That camera isn't registered.")
    sdp = _sdp(body)
    if sdp is None:
        return error(400, "bad_sdp", "The offer needs an SDP description.")
    purpose = body.get("purpose") or "live"
    if purpose not in PURPOSES:
        return error(400, "bad_purpose", "The purpose must be live or ingest.")
    store.drop_rtc_offers_before(_cutoff())
    return 201, {"offer_id": store.add_rtc_offer(camera_id, sdp, purpose)["offer_id"]}


def pending(store: Store, camera_id: str) -> Response:
    """GET /api/rtc/{camera_id}/offers: the camera page collects offers it hasn't answered yet."""
    offers = store.pending_rtc_offers(camera_id, _cutoff())
    return 200, {"offers": [{"offer_id": o["offer_id"], "sdp": o["offer"], "purpose": o.get("purpose", "live")}
                            for o in offers]}


def answer(store: Store, offer_id: str, body: dict[str, Any]) -> Response:
    """POST /api/rtc/offers/{offer_id}/answer {"sdp"}: the camera page replies to one offer."""
    sdp = _sdp(body)
    if sdp is None:
        return error(400, "bad_sdp", "The answer needs an SDP description.")
    try:
        store.answer_rtc_offer(offer_id, sdp)
    except StoreError:
        return error(404, "no_offer", "That offer has expired or was already answered.")
    return 200, {"offer_id": offer_id}


def answer_for(store: Store, offer_id: str) -> Response:
    """GET /api/rtc/offers/{offer_id}: the viewer waits for the camera's answer (null until it arrives)."""
    found = store.get_rtc_offer(offer_id)
    if found is None:
        return error(404, "no_offer", "That offer has expired.")
    return 200, {"offer_id": offer_id, "sdp": found["answer"]}
