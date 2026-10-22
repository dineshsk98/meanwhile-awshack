// The connection settings the repository reads: hub, ports, key and scheme.
package com.meanwhile.android.data

/** The connection settings the repository needs, so it can be tested without an Android Context. */
interface PreferencesProvider {
    val hubAddress: String
    val corePort: Int
    val voicePort: Int
    /** The access key or the paired device's token, sent as `X-Meanwhile-Key`. */
    val apiKey: String
    val useHttps: Boolean

    /** Base URL of the core on [hub] and [port]. */
    fun getCoreBaseUrl(hub: String, port: Int): String
    /** Base URL of the voice side on [hub] and [port]. */
    fun getVoiceBaseUrl(hub: String, port: Int): String

    /** Base URL of the core on [hub] with the stored core port. */
    fun getCoreBaseUrl(hub: String): String = getCoreBaseUrl(hub, corePort)

    /** Base URL of the core on the stored hub. */
    fun getCoreBaseUrl(): String = getCoreBaseUrl(hubAddress, corePort)
    /** Base URL of the voice side on the stored hub. */
    fun getVoiceBaseUrl(): String = getVoiceBaseUrl(hubAddress, voicePort)
}
