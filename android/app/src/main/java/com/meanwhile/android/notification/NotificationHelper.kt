// Notification channels and the notifications shown for the core's pushes.
package com.meanwhile.android.notification

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.compose.ui.graphics.toArgb
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.meanwhile.android.MainActivity
import com.meanwhile.android.MeanwhileApplication
import com.meanwhile.android.ui.theme.MeanwhileAlertColors
import java.util.concurrent.atomic.AtomicInteger

/** Creates the notification channels and shows one notification per push, opening its event when tapped. */
class NotificationHelper(private val context: Context) {

    companion object {
        const val CHANNEL_HEADS_UP = "heads_up"
        const val CHANNEL_VERDICT = "verdict"
        const val CHANNEL_ALERT = "alert"
        const val GROUP_KEY_MEANWHILE = "com.meanwhile.android.ALERTS_GROUP"
        private val notificationIdGenerator = AtomicInteger(1000)
    }

    /** Creates the heads-up, verdict and alert channels (Android 8+). */
    fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val notificationManager =
                context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

            val headsUpChannel = NotificationChannel(
                CHANNEL_HEADS_UP,
                "Meanwhile Heads-ups",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Immediate notification when an object is being handled"
                enableVibration(true)
            }

            val verdictChannel = NotificationChannel(
                CHANNEL_VERDICT,
                "Meanwhile Verdicts",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Resolution verdicts (put back, handed back, taken)"
                enableVibration(true)
            }

            val alertChannel = NotificationChannel(
                CHANNEL_ALERT,
                "Meanwhile Urgent Alerts",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "An item taken away, or a watch spotting what you asked for"
                enableVibration(true)
            }

            notificationManager.createNotificationChannels(
                listOf(headsUpChannel, verdictChannel, alertChannel)
            )
        }
    }

    /** Shows a push on the channel for [alert]; tapping it opens event [eventId]. */
    fun showFcmNotification(
        alert: String,
        title: String?,
        summary: String,
        eventId: String?
    ) {
        val notifId = notificationIdGenerator.incrementAndGet()
        val channelId = when (alert) {
            "verdict" -> CHANNEL_VERDICT
            "alert" -> CHANNEL_ALERT
            else -> CHANNEL_HEADS_UP
        }

        val color = when (alert) {
            "verdict" -> MeanwhileAlertColors.VerdictGreen.toArgb()
            "alert" -> MeanwhileAlertColors.AlertRed.toArgb()
            else -> MeanwhileAlertColors.HeadsUpAmber.toArgb()
        }

        val notifTitle = title?.takeIf { it.isNotBlank() } ?: when (alert) {
            "verdict" -> "Verdict"
            "alert" -> "Alert"
            else -> "Heads-up"
        }

        val viewIntent = Intent(context, MainActivity::class.java).apply {
            if (!eventId.isNullOrEmpty()) {
                data = Uri.parse("meanwhile://event/$eventId")
            }
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val viewPendingIntent = PendingIntent.getActivity(
            context, notifId, viewIntent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val isWearOsEnabled = try {
            MeanwhileApplication.instance.preferencesManager.wearOsMirroring
        } catch (e: Exception) {
            true
        }

        val builder = NotificationCompat.Builder(context, channelId)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("Meanwhile · $notifTitle")
            .setContentText(summary)
            .setColor(color)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setGroup(GROUP_KEY_MEANWHILE)
            .setContentIntent(viewPendingIntent)
            .setAutoCancel(true)
            .setLocalOnly(!isWearOsEnabled)

        if (isWearOsEnabled) {
            builder.extend(NotificationCompat.WearableExtender())
        }

        if (alert == "verdict") {
            builder.addAction(android.R.drawable.ic_media_play, "View clip", viewPendingIntent)
        } else if (alert == "alert") {
            builder.addAction(android.R.drawable.ic_menu_camera, "Live view", viewPendingIntent)
        } else {
            builder.addAction(android.R.drawable.ic_media_play, "View", viewPendingIntent)
        }

        NotificationManagerCompat.from(context).notify(notifId, builder.build())
    }
}
