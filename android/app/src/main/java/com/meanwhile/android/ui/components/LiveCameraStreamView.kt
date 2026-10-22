// A camera view that plays live video over WebRTC and falls back to the latest snapshot.
package com.meanwhile.android.ui.components

import android.view.ViewGroup
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.meanwhile.android.data.MeanwhileRepository
import com.meanwhile.android.data.RtcConnectionState
import com.meanwhile.android.data.WebRtcLiveStreamManager
import com.meanwhile.android.ui.theme.MeanwhileAlertColors
import org.webrtc.RendererCommon
import org.webrtc.SurfaceViewRenderer

/** One camera's live WebRTC video, or its latest snapshot until the stream connects; [overlayContent] is drawn over the frame. */
@Composable
fun LiveCameraStreamView(
    cameraId: String,
    thumbUrl: String?,
    repo: MeanwhileRepository,
    modifier: Modifier = Modifier,
    enableLive: Boolean = true,
    overlayContent: @Composable BoxScope.(frameAspectRatio: Float) -> Unit = {}
) {
    val context = LocalContext.current
    var detectedAspectRatio by remember(cameraId) { mutableFloatStateOf(16f / 9f) }

    val streamManager = remember(cameraId, enableLive) {
        if (enableLive) {
            WebRtcLiveStreamManager(context, repo, cameraId)
        } else null
    }

    val rtcState by (streamManager?.connectionState ?: kotlinx.coroutines.flow.MutableStateFlow(RtcConnectionState.CLOSED)).collectAsState()
    val videoTrack by (streamManager?.videoTrack ?: kotlinx.coroutines.flow.MutableStateFlow(null)).collectAsState()

    DisposableEffect(streamManager) {
        streamManager?.start()
        onDispose {
            streamManager?.stop()
        }
    }

    BoxWithConstraints(modifier = modifier.background(Color.Black)) {
        val tileWidth = maxWidth
        val tileHeight = maxHeight
        val tileRatio = if (tileHeight.value > 0f) tileWidth.value / tileHeight.value else (16f / 9f)

        // Sized to the exact shape of the video frame to prevent cropping and misaligned overlays
        Box(
            modifier = Modifier
                .align(Alignment.Center)
                .aspectRatio(
                    detectedAspectRatio,
                    matchHeightConstraintsFirst = (detectedAspectRatio < tileRatio)
                )
        ) {
            if (rtcState == RtcConnectionState.CONNECTED && videoTrack != null) {
                AndroidView(
                    factory = { ctx ->
                        SurfaceViewRenderer(ctx).apply {
                            layoutParams = ViewGroup.LayoutParams(
                                ViewGroup.LayoutParams.MATCH_PARENT,
                                ViewGroup.LayoutParams.MATCH_PARENT
                            )
                            val rendererEvents = object : RendererCommon.RendererEvents {
                                override fun onFirstFrameRendered() {}
                                override fun onFrameResolutionChanged(videoWidth: Int, videoHeight: Int, rotation: Int) {
                                    if (videoWidth > 0 && videoHeight > 0) {
                                        val aspect = if (rotation == 90 || rotation == 270) {
                                            videoHeight.toFloat() / videoWidth.toFloat()
                                        } else {
                                            videoWidth.toFloat() / videoHeight.toFloat()
                                        }
                                        android.os.Handler(android.os.Looper.getMainLooper()).post {
                                            detectedAspectRatio = aspect
                                        }
                                    }
                                }
                            }
                            init(WebRtcLiveStreamManager.eglBase.eglBaseContext, rendererEvents)
                            setScalingType(RendererCommon.ScalingType.SCALE_ASPECT_FIT)
                            setEnableHardwareScaler(true)
                            videoTrack?.addSink(this)
                        }
                    },
                    update = { renderer ->
                        videoTrack?.addSink(renderer)
                    },
                    onRelease = { renderer ->
                        videoTrack?.removeSink(renderer)
                        renderer.release()
                    },
                    modifier = Modifier.fillMaxSize()
                )
            } else {
                // Latest snapshot until live video arrives
                if (!thumbUrl.isNullOrBlank()) {
                    MediaImage(
                        url = thumbUrl,
                        contentDescription = "Camera $cameraId",
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Fit,
                        onSuccess = { state ->
                            val w = state.result.drawable.intrinsicWidth
                            val h = state.result.drawable.intrinsicHeight
                            if (w > 0 && h > 0) {
                                detectedAspectRatio = w.toFloat() / h.toFloat()
                            }
                        }
                    )
                } else {
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .background(Color.Black.copy(alpha = 0.85f)),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            text = "Camera Offline",
                            color = Color.LightGray,
                            style = MaterialTheme.typography.bodyMedium
                        )
                    }
                }
            }

            // Drawn inside the frame-shaped box so the overlay lines up with the video
            overlayContent(detectedAspectRatio)
        }

        Box(
            modifier = Modifier
                .padding(8.dp)
                .align(Alignment.TopStart)
                .clip(RoundedCornerShape(6.dp))
                .background(
                    when (rtcState) {
                        RtcConnectionState.CONNECTED -> MeanwhileAlertColors.VerdictGreen
                        RtcConnectionState.CONNECTING -> MeanwhileAlertColors.HeadsUpAmber
                        else -> Color.Black.copy(alpha = 0.6f)
                    }
                )
                .padding(horizontal = 6.dp, vertical = 2.dp)
        ) {
            Text(
                text = when (rtcState) {
                    RtcConnectionState.CONNECTED -> "LIVE"
                    RtcConnectionState.CONNECTING -> "CONNECTING LIVE..."
                    else -> "SNAPSHOT"
                },
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold,
                color = Color.White
            )
        }
    }
}
