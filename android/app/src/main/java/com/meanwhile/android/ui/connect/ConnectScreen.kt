// The pairing screen: connects the app to a hub by QR code, one-time code, or a typed address and key.
package com.meanwhile.android.ui.connect

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.meanwhile.android.data.MeanwhileRepository
import com.meanwhile.android.ui.components.QrCodeScannerView
import com.meanwhile.android.ui.theme.MeanwhileAlertColors
import com.meanwhile.android.ui.theme.MeanwhileDeepTeal
import com.meanwhile.android.util.QrLinkValidator
import kotlinx.coroutines.launch

/** Where pairing is: scanning, waiting for the hub, paired, or failed. */
enum class ConnectionState {
    SCANNING,
    CONNECTING,
    CONNECTED,
    FAILED
}

/** The pairing screen: scan a hub's QR code, type a one-time code, or (advanced) enter a hub address and key. */
@Composable
fun ConnectScreen(
    currentAddress: String,
    currentKey: String,
    repo: MeanwhileRepository,
    onConnectSuccess: (address: String, key: String, useHttps: Boolean?) -> Unit,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()

    var connectionState by remember { mutableStateOf(ConnectionState.SCANNING) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    var showManualDialog by remember { mutableStateOf(false) }
    var showPairCodeDialog by remember { mutableStateOf(false) }

    var addressInput by remember { mutableStateOf(currentAddress) }
    var keyInput by remember { mutableStateOf(currentKey) }
    var codeInput by remember { mutableStateOf("") }

    var hasCameraPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        )
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission()
    ) { isGranted ->
        hasCameraPermission = isGranted
    }

    /** Pairs with [targetHub] using [code] and, on success, stores the hub ([hubToStore] if given) and the new token. */
    fun handlePairing(targetHub: String?, code: String, hubToStore: String? = null, useHttps: Boolean? = null) {
        connectionState = ConnectionState.CONNECTING
        errorMessage = null
        coroutineScope.launch {
            try {
                val deviceName = "${Build.MANUFACTURER.replaceFirstChar { it.uppercase() }} ${Build.MODEL}"
                val pairResult = repo.pairWithCode(targetHub, code, deviceName)
                val resolvedAddress = hubToStore ?: targetHub ?: addressInput
                connectionState = ConnectionState.CONNECTED
                onConnectSuccess(resolvedAddress, pairResult.deviceToken, useHttps)
            } catch (e: Exception) {
                connectionState = ConnectionState.FAILED
                errorMessage = e.message ?: "Failed to pair with code. Check if code has expired."
            }
        }
    }

    var pendingPairInfo by remember { mutableStateOf<QrLinkValidator.QrPairInfo?>(null) }
    var showConfirmHubDialog by remember { mutableStateOf(false) }

    /** Reads a scanned QR code and asks to confirm its hub, or shows why it was refused. */
    fun parseQrCode(content: String) {
        try {
            val info = QrLinkValidator.parse(content)
            if (info != null) {
                pendingPairInfo = info
                showConfirmHubDialog = true
                return
            }
            connectionState = ConnectionState.FAILED
            errorMessage = "Invalid pairing QR code. Scan a valid Meanwhile QR code (https://.../pair?code=...)."
        } catch (e: Exception) {
            connectionState = ConnectionState.FAILED
            errorMessage = "Failed to parse QR code."
        }
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text(
            text = "Connect to your Home",
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurface
        )

        Spacer(modifier = Modifier.height(8.dp))

        Text(
            text = "Scan the QR code displayed on your Meanwhile paired device or browser",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )

        Spacer(modifier = Modifier.height(28.dp))

        Box(
            modifier = Modifier
                .size(280.dp)
                .clip(RoundedCornerShape(24.dp))
                .background(Color.Black.copy(alpha = 0.05f))
                .border(3.dp, MeanwhileDeepTeal, RoundedCornerShape(24.dp)),
            contentAlignment = Alignment.Center
        ) {
            when (connectionState) {
                ConnectionState.SCANNING -> {
                    if (hasCameraPermission) {
                        QrCodeScannerView(
                            modifier = Modifier.fillMaxSize(),
                            onQrCodeScanned = { rawQr ->
                                parseQrCode(rawQr)
                            }
                        )
                    } else {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            modifier = Modifier.padding(16.dp)
                        ) {
                            Icon(
                                imageVector = Icons.Default.CameraAlt,
                                contentDescription = "Camera Permission",
                                tint = MeanwhileDeepTeal,
                                modifier = Modifier.size(54.dp)
                            )
                            Spacer(modifier = Modifier.height(12.dp))
                            Text(
                                text = "Camera permission needed",
                                style = MaterialTheme.typography.labelLarge,
                                fontWeight = FontWeight.SemiBold
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                            Button(
                                onClick = { permissionLauncher.launch(Manifest.permission.CAMERA) },
                                colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal),
                                shape = RoundedCornerShape(8.dp)
                            ) {
                                Text("Allow Camera")
                            }
                        }
                    }
                }
                ConnectionState.CONNECTING -> {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        CircularProgressIndicator(
                            color = MeanwhileDeepTeal,
                            modifier = Modifier.size(48.dp)
                        )
                        Spacer(modifier = Modifier.height(14.dp))
                        Text(
                            text = "Pairing with Meanwhile hub...",
                            style = MaterialTheme.typography.bodyMedium
                        )
                    }
                }
                ConnectionState.CONNECTED -> {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(
                            imageVector = Icons.Default.CheckCircle,
                            contentDescription = "Connected",
                            tint = MeanwhileAlertColors.VerdictGreen,
                            modifier = Modifier.size(64.dp)
                        )
                        Spacer(modifier = Modifier.height(12.dp))
                        Text(
                            text = "Paired & Connected",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            color = MeanwhileAlertColors.VerdictGreen
                        )
                    }
                }
                ConnectionState.FAILED -> {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        modifier = Modifier.padding(16.dp)
                    ) {
                        Icon(
                            imageVector = Icons.Default.Error,
                            contentDescription = "Failed",
                            tint = MeanwhileAlertColors.AlertRed,
                            modifier = Modifier.size(54.dp)
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(
                            text = "Pairing Failed",
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold,
                            color = MeanwhileAlertColors.AlertRed
                        )
                        if (!errorMessage.isNullOrBlank()) {
                            Text(
                                text = errorMessage!!,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                        Spacer(modifier = Modifier.height(10.dp))
                        Button(
                            onClick = { connectionState = ConnectionState.SCANNING },
                            colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal)
                        ) {
                            Text("Retry Scan")
                        }
                    }
                }
            }
        }

        Spacer(modifier = Modifier.height(20.dp))

        Row(
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            modifier = Modifier.fillMaxWidth(0.9f)
        ) {
            OutlinedButton(
                onClick = { showPairCodeDialog = true },
                modifier = Modifier.weight(1f),
                shape = RoundedCornerShape(12.dp)
            ) {
                Text("Enter Code")
            }

            Button(
                onClick = {
                    if (hasCameraPermission) {
                        connectionState = ConnectionState.SCANNING
                    } else {
                        permissionLauncher.launch(Manifest.permission.CAMERA)
                    }
                },
                colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.weight(1f)
            ) {
                Icon(imageVector = Icons.Default.QrCodeScanner, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(modifier = Modifier.width(6.dp))
                Text("Scan to Pair")
            }
        }

        Spacer(modifier = Modifier.height(12.dp))

        // Manual hub and key entry
        TextButton(onClick = { showManualDialog = true }) {
            Text(
                text = "Advanced: Enter Hub & Key",
                color = MaterialTheme.colorScheme.outline,
                style = MaterialTheme.typography.labelMedium
            )
        }
    }

    // Confirm the hub from a scanned QR code
    if (showConfirmHubDialog) {
        AlertDialog(
            onDismissRequest = { showConfirmHubDialog = false },
            title = { Text("Connect to Hub") },
            text = {
                Text("Pair this phone with the Meanwhile hub at '${pendingPairInfo?.pairingOrigin ?: ""}'?")
            },
            confirmButton = {
                Button(
                    onClick = {
                        val info = pendingPairInfo
                        showConfirmHubDialog = false
                        if (info != null) {
                            handlePairing(
                                targetHub = info.pairingOrigin,
                                code = info.code,
                                hubToStore = info.hubToStore,
                                useHttps = (info.scheme == "https")
                            )
                        }
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal)
                ) {
                    Text("Pair")
                }
            },
            dismissButton = {
                TextButton(onClick = { showConfirmHubDialog = false }) {
                    Text("Cancel")
                }
            }
        )
    }

    // Enter a one-time pairing code
    if (showPairCodeDialog) {
        AlertDialog(
            onDismissRequest = { showPairCodeDialog = false },
            title = { Text("Pair with One-Time Code") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        text = "Enter the one-time pairing code shown on your paired device's 'Pair a device' screen.",
                        style = MaterialTheme.typography.bodySmall
                    )
                    OutlinedTextField(
                        value = codeInput,
                        onValueChange = { codeInput = it.trim() },
                        label = { Text("Pairing Code") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    OutlinedTextField(
                        value = addressInput,
                        onValueChange = { addressInput = it },
                        label = { Text("Hub Address (Optional)") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                }
            },
            confirmButton = {
                Button(
                    onClick = {
                        if (codeInput.isNotBlank()) {
                            showPairCodeDialog = false
                            handlePairing(addressInput.ifBlank { null }, codeInput)
                        }
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal)
                ) {
                    Text("Pair")
                }
            },
            dismissButton = {
                TextButton(onClick = { showPairCodeDialog = false }) {
                    Text("Cancel")
                }
            }
        )
    }

    // Enter a hub address and key by hand
    if (showManualDialog) {
        AlertDialog(
            onDismissRequest = { showManualDialog = false },
            title = { Text("Manual Hub Connection") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        text = "Direct entry for developer setups without QR pairing.",
                        style = MaterialTheme.typography.bodySmall
                    )
                    OutlinedTextField(
                        value = addressInput,
                        onValueChange = { addressInput = it },
                        label = { Text("Hub Address (IP or hostname)") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    OutlinedTextField(
                        value = keyInput,
                        onValueChange = { keyInput = it },
                        label = { Text("Access Key (X-Meanwhile-Key)") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                }
            },
            confirmButton = {
                Button(
                    onClick = {
                        showManualDialog = false
                        connectionState = ConnectionState.CONNECTING
                        val manualHttps = if (addressInput.startsWith("https://")) true else if (addressInput.startsWith("http://")) false else null
                        onConnectSuccess(addressInput, keyInput, manualHttps)
                        connectionState = ConnectionState.CONNECTED
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal)
                ) {
                    Text("Connect")
                }
            },
            dismissButton = {
                TextButton(onClick = { showManualDialog = false }) {
                    Text("Cancel")
                }
            }
        )
    }
}
