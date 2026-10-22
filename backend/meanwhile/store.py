"""The core's store: cameras, people, sessions, events, things' states, pairing codes and paired devices in a local
SQLite file."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from meanwhile import config
from meanwhile.contract import Camera, Event, OrderResult, Person, Session, ThingState
from meanwhile.media_links import MediaLinks, secret_from

DEFAULT_DATA_DIR = config.DATA_DIR

OFFLINE_AFTER_S = 10


def now() -> str:
    """Current UTC time as ISO 8601 with milliseconds and Z."""
    return to_iso(datetime.now(timezone.utc))


def to_iso(dt: datetime) -> str:
    """Format a datetime as ISO 8601 UTC with milliseconds and Z."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse(ts: str) -> datetime:
    """Parse a timestamp written by to_iso."""
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def seconds_since(ts: str, at: str | None = None) -> float:
    """Seconds from ts to at (or to now)."""
    return ((parse(at) if at else datetime.now(timezone.utc)) - parse(ts)).total_seconds()


def new_id(prefix: str) -> str:
    """A new id like ses_1a2b3c4d5e6f."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class StoreError(Exception):
    """A store rule was broken, e.g. a second active session."""
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras      (camera_id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS people       (person_id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions     (session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                                         active INTEGER NOT NULL, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events       (session_id TEXT NOT NULL, sk TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE,
                                         doc TEXT NOT NULL, PRIMARY KEY (session_id, sk));
CREATE TABLE IF NOT EXISTS camera_state (camera_id TEXT NOT NULL, key TEXT NOT NULL, doc TEXT NOT NULL,
                                         PRIMARY KEY (camera_id, key));
CREATE TABLE IF NOT EXISTS meta         (key TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rtc_offers   (offer_id TEXT PRIMARY KEY, camera_id TEXT NOT NULL, created TEXT NOT NULL,
                                         doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pairing_codes  (code_hash TEXT PRIMARY KEY, expires TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paired_devices (device_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                                           doc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sessions_by_start ON sessions (started_at);
"""


class Store:

    """SQLite-backed store; methods take and return plain dicts shaped as in contract.py."""
    def __init__(self, data_dir: str | Path | None = None):
        """Open (and create if needed) the database and media folder under data_dir."""
        self.data_dir = Path(data_dir or config.DATA_DIR)
        self.media_dir = self.data_dir / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "meanwhile.db"
        self.event_listeners: list[Callable[[Event], None]] = []
        self.links = MediaLinks(secret_from(config.API_KEY), config.MEDIA_URL_TTL_S)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """A connection that commits on success and rolls back on error."""
        db = sqlite3.connect(self.db_path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()


    def put_camera(self, camera: Camera) -> Camera:
        """Create or update a camera, merging the given fields into the stored ones."""
        merged = {**(self.get_camera(camera["camera_id"]) or {}), **camera}
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO cameras VALUES (?, ?)", (merged["camera_id"], json.dumps(merged)))
        return merged

    def heartbeat(self, camera_id: str, **fields: Any) -> Camera:
        """Mark a camera as seen now, with any extra fields."""
        return self.put_camera({"camera_id": camera_id, **fields, "last_seen": now()})

    def get_camera(self, camera_id: str) -> Camera | None:
        """One camera, or None."""
        return self._one("SELECT doc FROM cameras WHERE camera_id = ?", (camera_id,))

    def list_cameras(self) -> list[Camera]:
        """All cameras not removed by the user, by id."""
        return [c for c in self._all("SELECT doc FROM cameras ORDER BY camera_id") if not c.get("removed")]

    def camera_by_label(self, label: str) -> Camera | None:
        """The camera (removed ones included) whose label matches, ignoring case and outer spaces, or None."""
        wanted = label.strip().lower()
        return next((c for c in self._all("SELECT doc FROM cameras ORDER BY camera_id")
                     if str(c.get("label", "")).strip().lower() == wanted), None)

    @staticmethod
    def is_online(camera: Camera, at: str | None = None) -> bool:
        """Whether the camera was seen within the last OFFLINE_AFTER_S seconds."""
        seen = camera.get("last_seen")
        return bool(seen) and seconds_since(seen, at) <= OFFLINE_AFTER_S


    def put_person(self, person: Person) -> Person:
        """Create or update a registered person, merging fields."""
        merged = {**(self.get_person(person["person_id"]) or {}), **person}
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO people VALUES (?, ?)", (merged["person_id"], json.dumps(merged)))
        return merged

    def get_person(self, person_id: str) -> Person | None:
        """One person, or None."""
        return self._one("SELECT doc FROM people WHERE person_id = ?", (person_id,))

    def list_people(self) -> list[Person]:
        """All registered people, by id."""
        return self._all("SELECT doc FROM people ORDER BY person_id")

    def remove_person(self, person_id: str) -> None:
        """Delete a registered person."""
        with self._db() as db:
            db.execute("DELETE FROM people WHERE person_id = ?", (person_id,))


    def start_session(self, instruction: str, order: OrderResult) -> Session:
        """Start the one active away session; raises StoreError if one is active."""
        if self.active_session():
            raise StoreError("a session is already active")
        session: Session = {
            "session_id": new_id("ses"),
            "started_at": now(),
            "ended_at": None,
            "instruction": instruction,
            "watch_order": order["watch_order"],
            "display_command": None,
        }
        for field in ("focus_object", "focus_camera_id", "focus_box"):
            if order.get(field):
                session[field] = order[field]
        if session["watch_order"].get("kind") == "object":
            session["state"], session["state_since"] = "on_surface", session["started_at"]
        with self._db() as db:
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, 1, ?)",
                (session["session_id"], session["started_at"], json.dumps(session)),
            )
        return session

    def get_session(self, session_id: str) -> Session | None:
        """One session, or None."""
        return self._one("SELECT doc FROM sessions WHERE session_id = ?", (session_id,))

    def active_session(self) -> Session | None:
        """The active session, or None."""
        return self._one("SELECT doc FROM sessions WHERE active = 1 ORDER BY started_at DESC LIMIT 1")

    def latest_session(self) -> Session | None:
        """The active session, or else the most recently started one."""
        return self._one("SELECT doc FROM sessions ORDER BY active DESC, started_at DESC LIMIT 1")

    def list_sessions(self, limit: int = 20) -> list[Session]:
        """The newest sessions first."""
        return self._all("SELECT doc FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,))

    def update_session(self, session_id: str, **fields: Any) -> Session:
        """Merge fields into a session; setting ended_at makes it inactive."""
        session = self.get_session(session_id)
        if session is None:
            raise StoreError(f"no session {session_id}")
        session.update(fields)
        with self._db() as db:
            db.execute(
                "UPDATE sessions SET active = ?, doc = ? WHERE session_id = ?",
                (int(session.get("ended_at") is None), json.dumps(session), session_id),
            )
        return session

    def set_session_field(self, session_id: str, field: str, value: Any) -> bool:
        """Set one field of an active session in a single statement, leaving every other field as stored; returns
        False if the session isn't active."""
        with self._db() as db:
            changed = db.execute("UPDATE sessions SET doc = json_set(doc, '$.' || ?, json(?)) "
                                 "WHERE session_id = ? AND active = 1", (field, json.dumps(value), session_id))
            return changed.rowcount == 1

    def end_session(self, session_id: str) -> Session:
        """End a session now."""
        return self.update_session(session_id, ended_at=now())


    def add_event(self, event: Event) -> Event:
        """Append an event to its session (assigns event_id, and ts if missing), then tell the event listeners."""
        if not event.get("session_id"):
            raise StoreError("event needs a session_id")
        stored: Event = {"alert": "none", **event, "event_id": new_id("evt"), "ts": event.get("ts") or now()}
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            (seq,) = db.execute("SELECT COUNT(*) FROM events WHERE session_id = ?", (stored["session_id"],)).fetchone()
            db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?)",
                (stored["session_id"], f'{stored["ts"]}#{seq:08d}#{stored["event_id"]}',
                 stored["event_id"], json.dumps(stored)),
            )
        for listener in self.event_listeners:
            try:
                listener(stored)
            except Exception as exc:
                print(f"Event listener failed: {exc!r}", flush=True)
        return stored

    def list_events(self, session_id: str, since: str | None = None) -> list[Event]:
        """A session's events in time order, only those after `since` if given."""
        if since:
            return self._all(
                "SELECT doc FROM events WHERE session_id = ? AND sk > ? ORDER BY sk", (session_id, f"{since}#~")
            )
        return self._all("SELECT doc FROM events WHERE session_id = ? ORDER BY sk", (session_id,))

    def update_event(self, event_id: str, **fields: Any) -> Event:
        """Merge fields into an event (its id, session and time stay)."""
        event = self.get_event(event_id)
        if event is None:
            raise StoreError(f"no event {event_id}")
        event.update({k: v for k, v in fields.items() if k not in ("event_id", "session_id", "ts")})
        with self._db() as db:
            db.execute("UPDATE events SET doc = ? WHERE event_id = ?", (json.dumps(event), event_id))
        return event

    def all_events_newest_first(self) -> list[Event]:
        """Every session's events, newest first."""
        return self._all("SELECT doc FROM events ORDER BY sk DESC")

    def get_event(self, event_id: str) -> Event | None:
        """One event by id, or None."""
        return self._one("SELECT doc FROM events WHERE event_id = ?", (event_id,))

    def set_state(self, state: ThingState) -> ThingState:
        """Record the latest state of a thing as one camera saw it (one row per camera and thing)."""
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO camera_state VALUES (?, ?, ?)",
                       (state["camera_id"], state["key"], json.dumps(state)))
        return state

    def list_states(self) -> list[ThingState]:
        """Every recorded state of every thing, newest first."""
        return sorted(self._all("SELECT doc FROM camera_state"), key=lambda s: s.get("at", ""), reverse=True)

    def add_rtc_offer(self, camera_id: str, sdp: str, purpose: str = "live") -> dict[str, Any]:
        """Store a viewer's WebRTC offer for a camera (purpose: live for a person watching, ingest for the
        detector); returns it with its offer_id."""
        offer = {"offer_id": new_id("rtc"), "camera_id": camera_id, "created": now(), "offer": sdp, "answer": None,
                 "purpose": purpose}
        with self._db() as db:
            db.execute("INSERT INTO rtc_offers VALUES (?, ?, ?, ?)",
                       (offer["offer_id"], camera_id, offer["created"], json.dumps(offer)))
        return offer

    def get_rtc_offer(self, offer_id: str) -> dict[str, Any] | None:
        """One WebRTC offer (with its answer once the camera has replied), or None."""
        return self._one("SELECT doc FROM rtc_offers WHERE offer_id = ?", (offer_id,))

    def pending_rtc_offers(self, camera_id: str, since: str) -> list[dict[str, Any]]:
        """A camera's unanswered offers created after `since`, oldest first."""
        offers = self._all("SELECT doc FROM rtc_offers WHERE camera_id = ? AND created > ? ORDER BY created",
                           (camera_id, since))
        return [o for o in offers if o["answer"] is None]

    def answer_rtc_offer(self, offer_id: str, sdp: str) -> dict[str, Any]:
        """Attach the camera's answer to an offer; raises StoreError if it doesn't exist or was answered."""
        offer = self.get_rtc_offer(offer_id)
        if offer is None or offer["answer"] is not None:
            raise StoreError(f"no open offer {offer_id}")
        offer["answer"] = sdp
        with self._db() as db:
            db.execute("UPDATE rtc_offers SET doc = ? WHERE offer_id = ?", (json.dumps(offer), offer_id))
        return offer

    def drop_rtc_offers_before(self, before: str) -> None:
        """Forget offers created before `before`."""
        with self._db() as db:
            db.execute("DELETE FROM rtc_offers WHERE created < ?", (before,))

    def add_pairing_code(self, code_hash: str, expires: str) -> None:
        """Store a one-time pairing code (only its hash) that works until `expires`."""
        with self._db() as db:
            db.execute("INSERT INTO pairing_codes VALUES (?, ?)", (code_hash, expires))

    def use_pairing_code(self, code_hash: str, at: str) -> bool:
        """Spend a pairing code: True (and it is gone) if it existed and hadn't expired at `at`; expired codes are
        dropped too."""
        with self._db() as db:
            used = db.execute("DELETE FROM pairing_codes WHERE code_hash = ? AND expires > ?", (code_hash, at)).rowcount
            db.execute("DELETE FROM pairing_codes WHERE expires <= ?", (at,))
        return used == 1

    def add_paired_device(self, device: dict[str, Any], token_hash: str) -> dict[str, Any]:
        """Store a paired device with the hash of its token."""
        with self._db() as db:
            db.execute("INSERT INTO paired_devices VALUES (?, ?, ?)",
                       (device["device_id"], token_hash, json.dumps(device)))
        return device

    def paired_device_by_token(self, token_hash: str) -> dict[str, Any] | None:
        """The paired device whose token has this hash, or None."""
        return self._one("SELECT doc FROM paired_devices WHERE token_hash = ?", (token_hash,))

    def list_paired_devices(self) -> list[dict[str, Any]]:
        """Every paired device, oldest first."""
        return sorted(self._all("SELECT doc FROM paired_devices"), key=lambda d: d["created"])

    def touch_paired_device(self, device_id: str, at: str) -> None:
        """Record when a paired device was last seen."""
        with self._db() as db:
            db.execute("UPDATE paired_devices SET doc = json_set(doc, '$.last_seen', ?) WHERE device_id = ?",
                       (at, device_id))

    def remove_paired_device(self, device_id: str) -> bool:
        """Revoke a paired device; False if there was none."""
        with self._db() as db:
            return db.execute("DELETE FROM paired_devices WHERE device_id = ?", (device_id,)).rowcount == 1

    def put_meta(self, key: str, value: Any) -> None:
        """Store a small key/value note."""
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))

    def get_meta(self, key: str, default: Any = None) -> Any:
        """Read a key/value note, or the default."""
        found = self._one("SELECT doc FROM meta WHERE key = ?", (key,))
        return default if found is None else found


    def media_url(self, key: str | None) -> str | None:
        """The signed, expiring URL a browser or the app can load for a media key, without the key or a token."""
        return self.links.sign(f"/media/{key}") if key else None

    def upload_url(self, key: str) -> str:
        """The URL a browser PUTs a new media object's bytes to."""
        return f"/api/media/{key}"

    def media_path(self, key: str) -> Path:
        """Where a media key lives on disk; raises StoreError if it would escape the media folder."""
        path = (self.media_dir / key).resolve()
        if not path.is_relative_to(self.media_dir.resolve()):
            raise StoreError(f"bad media key {key}")
        return path

    def write_media(self, key: str, data: bytes) -> None:
        """Save a media object's bytes."""
        path = self.media_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def delete_media(self, key: str) -> None:
        """Remove a media object if it exists."""
        self.media_path(key).unlink(missing_ok=True)


    def _one(self, sql: str, args: tuple = ()) -> Any:
        """First row of a query as a dict, or None."""
        with self._db() as db:
            row = db.execute(sql, args).fetchone()
        return json.loads(row[0]) if row else None

    def _all(self, sql: str, args: tuple = ()) -> list[Any]:
        """All rows of a query as dicts."""
        with self._db() as db:
            return [json.loads(r[0]) for r in db.execute(sql, args).fetchall()]


_default: Store | None = None


def get_store() -> Store:
    """The process-wide store."""
    global _default
    if _default is None:
        _default = Store()
    return _default
