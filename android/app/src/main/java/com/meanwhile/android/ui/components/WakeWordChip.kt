// The small chip in the app bar that shows the app is listening for the wake word.
package com.meanwhile.android.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Hearing
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import com.meanwhile.android.ui.theme.MeanwhileDeepTeal

/** "Listening for "[wakeWord]""; dimmed while [paused] (the app is speaking, thinking or using the manual mic). */
@Composable
fun WakeWordChip(wakeWord: String, paused: Boolean, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier
            .alpha(if (paused) 0.5f else 1f)
            .clip(RoundedCornerShape(12.dp))
            .background(MeanwhileDeepTeal.copy(alpha = 0.18f))
            .padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(
            imageVector = Icons.Default.Hearing,
            contentDescription = null,
            tint = MeanwhileDeepTeal,
            modifier = Modifier.size(14.dp)
        )
        Spacer(modifier = Modifier.width(4.dp))
        Text(
            text = "Listening for \"$wakeWord\"",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurface
        )
    }
}
