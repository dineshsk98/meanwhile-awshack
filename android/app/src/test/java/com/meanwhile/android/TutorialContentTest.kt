// Unit tests for the first-run tutorial's content and navigation.
package com.meanwhile.android

import com.meanwhile.android.ui.onboarding.TutorialContent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Unit tests for the tutorial's show-once decision, card navigation and content. */
class TutorialContentTest {

    /** The tutorial opens by itself only until it has been seen. */
    @Test
    fun autoShowsOnlyUntilSeen() {
        assertTrue(TutorialContent.shouldAutoShow(seen = false))
        assertFalse(TutorialContent.shouldAutoShow(seen = true))
    }

    /** Next and Back stop at the last and first cards. */
    @Test
    fun navigationStopsAtBothEnds() {
        val last = TutorialContent.pages.lastIndex
        assertEquals(1, TutorialContent.nextIndex(0))
        assertNull(TutorialContent.nextIndex(last))
        assertNull(TutorialContent.previousIndex(0))
        assertEquals(last - 1, TutorialContent.previousIndex(last))
    }

    /** Every card has a title and a body. */
    @Test
    fun everyPageHasTitleAndBody() {
        assertTrue(TutorialContent.pages.isNotEmpty())
        TutorialContent.pages.forEach {
            assertTrue(it.title.isNotBlank())
            assertTrue(it.body.isNotBlank())
        }
    }

    /** A card teaches "What did I miss?". */
    @Test
    fun coversTheCatchUpPhrase() {
        val examples = TutorialContent.pages.flatMap { it.examples }
        assertTrue(examples.any { it.contains("What did I miss", ignoreCase = true) })
    }
}
