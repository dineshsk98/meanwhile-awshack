// Entry point of the Meanwhile app: one activity that shows the Compose UI and routes notification taps to events.
package com.meanwhile.android

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.mutableStateOf
import androidx.core.content.ContextCompat
import com.meanwhile.android.ui.MainScreen
import com.meanwhile.android.ui.theme.MeanwhileTheme

/** A request to open an event from a notification; [nonce] makes a repeat tap on the same event count again. */
data class DeepLinkAction(val eventId: String, val nonce: Long)

/** The app's only activity: asks for notification permission, opens notification deep links and hosts [MainScreen]. */
class MainActivity : ComponentActivity() {

    private val deepLinkActionState = mutableStateOf<DeepLinkAction?>(null)

    /** Sets up edge-to-edge drawing, asks for notification permission and shows the app. */
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(
                    this,
                    Manifest.permission.POST_NOTIFICATIONS
                ) != PackageManager.PERMISSION_GRANTED
            ) {
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 101)
            }
        }

        // A recreated activity (rotation, process restore) gets its launch intent again; only a fresh start opens it.
        val eventId = if (savedInstanceState == null) extractEventId(intent) else null
        if (!eventId.isNullOrEmpty()) {
            deepLinkActionState.value = DeepLinkAction(eventId, System.currentTimeMillis())
        }

        setContent {
            MeanwhileTheme {
                MainScreen(
                    deepLinkAction = deepLinkActionState.value,
                    onDeepLinkConsumed = {
                        deepLinkActionState.value = null
                        intent?.removeExtra("event_id")
                    }
                )
            }
        }
    }

    /** Opens the event of a notification tapped while the app is already running. */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val eventId = extractEventId(intent)
        if (!eventId.isNullOrEmpty()) {
            deepLinkActionState.value = DeepLinkAction(eventId, System.currentTimeMillis())
        }
    }

    /** The event id from a `meanwhile://event/{id}` link or an `event_id` extra, if any. */
    private fun extractEventId(intent: Intent?): String? {
        if (intent == null) return null
        return intent.data?.lastPathSegment
            ?: intent.getStringExtra("event_id")
    }
}
