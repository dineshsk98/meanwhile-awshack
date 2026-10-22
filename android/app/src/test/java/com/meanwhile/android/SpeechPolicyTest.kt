// Unit tests for which Ask messages are read aloud.
package com.meanwhile.android

import com.meanwhile.android.model.MessageSender
import com.meanwhile.android.util.SpeechPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Unit tests for which conversation messages are spoken aloud. */
class SpeechPolicyTest {

    /** Meanwhile's replies are spoken with whitespace tidied. */
    @Test
    fun speaksAgentRepliesWithTidiedWhitespace() {
        assertEquals(
            "Okay, I'm keeping an eye on your lamp.",
            SpeechPolicy.textToSpeak(MessageSender.MEANWHILE, "  Okay,  I'm keeping an eye\non your lamp. ", muted = false)
        )
    }

    /** Nothing is spoken while muted. */
    @Test
    fun staysSilentWhenMuted() {
        assertNull(SpeechPolicy.textToSpeak(MessageSender.MEANWHILE, "Hello", muted = true))
    }

    /** The user's own messages are never spoken. */
    @Test
    fun neverSpeaksTheUsersOwnMessages() {
        assertNull(SpeechPolicy.textToSpeak(MessageSender.USER, "What did I miss?", muted = false))
    }

    /** Blank replies stay silent. */
    @Test
    fun staysSilentForBlankReplies() {
        assertNull(SpeechPolicy.textToSpeak(MessageSender.MEANWHILE, "   ", muted = false))
    }
}
