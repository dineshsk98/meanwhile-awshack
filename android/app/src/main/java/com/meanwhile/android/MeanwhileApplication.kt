// The Application class: creates the objects the whole app shares and registers the phone for push alerts.
package com.meanwhile.android

import android.app.Application
import android.util.Log
import com.google.firebase.messaging.FirebaseMessaging
import com.meanwhile.android.data.PreferencesManager
import com.meanwhile.android.data.RemoteMeanwhileRepository
import com.meanwhile.android.notification.NotificationHelper
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/** Holds the app-wide preferences, repository and notification helper, and registers the phone for push alerts. */
class MeanwhileApplication : Application() {

    lateinit var preferencesManager: PreferencesManager
        private set

    lateinit var repository: RemoteMeanwhileRepository
        private set

    lateinit var notificationHelper: NotificationHelper
        private set

    /** Creates the shared objects and notification channels, then registers for push alerts. */
    override fun onCreate() {
        super.onCreate()
        instance = this

        preferencesManager = PreferencesManager(this)
        repository = RemoteMeanwhileRepository(preferencesManager)
        notificationHelper = NotificationHelper(this)
        notificationHelper.createNotificationChannels()

        registerPushDevice()
    }

    /** Fetches this phone's FCM token and registers it with the core (`POST /api/devices`). */
    fun registerPushDevice() {
        try {
            FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
                if (task.isSuccessful && !task.result.isNullOrBlank()) {
                    val token = task.result
                    Log.d("MeanwhileApp", "Current FCM token: ...${token.takeLast(4)}")
                    CoroutineScope(Dispatchers.IO).launch {
                        val registered = repository.registerDevice(token)
                        Log.d("MeanwhileApp", "Token registered with core: $registered")
                    }
                } else {
                    Log.w("MeanwhileApp", "Fetching FCM registration token failed", task.exception)
                }
            }
        } catch (e: Exception) {
            Log.e("MeanwhileApp", "Firebase initialization deferred or unavailable: ${e.message}")
        }
    }

    companion object {
        /** The running application, for code without a Context. */
        lateinit var instance: MeanwhileApplication
            private set
    }
}
