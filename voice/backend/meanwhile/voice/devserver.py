"""The home-screen devserver: one process serving /mcp, the /voice API (state, the simulated Alexa's /voice/ask, and
device pairing passed on to the core), /media (proxied to the core) and web/.
Run: python -m meanwhile.voice.devserver [--port N] [--lan] (port from MEANWHILE_VOICE_PORT).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from meanwhile.voice import config, mcp_server, wording
from meanwhile.voice.alexa_sim import AlexaSim, BrainUnavailable
from meanwhile.voice.core_client import CoreClient, CoreError
from meanwhile.voice.screen import Screen

log = logging.getLogger("meanwhile.voice")

WEB_DIR = Path(__file__).resolve().parents[3] / "web"
MEDIA_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges", "last-modified", "etag")
QUIET_PATHS = ("/voice/state", "/media/")


def voice_state(core: CoreClient, screen: Screen) -> dict:
    """Everything the home screen shows: the latest session, its card text, events, cameras and display."""
    session, active = core.latest_session()
    events = core.events(session["session_id"]) if session else []
    cameras = core.cameras()
    labels = {c["camera_id"]: c.get("label") or c["camera_id"] for c in cameras}
    return {
        "active": active,
        "session": session,
        "card": wording.screen_card(session, active, events, labels),
        "events": [
            {
                "event_id": e["event_id"],
                "ts": e["ts"],
                "time": wording.spoken_time(wording.happened_at(e)),
                "camera": labels.get(e.get("camera_id", ""), e.get("camera_id", "")),
                "summary": e.get("summary", ""),
                "alert": e.get("alert", "none"),
                "clip_url": e.get("clip_url"),
                "banner": wording.banner(e),
            }
            for e in events
        ],
        "cameras": [{"camera_id": c["camera_id"], "label": labels[c["camera_id"]], "online": bool(c.get("online"))}
                    for c in cameras],
        "display": screen.display,
    }


class _FreshStatic(StaticFiles):
    """Static files that browsers revalidate on every load, so a reload always gets the current page."""
    def file_response(self, *args, **kwargs) -> Response:
        """The file, marked no-cache (a cheap 304 when unchanged)."""
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


class _QuietPolls(logging.Filter):
    """Drop access-log lines for the page's 1 s poll and clip requests."""
    def filter(self, record: logging.LogRecord) -> bool:
        """Keep the record unless it is for a quiet path."""
        return not any(p in record.getMessage() for p in QUIET_PATHS)


def http_app(
    core: CoreClient | None = None,
    screen: Screen | None = None,
    web_dir: Path = WEB_DIR,
    alexa: AlexaSim | None = None,
    extra_hosts: list[str] | None = None,
    self_url: str | None = None,
) -> Starlette:
    """The MCP app with /voice/state, /voice/ask, /media and the web page added, sharing one core client and screen.
    extra_hosts are Host names besides localhost that /mcp accepts (the LAN address with --lan). self_url is this
    server's own local address for the simulated Alexa's MCP calls; without it, the request's base URL is used."""
    core = core or CoreClient()
    screen = screen or Screen()
    alexa = alexa or AlexaSim()
    media_http = httpx.AsyncClient(timeout=30)

    def state(request: Request) -> JSONResponse:
        """GET /voice/state: the home screen's poll."""
        try:
            return JSONResponse(voice_state(core, screen))
        except (httpx.HTTPError, CoreError) as exc:
            log.warning("voice/state: core unreachable: %s", exc)
            return JSONResponse({"error": "core_unreachable", "message": "I can't reach the Meanwhile core."},
                                status_code=503)

    async def ask(request: Request) -> JSONResponse:
        """POST /voice/ask {"text"}: the simulated Alexa answers one sentence through this server's own /mcp."""
        text = ((await request.json()).get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "empty", "message": "Nothing was heard."}, status_code=400)
        try:
            base = self_url or str(request.base_url).rstrip("/")
            return JSONResponse(await alexa.ask(text, f"{base}/mcp"))
        except BrainUnavailable as exc:
            log.warning("voice/ask: %s", exc)
            return JSONResponse({"error": "brain_unavailable", "message": str(exc)}, status_code=503)

    def pairing(request: Request) -> JSONResponse:
        """POST /voice/pairing-codes, GET /voice/paired-devices, DELETE /voice/paired-devices/{id}: the panel's
        "Pair a device" screen, passed on to the core's /api routes of the same names."""
        path = request.url.path.removeprefix("/voice/")
        try:
            status, body = core.pairing(request.method, path, request.headers.get("cookie"))
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("pairing: core unreachable: %s", exc)
            return JSONResponse({"error": "core_unreachable", "message": "I can't reach the Meanwhile core."},
                                status_code=503)
        return JSONResponse(body, status_code=status)

    async def media(request: Request) -> Response:
        """GET /media/{path}: stream a clip or frame from the core, passing Range through for <video>."""
        headers = {"Range": request.headers["range"]} if "range" in request.headers else {}
        if key := core.http.headers.get("X-Meanwhile-Key"):
            headers["X-Meanwhile-Key"] = key
        url = f"{core.base_url}/media/{request.path_params['path']}"
        try:
            upstream = await media_http.send(media_http.build_request("GET", url, headers=headers), stream=True)
        except httpx.HTTPError as exc:
            log.warning("media: core unreachable: %s", exc)
            return Response(status_code=502)
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers={h: upstream.headers[h] for h in MEDIA_HEADERS if h in upstream.headers},
            background=BackgroundTask(upstream.aclose),
        )

    app = mcp_server.http_app(core, screen, extra_hosts)
    app.router.routes.extend([
        Route("/voice/state", state),
        Route("/voice/ask", ask, methods=["POST"]),
        Route("/voice/pairing-codes", pairing, methods=["POST"]),
        Route("/voice/paired-devices", pairing),
        Route("/voice/paired-devices/{device_id}", pairing, methods=["DELETE"]),
        Route("/media/{path:path}", media),
        Mount("/", _FreshStatic(directory=web_dir, html=True)),
    ])
    return app


def _warm_alexa(alexa: AlexaSim, base_url: str) -> None:
    """Once the server answers, warm the simulated Alexa's model in the background."""
    for _ in range(100):
        try:
            httpx.get(f"{base_url}/voice/state", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    try:
        asyncio.run(alexa.warm(f"{base_url}/mcp"))
        log.info("Alexa's model is ready")
    except Exception as exc:
        log.warning("Alexa's model isn't ready: %s", exc)


def lan_addresses() -> list[str]:
    """This machine's private IPv4 addresses (Wi-Fi or Ethernet), for phones on the same network."""
    import socket

    found = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    return sorted(a for a in found if a.startswith(("192.168.", "10.")) or
                  (a.startswith("172.") and 16 <= int(a.split(".")[1]) <= 31))


if __name__ == "__main__":
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="Meanwhile voice: MCP tools, home screen and the simulated Alexa+.")
    ap.add_argument("--port", type=int, default=config.PORT, help="port (MEANWHILE_VOICE_PORT)")
    ap.add_argument("--lan", action="store_true", help="listen on every interface so phones on the Wi-Fi can connect")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("uvicorn.access").addFilter(_QuietPolls())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    alexa_sim = AlexaSim()
    base_url = f"http://127.0.0.1:{args.port}"
    threading.Thread(target=_warm_alexa, args=(alexa_sim, base_url), daemon=True).start()
    hosts = lan_addresses() if args.lan else []
    if hosts:
        print(f"Phones: http://{hosts[0]}:{args.port}")
    uvicorn.run(http_app(alexa=alexa_sim, extra_hosts=hosts, self_url=base_url), host="0.0.0.0" if args.lan else "127.0.0.1",
                port=args.port)
