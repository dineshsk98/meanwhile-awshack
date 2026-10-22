// Listens for the wake word while the app is open (one continuous on-device session) and hears the request after a
// lone wake word with the online recognizer.
package com.meanwhile.android.util

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log
import java.util.Locale

/**
 * Spots [wakeWord] in a continuous, segmented on-device session (Android 13+; elsewhere, back-to-back online
 * sessions). "Wake word + request" in one breath gives [onHeard] the request; the wake word alone gives [onHeard] null,
 * and the next [start] within a few seconds listens once with the online recognizer for the request, reporting
 * [onRequestListening] while the microphone is open for it. [onMicDenied] runs if the permission is missing. Call
 * everything on the main thread.
 */
class WakeWordListener(
    context: Context,
    private val wakeWord: String,
    private val onHeard: (request: String?) -> Unit,
    private val onRequestListening: (Boolean) -> Unit,
    private val onMicDenied: () -> Unit
) {
    private val appContext = context.applicationContext
    private val handler = Handler(Looper.getMainLooper())
    private var spotter: SpeechRecognizer? = null
    private var online: SpeechRecognizer? = null
    private var continuous = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
        SpeechRecognizer.isOnDeviceRecognitionAvailable(appContext)
    private var active = false
    private var backoffMs = MIN_BACKOFF_MS
    private var awaitingUntil = 0L

    private val spotRunnable = Runnable { spot() }

    /** Starts listening at once: for the request if a lone wake word is pending, else for the wake word. */
    fun start() {
        if (active || !SpeechRecognizer.isRecognitionAvailable(appContext)) return
        active = true
        if (awaiting()) listenForRequest() else spot()
    }

    /** Stops listening at once; [start] resumes. */
    fun stop() {
        if (!active) return
        active = false
        handler.removeCallbacks(spotRunnable)
        spotter?.cancel()
        online?.cancel()
        onRequestListening(false)
        Log.d(TAG, "paused")
    }

    /** Stops and frees both recognizers. */
    fun release() {
        stop()
        spotter?.destroy()
        online?.destroy()
        spotter = null
        online = null
    }

    /** True while a lone wake word is waiting for its request. */
    private fun awaiting(): Boolean = SystemClock.elapsedRealtime() < awaitingUntil

    /** Spots the wake word again after [delayMs] (0 = now). */
    private fun spotAgain(delayMs: Long = 0L) {
        handler.removeCallbacks(spotRunnable)
        if (active) handler.postDelayed(spotRunnable, delayMs)
    }

    /** Opens the wake-word session: one long segmented on-device session, or one online utterance as a fallback. */
    private fun spot() {
        if (!active) return
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, MAX_RESULTS)
            putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, appContext.packageName)
            if (continuous) {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().let { "${it.language}-${it.country}" })
                putExtra(RecognizerIntent.EXTRA_SEGMENTED_SESSION, RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS)
                putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, SESSION_MS)
                putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, SESSION_MS)
            }
        }
        val recognizer = spotter ?: create(continuous).also {
            it.setRecognitionListener(spotListener)
            spotter = it
        }
        recognizer.startListening(intent)
        Log.d(TAG, if (continuous) "spotting (continuous on-device session)" else "spotting (one online utterance)")
    }

    /** Opens the online recognizer once for the request after a lone wake word. */
    private fun listenForRequest() {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, false)
            putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, appContext.packageName)
        }
        val recognizer = online ?: SpeechRecognizer.createSpeechRecognizer(appContext).also {
            it.setRecognitionListener(requestListener)
            online = it
        }
        recognizer.startListening(intent)
        Log.d(TAG, "listening for the request (online)")
    }

    /** A recognizer for spotting: on-device when the continuous session is used, the default service otherwise. */
    private fun create(onDevice: Boolean): SpeechRecognizer =
        if (onDevice && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            SpeechRecognizer.createOnDeviceSpeechRecognizer(appContext)
        } else {
            SpeechRecognizer.createSpeechRecognizer(appContext)
        }

    /** Acts on what the spotter heard: a request, a lone wake word, or nothing. */
    private fun handleSpotted(results: Bundle?) {
        val texts = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION).orEmpty()
        when (val heard = WakePhrase.parseAny(texts, wakeWord)) {
            is WakeHeard.Request -> {
                Log.d(TAG, "wake word with a request")
                onHeard(heard.text)
            }
            WakeHeard.WakeOnly -> {
                awaitingUntil = SystemClock.elapsedRealtime() + FOLLOW_UP_MS
                Log.d(TAG, "wake word alone; the request comes next")
                onHeard(null)
            }
            WakeHeard.Nothing -> Log.d(TAG, "speech without the wake word")
        }
    }

    /** Handles errors of either recognizer: microphone denied, or a retry with backoff. */
    private fun retryAfter(error: Int) {
        if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS) {
            Log.w(TAG, "no microphone permission; wake word off")
            active = false
            onMicDenied()
            return
        }
        Log.w(TAG, "recognizer error $error; retrying in $backoffMs ms")
        spotter?.destroy()
        spotter = null
        spotAgain(backoffMs)
        backoffMs = (backoffMs * 2).coerceAtMost(MAX_BACKOFF_MS)
    }

    private val spotListener = object : RecognitionListener {
        /** The microphone is open: errors so far are over. */
        override fun onReadyForSpeech(params: Bundle?) {
            backoffMs = MIN_BACKOFF_MS
            Log.d(TAG, "microphone open")
        }

        /** One segment of the continuous session. */
        override fun onSegmentResults(segmentResults: Bundle) = handleSpotted(segmentResults)

        /** The continuous session reached its length: open the next one at once. */
        override fun onEndOfSegmentedSession() {
            Log.d(TAG, "continuous session ended; reopening")
            spotAgain()
        }

        /** One utterance (fallback mode, or a recognizer that ignores segmenting): act on it and listen again. */
        override fun onResults(results: Bundle?) {
            handleSpotted(results)
            spotAgain()
        }

        /** Silence reopens at once; no on-device support switches to the online fallback; other errors back off. */
        override fun onError(error: Int) {
            if (!active) return
            when {
                error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> {
                    Log.d(TAG, "no speech (error $error); reopening")
                    spotAgain()
                }
                continuous && error in ON_DEVICE_FAILURES -> {
                    Log.w(TAG, "continuous on-device session unavailable (error $error); using online utterances")
                    continuous = false
                    spotter?.destroy()
                    spotter = null
                    spotAgain()
                }
                else -> retryAfter(error)
            }
        }

        /** Not used. */
        override fun onBeginningOfSpeech() = Unit
        /** Not used. */
        override fun onRmsChanged(rmsdB: Float) = Unit
        /** Not used. */
        override fun onBufferReceived(buffer: ByteArray?) = Unit
        /** Not used. */
        override fun onEndOfSpeech() = Unit
        /** Not used. */
        override fun onPartialResults(partialResults: Bundle?) = Unit
        /** Not used. */
        override fun onEvent(eventType: Int, params: Bundle?) = Unit
    }

    private val requestListener = object : RecognitionListener {
        /** The microphone is open for the request: show it. */
        override fun onReadyForSpeech(params: Bundle?) {
            backoffMs = MIN_BACKOFF_MS
            onRequestListening(true)
        }

        /** The request: pass it on, then go back to spotting. */
        override fun onResults(results: Bundle?) {
            awaitingUntil = 0L
            onRequestListening(false)
            val texts = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION).orEmpty()
            when (val heard = WakePhrase.parseAny(texts, wakeWord, awaitingRequest = true)) {
                is WakeHeard.Request -> {
                    Log.d(TAG, "request after the wake word")
                    onHeard(heard.text)
                }
                else -> Log.d(TAG, "no request after the wake word")
            }
            spotAgain()
        }

        /** Nothing usable was said: give up on this request and go back to spotting. */
        override fun onError(error: Int) {
            if (!active) return
            awaitingUntil = 0L
            onRequestListening(false)
            Log.d(TAG, "no request after the wake word (error $error)")
            if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS) retryAfter(error) else spotAgain()
        }

        /** Not used. */
        override fun onBeginningOfSpeech() = Unit
        /** Not used. */
        override fun onRmsChanged(rmsdB: Float) = Unit
        /** Not used. */
        override fun onBufferReceived(buffer: ByteArray?) = Unit
        /** Not used. */
        override fun onEndOfSpeech() = Unit
        /** Not used. */
        override fun onPartialResults(partialResults: Bundle?) = Unit
        /** Not used. */
        override fun onEvent(eventType: Int, params: Bundle?) = Unit
    }

    companion object {
        private const val TAG = "MeanwhileWake"
        private const val MAX_RESULTS = 3
        private const val SESSION_MS = 10 * 60 * 1_000L
        private const val MIN_BACKOFF_MS = 1_000L
        private const val MAX_BACKOFF_MS = 10_000L
        private const val FOLLOW_UP_MS = 10_000L

        /** Errors that mean the on-device recognizer can't run this session. */
        private val ON_DEVICE_FAILURES = setOf(
            SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED,
            SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE,
            SpeechRecognizer.ERROR_CANNOT_CHECK_SUPPORT,
            SpeechRecognizer.ERROR_SERVER_DISCONNECTED
        )
    }
}
