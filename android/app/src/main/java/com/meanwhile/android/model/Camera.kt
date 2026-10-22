// A camera as the core lists it: id, label, whether it is online, when it was last seen and its latest snapshot.
package com.meanwhile.android.model

import com.meanwhile.android.util.DateTimeUtils
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** One camera from `GET /api/cameras`. */
@Serializable
data class Camera(
    val id: String = "",
    @SerialName("camera_id")
    val cameraId: String? = null,
    val label: String = "",
    val online: Boolean = false,
    @SerialName("last_seen")
    val lastSeen: String? = null,
    @SerialName("thumb_url")
    val thumbUrl: String? = null
) {
    /** `last_seen` as local "HH:mm", or null when it is missing or unreadable. */
    val lastSeenTime: String?
        get() = lastSeen?.let { DateTimeUtils.formatTime(it) }?.takeIf { it.isNotBlank() }
}
