// Firebase Cloud Messaging service: turns the core's pushes into notifications and keeps the phone's token registered.
package com.meanwhile.android.notification

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.meanwhile.android.MeanwhileApplication
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/** Receives FCM pushes from the core and shows them, and re-registers the phone when its token changes. */
class MeanwhileFirebaseMessagingService : FirebaseMessagingService() {

    private val job = SupervisorJob()
    private val scope = CoroutineScope(Dispatchers.IO + job)

    /** Registers a new FCM token with the core. */
    override fun onNewToken(token: String) {
        super.onNewToken(token)
        Log.d(TAG, "New FCM Token received: ...${token.takeLast(4)}")

        val app = MeanwhileApplication.instance

        // POST /api/devices {"fcm_token"}
        scope.launch {
            try {
                val success = app.repository.registerDevice(token)
                Log.d(TAG, "Device token registered with core: $success")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to register FCM token with core", e)
            }
        }
    }

    /** Shows a push as a notification unless its alert type is switched off in Settings. */
    override fun onMessageReceived(remoteMessage: RemoteMessage) {
        super.onMessageReceived(remoteMessage)
        Log.d(TAG, "FCM message received from: ${remoteMessage.from}")

        // Core sends data: {"event_id", "summary", "alert", "frame_url", "session_id"}
        // and notification: {"title", "body"}
        val data = remoteMessage.data
        val alert = data["alert"] ?: run {
            Log.w(TAG, "Ignoring push message without alert type")
            return
        }
        val eventId = data["event_id"]
        val summary = data["summary"]
            ?: remoteMessage.notification?.body
            ?: "Activity detected at home."
        val title = remoteMessage.notification?.title
            ?: when (alert) {
                "verdict" -> "Verdict"
                "alert" -> "Alert"
                else -> "Heads-up"
            }

        val app = MeanwhileApplication.instance

        val prefs = app.preferencesManager
        val shouldShow = when (alert) {
            "heads_up" -> prefs.headsUpNotifications
            "verdict" -> prefs.verdictNotifications
            "alert" -> prefs.alertNotifications
            else -> true
        }

        if (shouldShow) {
            app.notificationHelper.showFcmNotification(
                alert = alert,
                title = title,
                summary = summary,
                eventId = eventId
            )
        }
    }

    /** Cancels pending token registrations. */
    override fun onDestroy() {
        super.onDestroy()
        job.cancel()
    }

    companion object {
        private const val TAG = "MeanwhileFCM"
    }
}
