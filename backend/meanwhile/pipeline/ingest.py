"""Live frames for the detector: one WebRTC viewer per camera the detector follows, opened through the core's own
offer relay while a session needs it, closed when the camera goes offline or the session ends, and reopened when
the stream stalls (for example after a camera page reload). Every stride-th frame goes to analyze.follow(), and the
followers' newest frames serve the vision-model checks (new tracks, and the heads-up gate the analyser's timers use).
aiortc loads on first use, so the rest of the core runs without it.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

import cv2

from meanwhile import config
from meanwhile.api import rtc
from meanwhile.contract import Session
from meanwhile.pipeline import analyze
from meanwhile.pipeline.checks import Checks
from meanwhile.pipeline.detector import PERSON, Detector, phrase_for
from meanwhile.store import Store, to_iso

CHECK_S = 1.0
ANSWER_POLL_S = 0.2
ANSWER_WAIT_S = 20
FIRST_FRAME_WAIT_S = 10
STALL_S = 3.0
RETRY_S = 1.0


class CameraFeed:
    """One camera's viewer: keeps the newest frame and sends every stride-th one through the detector path,
    taking the newest whenever the detector is free, so it never falls behind live."""

    def __init__(self, ingest: "Ingest", camera_id: str):
        """A feed for one camera; run() connects."""
        self.ingest, self.camera_id = ingest, camera_id
        self.pc: Any = None
        self.latest: tuple[Any, float] | None = None
        self.arrived = asyncio.Event()
        self.received = self.used = self.processed = self.connections = 0
        self.last_frame_at = 0.0
        self.shape: tuple[int, int] | None = None
        self.outcome = "connecting"

    async def run(self) -> None:
        """Connect, receive until the stream ends or stalls, and reconnect, until cancelled."""
        while True:
            try:
                await self.connect()
                await self.watch()
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception as exc:
                self.outcome = f"connection failed: {exc!r}"
                self.ingest.log(f"Ingest:  {self.camera_id}: {self.outcome}")
            await self.close()
            await asyncio.sleep(RETRY_S)

    async def connect(self) -> None:
        """Offer to receive the camera's video (purpose ingest) using the configured STUN/TURN servers, wait for
        the page's answer, and start reading."""
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

        servers = [RTCIceServer(url, config.TURN_USERNAME or None, config.TURN_CREDENTIAL or None)
                   if url.startswith("turn") else RTCIceServer(url) for url in config.ICE_SERVERS]
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=servers))
        self.pc.addTransceiver("video", direction="recvonly")
        self.pc.on("track", lambda track: asyncio.ensure_future(self.read(track)))
        await self.pc.setLocalDescription(await self.pc.createOffer())
        status, body = rtc.offer(self.ingest.store, self.camera_id, {"sdp": self.pc.localDescription.sdp,
                                                                     "purpose": "ingest"})
        if status != 201:
            raise RuntimeError(body.get("message", "offer refused"))
        deadline = time.time() + ANSWER_WAIT_S
        while time.time() < deadline:
            status, found = rtc.answer_for(self.ingest.store, body["offer_id"])
            if status == 200 and found["sdp"]:
                await self.pc.setRemoteDescription(RTCSessionDescription(sdp=found["sdp"], type="answer"))
                self.connections += 1
                self.ingest.log(f"Ingest:  {self.camera_id}: connected")
                return
            await asyncio.sleep(ANSWER_POLL_S)
        raise TimeoutError("the camera page did not answer")

    async def watch(self) -> None:
        """Process frames until the connection fails, no first frame comes, or frames stop for STALL_S."""
        started, worker = time.time(), asyncio.ensure_future(self.process())
        try:
            while self.pc.connectionState not in ("failed", "closed"):
                await asyncio.sleep(0.2)
                if self.last_frame_at < started and time.time() - started > FIRST_FRAME_WAIT_S:
                    raise TimeoutError("no frames arrived")
                if self.last_frame_at >= started and time.time() - self.last_frame_at > STALL_S:
                    raise TimeoutError(f"no frames for {STALL_S:.0f} s")
        finally:
            worker.cancel()

    async def read(self, track: Any) -> None:
        """Keep the newest frame from the camera's video track until it ends."""
        while True:
            try:
                frame = await track.recv()
            except Exception:
                return
            self.latest, self.last_frame_at = (frame, time.time()), time.time()
            self.received += 1
            self.arrived.set()

    async def process(self) -> None:
        """Send the newest frame through the detector path once `stride` new frames have arrived."""
        while True:
            await self.arrived.wait()
            self.arrived.clear()
            if self.received - self.used < self.ingest.stride or self.latest is None:
                continue
            self.used = self.received
            frame, t = self.latest
            image = await asyncio.to_thread(frame.to_ndarray, format="bgr24")
            self.shape = image.shape[:2]
            at = to_iso(datetime.fromtimestamp(t, timezone.utc))
            self.outcome = await asyncio.to_thread(self.ingest.follow, self.camera_id, at, image)
            self.processed += 1
            if self.outcome not in ("no change",) and not self.outcome.startswith("skipped"):
                self.ingest.log(f"Detector: {self.camera_id} {at[11:23]}: {self.outcome}")

    async def close(self) -> None:
        """Close the viewer connection, if open."""
        if self.pc is not None:
            pc, self.pc = self.pc, None
            await pc.close()

    def status(self) -> dict[str, Any]:
        """Frames received and processed, frames where the object was seen, vision-model locate answers that found
        it out of those asked, the frame size, connections made and the latest outcome."""
        follower = self.ingest.followers.get(self.camera_id)
        seen = f"{follower.rules.sightings}/{follower.rules.frames}" if follower else None
        located = f"{follower.rules.located}/{follower.rules.locate_calls}" if follower else None
        return {"camera_id": self.camera_id, "received": self.received, "processed": self.processed, "seen": seen,
                "located": located, "trace": follower.rules.trace if follower else "",
                "shape": list(self.shape[::-1]) if self.shape else None, "connections": self.connections,
                "last_frame_s_ago": round(time.time() - self.last_frame_at, 1) if self.last_frame_at else None,
                "outcome": self.outcome}


class Ingest:
    """Keeps one CameraFeed running for each online camera the detector follows in the active session (with
    detector tracking), starting and stopping them as cameras and sessions come and go."""

    def __init__(self, store: Store, lock: threading.Lock, detector: Detector | None = None,
                 tracking: str | None = None, stride: int | None = None, checks: Checks | None = None,
                 log: Callable[[str], None] = lambda line: print(line, flush=True)):
        """Set up over the store and the analyser's lock; run() starts the work."""
        self.store, self.lock, self.log = store, lock, log
        self.detector = detector or Detector()
        self.checks = checks or Checks()
        self.tracking = tracking or config.TRACKING
        self.stride = stride or config.DETECTOR_STRIDE
        self.followers: dict[str, analyze.Follower] = {}
        self.feeds: dict[str, tuple[CameraFeed, asyncio.Task]] = {}
        self.prompted: str | None = None

    def wanted(self) -> set[str]:
        """The online cameras the detector should follow now."""
        session = self.store.active_session()
        if session is None:
            return set()
        return {c["camera_id"] for c in self.store.list_cameras() if self.store.is_online(c)
                and analyze.watches(session, c["camera_id"]) and analyze.followed(session, c["camera_id"], self.tracking)}

    def follow(self, camera_id: str, at: str, image: Any) -> str:
        """Run one frame through the detector path (in a worker thread)."""
        try:
            return analyze.follow(self.store, camera_id, at, image, self.followers, self.detector, self.lock,
                                  checks=self.checks)
        except Exception as exc:
            return f"failed: {exc!r}"

    def newest_frame(self, camera_id: str) -> tuple[bytes, str] | None:
        """The newest live frame a camera's feed received, as (JPEG, when it arrived); None without a feed or frame."""
        feed = self.feeds.get(camera_id)
        latest = feed[0].latest if feed else None
        if latest is None:
            return None
        frame, t = latest
        image = frame.to_ndarray(format="bgr24")
        return cv2.imencode(".jpg", image)[1].tobytes(), to_iso(datetime.fromtimestamp(t, timezone.utc))

    def heads_up_gate(self, session: Session) -> str:
        """The analyser's gate for a heads-up that the object left view: one check on the focus camera's newest
        frame (see Checks.heads_up)."""
        follower = self.followers.get(session.get("focus_camera_id") or "")
        verdict = self.checks.heads_up(session, follower)
        if verdict != "hold":
            self.log(f"Check:   heads-up {'dropped, still there' if verdict == 'there' else 'sent'}")
        return verdict

    def prompt_for_session(self) -> None:
        """Encode the active session's object phrase (and 'person') for the detector, once per session, so the
        first live frame doesn't wait for the text encoder."""
        session = self.store.active_session()
        if session is None or self.tracking != "detector" or session["session_id"] == self.prompted:
            return
        self.prompted = session["session_id"]
        try:
            self.detector.prepare([phrase_for(session["watch_order"].get("target", "object")), PERSON])
        except Exception as exc:
            self.log(f"Ingest:  prompting the detector failed: {exc!r}")

    async def reconcile(self) -> None:
        """Prompt the detector for a new session, start feeds for wanted cameras and stop the rest."""
        asyncio.ensure_future(asyncio.to_thread(self.prompt_for_session))
        wanted = await asyncio.to_thread(self.wanted)
        for camera_id in wanted - self.feeds.keys():
            feed = CameraFeed(self, camera_id)
            self.feeds[camera_id] = (feed, asyncio.ensure_future(feed.run()))
            self.log(f"Ingest:  {camera_id}: following")
        for camera_id in self.feeds.keys() - wanted:
            _, task = self.feeds.pop(camera_id)
            task.cancel()
            self.log(f"Ingest:  {camera_id}: stopped")

    async def run(self) -> None:
        """Reconcile every CHECK_S until cancelled, then stop every feed."""
        try:
            while True:
                try:
                    await self.reconcile()
                except Exception as exc:
                    self.log(f"Ingest:  check failed: {exc!r}")
                await asyncio.sleep(CHECK_S)
        finally:
            for _, task in self.feeds.values():
                task.cancel()

    def status(self) -> dict[str, Any]:
        """The tracking mode, each feed's status, the detector's call count and mean time, and the vision-model
        checks for the latest session."""
        ms = self.detector.ms[-50:]
        session = self.store.latest_session()
        return {"tracking": self.tracking, "feeds": [f.status() for f, _ in self.feeds.values()],
                "detector_calls": self.detector.calls, "visual_prompt": self.detector.look is not None,
                "detector_ms": round(sum(ms) / len(ms), 1) if ms else None,
                "checks": self.checks.status(session["session_id"] if session else None)}
