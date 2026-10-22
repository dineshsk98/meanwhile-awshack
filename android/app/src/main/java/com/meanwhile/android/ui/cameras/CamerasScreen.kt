// The Cameras tab: every camera as a tile (rename or remove it from its menu) and a live view sheet for one camera.
package com.meanwhile.android.ui.cameras

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
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.material.icons.filled.VideocamOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import kotlinx.coroutines.launch
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.meanwhile.android.data.MeanwhileRepository
import com.meanwhile.android.model.Camera
import com.meanwhile.android.model.LiveBox
import com.meanwhile.android.model.Session
import com.meanwhile.android.ui.components.LiveBoxOverlay
import com.meanwhile.android.ui.components.LiveCameraStreamView
import com.meanwhile.android.ui.components.MediaImage
import com.meanwhile.android.ui.theme.MeanwhileAlertColors

/** The Cameras tab: a grid of camera tiles; tapping an online one opens its live view. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CamerasScreen(
    cameras: List<Camera>,
    session: Session?,
    repo: MeanwhileRepository,
    modifier: Modifier = Modifier
) {
    var selectedLiveCamera by remember { mutableStateOf<Camera?>(null) }
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var renaming by remember { mutableStateOf<Camera?>(null) }
    var removing by remember { mutableStateOf<Camera?>(null) }
    var actionError by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }

    /** Run a camera action against the core, closing its dialog on success and showing the core's message on failure. */
    fun runAction(action: suspend () -> Unit) {
        busy = true
        actionError = null
        scope.launch {
            try {
                action()
                renaming = null
                removing = null
            } catch (e: Exception) {
                actionError = e.message ?: "That didn't work. Please try again."
            } finally {
                busy = false
            }
        }
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        Text(
            text = "Home Cameras",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurface
        )
        Text(
            text = "Phones and laptops running the Meanwhile camera page",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )

        Spacer(modifier = Modifier.height(16.dp))

        LazyVerticalGrid(
            columns = GridCells.Fixed(2),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            contentPadding = PaddingValues(bottom = 80.dp),
            modifier = Modifier.fillMaxSize()
        ) {
            items(cameras) { camera ->
                CameraGridTile(
                    camera = camera,
                    liveBox = if (session?.liveBox?.cameraId == camera.id) session.liveBox else null,
                    onClick = {
                        if (camera.online) {
                            selectedLiveCamera = camera
                        }
                    },
                    onRename = { actionError = null; renaming = camera },
                    onRemove = { actionError = null; removing = camera }
                )
            }
        }
    }

    renaming?.let { camera ->
        RenameCameraDialog(
            camera = camera,
            error = actionError,
            busy = busy,
            onConfirm = { label -> runAction { repo.renameCamera(camera.id, label) } },
            onDismiss = { renaming = null }
        )
    }
    removing?.let { camera ->
        RemoveCameraDialog(
            camera = camera,
            error = actionError,
            busy = busy,
            onConfirm = { runAction { repo.removeCamera(camera.id) } },
            onDismiss = { removing = null }
        )
    }

    if (selectedLiveCamera != null) {
        ModalBottomSheet(
            onDismissRequest = { selectedLiveCamera = null },
            sheetState = sheetState,
            containerColor = Color.Black
        ) {
            LiveCameraViewSheet(
                camera = selectedLiveCamera!!,
                allCameras = cameras.filter { it.online },
                liveBox = if (session?.liveBox?.cameraId == selectedLiveCamera?.id) session?.liveBox else null,
                repo = repo,
                onSelectCamera = { selectedLiveCamera = it },
                onClose = { selectedLiveCamera = null }
            )
        }
    }
}

/** One camera tile: its snapshot with the live box, online state, last seen time and a Rename / Remove menu. */
@Composable
fun CameraGridTile(
    camera: Camera,
    liveBox: LiveBox?,
    onClick: () -> Unit,
    onRename: () -> Unit,
    onRemove: () -> Unit
) {
    val alpha = if (camera.online) 1.0f else 0.5f

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = camera.online) { onClick() },
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = if (camera.online) 0.5f else 0.25f)
        )
    ) {
        Column {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(16f / 9f)
                    .background(Color.DarkGray)
            ) {
                var thumbAspect by remember(camera.id) { mutableFloatStateOf(16f / 9f) }
                if (camera.thumbUrl != null) {
                    MediaImage(
                        url = camera.thumbUrl,
                        contentDescription = camera.label,
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Fit,
                        alpha = alpha,
                        onSuccess = { state ->
                            val w = state.result.drawable.intrinsicWidth
                            val h = state.result.drawable.intrinsicHeight
                            if (w > 0 && h > 0) thumbAspect = w.toFloat() / h.toFloat()
                        }
                    )
                } else {
                    Icon(
                        imageVector = if (camera.online) Icons.Default.Videocam else Icons.Default.VideocamOff,
                        contentDescription = null,
                        tint = Color.LightGray,
                        modifier = Modifier
                            .align(Alignment.Center)
                            .size(36.dp)
                    )
                }

                if (liveBox != null && liveBox.cameraId == camera.id) {
                    LiveBoxOverlay(
                        liveBox = liveBox,
                        frameAspectRatio = thumbAspect
                    )
                }

                // Online indicator tag
                Row(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(8.dp)
                        .clip(RoundedCornerShape(6.dp))
                        .background(Color.Black.copy(alpha = 0.7f))
                        .padding(horizontal = 6.dp, vertical = 3.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        modifier = Modifier
                            .size(6.dp)
                            .clip(CircleShape)
                            .background(
                                if (camera.online)
                                    MeanwhileAlertColors.VerdictGreen
                                else
                                    Color.Gray
                            )
                    )
                    Spacer(modifier = Modifier.width(4.dp))
                    Text(
                        text = if (camera.online) "Online" else "Offline",
                        color = Color.White,
                        style = MaterialTheme.typography.labelSmall
                    )
                }
            }

            Row(
                modifier = Modifier.padding(start = 12.dp, top = 12.dp, bottom = 12.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = camera.label,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    Spacer(modifier = Modifier.height(2.dp))
                    Text(
                        text = camera.lastSeenTime?.let { "last seen $it" } ?: if (camera.online) "online" else "offline",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                var menuOpen by remember { mutableStateOf(false) }
                Box {
                    IconButton(onClick = { menuOpen = true }) {
                        Icon(Icons.Default.MoreVert, contentDescription = "Camera options for ${camera.label}")
                    }
                    DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                        DropdownMenuItem(text = { Text("Rename") }, onClick = { menuOpen = false; onRename() })
                        DropdownMenuItem(text = { Text("Remove") }, onClick = { menuOpen = false; onRemove() })
                    }
                }
            }
        }
    }
}

/** Ask for a camera's new label; shows the core's refusal (e.g. the label is taken) under the field. */
@Composable
fun RenameCameraDialog(
    camera: Camera,
    error: String?,
    busy: Boolean,
    onConfirm: (String) -> Unit,
    onDismiss: () -> Unit
) {
    var label by remember(camera.id) { mutableStateOf(camera.label) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Rename camera") },
        text = {
            Column {
                OutlinedTextField(
                    value = label,
                    onValueChange = { label = it },
                    label = { Text("Name") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                if (error != null) {
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = {
            TextButton(enabled = !busy && label.isNotBlank(), onClick = { onConfirm(label.trim()) }) { Text("Rename") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

/** Confirm removing a camera; its past events stay, and starting its page again brings it back. */
@Composable
fun RemoveCameraDialog(
    camera: Camera,
    error: String?,
    busy: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Remove ${camera.label}?") },
        text = {
            Column {
                Text("It leaves the list and its page stops. Past events stay. Start the camera page again to add it back.")
                if (error != null) {
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = {
            TextButton(enabled = !busy, onClick = onConfirm) { Text("Remove", color = MaterialTheme.colorScheme.error) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

/** The live view sheet: one camera's video with the live box, and buttons to switch to another online camera. */
@Composable
fun LiveCameraViewSheet(
    camera: Camera,
    allCameras: List<Camera>,
    liveBox: LiveBox?,
    repo: MeanwhileRepository,
    onSelectCamera: (Camera) -> Unit,
    onClose: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(16.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = camera.label,
                color = Color.White,
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold
            )

            IconButton(onClick = onClose) {
                Icon(
                    imageVector = Icons.Default.Close,
                    contentDescription = "Close Live",
                    tint = Color.White
                )
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(16f / 9f)
                .clip(RoundedCornerShape(12.dp))
                .background(Color.Black)
        ) {
            LiveCameraStreamView(
                cameraId = camera.id,
                thumbUrl = camera.thumbUrl,
                repo = repo,
                modifier = Modifier.fillMaxSize(),
                enableLive = camera.online
            ) { _ ->
                if (liveBox != null && liveBox.cameraId == camera.id) {
                    LiveBoxOverlay(liveBox = liveBox)
                }
            }
        }

        Spacer(modifier = Modifier.height(20.dp))

        // Switch to other online cameras
        Text(
            text = "Switch Camera",
            color = Color.LightGray,
            style = MaterialTheme.typography.labelMedium
        )
        Spacer(modifier = Modifier.height(8.dp))
        Row(
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            allCameras.forEach { cam ->
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(8.dp))
                        .background(
                            if (cam.id == camera.id)
                                MeanwhileAlertColors.VerdictGreenContainer
                            else
                                Color.DarkGray
                        )
                        .clickable { onSelectCamera(cam) }
                        .padding(horizontal = 14.dp, vertical = 8.dp)
                ) {
                    Text(
                        text = cam.label,
                        color = if (cam.id == camera.id)
                            MeanwhileAlertColors.VerdictOnGreenContainer
                        else
                            Color.White,
                        style = MaterialTheme.typography.bodyMedium
                    )
                }
            }
        }

        Spacer(modifier = Modifier.height(30.dp))
    }
}
