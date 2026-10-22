// Unit tests that the fictional scenario dataset is consistent and reads correctly through the models.
package com.meanwhile.android

import com.meanwhile.android.model.AlertType
import com.meanwhile.android.model.SessionObjectState
import com.meanwhile.android.testdata.ScenarioDataset
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Checks the scenario dataset is self-consistent and renders through the app's model logic. */
class ScenarioDatasetTest {

    /** Events and the live box point at the dataset's session and cameras. */
    @Test
    fun eventsAndCamerasReferenceKnownSessionsAndCameras() {
        val cameraIds = ScenarioDataset.cameras.map { it.id }.toSet()
        ScenarioDataset.events.forEach {
            assertEquals(ScenarioDataset.ACTIVE_SESSION_ID, it.sessionId)
            assertTrue(it.cameraId in cameraIds)
        }
        assertTrue(ScenarioDataset.activeSession.focusCameraId in cameraIds)
        assertEquals(ScenarioDataset.activeSession.focusCameraId, ScenarioDataset.activeSession.liveBox?.cameraId)
    }

    /** The active session reads as held, with its summary. */
    @Test
    fun activeSessionReadsAsHeldByHelper() {
        val s = ScenarioDataset.activeSession
        assertEquals(SessionObjectState.fromString("with_person"), s.objectState)
        assertEquals("Keeping an eye on your watch on the Desk camera", s.comprehensionSummary)
        assertTrue(s.active)
        ScenarioDataset.pastSessions.forEach { assertFalse(it.active) }
    }

    /** Events have alert types and formatted times. */
    @Test
    fun eventsFormatTimesAndAlertTypes() {
        val types = ScenarioDataset.events.map { it.alertType }
        assertTrue(AlertType.fromString("verdict") in types)
        assertTrue(AlertType.fromString("heads_up") in types)
        ScenarioDataset.events.forEach { assertTrue(it.formattedTime.isNotBlank()) }
    }

    /** The owner is "You" and the stranger is a numbered unnamed person. */
    @Test
    fun ownerIsShownAsYouAndStrangersAreUnnamed() {
        val owner = ScenarioDataset.people.single { it.role == "Owner" }
        assertEquals("You", owner.name)
        val unnamed = ScenarioDataset.people.filter { it.unnamed }
        assertEquals(1, unnamed.size)
        assertNotNull(unnamed.single().number)
    }

    /** The dataset has no media URLs. */
    @Test
    fun datasetCarriesNoUrls() {
        val text = listOf(
            ScenarioDataset.events.map { listOf(it.clipUrl, it.frameUrl) },
            ScenarioDataset.cameras.map { listOf(it.thumbUrl) },
            ScenarioDataset.people.map { listOf(it.photoUrl) }
        ).flatten().flatten()
        assertTrue(text.all { it == null })
    }
}
