// The rule for which Ask messages are read aloud.
package com.meanwhile.android.util

import com.meanwhile.android.model.MessageSender

/** Decides which conversation messages are read aloud. */
object SpeechPolicy {

    /** The text to speak for a message, or null when it should stay silent (user message, muted or blank). */
    fun textToSpeak(sender: MessageSender, text: String, muted: Boolean): String? {
        if (muted || sender != MessageSender.MEANWHILE) return null
        val cleaned = text.replace(Regex("\\s+"), " ").trim()
        return cleaned.ifEmpty { null }
    }
}
