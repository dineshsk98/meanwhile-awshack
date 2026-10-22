// An event the core logged in a session (a heads-up, verdict, alert or note) and its alert level.
package com.meanwhile.android.model

import com.meanwhile.android.util.DateTimeUtils
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** How loudly an event is reported: a heads-up, a verdict, an alert, or a quiet note. */
@Serializable
enum class AlertType(val value: String) {
    @SerialName("heads_up")
    HEADS_UP("heads_up"),

    @SerialName("verdict")
    VERDICT("verdict"),

    @SerialName("alert")
    ALERT("alert"),

    @SerialName("none")
    NONE("none");

    companion object {
        /** The alert type for the core's `alert` value; anything unknown is [NONE]. */
        fun fromString(str: String?): AlertType {
            return when (str?.lowercase()) {
                "heads_up" -> HEADS_UP
                "verdict" -> VERDICT
                "alert" -> ALERT
                else -> NONE
            }
        }
    }
}

/** One event from `GET /api/sessions/{id}/events`; clip and frame URLs are made absolute by the repository. */
@Serializable
data class Event(
    val id: String = "",
    @SerialName("event_id")
    val eventId: String? = null,
    @SerialName("session_id")
    val sessionId: String? = null,
    val summary: String = "",
    val alert: String = "none",
    @SerialName("actor_id")
    val actorId: String? = null,
    @SerialName("actor_name")
    val actorName: String? = null,
    @SerialName("actor_role")
    val actorRole: String? = null,
    @SerialName("camera_id")
    val cameraId: String = "",
    @SerialName("happened_at")
    val happenedAt: String? = null,
    val ts: String? = null,
    @SerialName("clip_url")
    val clipUrl: String? = null,
    @SerialName("frame_url")
    val frameUrl: String? = null,
    val action: String? = null,
    @SerialName("verdict_outcome")
    val verdictOutcome: String? = null,
    @SerialName("actor_unnamed")
    val actorUnnamed: Boolean = false
) {
    /** [alert] as an [AlertType]. */
    val alertType: AlertType
        get() = AlertType.fromString(alert)

    /** Who to name on the card: "You" for the owner, else the actor's name. */
    val displayActorName: String
        get() = when {
            actorRole?.lowercase() == "owner" -> "You"
            actorUnnamed -> actorName ?: "Unnamed person"
            else -> actorName ?: "Home"
        }

    /** True when the owner did it. */
    val isOwner: Boolean
        get() = actorRole?.lowercase() == "owner"

    /** When it happened, as local "HH:mm". */
    val formattedTime: String
        get() = DateTimeUtils.formatTime(happenedAt ?: ts ?: "")
}
