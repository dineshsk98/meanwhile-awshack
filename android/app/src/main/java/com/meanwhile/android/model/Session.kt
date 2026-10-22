// A watch session as the core reports it: the instruction, the object's state and its live box.
package com.meanwhile.android.model

import com.meanwhile.android.util.DateTimeUtils
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Where the watched object is, as the session's `state` says. */
@Serializable
enum class SessionObjectState(val label: String) {
    @SerialName("on_surface")
    ON_SURFACE("On surface"),

    @SerialName("with_person")
    WITH_PERSON("With person"),

    @SerialName("with_owner")
    WITH_OWNER("Back with you"),

    @SerialName("taken")
    TAKEN("Taken"),

    @SerialName("unknown")
    UNKNOWN("Unknown");

    companion object {
        /** The state for the core's `state` value; anything unknown is [UNKNOWN]. */
        fun fromString(value: String?): SessionObjectState {
            return when (value?.lowercase()) {
                "on_surface" -> ON_SURFACE
                "with_person" -> WITH_PERSON
                "with_owner" -> WITH_OWNER
                "taken" -> TAKEN
                else -> UNKNOWN
            }
        }
    }
}

/** The core's reading of the instruction; `summary_for_alexa` is the sentence it confirms with. */
@Serializable
data class WatchOrder(
    @SerialName("summary_for_alexa")
    val summaryForAlexa: String? = null,
    val instruction: String? = null
)

/** Where the watched object is in one camera's frame right now. */
@Serializable
data class LiveBox(
    @SerialName("camera_id")
    val cameraId: String = "",
    /** [x0, y0, x1, y1] as fractions of the frame. */
    val box: List<Float> = emptyList(),
    val held: Boolean = false,
    val missing: Boolean = false,
    val at: String? = null,
    /** What placed the box: "detector", "vision" or "step_away". */
    val source: String? = null
) {
    /** True when the box has its four numbers. */
    val isValid: Boolean
        get() = box.size == 4
}

/** One watch session from `GET /api/sessions/latest` or `GET /api/sessions`. */
@Serializable
data class Session(
    val id: String = "",
    @SerialName("session_id")
    val sessionId: String? = null,
    val instruction: String = "",
    @SerialName("watch_order")
    val watchOrder: WatchOrder? = null,
    val summary: String? = null,
    val state: String = "on_surface",
    @SerialName("state_actor")
    val stateActor: String? = null,
    val active: Boolean = true,
    @SerialName("started_at")
    val startedAt: String? = null,
    @SerialName("ended_at")
    val endedAt: String? = null,
    @SerialName("event_count")
    val eventCount: Int? = null,
    @SerialName("last_verdict")
    val lastVerdict: String? = null,
    @SerialName("focus_object")
    val focusObject: String? = null,
    @SerialName("focus_camera_id")
    val focusCameraId: String? = null,
    @SerialName("live_box")
    val liveBox: LiveBox? = null
) {
    /** [state] as a [SessionObjectState]. */
    val objectState: SessionObjectState
        get() = SessionObjectState.fromString(state)

    /** The sentence that says what is being watched. */
    val comprehensionSummary: String
        get() = watchOrder?.summaryForAlexa ?: summary ?: instruction

    /** When the session started, as local "HH:mm". */
    val formattedStartedAt: String?
        get() = startedAt?.let { DateTimeUtils.formatTime(it) }
}
