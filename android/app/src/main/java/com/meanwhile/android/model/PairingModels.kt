// Device pairing: one-time codes, the pairing reply, and the list of paired devices.
package com.meanwhile.android.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** A one-time pairing code from `POST /api/pairing-codes`, with the link its QR code holds. */
@Serializable
data class PairingCode(
    val code: String,
    @SerialName("pair_url") val pairUrl: String,
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("expires_in_s") val expiresInS: Double? = null,
    @SerialName("qr_svg") val qrSvg: String? = null
)

/** The reply to `POST /api/pair`: this device's entry and its token. */
@Serializable
data class PairResponse(
    @SerialName("device_token") val deviceToken: String,
    @SerialName("device_id") val deviceId: String? = null,
    val id: String? = null,
    val name: String? = null,
    val kind: String = "app",
    val created: String? = null,
    val current: Boolean = true
)

/** One paired phone or browser from `GET /api/paired-devices`. */
@Serializable
data class PairedDevice(
    val id: String = "",
    @SerialName("device_id") val deviceId: String = "",
    val name: String = "",
    val kind: String = "app",
    val created: String? = null,
    @SerialName("last_seen") val lastSeen: String? = null,
    val current: Boolean = false
) {
    /** The id to revoke it by. */
    val displayId: String get() = id.ifBlank { deviceId }
    /** Its name, or "Device" when it has none. */
    val deviceName: String get() = name.ifBlank { "Device" }
}
