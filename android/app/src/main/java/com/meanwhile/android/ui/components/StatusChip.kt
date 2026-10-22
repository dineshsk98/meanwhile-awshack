// Small coloured chips: the watched object's state and an event's alert level.
package com.meanwhile.android.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.RadioButtonChecked
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import com.meanwhile.android.model.AlertType
import com.meanwhile.android.model.SessionObjectState
import com.meanwhile.android.ui.theme.MeanwhileAlertColors

/** The wording of the session status chip. */
object StatusChipHelper {
    /** The chip's text for [state]: "With you" / "With <name>" when someone holds the object, else the state's label. */
    fun resolveLabel(state: SessionObjectState, actorName: String? = null): String {
        return when {
            state == SessionObjectState.WITH_PERSON && !actorName.isNullOrBlank() -> {
                if (actorName.equals("you", ignoreCase = true) || actorName.equals("owner", ignoreCase = true)) {
                    "With you"
                } else {
                    "With $actorName"
                }
            }
            else -> state.label
        }
    }
}

/** A coloured chip with the watched object's state. */
@Composable
fun StatusChip(
    state: SessionObjectState,
    actorName: String? = null,
    modifier: Modifier = Modifier
) {
    val (bg, fg, icon) = when (state) {
        SessionObjectState.ON_SURFACE -> Triple(
            MeanwhileAlertColors.NoteNeutralContainer,
            MeanwhileAlertColors.NoteOnNeutralContainer,
            Icons.Default.RadioButtonChecked
        )
        SessionObjectState.WITH_PERSON -> Triple(
            MeanwhileAlertColors.HeadsUpAmberContainer,
            MeanwhileAlertColors.HeadsUpOnAmberContainer,
            Icons.Default.Info
        )
        SessionObjectState.WITH_OWNER -> Triple(
            MeanwhileAlertColors.VerdictGreenContainer,
            MeanwhileAlertColors.VerdictOnGreenContainer,
            Icons.Default.CheckCircle
        )
        SessionObjectState.TAKEN -> Triple(
            MeanwhileAlertColors.AlertRedContainer,
            MeanwhileAlertColors.AlertOnRedContainer,
            Icons.Default.Warning
        )
        SessionObjectState.UNKNOWN -> Triple(
            MeanwhileAlertColors.NoteNeutralContainer,
            MeanwhileAlertColors.NoteOnNeutralContainer,
            Icons.Default.Info
        )
    }

    val displayText = StatusChipHelper.resolveLabel(state, actorName)

    Row(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(bg)
            .padding(horizontal = 10.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = fg,
            modifier = Modifier.size(16.dp)
        )
        Spacer(modifier = Modifier.width(6.dp))
        Text(
            text = displayText,
            color = fg,
            style = MaterialTheme.typography.labelMedium
        )
    }
}

/** A coloured pill with an event's alert level, or [customText] (e.g. the verdict) instead of the default label. */
@Composable
fun AlertPill(
    alertType: AlertType,
    customText: String? = null,
    modifier: Modifier = Modifier
) {
    val (bg, fg, label, icon) = when (alertType) {
        AlertType.HEADS_UP -> Quadruple(
            MeanwhileAlertColors.HeadsUpAmberContainer,
            MeanwhileAlertColors.HeadsUpOnAmberContainer,
            customText ?: "Heads-up",
            Icons.Default.Info
        )
        AlertType.VERDICT -> Quadruple(
            MeanwhileAlertColors.VerdictGreenContainer,
            MeanwhileAlertColors.VerdictOnGreenContainer,
            customText ?: "Verdict: All fine",
            Icons.Default.CheckCircle
        )
        AlertType.ALERT -> Quadruple(
            MeanwhileAlertColors.AlertRedContainer,
            MeanwhileAlertColors.AlertOnRedContainer,
            customText ?: "Alert",
            Icons.Default.Warning
        )
        AlertType.NONE -> Quadruple(
            MeanwhileAlertColors.NoteNeutralContainer,
            MeanwhileAlertColors.NoteOnNeutralContainer,
            customText ?: "Note",
            Icons.Default.RadioButtonChecked
        )
    }

    Row(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(bg)
            .padding(horizontal = 10.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = fg,
            modifier = Modifier.size(16.dp)
        )
        Spacer(modifier = Modifier.width(6.dp))
        Text(
            text = label,
            color = fg,
            style = MaterialTheme.typography.labelMedium
        )
    }
}

/** Four values, for destructuring the pill's colours, label and icon. */
private data class Quadruple<A, B, C, D>(val first: A, val second: B, val third: C, val fourth: D)
