// The Settings screen: device pairing, the hub connection and notification switches.
package com.meanwhile.android.ui.settings

import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.meanwhile.android.BuildConfig
import com.meanwhile.android.data.MeanwhileRepository
import com.meanwhile.android.data.PreferencesManager
import com.meanwhile.android.model.PairedDevice
import com.meanwhile.android.model.PairingCode
import com.meanwhile.android.ui.components.QrCodeGenerator
import com.meanwhile.android.ui.theme.MeanwhileDeepTeal
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** Settings: pair another device, the paired devices, the hub, the wake word, notification switches, the tutorial and past sessions. */
@Composable
fun SettingsScreen(
    prefs: PreferencesManager,
    repo: MeanwhileRepository,
    onBackClick: () -> Unit,
    onRePairClick: () -> Unit,
    onPastSessionsClick: () -> Unit,
    onShowTutorialClick: () -> Unit,
    wakeWordEnabled: Boolean,
    onWakeWordChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier
) {
    val scrollState = rememberScrollState()
    val coroutineScope = rememberCoroutineScope()

    var headsUpNotif by remember { mutableStateOf(prefs.headsUpNotifications) }
    var verdictNotif by remember { mutableStateOf(prefs.verdictNotifications) }
    var alertNotif by remember { mutableStateOf(prefs.alertNotifications) }
    var wearMirror by remember { mutableStateOf(prefs.wearOsMirroring) }

    var showPairDialog by remember { mutableStateOf(false) }
    var revokeTargetDevice by remember { mutableStateOf<PairedDevice?>(null) }

    val pairedDevicesFlow = remember(repo) { repo.observePairedDevices() }
    val pairedDevices by pairedDevicesFlow.collectAsState(initial = emptyList())

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(scrollState)
            .padding(16.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(bottom = 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            IconButton(onClick = onBackClick) {
                Icon(imageVector = Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
            }
            Text(
                text = "Settings",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold
            )
        }

        Text(
            text = "Device Pairing",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            color = MeanwhileDeepTeal
        )
        Spacer(modifier = Modifier.height(8.dp))
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
        ) {
            Column(modifier = Modifier.padding(14.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = "Pair a New Device",
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold
                        )
                        Text(
                            text = "Generate a single-use QR code to connect another phone, tablet or browser securely without revealing the master key.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    Spacer(modifier = Modifier.width(12.dp))
                    Button(
                        onClick = { showPairDialog = true },
                        colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal)
                    ) {
                        Icon(imageVector = Icons.Default.QrCode, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(modifier = Modifier.width(6.dp))
                        Text("Pair")
                    }
                }
            }
        }

        Spacer(modifier = Modifier.height(16.dp))

        Text(
            text = "Paired Devices (${pairedDevices.size})",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            color = MeanwhileDeepTeal
        )
        Spacer(modifier = Modifier.height(8.dp))
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
        ) {
            Column(modifier = Modifier.padding(14.dp)) {
                if (pairedDevices.isEmpty()) {
                    Text(
                        text = "No other paired devices. Tap 'Pair' above to connect new devices.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.outline,
                        modifier = Modifier.padding(vertical = 8.dp)
                    )
                } else {
                    pairedDevices.forEachIndexed { index, device ->
                        if (index > 0) HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                modifier = Modifier.weight(1f)
                            ) {
                                Box(
                                    modifier = Modifier
                                        .size(36.dp)
                                        .clip(CircleShape)
                                        .background(MeanwhileDeepTeal.copy(alpha = 0.12f)),
                                    contentAlignment = Alignment.Center
                                ) {
                                    Icon(
                                        imageVector = if (device.deviceName.contains("Tablet", true)) Icons.Default.Tablet else Icons.Default.Smartphone,
                                        contentDescription = null,
                                        tint = MeanwhileDeepTeal,
                                        modifier = Modifier.size(20.dp)
                                    )
                                }
                                Spacer(modifier = Modifier.width(10.dp))
                                Column {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text(
                                            text = device.deviceName,
                                            style = MaterialTheme.typography.bodyMedium,
                                            fontWeight = FontWeight.SemiBold
                                        )
                                        if (device.current) {
                                            Spacer(modifier = Modifier.width(6.dp))
                                            Text(
                                                text = "(This device)",
                                                style = MaterialTheme.typography.labelSmall,
                                                color = MeanwhileDeepTeal,
                                                fontWeight = FontWeight.Medium
                                            )
                                        }
                                    }
                                    Text(
                                        text = device.lastSeen
                                            ?.let { com.meanwhile.android.util.DateTimeUtils.formatTime(it, pattern = "d MMM, HH:mm") }
                                            ?.takeIf { it.isNotBlank() }
                                            ?.let { "Last active $it" } ?: "Paired device",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant
                                    )
                                }
                            }

                            if (!device.current) {
                                TextButton(onClick = { revokeTargetDevice = device }) {
                                    Text("Revoke", color = MaterialTheme.colorScheme.error)
                                }
                            }
                        }
                    }
                }
            }
        }

        Spacer(modifier = Modifier.height(20.dp))

        Text(
            text = "Home Hub Connection",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            color = MeanwhileDeepTeal
        )
        Spacer(modifier = Modifier.height(8.dp))
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
        ) {
            Column(modifier = Modifier.padding(14.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column {
                        Text(text = "Hub Address", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                        Text(text = prefs.getCoreBaseUrl(), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    OutlinedButton(onClick = onRePairClick) {
                        Text("Re-pair")
                    }
                }
            }
        }

        Spacer(modifier = Modifier.height(20.dp))

        Text(
            text = "Voice",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            color = MeanwhileDeepTeal
        )
        Spacer(modifier = Modifier.height(8.dp))
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
        ) {
            Column(modifier = Modifier.padding(14.dp)) {
                NotificationToggleRow(
                    title = "Wake word \"${BuildConfig.WAKE_WORD}\"",
                    subtitle = "While the app is open, say \"${BuildConfig.WAKE_WORD}, …\" to ask hands-free",
                    checked = wakeWordEnabled,
                    onCheckedChange = onWakeWordChange
                )
            }
        }

        Spacer(modifier = Modifier.height(20.dp))

        Text(
            text = "Notifications (Phone & Wear OS)",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            color = MeanwhileDeepTeal
        )
        Spacer(modifier = Modifier.height(8.dp))
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
        ) {
            Column(modifier = Modifier.padding(14.dp)) {
                NotificationToggleRow(
                    title = "Heads-ups (Amber)",
                    subtitle = "When an object is handled or moved",
                    checked = headsUpNotif,
                    onCheckedChange = { headsUpNotif = it; prefs.headsUpNotifications = it }
                )
                HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                NotificationToggleRow(
                    title = "Verdicts (Green / Red)",
                    subtitle = "When an item is returned or taken",
                    checked = verdictNotif,
                    onCheckedChange = { verdictNotif = it; prefs.verdictNotifications = it }
                )
                HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                NotificationToggleRow(
                    title = "Alerts (Red)",
                    subtitle = "When an item is taken away or a watch spots what you asked for",
                    checked = alertNotif,
                    onCheckedChange = { alertNotif = it; prefs.alertNotifications = it }
                )
                HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                NotificationToggleRow(
                    title = "Wear OS Mirroring",
                    subtitle = "Mirror glanceable alerts to watch",
                    checked = wearMirror,
                    onCheckedChange = { wearMirror = it; prefs.wearOsMirroring = it }
                )
            }
        }

        Spacer(modifier = Modifier.height(20.dp))

        Card(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { onShowTutorialClick() },
            shape = RoundedCornerShape(12.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Text(text = "How Meanwhile works", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Text(text = "Replay the quick tutorial", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Icon(imageVector = Icons.Default.ChevronRight, contentDescription = null, tint = MaterialTheme.colorScheme.outline)
            }
        }

        Spacer(modifier = Modifier.height(12.dp))

        Card(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { onPastSessionsClick() },
            shape = RoundedCornerShape(12.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Text(text = "Past Sessions", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Text(text = "Archive of completed watches and verdicts", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Icon(imageVector = Icons.Default.ChevronRight, contentDescription = null, tint = MaterialTheme.colorScheme.outline)
            }
        }

        Spacer(modifier = Modifier.height(40.dp))
    }

    if (showPairDialog) {
        PairDeviceDialog(
            repo = repo,
            onDismiss = { showPairDialog = false }
        )
    }

    if (revokeTargetDevice != null) {
        val target = revokeTargetDevice!!
        AlertDialog(
            onDismissRequest = { revokeTargetDevice = null },
            title = { Text("Revoke Paired Device?") },
            text = {
                Text("Revoking '${target.deviceName}' will immediately invalidate its access token. It will no longer be able to connect to Meanwhile without pairing again.")
            },
            confirmButton = {
                Button(
                    onClick = {
                        val devId = target.displayId
                        revokeTargetDevice = null
                        coroutineScope.launch {
                            repo.revokePairedDevice(devId)
                        }
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error)
                ) {
                    Text("Revoke")
                }
            },
            dismissButton = {
                TextButton(onClick = { revokeTargetDevice = null }) {
                    Text("Cancel")
                }
            }
        )
    }
}

/** Shows a one-time pairing QR code with its countdown; the code itself only on request. */
@Composable
fun PairDeviceDialog(
    repo: MeanwhileRepository,
    onDismiss: () -> Unit
) {
    var pairingCode by remember { mutableStateOf<PairingCode?>(null) }
    var qrBitmap by remember { mutableStateOf<Bitmap?>(null) }
    var secondsRemaining by remember { mutableIntStateOf(0) }
    var isLoading by remember { mutableStateOf(true) }
    var errorText by remember { mutableStateOf<String?>(null) }
    var showCode by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        try {
            isLoading = true
            val code = repo.createPairingCode()
            // Count down from the core's own lifetime for the code (expires_in_s, else expires_at)
            secondsRemaining = code.expiresInS?.toInt()
                ?: code.expiresAt?.let {
                    try { java.time.Duration.between(java.time.Instant.now(), java.time.Instant.parse(it)).seconds.toInt() }
                    catch (e: Exception) { null }
                }
                ?: 0
            pairingCode = code
            qrBitmap = QrCodeGenerator.generateQrBitmap(code.pairUrl, size = 480)
            isLoading = false
        } catch (e: Exception) {
            isLoading = false
            errorText = e.message ?: "Failed to generate pairing code"
        }
    }

    // Countdown until the code expires
    LaunchedEffect(pairingCode) {
        if (pairingCode != null) {
            showCode = false
            while (secondsRemaining > 0) {
                delay(1000)
                secondsRemaining--
            }
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text("Pair a New Device", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                if (isLoading) {
                    Box(modifier = Modifier.height(240.dp), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator(color = MeanwhileDeepTeal)
                    }
                } else if (errorText != null) {
                    Text(
                        text = errorText!!,
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodyMedium
                    )
                } else if (pairingCode != null && secondsRemaining > 0) {
                    if (qrBitmap != null) {
                        Box(
                            modifier = Modifier
                                .size(210.dp)
                                .clip(RoundedCornerShape(12.dp))
                                .background(Color.White)
                                .padding(8.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Image(
                                bitmap = qrBitmap!!.asImageBitmap(),
                                contentDescription = "Pairing QR Code",
                                modifier = Modifier.fillMaxSize()
                            )
                        }
                    }

                    // The one-time code stays hidden (it pairs a device until it expires); shown only on request
                    if (showCode) {
                        Text(
                            text = pairingCode!!.code,
                            style = MaterialTheme.typography.titleMedium.copy(fontFamily = FontFamily.Monospace),
                            color = MeanwhileDeepTeal
                        )
                    } else {
                        TextButton(onClick = { showCode = true }) { Text("Can't scan? Show the code") }
                    }

                    val minutes = secondsRemaining / 60
                    val seconds = secondsRemaining % 60
                    val timerString = String.format("%02d:%02d", minutes, seconds)
                    Text(
                        text = "Code expires in $timerString",
                        style = MaterialTheme.typography.labelLarge,
                        fontWeight = FontWeight.SemiBold,
                        color = if (secondsRemaining < 30) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant
                    )

                    Text(
                        text = "On your new device, open Meanwhile and tap 'Scan to Pair', or visit the link in a browser.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                } else {
                    Text(
                        text = "Pairing code expired. Close and reopen to generate a fresh one.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.error
                    )
                }
            }
        },
        confirmButton = {
            Button(
                onClick = onDismiss,
                colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal)
            ) {
                Text("Done")
            }
        }
    )
}

/** One settings switch with its title and subtitle. */
@Composable
fun NotificationToggleRow(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(text = title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
            Text(text = subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            colors = SwitchDefaults.colors(checkedThumbColor = MeanwhileDeepTeal)
        )
    }
}
