// Live camera video over WebRTC: the decoder factory and the per-camera stream manager.
package com.meanwhile.android.data

import android.content.Context
import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.webrtc.*
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Video decoders from Android's MediaCodec only: the phone's hardware decoders, then the platform's software ones.
 * Decoding never goes through WebRTC's bundled software codecs, whose native side must match the Java classes exactly
 * (a mismatch aborts the app on the decoding thread).
 */
class MediaCodecVideoDecoderFactory(eglContext: EglBase.Context) : VideoDecoderFactory {
    private val hardware = HardwareVideoDecoderFactory(eglContext)
    private val platformSoftware = PlatformSoftwareVideoDecoderFactory(eglContext)

    /** A hardware decoder for the codec if the phone has one, else the platform's software decoder, else null. */
    override fun createDecoder(info: VideoCodecInfo): VideoDecoder? =
        hardware.createDecoder(info) ?: platformSoftware.createDecoder(info)

    /** Every codec either factory can decode, each listed once. */
    override fun getSupportedCodecs(): Array<VideoCodecInfo> =
        LinkedHashSet<VideoCodecInfo>().apply {
            addAll(hardware.supportedCodecs)
            addAll(platformSoftware.supportedCodecs)
        }.toTypedArray()
}

/** Where a live stream is: not started, connecting, playing, failed (retrying) or closed. */
enum class RtcConnectionState {
    IDLE,
    CONNECTING,
    CONNECTED,
    FAILED,
    CLOSED
}

/** Receives one camera's live video from the core: posts an offer, polls for the answer, and reconnects with backoff. */
class WebRtcLiveStreamManager(
    private val context: Context,
    private val repo: MeanwhileRepository,
    private val cameraId: String
) {
    private val tag = "WebRtcLiveStream-$cameraId"
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    private val _connectionState = MutableStateFlow(RtcConnectionState.IDLE)
    val connectionState: StateFlow<RtcConnectionState> = _connectionState.asStateFlow()

    private val _videoTrack = MutableStateFlow<VideoTrack?>(null)
    val videoTrack: StateFlow<VideoTrack?> = _videoTrack.asStateFlow()

    @Volatile
    private var peerConnection: PeerConnection? = null
    private val isClosed = AtomicBoolean(false)
    private var connectionJob: Job? = null
    private var retryDelayMs = 3000L

    companion object {
        /** The EGL context shared by the decoders and the video views. */
        val eglBase: EglBase by lazy { EglBase.create() }

        private val isInitialized = AtomicBoolean(false)

        /** Initialises WebRTC once per process. */
        fun initFactory(context: Context) {
            if (!isInitialized.getAndSet(true)) {
                val initOptions = PeerConnectionFactory.InitializationOptions.builder(context.applicationContext)
                    .setEnableInternalTracer(false)
                    .createInitializationOptions()
                PeerConnectionFactory.initialize(initOptions)
            }
        }

        private var sharedFactory: PeerConnectionFactory? = null

        /** The one PeerConnectionFactory the app uses, created on first use. */
        @Synchronized
        fun getSharedFactory(context: Context): PeerConnectionFactory {
            initFactory(context)
            if (sharedFactory == null) {
                sharedFactory = PeerConnectionFactory.builder()
                    .setVideoDecoderFactory(MediaCodecVideoDecoderFactory(eglBase.eglBaseContext))
                    .setVideoEncoderFactory(DefaultVideoEncoderFactory(eglBase.eglBaseContext, true, true))
                    .createPeerConnectionFactory()
            }
            return sharedFactory!!
        }
    }

    private val peerConnectionFactory: PeerConnectionFactory
        get() = getSharedFactory(context)

    /** Starts connecting (no-op once stopped). */
    fun start() {
        if (isClosed.get()) return
        retryDelayMs = 3000L
        connectionJob?.cancel()
        connectionJob = scope.launch {
            connectLive()
        }
    }

    /** Tries again after the current backoff delay, doubling it up to 30 s. */
    private fun reconnect() {
        if (isClosed.get()) return
        connectionJob?.cancel()
        val delayToWait = retryDelayMs
        retryDelayMs = (retryDelayMs * 2).coerceAtMost(30000L)
        connectionJob = scope.launch {
            delay(delayToWait)
            if (!isClosed.get()) {
                cleanupPeerConnection()
                connectLive()
            }
        }
    }

    /** One connection attempt: non-trickle offer, answer from the core, then the video track arrives via the observer. */
    private suspend fun connectLive() {
        if (isClosed.get()) return
        cleanupPeerConnection()
        _connectionState.value = RtcConnectionState.CONNECTING

        try {
            // 1. Fetch ICE servers from core
            val iceConfigList = repo.getIceServers()
            val rtcIceServers = iceConfigList.map { config ->
                PeerConnection.IceServer.builder(config.urls).apply {
                    if (!config.username.isNullOrBlank()) setUsername(config.username)
                    if (!config.credential.isNullOrBlank()) setPassword(config.credential)
                }.createIceServer()
            }

            val rtcConfig = PeerConnection.RTCConfiguration(rtcIceServers).apply {
                sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
                continualGatheringPolicy = PeerConnection.ContinualGatheringPolicy.GATHER_ONCE
            }

            // Latch to wait for non-trickle candidate gathering
            val gatheringLatch = CountDownLatch(1)

            val pcObserver = object : PeerConnection.Observer {
                override fun onSignalingChange(state: PeerConnection.SignalingState?) {}

                override fun onIceConnectionChange(state: PeerConnection.IceConnectionState?) {
                    Log.d(tag, "ICE connection state: $state")
                    if (state == PeerConnection.IceConnectionState.FAILED) {
                        if (!isClosed.get()) {
                            _connectionState.value = RtcConnectionState.FAILED
                            reconnect()
                        }
                    }
                }

                override fun onIceConnectionReceivingChange(receiving: Boolean) {}

                override fun onIceGatheringChange(state: PeerConnection.IceGatheringState?) {
                    Log.d(tag, "ICE gathering state: $state")
                    if (state == PeerConnection.IceGatheringState.COMPLETE) {
                        gatheringLatch.countDown()
                    }
                }

                override fun onIceCandidate(candidate: IceCandidate?) {}

                override fun onIceCandidatesRemoved(candidates: Array<out IceCandidate>?) {}

                override fun onAddStream(stream: MediaStream?) {}

                override fun onRemoveStream(stream: MediaStream?) {}

                override fun onDataChannel(dataChannel: DataChannel?) {}

                override fun onRenegotiationNeeded() {}

                override fun onAddTrack(receiver: RtpReceiver?, mediaStreams: Array<out MediaStream>?) {
                    val track = receiver?.track() as? VideoTrack
                    if (track != null) {
                        Log.d(tag, "Video track received: ${track.id()}")
                        _videoTrack.value = track
                        _connectionState.value = RtcConnectionState.CONNECTED
                        retryDelayMs = 3000L
                    }
                }

                override fun onTrack(transceiver: RtpTransceiver?) {
                    val track = transceiver?.receiver?.track() as? VideoTrack
                    if (track != null) {
                        Log.d(tag, "Video track received via transceiver: ${track.id()}")
                        _videoTrack.value = track
                        _connectionState.value = RtcConnectionState.CONNECTED
                        retryDelayMs = 3000L
                    }
                }
            }

            val pc = peerConnectionFactory.createPeerConnection(rtcConfig, pcObserver)
                ?: throw IllegalStateException("Failed to create PeerConnection")
            if (isClosed.get()) {
                pc.close()
                pc.dispose()
                return
            }
            peerConnection = pc

            // Add recvonly transceiver for video
            val transceiverInit = RtpTransceiver.RtpTransceiverInit(
                RtpTransceiver.RtpTransceiverDirection.RECV_ONLY
            )
            pc.addTransceiver(MediaStreamTrack.MediaType.MEDIA_TYPE_VIDEO, transceiverInit)

            // 2. Create offer
            val offerSdp = suspendCancellableCoroutine<SessionDescription> { cont ->
                pc.createOffer(object : SdpObserver {
                    override fun onCreateSuccess(desc: SessionDescription?) {
                        if (desc != null) cont.resume(desc, null)
                        else cont.cancel(IllegalStateException("Offer SDP is null"))
                    }
                    override fun onSetSuccess() {}
                    override fun onCreateFailure(err: String?) { cont.cancel(RuntimeException(err)) }
                    override fun onSetFailure(err: String?) {}
                }, MediaConstraints())
            }

            // Set local description
            suspendCancellableCoroutine<Unit> { cont ->
                pc.setLocalDescription(object : SdpObserver {
                    override fun onCreateSuccess(desc: SessionDescription?) {}
                    override fun onSetSuccess() { cont.resume(Unit, null) }
                    override fun onCreateFailure(err: String?) {}
                    override fun onSetFailure(err: String?) { cont.cancel(RuntimeException(err)) }
                }, offerSdp)
            }

            // Wait for candidate gathering or 2 s timeout (non-trickle)
            withContext(Dispatchers.IO) {
                gatheringLatch.await(2000, TimeUnit.MILLISECONDS)
            }

            val finalLocalSdp = pc.localDescription?.description ?: offerSdp.description

            // 3. POST /api/rtc/{camera_id}/offers {"sdp", "purpose": "live"} -> 201 {"offer_id"}
            val offerId = repo.createRtcOffer(cameraId, finalLocalSdp)
            Log.d(tag, "RTC offer posted: $offerId")

            // 4. Poll GET /api/rtc/offers/{offer_id} every 1s for answer
            val until = System.currentTimeMillis() + 30000
            var answerSdp: String? = null
            while (System.currentTimeMillis() < until && !isClosed.get()) {
                delay(1000)
                answerSdp = repo.pollRtcAnswer(offerId)
                if (answerSdp != null) break
            }

            if (answerSdp == null) {
                throw TimeoutException("RTC Answer timed out after 30 s")
            }

            // 5. Set remote description
            val remoteDesc = SessionDescription(SessionDescription.Type.ANSWER, answerSdp)
            suspendCancellableCoroutine<Unit> { cont ->
                pc.setRemoteDescription(object : SdpObserver {
                    override fun onCreateSuccess(desc: SessionDescription?) {}
                    override fun onSetSuccess() {
                        Log.d(tag, "Remote description set successfully")
                        cont.resume(Unit, null)
                    }
                    override fun onCreateFailure(err: String?) {}
                    override fun onSetFailure(err: String?) { cont.cancel(RuntimeException(err)) }
                }, remoteDesc)
            }

        } catch (e: CancellationException) {
            cleanupPeerConnection()
            throw e
        } catch (e: Exception) {
            Log.w(tag, "WebRTC live connection failed: ${e.message}")
            if (isClosed.get()) {
                cleanupPeerConnection()
            } else {
                _connectionState.value = RtcConnectionState.FAILED
                reconnect()
            }
        }
    }

    /** Stops the stream for good and releases the connection. */
    fun stop() {
        if (isClosed.getAndSet(true)) return
        connectionJob?.cancel()
        _connectionState.value = RtcConnectionState.CLOSED
        _videoTrack.value = null
        cleanupPeerConnection()
        scope.cancel()
    }

    /** Closes and disposes the current peer connection, if any. */
    private fun cleanupPeerConnection() {
        try {
            peerConnection?.close()
            peerConnection?.dispose()
        } catch (e: Exception) {
            // Already closed
        }
        peerConnection = null
    }
}
