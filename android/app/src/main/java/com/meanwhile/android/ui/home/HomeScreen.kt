// The Home tab: what is being watched right now and the cameras.
package com.meanwhile.android.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.meanwhile.android.data.MeanwhileRepository
import com.meanwhile.android.model.Camera
import com.meanwhile.android.model.Event
import com.meanwhile.android.model.LiveBox
import com.meanwhile.android.model.Session
import com.meanwhile.android.ui.components.LiveBoxOverlay
import com.meanwhile.android.ui.components.LiveCameraStreamView
import com.meanwhile.android.ui.components.MediaImage
import com.meanwhile.android.ui.components.StatusChip
import com.meanwhile.android.ui.theme.MeanwhileAlertColors
import com.meanwhile.android.ui.theme.MeanwhileDeepTeal

/** The Home tab: the active session with its live camera (or an idle card), and the camera strip. */
@Composable
fun HomeScreen(
    session: Session?,
    latestEvent: Event?,
    cameras: List<Camera>,
    lagSeconds: Int?,
    repo: MeanwhileRepository,
    onImBackClick: () -> Unit,
    onShowTutorialClick: () -> Unit,
    onCameraClick: (Camera) -> Unit,
    modifier: Modifier = Modifier
) {
    val scrollState = rememberScrollState()

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(scrollState)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // The active session, or the idle card
        if (session != null && session.active) {
            ElevatedCard(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.elevatedCardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f)
                )
            ) {
                Column(modifier = Modifier.padding(18.dp)) {
                    Text(
                        text = "\"${session.instruction}\"",
                        style = MaterialTheme.typography.titleLarge,
                        color = MaterialTheme.colorScheme.onSurface,
                        fontWeight = FontWeight.SemiBold
                    )
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(
                        text = session.comprehensionSummary,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )

                    Spacer(modifier = Modifier.height(14.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        StatusChip(
                            state = session.objectState,
                            actorName = session.stateActor
                        )

                        Button(
                            onClick = onImBackClick,
                            colors = ButtonDefaults.buttonColors(
                                containerColor = MeanwhileDeepTeal
                            ),
                            shape = RoundedCornerShape(10.dp)
                        ) {
                            Text("I'm back")
                        }
                    }

                    // The focus camera, live, with the object's box
                    val activeCamera = cameras.find { it.id == (session.liveBox?.cameraId ?: session.focusCameraId) }
                    if (activeCamera != null) {
                        Spacer(modifier = Modifier.height(14.dp))
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .aspectRatio(16f / 9f)
                                .clip(RoundedCornerShape(12.dp))
                                .background(Color.Black)
                                .clickable { onCameraClick(activeCamera) }
                        ) {
                            LiveCameraStreamView(
                                cameraId = activeCamera.id,
                                thumbUrl = activeCamera.thumbUrl,
                                repo = repo,
                                modifier = Modifier.fillMaxSize(),
                                enableLive = activeCamera.online
                            ) { _ ->
                                if (session.liveBox != null && session.liveBox.cameraId == activeCamera.id) {
                                    LiveBoxOverlay(liveBox = session.liveBox)
                                }
                            }

                            Row(
                                modifier = Modifier
                                    .align(Alignment.BottomStart)
                                    .padding(8.dp)
                                    .clip(RoundedCornerShape(6.dp))
                                    .background(Color.Black.copy(alpha = 0.65f))
                                    .padding(horizontal = 8.dp, vertical = 4.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Box(
                                    modifier = Modifier
                                        .size(6.dp)
                                        .clip(CircleShape)
                                        .background(
                                            when {
                                                session.liveBox?.missing == true -> MeanwhileAlertColors.AlertRed
                                                session.liveBox?.held == true -> MeanwhileAlertColors.HeadsUpAmber
                                                else -> MeanwhileAlertColors.VerdictGreen
                                            }
                                        )
                                )
                                Spacer(modifier = Modifier.width(6.dp))
                                Text(
                                    text = "${activeCamera.label} · " + when {
                                        session.liveBox?.missing == true -> "Missing (last seen)"
                                        session.liveBox?.held == true -> "Held"
                                        session.liveBox != null -> "Tracking"
                                        else -> "Live"
                                    },
                                    color = Color.White,
                                    style = MaterialTheme.typography.labelSmall,
                                    fontWeight = FontWeight.Medium
                                )
                            }
                        }
                    }

                    if (latestEvent != null) {
                        Spacer(modifier = Modifier.height(12.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(
                                modifier = Modifier
                                    .size(8.dp)
                                    .clip(CircleShape)
                                    .background(MeanwhileAlertColors.HeadsUpAmber)
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(
                                text = "${latestEvent.summary} · ${latestEvent.formattedTime}",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurface
                            )
                        }
                    }
                }
            }

            // Lag line
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.padding(horizontal = 4.dp)
            ) {
                Icon(
                    imageVector = Icons.Default.Schedule,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.outline,
                    modifier = Modifier.size(16.dp)
                )
                Spacer(modifier = Modifier.width(6.dp))
                Text(
                    text = if (lagSeconds != null) "Watching, $lagSeconds s behind live" else "Watching · lag unknown",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.outline
                )
            }
        } else {
            // Nothing is being watched
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f)
                )
            ) {
                Column(modifier = Modifier.padding(20.dp)) {
                    Text(
                        text = "Nothing is being watched. Tell me what to keep an eye on.",
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = "Try saying “Keep an eye on my lamp” or “Take notes of who comes in”.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        fontStyle = FontStyle.Italic
                    )
                    TextButton(onClick = onShowTutorialClick) {
                        Text("How it works", color = MeanwhileDeepTeal)
                    }
                }
            }
        }

        // Camera strip
        Column {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "Cameras",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurface
                )
            }
            Spacer(modifier = Modifier.height(10.dp))
            LazyRow(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                contentPadding = PaddingValues(horizontal = 2.dp)
            ) {
                items(cameras) { cam ->
                    CameraThumbnailTile(
                        camera = cam,
                        liveBox = if (session?.liveBox?.cameraId == cam.id) session.liveBox else null,
                        onClick = { onCameraClick(cam) }
                    )
                }
            }
        }

        // Room for the pinned Ask bar
        Spacer(modifier = Modifier.height(60.dp))
    }
}

/** A small camera tile for the Home strip: snapshot, live box, online badge, label and last seen time. */
@Composable
fun CameraThumbnailTile(
    camera: Camera,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    liveBox: LiveBox? = null
) {
    Card(
        modifier = modifier
            .width(160.dp)
            .clickable { onClick() },
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f)
        )
    ) {
        Column {
            var thumbAspect by remember(camera.id) { mutableFloatStateOf(16f / 9f) }
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(16f / 9f)
                    .background(Color.Black)
            ) {
                if (camera.thumbUrl != null) {
                    MediaImage(
                        url = camera.thumbUrl,
                        contentDescription = camera.label,
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Fit,
                        onSuccess = { state ->
                            val w = state.result.drawable.intrinsicWidth
                            val h = state.result.drawable.intrinsicHeight
                            if (w > 0 && h > 0) thumbAspect = w.toFloat() / h.toFloat()
                        }
                    )
                } else {
                    Icon(
                        imageVector = Icons.Default.Videocam,
                        contentDescription = null,
                        tint = Color.LightGray,
                        modifier = Modifier
                            .align(Alignment.Center)
                            .size(32.dp)
                    )
                }

                if (liveBox != null && liveBox.cameraId == camera.id) {
                    LiveBoxOverlay(liveBox = liveBox, frameAspectRatio = thumbAspect)
                }

                // Online indicator
                Row(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(6.dp)
                        .clip(RoundedCornerShape(6.dp))
                        .background(Color.Black.copy(alpha = 0.6f))
                        .padding(horizontal = 6.dp, vertical = 2.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        modifier = Modifier
                            .size(6.dp)
                            .clip(CircleShape)
                            .background(
                                if (camera.online) MeanwhileAlertColors.VerdictGreen else Color.Gray
                            )
                    )
                    Spacer(modifier = Modifier.width(4.dp))
                    Text(
                        text = if (camera.online) "Live" else "Off",
                        color = Color.White,
                        style = MaterialTheme.typography.labelSmall
                    )
                }
            }

            Column(modifier = Modifier.padding(10.dp)) {
                Text(
                    text = camera.label,
                    style = MaterialTheme.typography.titleSmall,
                    color = MaterialTheme.colorScheme.onSurface
                )
                Text(
                    text = camera.lastSeenTime?.let { "last seen $it" } ?: if (camera.online) "online" else "offline",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}
