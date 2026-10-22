// The app's navigation routes.
package com.meanwhile.android.ui.navigation

/** One navigation destination and its route. */
sealed class Screen(val route: String) {
    data object Home : Screen("home")
    data object Timeline : Screen("timeline")
    data object Cameras : Screen("cameras")
    data object People : Screen("people")
    data object Connect : Screen("connect")
    data object Settings : Screen("settings")
    data object PastSessions : Screen("past_sessions")
    data object EventDetail : Screen("event/{eventId}") {
        /** The route that opens event [eventId]. */
        fun createRoute(eventId: String) = "event/$eventId"
    }
}
