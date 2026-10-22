// Reads a speech transcript for the wake word: the request after it (wake word removed), the wake word alone, or neither.
package com.meanwhile.android.util

/** What a transcript means for the wake-word listener. */
sealed class WakeHeard {
    /** The wake word followed by a request, e.g. "Alexa, watch my bag" → "watch my bag". */
    data class Request(val text: String) : WakeHeard()

    /** The wake word on its own: the next thing said is the request. */
    data object WakeOnly : WakeHeard()

    /** Nothing addressed to the app. */
    data object Nothing : WakeHeard()
}

/**
 * Finds the wake word as a whole word anywhere in a transcript, ignoring case, like the web Alexa panel: what comes
 * before it is dropped, and the request is what follows it without the punctuation and spaces right after it.
 */
object WakePhrase {

    private val leading = Regex("""^[\s\p{Punct}]+""")
    private val letter = Regex("""\p{L}""")

    /** What [transcript] means for [wakeWord]; with [awaitingRequest], any speech with a letter in it is the request. */
    fun parse(transcript: String, wakeWord: String, awaitingRequest: Boolean = false): WakeHeard {
        val text = transcript.trim()
        val word = wakeWord.trim()
        if (word.isEmpty()) return WakeHeard.Nothing
        val match = Regex("""(?<![\p{L}\p{N}])${Regex.escape(word)}(?![\p{L}\p{N}])""", RegexOption.IGNORE_CASE).find(text)
        if (match != null) {
            val rest = text.substring(match.range.last + 1).replace(leading, "")
            return if (letter.containsMatchIn(rest)) WakeHeard.Request(rest) else WakeHeard.WakeOnly
        }
        return if (awaitingRequest && letter.containsMatchIn(text)) WakeHeard.Request(text) else WakeHeard.Nothing
    }

    /** The first meaningful reading among the recognizer's alternatives [transcripts], best first. */
    fun parseAny(transcripts: List<String>, wakeWord: String, awaitingRequest: Boolean = false): WakeHeard =
        transcripts.map { parse(it, wakeWord, awaitingRequest) }.firstOrNull { it != WakeHeard.Nothing }
            ?: WakeHeard.Nothing
}
