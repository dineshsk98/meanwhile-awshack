// The Ask sheet, where you talk or type to Meanwhile and hear its replies.
package com.meanwhile.android.ui.ask

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.VolumeOff
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SheetState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.meanwhile.android.model.ChatMessage
import com.meanwhile.android.model.MessageSender
import com.meanwhile.android.ui.theme.MeanwhileAlertColors
import com.meanwhile.android.ui.theme.MeanwhileDeepTeal

/** The Ask sheet: the conversation with Meanwhile, a text box, the mic button and the speak-replies switch. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AskBottomSheet(
    sheetState: SheetState,
    messages: List<ChatMessage>,
    isListening: Boolean,
    isProcessing: Boolean,
    speakReplies: Boolean,
    speechUnavailable: Boolean,
    onToggleSpeak: () -> Unit,
    onDismissRequest: () -> Unit,
    onSendUtterance: (String) -> Unit,
    onToggleMic: () -> Unit,
    onSelectOption: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    var inputText by remember { mutableStateOf("") }

    ModalBottomSheet(
        onDismissRequest = onDismissRequest,
        sheetState = sheetState,
        modifier = modifier.fillMaxHeight(0.92f),
        containerColor = MaterialTheme.colorScheme.surface
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp)
        ) {
            // Header
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Text(
                        text = "Meanwhile Assistant",
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    Text(
                        text = "Voice MCP Home Agent",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onToggleSpeak) {
                        Icon(
                            imageVector = if (speakReplies) Icons.AutoMirrored.Filled.VolumeUp else Icons.AutoMirrored.Filled.VolumeOff,
                            contentDescription = if (speakReplies) "Mute spoken replies" else "Speak replies"
                        )
                    }
                    IconButton(onClick = onDismissRequest) {
                        Icon(imageVector = Icons.Default.Close, contentDescription = "Close")
                    }
                }
            }
            if (speakReplies && speechUnavailable) {
                Text(
                    text = "No text-to-speech engine is available on this phone, so replies are text only.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error
                )
            }

            Spacer(modifier = Modifier.height(8.dp))

            // Conversation history list
            LazyColumn(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                contentPadding = PaddingValues(vertical = 8.dp)
            ) {
                items(messages) { message ->
                    ChatMessageBubble(
                        message = message,
                        onOptionSelected = onSelectOption
                    )
                }

                if (isProcessing) {
                    item {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier
                                .clip(RoundedCornerShape(12.dp))
                                .background(MaterialTheme.colorScheme.surfaceVariant)
                                .padding(horizontal = 14.dp, vertical = 10.dp)
                        ) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(16.dp),
                                strokeWidth = 2.dp,
                                color = MeanwhileDeepTeal
                            )
                            Spacer(modifier = Modifier.width(10.dp))
                            Text(
                                text = "One moment.",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
            }

            // Example phrases, plain text so they never read as the user's own data
            Text(
                text = "Try saying “Keep an eye on my lamp”, “Take notes of who comes in” or “What did I miss?”",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontStyle = FontStyle.Italic,
                modifier = Modifier.padding(vertical = 8.dp)
            )

            // Input bar with text and large mic button with listening animation
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                OutlinedTextField(
                    value = inputText,
                    onValueChange = { inputText = it },
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("Type or tap mic to speak...") },
                    singleLine = true,
                    shape = RoundedCornerShape(24.dp),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = {
                        if (inputText.isNotBlank()) {
                            onSendUtterance(inputText)
                            inputText = ""
                        }
                    }),
                    trailingIcon = {
                        if (inputText.isNotBlank()) {
                            IconButton(onClick = {
                                onSendUtterance(inputText)
                                inputText = ""
                            }) {
                                Icon(
                                    imageVector = Icons.AutoMirrored.Filled.Send,
                                    contentDescription = "Send",
                                    tint = MeanwhileDeepTeal
                                )
                            }
                        }
                    }
                )

                Spacer(modifier = Modifier.width(10.dp))

                VoiceMicButton(
                    isListening = isListening,
                    onClick = onToggleMic
                )
            }
        }
    }
}

/** One message bubble, with camera chips under a clarifying question. */
@Composable
fun ChatMessageBubble(
    message: ChatMessage,
    onOptionSelected: (String) -> Unit
) {
    val isUser = message.sender == MessageSender.USER

    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = if (isUser) Alignment.End else Alignment.Start
    ) {
        Box(
            modifier = Modifier
                .clip(
                    RoundedCornerShape(
                        topStart = 16.dp,
                        topEnd = 16.dp,
                        bottomStart = if (isUser) 16.dp else 4.dp,
                        bottomEnd = if (isUser) 4.dp else 16.dp
                    )
                )
                .background(
                    if (isUser)
                        MeanwhileDeepTeal
                    else
                        MaterialTheme.colorScheme.surfaceVariant
                )
                .padding(horizontal = 14.dp, vertical = 10.dp)
        ) {
            Text(
                text = message.text,
                color = if (isUser) Color.White else MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodyLarge
            )
        }

        // Camera choices for a clarifying question
        if (message.clarificationOptions.isNotEmpty()) {
            Spacer(modifier = Modifier.height(6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                message.clarificationOptions.forEach { opt ->
                    FilterChip(
                        selected = false,
                        onClick = { onOptionSelected(opt) },
                        label = { Text(opt) }
                    )
                }
            }
        }
    }
}

/** The round mic button; it pulses and turns red while listening. */
@Composable
fun VoiceMicButton(
    isListening: Boolean,
    onClick: () -> Unit
) {
    val transition = rememberInfiniteTransition(label = "pulse")
    val scale by transition.animateFloat(
        initialValue = 1f,
        targetValue = if (isListening) 1.25f else 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(600, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "micScale"
    )

    Box(
        modifier = Modifier
            .size(52.dp)
            .clip(CircleShape)
            .background(
                if (isListening)
                    MeanwhileDeepTeal.copy(alpha = 0.3f)
                else
                    Color.Transparent
            ),
        contentAlignment = Alignment.Center
    ) {
        IconButton(
            onClick = onClick,
            modifier = Modifier
                .size(46.dp)
                .clip(CircleShape)
                .background(if (isListening) MeanwhileAlertColors.AlertRed else MeanwhileDeepTeal)
        ) {
            Icon(
                imageVector = Icons.Default.Mic,
                contentDescription = if (isListening) "Listening" else "Speak",
                tint = Color.White
            )
        }
    }
}
