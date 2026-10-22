// Turns the core's ISO 8601 UTC timestamps into local times for the screens.
package com.meanwhile.android.util

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** Formats the core's UTC timestamps as local times. */
object DateTimeUtils {

    /** [isoString] (e.g. "2026-01-01T09:20:00Z") in [zoneId] as [pattern]; "" when blank or unreadable. */
    fun formatTime(
        isoString: String,
        zoneId: ZoneId = ZoneId.systemDefault(),
        pattern: String = "HH:mm"
    ): String {
        if (isoString.isBlank()) return ""
        return try {
            val instant = Instant.parse(isoString)
            val zonedDateTime = instant.atZone(zoneId)
            val formatter = DateTimeFormatter.ofPattern(pattern)
            formatter.format(zonedDateTime)
        } catch (e: Exception) {
            ""
        }
    }
}
