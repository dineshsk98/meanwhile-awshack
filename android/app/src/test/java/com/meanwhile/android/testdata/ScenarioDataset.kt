// A fictional home for tests: one watch session, its events, cameras and people.
package com.meanwhile.android.testdata

import com.meanwhile.android.model.Camera
import com.meanwhile.android.model.Event
import com.meanwhile.android.model.LiveBox
import com.meanwhile.android.model.Person
import com.meanwhile.android.model.Session
import com.meanwhile.android.model.WatchOrder

/** Fictional scenario data (one watch session, its events, cameras and people) for tests and evaluation. */
object ScenarioDataset {

    const val ACTIVE_SESSION_ID = "ses_desk_watch_01"

    val activeSession = Session(
        id = ACTIVE_SESSION_ID,
        instruction = "keep an eye on my watch",
        watchOrder = WatchOrder(
            summaryForAlexa = "Keeping an eye on your watch on the Desk camera",
            instruction = "keep an eye on my watch"
        ),
        summary = "Keeping an eye on your watch on the Desk camera",
        state = "with_person",
        stateActor = "Ravi",
        focusObject = "watch",
        focusCameraId = "desk",
        liveBox = LiveBox(
            cameraId = "desk",
            box = listOf(0.32f, 0.38f, 0.58f, 0.68f),
            held = true,
            missing = false,
            at = "2026-10-13T14:12:00Z"
        ),
        active = true,
        startedAt = "2026-10-13T14:05:00Z"
    )

    val pastSessions = listOf(
        Session(
            id = "ses_past_01",
            instruction = "Watch the front door",
            watchOrder = WatchOrder("Watching front door", "Watch the front door"),
            state = "on_surface",
            active = false,
            startedAt = "2026-10-12T09:00:00Z",
            endedAt = "2026-10-12T17:30:00Z"
        ),
        Session(
            id = "ses_past_02",
            instruction = "Keep an eye on my laptop",
            watchOrder = WatchOrder("Keeping an eye on laptop in Study", "Keep an eye on my laptop"),
            state = "on_surface",
            active = false,
            startedAt = "2026-10-11T13:00:00Z",
            endedAt = "2026-10-11T18:00:00Z"
        )
    )

    val events = listOf(
        Event(
            id = "evt_002",
            sessionId = ACTIVE_SESSION_ID,
            summary = "Ravi handed your watch back to you. Not taken.",
            alert = "verdict",
            actorName = "Ravi",
            actorRole = "Helper",
            cameraId = "room",
            happenedAt = "2026-10-13T14:13:00Z",
            ts = "2026-10-13T14:13:04Z",
            action = "handed_back",
            verdictOutcome = "Not taken"
        ),
        Event(
            id = "evt_001",
            sessionId = ACTIVE_SESSION_ID,
            summary = "Your watch was picked up by Ravi.",
            alert = "heads_up",
            actorName = "Ravi",
            actorRole = "Helper",
            cameraId = "desk",
            happenedAt = "2026-10-13T14:12:03Z",
            ts = "2026-10-13T14:12:07Z",
            action = "picked_up"
        )
    )

    val cameras = listOf(
        Camera(id = "desk", label = "Desk", online = true, lastSeen = "3 s ago"),
        Camera(id = "room", label = "Room", online = true, lastSeen = "3 s ago"),
        Camera(id = "door", label = "Door", online = true, lastSeen = "3 s ago"),
        Camera(id = "garden", label = "Garden", online = false, lastSeen = "Offline since 13:50")
    )

    val people = listOf(
        Person(id = "p1", name = "You", role = "Owner", note = "Home owner"),
        Person(id = "p2", name = "Ravi", role = "Helper", note = "Domestic helper"),
        Person(id = "p3", name = "Meera", role = "Family", note = "Sister"),
        Person(
            id = "unnamed-4f1b83",
            name = "Person 3",
            role = "",
            unnamed = true,
            number = 3,
            firstSeen = "2026-10-20T10:14:00Z",
            lastSeen = "2026-10-20T11:22:00Z",
            cameraId = "desk",
            faces = 1
        )
    )
}
