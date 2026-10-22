// Text-to-speech for Ask replies.
package com.meanwhile.android.util

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import java.util.Locale

/** Owns one Android TextToSpeech engine: speaks English replies, stops on request, reports when no engine works. */
class SpeechController(context: Context) {

    private var ready = false

    /** True once engine start-up failed or English is not supported. */
    var unavailable by mutableStateOf(false)
        private set

    /** True from a speak request until the engine has finished, failed or been stopped. */
    var speaking by mutableStateOf(false)
        private set

    private val engine: TextToSpeech = TextToSpeech(context.applicationContext) { status ->
        val languageResult = if (status == TextToSpeech.SUCCESS) engineLanguage() else TextToSpeech.LANG_NOT_SUPPORTED
        ready = status == TextToSpeech.SUCCESS &&
            languageResult != TextToSpeech.LANG_MISSING_DATA &&
            languageResult != TextToSpeech.LANG_NOT_SUPPORTED
        unavailable = !ready
    }

    init {
        engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            /** The engine started speaking. */
            override fun onStart(utteranceId: String?) {
                speaking = true
            }

            /** The engine finished speaking. */
            override fun onDone(utteranceId: String?) {
                speaking = false
            }

            /** The engine could not speak. */
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) {
                speaking = false
            }

            /** The engine was stopped mid-sentence. */
            override fun onStop(utteranceId: String?, interrupted: Boolean) {
                speaking = false
            }
        })
    }

    /** Sets English on the engine and returns the engine's support code for it. */
    private fun engineLanguage(): Int = engine.setLanguage(Locale.ENGLISH)

    /** Speaks [text], replacing anything still being spoken. */
    fun speak(text: String) {
        if (!ready) return
        speaking = true
        if (engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, "meanwhile-reply") != TextToSpeech.SUCCESS) speaking = false
    }

    /** Stops any speech in progress. */
    fun stop() {
        if (ready) engine.stop()
        speaking = false
    }

    /** Releases the engine; the controller cannot speak afterwards. */
    fun shutdown() {
        ready = false
        speaking = false
        engine.stop()
        engine.shutdown()
    }
}
