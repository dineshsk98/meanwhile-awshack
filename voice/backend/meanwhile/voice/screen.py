"""What the home screen should show, and what the voice tools last talked about (in process memory)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone


def _now() -> str:
    """Current UTC time in the contract's ISO format."""
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class Screen:
    """Display state shared by the voice tools and the home screen."""
    def __init__(self) -> None:
        """Start with nothing on screen."""
        self._lock = threading.Lock()
        self.display: dict = {"cmd": "clear", "clips": [], "at": _now()}
        self.last_referenced: list[dict] = []

    def show(self, clips: list[dict]) -> None:
        """Show these clips (each clip file once, in order), and remember them as the last thing talked about."""
        seen: set[str] = set()
        clips = [c for c in clips if not (c.get("clip_url") in seen or seen.add(c.get("clip_url")))]
        with self._lock:
            self.last_referenced = clips
            if clips:
                self.display = {"cmd": "show", "clips": clips, "at": _now()}

    def show_frame(self, frame: dict) -> None:
        """Show one still frame (a dict with frame_url instead of clip_url), without making it the last thing talked
        about, so "show me" still means the last clips."""
        with self._lock:
            self.display = {"cmd": "show", "clips": [frame], "at": _now()}

    def remember(self, clips: list[dict]) -> None:
        """Remember clips as the last thing talked about, without showing them."""
        with self._lock:
            self.last_referenced = clips

    def clear(self) -> None:
        """Clear the screen."""
        with self._lock:
            self.display = {"cmd": "clear", "clips": [], "at": _now()}
