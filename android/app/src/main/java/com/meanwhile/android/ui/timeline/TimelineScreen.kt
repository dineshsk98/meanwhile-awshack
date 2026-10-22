// The Timeline tab: what happened in the current session.
package com.meanwhile.android.ui.timeline

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.RadioButtonChecked
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.meanwhile.android.model.AlertType
import com.meanwhile.android.model.Event
import com.meanwhile.android.model.Session
import com.meanwhile.android.ui.components.MediaImage
import com.meanwhile.android.ui.theme.MeanwhileAlertColors
import com.meanwhile.android.ui.theme.MeanwhileDeepTeal

/** The Timeline tab: the session's events newest first, and "I'm back" while it is active. */
@Composable
fun TimelineScreen(
    session: Session?,
    events: List<Event>,
    onEventClick: (Event) -> Unit,
    onImBackClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp, vertical = 8.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "What you missed",
                    style = MaterialTheme.typography.headlineSmall,
                    color = MaterialTheme.colorScheme.onSurface,
                    fontWeight = FontWeight.Bold
                )
                Spacer(modifier = Modifier.height(4.dp))
                if (session != null) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .size(8.dp)
                                .clip(CircleShape)
                                .background(
                                    if (session.active) MeanwhileAlertColors.VerdictGreen else Color.Gray
                                )
                        )
                        Spacer(modifier = Modifier.width(6.dp))
                        val startTime = session.formattedStartedAt
                        Text(
                            text = if (!startTime.isNullOrBlank()) {
                                "${session.instruction} · since $startTime"
                            } else {
                                session.instruction
                            },
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }
        }

        if (events.isEmpty()) {
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = "Nothing has happened yet. I'll tell you if it does.",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        } else {
            LazyColumn(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(16.dp),
                contentPadding = PaddingValues(vertical = 8.dp)
            ) {
                items(events) { event ->
                    TimelineEventCard(
                        event = event,
                        onClick = { onEventClick(event) }
                    )
                }
            }
        }

        // Sticky "I'm back" button
        if (session != null && session.active) {
            Spacer(modifier = Modifier.height(12.dp))
            Button(
                onClick = onImBackClick,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp),
                colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal),
                shape = RoundedCornerShape(12.dp)
            ) {
                Text(
                    text = "I'm back",
                    style = MaterialTheme.typography.titleMedium
                )
            }
            Spacer(modifier = Modifier.height(72.dp))
        } else {
            Spacer(modifier = Modifier.height(60.dp))
        }
    }
}

/** One event card: alert colour rail, who, when and where, the sentence, and the event's frame if it has a clip. */
@Composable
fun TimelineEventCard(
    event: Event,
    onClick: () -> Unit
) {
    val railColor = when (event.alertType) {
        AlertType.HEADS_UP -> MeanwhileAlertColors.HeadsUpAmber
        AlertType.VERDICT -> MeanwhileAlertColors.VerdictGreen
        AlertType.ALERT -> MeanwhileAlertColors.AlertRed
        AlertType.NONE -> MeanwhileAlertColors.NoteNeutral
    }

    val iconVector = when (event.alertType) {
        AlertType.HEADS_UP -> Icons.Default.Info
        AlertType.VERDICT -> Icons.Default.CheckCircle
        AlertType.ALERT -> Icons.Default.Warning
        AlertType.NONE -> Icons.Default.RadioButtonChecked
    }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onClick() },
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f)
        )
    ) {
        Row(modifier = Modifier.fillMaxWidth()) {
            Box(
                modifier = Modifier
                    .width(6.dp)
                    .height(160.dp)
                    .background(railColor)
            )

            Column(
                modifier = Modifier
                    .weight(1f)
                    .padding(14.dp)
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = iconVector,
                            contentDescription = null,
                            tint = railColor,
                            modifier = Modifier.size(18.dp)
                        )
                        Spacer(modifier = Modifier.width(6.dp))
                        Text(
                            text = event.displayActorName,
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onSurface
                        )
                        if (!event.isOwner && event.actorRole != null) {
                            Text(
                                text = " (${event.actorRole})",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                        if (event.actorUnnamed) {
                            Spacer(modifier = Modifier.width(6.dp))
                            Box(
                                modifier = Modifier
                                    .clip(RoundedCornerShape(6.dp))
                                    .background(MeanwhileAlertColors.HeadsUpAmberContainer)
                                    .padding(horizontal = 6.dp, vertical = 2.dp)
                            ) {
                                Text(
                                    text = "Name",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MeanwhileAlertColors.HeadsUpOnAmberContainer,
                                    fontWeight = FontWeight.Bold
                                )
                            }
                        }
                    }

                    Text(
                        text = "${event.formattedTime} · ${event.cameraId.replaceFirstChar { it.uppercase() }}",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }

                Spacer(modifier = Modifier.height(8.dp))

                Text(
                    text = event.summary,
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                    fontWeight = FontWeight.SemiBold
                )

                // 16:9 clip preview: the event's own frame (black until there is one), with a play button
                if (event.clipUrl != null) {
                    Spacer(modifier = Modifier.height(10.dp))
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .aspectRatio(16f / 9f)
                            .clip(RoundedCornerShape(10.dp))
                            .background(Color.Black)
                    ) {
                        if (event.frameUrl != null) {
                            MediaImage(
                                url = event.frameUrl,
                                contentDescription = "Frame from this event",
                                modifier = Modifier.fillMaxSize(),
                                contentScale = ContentScale.Fit
                            )
                        }

                        Box(
                            modifier = Modifier
                                .size(40.dp)
                                .align(Alignment.Center)
                                .clip(CircleShape)
                                .background(Color.Black.copy(alpha = 0.6f)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(
                                imageVector = Icons.Default.PlayArrow,
                                contentDescription = "Play clip",
                                tint = Color.White
                            )
                        }
                    }
                }
            }
        }
    }
}
