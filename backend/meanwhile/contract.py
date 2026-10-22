"""Shapes of the core's stored records: cameras, people, watch orders, sessions, events, camera states."""

from __future__ import annotations

from typing import Literal, Protocol, TypedDict

Kind = Literal["object", "notes", "monitor"]
Alert = Literal["heads_up", "verdict", "alert", "none"]
Source = Literal["video_model", "vision", "detector", "rekognition", "tracker"]


class WatchOrder(TypedDict, total=False):

    """What an instruction means: a full order, or only `clarify`."""
    kind: Kind
    target: str
    cameras: list[str] | str
    clip_question: str
    report: list[str]
    alert_when: list[str]
    summary_for_alexa: str
    clarify: str


class OrderResult(TypedDict, total=False):

    """What compiling an instruction returns."""
    watch_order: WatchOrder
    focus_object: str
    focus_camera_id: str
    focus_box: list[float]
    not_found: str


class OrderCompiler(Protocol):

    """Turns an instruction into a watch order, with grounding for objects."""
    def __call__(self, instruction: str, cameras: list["Camera"]) -> OrderResult:
        """Compile one instruction for the given cameras."""
        ...


class Camera(TypedDict, total=False):
    """A labelled browser camera."""
    camera_id: str
    label: str
    last_seen: str
    thumb_key: str
    focus_box: list[float]


class Person(TypedDict, total=False):
    """A registered person."""
    person_id: str
    name: str
    role: str
    rekognition_face_ids: list[str]
    photo_key: str
    note: str


class DisplayCommand(TypedDict):

    """What the home screen should show."""
    cmd: Literal["show", "clear"]
    event_ids: list[str]
    at: str


class Session(TypedDict, total=False):
    """One away session and its watch order."""
    session_id: str
    started_at: str
    ended_at: str | None
    instruction: str
    watch_order: WatchOrder
    focus_object: str
    focus_camera_id: str
    focus_box: list[float]
    ref_frame_key: str
    state: str
    state_actor: str
    state_since: str
    tracking_said: bool
    left_at: str | None
    in_view: bool
    missing_since: str | None
    votes: dict[str, int]
    episode: str | None
    focus_held: bool
    display_command: DisplayCommand | None


class Event(TypedDict, total=False):

    """One thing that happened, with the sentence Alexa reads back and its alert level."""
    session_id: str
    event_id: str
    ts: str
    happened_at: str
    camera_id: str
    action: str
    actor_id: str
    recipient_id: str
    summary: str
    clip_key: str
    frame_key: str
    source: Source
    model: str
    alert: Alert
    episode: str | None


class ThingState(TypedDict, total=False):
    """The latest state of a thing as one camera saw it: the thing in the user's words, its key (the words without
    my/the/our/a/an), the state, when and by whom, and where it came from (a watch's event, or a video check)."""
    camera_id: str
    key: str
    thing: str
    value: str
    at: str
    by: str
    event_id: str
    session_id: str
    clip_key: str | None
    source: str
