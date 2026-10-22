// Unit tests for reading the wake word and the request after it from a speech transcript.
package com.meanwhile.android

import com.meanwhile.android.util.WakeHeard
import com.meanwhile.android.util.WakePhrase
import org.junit.Assert.assertEquals
import org.junit.Test

/** Tests of WakePhrase: the wake word is removed from the request, case and punctuation, the wake word alone, no wake word. */
class WakePhraseTest {

    /** Only the request after the wake word (and its comma) is sent, as said. */
    @Test
    fun requestAfterTheWakeWord() {
        assertEquals(
            WakeHeard.Request("I'm stepping away, watch my bag"),
            WakePhrase.parse("Alexa, I'm stepping away, watch my bag", "Alexa")
        )
    }

    /** Like the web panel, words before the wake word are dropped. */
    @Test
    fun wordsBeforeTheWakeWordAreDropped() {
        assertEquals(WakeHeard.Request("watch my bag"), WakePhrase.parse("okay Alexa, watch my bag", "Alexa"))
        assertEquals(WakeHeard.Request("to watch my bag"), WakePhrase.parse("I told Alexa to watch my bag", "Alexa"))
    }

    /** Case and punctuation around the wake word don't matter. */
    @Test
    fun caseAndPunctuationAreIgnored() {
        assertEquals(WakeHeard.Request("what did I miss?"), WakePhrase.parse("alexa, what did I miss?", "Alexa"))
        assertEquals(WakeHeard.Request("I'm back"), WakePhrase.parse("  ALEXA. I'm back", "Alexa"))
        assertEquals(WakeHeard.Request("I'm back"), WakePhrase.parse("Alexa I'm back", " alexa "))
    }

    /** The wake word alone (with or without punctuation) asks for the request next. */
    @Test
    fun wakeWordAlone() {
        assertEquals(WakeHeard.WakeOnly, WakePhrase.parse("Alexa", "Alexa"))
        assertEquals(WakeHeard.WakeOnly, WakePhrase.parse("alexa?", "Alexa"))
        assertEquals(WakeHeard.WakeOnly, WakePhrase.parse(" Alexa, ", "Alexa"))
        assertEquals(WakeHeard.WakeOnly, WakePhrase.parse("Alexa ... 42", "Alexa"))
    }

    /** Speech without the wake word as a whole word is ignored. */
    @Test
    fun noWakeWordIsNothing() {
        assertEquals(WakeHeard.Nothing, WakePhrase.parse("watch my bag", "Alexa"))
        assertEquals(WakeHeard.Nothing, WakePhrase.parse("Alexander is here", "Alexa"))
        assertEquals(WakeHeard.Nothing, WakePhrase.parse("ask Alexa2 later", "Alexa"))
        assertEquals(WakeHeard.Nothing, WakePhrase.parse("", "Alexa"))
    }

    /** After the wake word alone, the next speech is the request even without the wake word. */
    @Test
    fun followUpAfterWakeOnly() {
        assertEquals(WakeHeard.Request("watch my bag"), WakePhrase.parse("watch my bag", "Alexa", awaitingRequest = true))
        assertEquals(WakeHeard.Request("watch my bag"), WakePhrase.parse("Alexa watch my bag", "Alexa", awaitingRequest = true))
        assertEquals(WakeHeard.Nothing, WakePhrase.parse("  ", "Alexa", awaitingRequest = true))
        assertEquals(WakeHeard.Nothing, WakePhrase.parse("...", "Alexa", awaitingRequest = true))
    }

    /** Among the recognizer's alternatives, the first one with the wake word wins. */
    @Test
    fun alternativesAreSearchedInOrder() {
        assertEquals(
            WakeHeard.Request("watch my keys"),
            WakePhrase.parseAny(listOf("a lexer watch my keys", "Alexa watch my keys"), "Alexa")
        )
        assertEquals(WakeHeard.Nothing, WakePhrase.parseAny(listOf("hello", "yellow"), "Alexa"))
    }
}
