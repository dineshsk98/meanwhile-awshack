"""The local dev server: the core's API handlers, uploaded media, the web pages and the analyser in one process.
Run from backend/: python -m local.devserver [--lan]  (HTTP on 127.0.0.1:MEANWHILE_PORT; --lan serves HTTP on all interfaces and adds HTTPS on MEANWHILE_LAN_PORT for phones).
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response as PlainResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from meanwhile import config
from meanwhile import push as push_module
from meanwhile.api import Response, auth, cameras, devices, error, look, pairing, people, rtc, sessions, uploads
from meanwhile.pipeline import models, orders, vision
from meanwhile.pipeline.faces import Faces, get_faces
from meanwhile.pipeline.analyze import Analyser
from meanwhile.pipeline.ingest import Ingest
from meanwhile.store import Store, get_store, to_iso

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
RECENT_UPLOADS = 20


class FreshStaticFiles(StaticFiles):
    """Static files the browser must revalidate on every load, so phones pick up page changes."""

    async def get_response(self, path: str, scope):
        """Serve the file with Cache-Control: no-cache."""
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def reply(result: Response) -> JSONResponse:
    """Turn a handler's (status, body) into a JSON response."""
    status, body = result
    return JSONResponse(body, status_code=status)


async def json_body(request: Request) -> dict[str, Any] | None:
    """The request's JSON object, {} if empty, or None if it isn't valid JSON."""
    raw = await request.body()
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def recent_uploads(store: Store, limit: int = RECENT_UPLOADS) -> list[dict[str, Any]]:
    """The newest frames and clips on disk, newest first."""
    files = [f for kind in ("frames", "clips") for f in (store.media_dir / kind).glob("*/*") if f.is_file()]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    out = []
    for f in files[:limit]:
        key = f.relative_to(store.media_dir).as_posix()
        out.append({"key": key, "url": store.media_url(key), "bytes": f.stat().st_size,
                    "at": to_iso(datetime.fromtimestamp(f.stat().st_mtime, timezone.utc))})
    return out


def warm_up(store: Store) -> None:
    """Load each configured model once with a real request of every kind it serves (naming the registered cameras,
    if any), so the first watch order isn't slow; print one line per model, with a fix-it hint if it fails."""
    cameras = store.list_cameras()
    blank = cv2.imencode(".jpg", np.zeros((360, 640, 3), np.uint8))[1].tobytes()
    steps = [(config.ORDER_MODEL, "watch orders", lambda: orders.understand("keep an eye on my keys", cameras)),
             (config.VISION_MODEL, "grounding", lambda: vision.find_in_frame("keys", orders.labels(cameras[:1]), blank))]
    for model in dict.fromkeys(m for m, _, _ in steps):
        uses = [(name, call) for m, name, call in steps if m == model]
        start = time.perf_counter()
        try:
            models.check(model)
            for _, call in uses:
                call()
            print(f"Model:   {model} ready for {' and '.join(n for n, _ in uses)} "
                  f"({time.perf_counter() - start:.1f} s)", flush=True)
        except models.ModelUnavailable as exc:
            print(f"Model:   {model} NOT ready, watch orders will answer 503: {exc}", flush=True)


def build_app(store: Store | None = None, compile_order: sessions.Compiler | None = None,
              analyser: Analyser | None = None, api_key: str | None = None,
              pusher: push_module.Push | None = None, ingest: Ingest | None = None,
              faces: Faces | None = None) -> auth.KeyGate:
    """Build the dev server app over the given store (default: backend/.data), watch-order compiler and
    analyser (uploads are analysed only when one is given), and face client for people's photos (default: the
    configured one, none when face ID is off). Every /api route except pairing requires api_key (default:
    MEANWHILE_KEY; an empty key is refused) or a paired device's token, in the header or the cookie; /media files
    need the same or a signed link."""
    store = store or get_store()
    faces = faces or get_faces()
    api_key = config.API_KEY if api_key is None else api_key
    grounding = ingest.detector if ingest and ingest.tracking == "detector" else None
    compile_order = compile_order or sessions.make_compiler(store, grounding)
    access = auth.Access(store, api_key)
    pair_limit = pairing.RateLimit(config.PAIR_TRIES, config.PAIR_TRIES_WINDOW_S)
    failures = auth.FailedAuth(config.AUTH_FAIL_TRIES, config.AUTH_FAIL_WINDOW_S)

    async def with_body(request: Request, handler) -> JSONResponse:
        """Parse the JSON body and call handler(body), or answer 400."""
        body = await json_body(request)
        if body is None:
            return reply(error(400, "bad_json", "The request body must be a JSON object."))
        return reply(handler(body))

    async def list_cameras(request: Request) -> JSONResponse:
        """GET /api/cameras."""
        return reply(cameras.list_cameras(store))

    async def register_camera(request: Request) -> JSONResponse:
        """POST /api/cameras."""
        return await with_body(request, lambda body: cameras.register(store, body))

    async def camera_heartbeat(request: Request) -> JSONResponse:
        """POST /api/cameras/{camera_id}/heartbeat."""
        camera_id = request.path_params["camera_id"]
        return await with_body(request, lambda body: cameras.heartbeat(store, camera_id, body))

    async def rename_camera(request: Request) -> JSONResponse:
        """PATCH /api/cameras/{camera_id}."""
        camera_id = request.path_params["camera_id"]
        return await with_body(request, lambda body: cameras.rename(store, camera_id, body))

    async def remove_camera(request: Request) -> JSONResponse:
        """DELETE /api/cameras/{camera_id}."""
        return reply(cameras.remove(store, request.path_params["camera_id"]))

    async def presign(request: Request) -> JSONResponse:
        """POST /api/uploads."""
        return await with_body(request, lambda body: uploads.presign(store, body))

    async def put_media(request: Request) -> JSONResponse:
        """PUT /api/media/{key}: an upload's bytes."""
        if int(request.headers.get("content-length") or 0) > uploads.MAX_UPLOAD_BYTES:
            return reply(error(413, "bad_size", "That upload is too big."))
        return reply(uploads.put_local(store, request.path_params["key"], await request.body(),
                                      analyser.submit if analyser else None))

    async def watch_orders(request: Request) -> JSONResponse:
        """POST /api/watch-orders (the model call runs off the event loop)."""
        body = await json_body(request)
        if body is None:
            return reply(error(400, "bad_json", "The request body must be a JSON object."))
        return reply(await run_in_threadpool(sessions.watch_orders, store, body, compile_order))

    async def latest_session(request: Request) -> JSONResponse:
        """GET /api/sessions/latest."""
        return reply(sessions.latest_session(store))

    async def list_sessions(request: Request) -> JSONResponse:
        """GET /api/sessions?limit=."""
        return reply(sessions.list_sessions(store, request.query_params.get("limit")))

    async def status(request: Request) -> JSONResponse:
        """GET /api/status."""
        info = {"enabled": pusher is not None, "devices": len(push_module.device_tokens(store)),
                "last": pusher.last if pusher else None}
        return reply(await run_in_threadpool(sessions.status, store, analyser.status() if analyser else None, info))

    async def register_device(request: Request) -> JSONResponse:
        """POST /api/devices."""
        return await with_body(request, lambda body: devices.register(store, body))

    async def session_events(request: Request) -> JSONResponse:
        """GET /api/sessions/{session_id}/events."""
        return reply(sessions.session_events(store, request.path_params["session_id"],
                                             request.query_params.get("since")))

    async def end_session(request: Request) -> JSONResponse:
        """POST /api/sessions/{session_id}/end."""
        return reply(sessions.end_session(store, request.path_params["session_id"]))

    async def caught_up(request: Request) -> JSONResponse:
        """POST /api/sessions/{session_id}/caught-up."""
        session_id = request.path_params["session_id"]
        return await with_body(request, lambda body: sessions.caught_up(store, session_id, body))

    async def latest_event(request: Request) -> JSONResponse:
        """GET /api/events/latest."""
        return reply(sessions.latest_event(store, dict(request.query_params)))

    async def rtc_ice_servers(request: Request) -> JSONResponse:
        """GET /api/rtc/ice-servers."""
        return reply(rtc.ice_servers())

    async def rtc_offer(request: Request) -> JSONResponse:
        """POST /api/rtc/{camera_id}/offers."""
        camera_id = request.path_params["camera_id"]
        return await with_body(request, lambda body: rtc.offer(store, camera_id, body))

    async def rtc_pending(request: Request) -> JSONResponse:
        """GET /api/rtc/{camera_id}/offers."""
        return reply(rtc.pending(store, request.path_params["camera_id"]))

    async def rtc_answer(request: Request) -> JSONResponse:
        """POST /api/rtc/offers/{offer_id}/answer."""
        offer_id = request.path_params["offer_id"]
        return await with_body(request, lambda body: rtc.answer(store, offer_id, body))

    async def rtc_answer_for(request: Request) -> JSONResponse:
        """GET /api/rtc/offers/{offer_id}."""
        return reply(rtc.answer_for(store, request.path_params["offer_id"]))

    async def look_now(request: Request) -> JSONResponse:
        """POST /api/look (faces and the model call run off the event loop)."""
        body = await json_body(request)
        if body is None:
            return reply(error(400, "bad_json", "The request body must be a JSON object."))
        return reply(await run_in_threadpool(look.look, store, body, faces, ingest.newest_frame if ingest else None))

    async def look_back(request: Request) -> JSONResponse:
        """POST /api/look-back (faces and the model call run off the event loop)."""
        body = await json_body(request)
        if body is None:
            return reply(error(400, "bad_json", "The request body must be a JSON object."))
        return reply(await run_in_threadpool(look.look_back, store, body, faces))

    async def state_of(request: Request) -> JSONResponse:
        """POST /api/state-of (a video fallback runs off the event loop)."""
        body = await json_body(request)
        if body is None:
            return reply(error(400, "bad_json", "The request body must be a JSON object."))
        return reply(await run_in_threadpool(look.state_of, store, body, faces,
                                             ingest.newest_frame if ingest else None))

    async def debug_uploads(request: Request) -> JSONResponse:
        """GET /api/debug/uploads: the newest frames and clips (local only)."""
        return JSONResponse({"uploads": recent_uploads(store)})

    async def debug_analysis(request: Request) -> JSONResponse:
        """GET /api/debug/analysis: the analyser's queue and latest outcomes, and the live detector feeds (local only)."""
        body = analyser.status() if analyser else {"queued": 0, "busy": None, "recent": [], "off": True}
        return JSONResponse({**body, "live": ingest.status() if ingest else None})

    async def list_people(request: Request) -> JSONResponse:
        """GET /api/people (expired unnamed people are deleted off the event loop first)."""
        return reply(await run_in_threadpool(people.list_people, store, faces))

    async def rename_person(request: Request) -> JSONResponse:
        """PATCH /api/people/{person_id} (moving faces runs off the event loop)."""
        body = await json_body(request)
        if body is None:
            return reply(error(400, "bad_json", "The request body must be a JSON object."))
        return reply(await run_in_threadpool(people.rename, store, request.path_params["person_id"], body, faces))

    async def merge_person(request: Request) -> JSONResponse:
        """POST /api/people/{person_id}/merge (moving faces runs off the event loop)."""
        body = await json_body(request)
        if body is None:
            return reply(error(400, "bad_json", "The request body must be a JSON object."))
        return reply(await run_in_threadpool(people.merge, store, request.path_params["person_id"], body, faces))

    async def remove_unnamed(request: Request) -> JSONResponse:
        """DELETE /api/people/unnamed (off the event loop)."""
        return reply(await run_in_threadpool(people.remove_unnamed, store, faces))

    async def add_person(request: Request) -> JSONResponse:
        """POST /api/people: JSON, or multipart form fields with `photo` files (indexing runs off the event loop)."""
        if not request.headers.get("content-type", "").startswith("multipart/form-data"):
            return await with_body(request, lambda body: people.add(store, body))
        if int(request.headers.get("content-length") or 0) > people.MAX_PHOTOS * people.MAX_PHOTO_BYTES:
            return reply(error(413, "bad_size", "Those photos are too big."))
        async with request.form(max_files=people.MAX_PHOTOS + 1) as form:
            body = {k: v for k, v in form.items() if isinstance(v, str)}
            photos = [await f.read() for f in form.getlist("photo") if not isinstance(f, str)]
        return reply(await run_in_threadpool(people.add, store, body, photos, faces))

    async def remove_person(request: Request) -> JSONResponse:
        """DELETE /api/people/{person_id} (with its faces, off the event loop)."""
        return reply(await run_in_threadpool(people.remove, store, request.path_params["person_id"], faces))

    async def create_pairing_code(request: Request) -> JSONResponse:
        """POST /api/pairing-codes (the link uses MEANWHILE_PUBLIC_URL, else this request's own address)."""
        base = config.PUBLIC_URL or str(request.base_url).rstrip("/")
        return reply(pairing.create_code(store, base, config.PAIR_CODE_TTL_S))

    async def pair_device(request: Request) -> JSONResponse:
        """POST /api/pair: a browser ("browser": true) gets its token only as an HttpOnly cookie, the app in the body."""
        body = await json_body(request)
        if body is None:
            return reply(error(400, "bad_json", "The request body must be a JSON object."))
        (status, result), token = pairing.pair(store, body, pair_limit, auth.client_address(request.scope),
                                               replaces=access.devices_of(request.scope["headers"]))
        if not (token and body.get("browser")):
            response = JSONResponse(result, status_code=status)
            if status == 429:
                response.headers["Retry-After"] = str(int(result["retry_after_s"]) + 1)
            return response
        response = JSONResponse({k: v for k, v in result.items() if k != "device_token"}, status_code=status)
        response.set_cookie(auth.COOKIE, token, max_age=int(config.PAIR_COOKIE_DAYS * 86400), path="/",
                            domain=config.PAIR_COOKIE_DOMAIN or None, secure=True, httponly=True, samesite="lax")
        return response

    async def paired_devices(request: Request) -> JSONResponse:
        """GET /api/paired-devices."""
        return reply(pairing.list_devices(store, access.devices_of(request.scope["headers"])))

    async def revoke_device(request: Request) -> JSONResponse:
        """DELETE /api/paired-devices/{device_id}."""
        return reply(pairing.revoke(store, request.path_params["device_id"]))

    async def auth_check(request: Request) -> PlainResponse:
        """GET /api/auth/check: 204 when the request may pass the gate (the gate answers 401 or 429 otherwise);
        Caddy's forward_auth asks this for every request to the box, naming the original in X-Forwarded-Uri."""
        return PlainResponse(status_code=204)

    async def pair_page(request: Request) -> FileResponse:
        """GET /pair?code=...: the browser pairing page (never cached; the code isn't sent on as a referrer)."""
        return FileResponse(WEB_DIR / "pair.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def camera_page(request: Request) -> FileResponse:
        """GET /camera: the camera page."""
        return FileResponse(WEB_DIR / "camera.html", headers={"Cache-Control": "no-cache"})

    app = Starlette(routes=[
        Route("/", lambda request: RedirectResponse("/debug.html")),
        Route("/camera", camera_page),
        Route("/pair", pair_page),
        Route("/api/auth/check", auth_check),
        Route("/api/pair", pair_device, methods=["POST"]),
        Route("/api/pairing-codes", create_pairing_code, methods=["POST"]),
        Route("/api/paired-devices", paired_devices),
        Route("/api/paired-devices/{device_id}", revoke_device, methods=["DELETE"]),
        Route("/api/cameras", list_cameras),
        Route("/api/cameras", register_camera, methods=["POST"]),
        Route("/api/cameras/{camera_id}/heartbeat", camera_heartbeat, methods=["POST"]),
        Route("/api/cameras/{camera_id}", rename_camera, methods=["PATCH"]),
        Route("/api/cameras/{camera_id}", remove_camera, methods=["DELETE"]),
        Route("/api/uploads", presign, methods=["POST"]),
        Route("/api/watch-orders", watch_orders, methods=["POST"]),
        Route("/api/sessions", list_sessions),
        Route("/api/sessions/latest", latest_session),
        Route("/api/sessions/{session_id}/events", session_events),
        Route("/api/sessions/{session_id}/end", end_session, methods=["POST"]),
        Route("/api/sessions/{session_id}/caught-up", caught_up, methods=["POST"]),
        Route("/api/status", status),
        Route("/api/devices", register_device, methods=["POST"]),
        Route("/api/events/latest", latest_event),
        Route("/api/look", look_now, methods=["POST"]),
        Route("/api/look-back", look_back, methods=["POST"]),
        Route("/api/state-of", state_of, methods=["POST"]),
        Route("/api/debug/uploads", debug_uploads),
        Route("/api/debug/analysis", debug_analysis),
        Route("/api/people", list_people),
        Route("/api/people", add_person, methods=["POST"]),
        Route("/api/people/unnamed", remove_unnamed, methods=["DELETE"]),
        Route("/api/people/{person_id}", remove_person, methods=["DELETE"]),
        Route("/api/people/{person_id}", rename_person, methods=["PATCH"]),
        Route("/api/people/{person_id}/merge", merge_person, methods=["POST"]),
        Route("/api/rtc/ice-servers", rtc_ice_servers),
        Route("/api/rtc/offers/{offer_id}", rtc_answer_for),
        Route("/api/rtc/offers/{offer_id}/answer", rtc_answer, methods=["POST"]),
        Route("/api/rtc/{camera_id}/offers", rtc_pending),
        Route("/api/rtc/{camera_id}/offers", rtc_offer, methods=["POST"]),
        Route("/api/media/{key:path}", put_media, methods=["PUT"]),
        Mount("/media", StaticFiles(directory=store.media_dir, check_dir=False)),
        Mount("/", FreshStaticFiles(directory=WEB_DIR)),
    ])
    return auth.KeyGate(app, access, failures)


def lan_ip() -> str:
    """This laptop's address on the local network (no packets are sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]


def ensure_cert(cert_dir: Path, ip: str) -> tuple[Path, Path]:
    """A self-signed certificate for localhost and `ip`, made once and remade when the IP changes."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cert_path, key_path = cert_dir / "devserver-cert.pem", cert_dir / "devserver-key.pem"
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if ipaddress.ip_address(ip) in names.get_values_for_type(x509.IPAddress) \
                and cert.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=1):
            return cert_path, key_path
    cert_dir.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Meanwhile dev server")])
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start).not_valid_after(start + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            x509.IPAddress(ipaddress.ip_address(ip)),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def quiet_resets(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    """Log asyncio errors as usual, except a browser closing its connection (Windows reports it as an error)."""
    if isinstance(context.get("exception"), ConnectionResetError):
        return
    loop.default_exception_handler(context)


async def serve_all(servers: list[uvicorn.Server], ingest: Ingest | None = None) -> None:
    """Run several servers (and the live detector feeds) until any server stops (Ctrl+C), then stop the rest."""
    asyncio.get_running_loop().set_exception_handler(quiet_resets)
    tasks = [asyncio.create_task(s.serve()) for s in servers]
    feeds = asyncio.create_task(ingest.run()) if ingest else None
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for s in servers:
        s.should_exit = True
    if feeds:
        feeds.cancel()
    await asyncio.gather(*tasks)


def main() -> None:
    """Command line: serve the core locally."""
    ap = argparse.ArgumentParser(description="Meanwhile core dev server.")
    ap.add_argument("--port", type=int, default=config.PORT, help="HTTP port (MEANWHILE_PORT)")
    ap.add_argument("--lan", action="store_true", help="serve on the local network: HTTP on all interfaces (for the phone app) and HTTPS on --lan-port (for phone cameras)")
    ap.add_argument("--lan-port", type=int, default=config.LAN_PORT, help="HTTPS port for phone cameras (MEANWHILE_LAN_PORT)")
    ap.add_argument("--no-warm-up", action="store_true", help="don't load the model at startup")
    args = ap.parse_args()

    store = get_store()
    analyser = Analyser(store).start()
    pusher = push_module.from_config(store, config.FCM_SERVICE_ACCOUNT)
    if pusher:
        store.event_listeners.append(pusher.notify)
    ingest = Ingest(store, analyser.lock)
    if ingest.tracking == "detector":
        analyser.gate = ingest.heads_up_gate
    app = build_app(store, analyser=analyser, pusher=pusher, ingest=ingest)
    configs = [uvicorn.Config(app, host="0.0.0.0" if args.lan else "127.0.0.1", port=args.port, log_level="warning")]
    print(f"Laptop:  http://localhost:{args.port}/debug.html  |  camera: http://localhost:{args.port}/camera")
    print(f"Push:    {'on (FCM)' if pusher else 'off (no MEANWHILE_FCM_SERVICE_ACCOUNT file)'}  |  API key: required")
    print(f"Tracking: {config.TRACKING}" + (f" ({config.DETECTOR_MODEL}, every {config.DETECTOR_STRIDE} live frames; "
                                            f"model checks {'on' if config.CHECKS else 'off'})"
                                            if config.TRACKING == "detector" else " (a vision-model check per frame)"))
    if args.lan:
        ip = lan_ip()
        print(f"Phone app: http://{ip}:{args.port}  (the core API on the home network)")
        cert, key = ensure_cert(store.data_dir / "certs", ip)
        configs.append(uvicorn.Config(app, host="0.0.0.0", port=args.lan_port, log_level="warning",
                                      ssl_certfile=str(cert), ssl_keyfile=str(key)))
        print(f"Phones:  https://{ip}:{args.lan_port}/camera  (accept the certificate warning once)")
    print(f"Faces:   {f'on (Rekognition collection {config.FACE_COLLECTION})' if config.FACES else 'off (clothes notes)'}"
          + (f"; unnamed people {'kept' if config.FACE_KEEP_UNNAMED else 'not kept'}, deleted after "
             f"{config.FACE_UNNAMED_DAYS:g} days unseen" if config.FACES else ""))
    print(f"Data:    {store.data_dir}")
    if not args.no_warm_up:
        threading.Thread(target=warm_up, args=(store,), daemon=True).start()
        if config.TRACKING == "detector":
            threading.Thread(target=ingest.detector.warm_up, daemon=True).start()
    asyncio.run(serve_all([uvicorn.Server(c) for c in configs], ingest))


if __name__ == "__main__":
    main()
