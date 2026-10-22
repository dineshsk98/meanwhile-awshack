// The app's stored settings: hub address and ports, the access token, notification, speech and wake-word switches.
package com.meanwhile.android.data

import android.content.Context
import android.content.SharedPreferences
import com.meanwhile.android.BuildConfig
import com.meanwhile.android.util.HubUrls

/** The app's settings in SharedPreferences; the hub defaults come from BuildConfig (local.properties). */
class PreferencesManager(context: Context) : PreferencesProvider {
    private val prefs: SharedPreferences =
        context.getSharedPreferences("meanwhile_prefs", Context.MODE_PRIVATE)

    override var hubAddress: String
        get() = prefs.getString("hub_address", BuildConfig.DEFAULT_HUB_ADDRESS) ?: BuildConfig.DEFAULT_HUB_ADDRESS
        set(value) = prefs.edit().putString("hub_address", value).apply()

    override var corePort: Int
        get() = prefs.getInt("core_port", BuildConfig.DEFAULT_CORE_PORT)
        set(value) = prefs.edit().putInt("core_port", value).apply()

    override var voicePort: Int
        get() = prefs.getInt("voice_port", BuildConfig.DEFAULT_VOICE_PORT)
        set(value) = prefs.edit().putInt("voice_port", value).apply()

    override var apiKey: String
        get() = prefs.getString("api_key", "") ?: ""
        set(value) = prefs.edit().putString("api_key", value).apply()

    /** Whether the first-run tutorial has been seen. */
    var tutorialSeen: Boolean
        get() = prefs.getBoolean("tutorial_seen", false)
        set(value) = prefs.edit().putBoolean("tutorial_seen", value).apply()

    /** Whether Ask replies are read aloud. */
    var speakReplies: Boolean
        get() = prefs.getBoolean("speak_replies", true)
        set(value) = prefs.edit().putBoolean("speak_replies", value).apply()

    /** Whether the app listens for the wake word while it is open. */
    var wakeWordEnabled: Boolean
        get() = prefs.getBoolean("wake_word_enabled", true)
        set(value) = prefs.edit().putBoolean("wake_word_enabled", value).apply()

    /** Whether heads-up pushes are shown. */
    var headsUpNotifications: Boolean
        get() = prefs.getBoolean("notif_heads_up", true)
        set(value) = prefs.edit().putBoolean("notif_heads_up", value).apply()

    /** Whether verdict pushes are shown. */
    var verdictNotifications: Boolean
        get() = prefs.getBoolean("notif_verdict", true)
        set(value) = prefs.edit().putBoolean("notif_verdict", value).apply()

    /** Whether alert pushes are shown. */
    var alertNotifications: Boolean
        get() = prefs.getBoolean("notif_alert", true)
        set(value) = prefs.edit().putBoolean("notif_alert", value).apply()

    /** Whether notifications are also shown on a paired Wear OS watch. */
    var wearOsMirroring: Boolean
        get() = prefs.getBoolean("wear_os_mirroring", true)
        set(value) = prefs.edit().putBoolean("wear_os_mirroring", value).apply()

    override var useHttps: Boolean
        get() = prefs.getBoolean("use_https", false)
        set(value) = prefs.edit().putBoolean("use_https", value).apply()

    /** The core's base URL; see [HubUrls.resolve]. */
    override fun getCoreBaseUrl(hub: String, port: Int): String = HubUrls.resolve(hub, port, useHttps)

    /** The voice side's base URL; see [HubUrls.resolve]. */
    override fun getVoiceBaseUrl(hub: String, port: Int): String = HubUrls.resolve(hub, port, useHttps)
}

