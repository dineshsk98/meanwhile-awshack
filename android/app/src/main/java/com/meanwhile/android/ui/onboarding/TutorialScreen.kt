// The full-screen first-run tutorial, one card at a time.
package com.meanwhile.android.ui.onboarding

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.meanwhile.android.ui.theme.MeanwhileDeepTeal

/** Full-screen card-by-card tutorial; [onFinish] runs on Skip and after the last card. */
@Composable
fun TutorialScreen(
    onFinish: () -> Unit,
    modifier: Modifier = Modifier
) {
    var index by rememberSaveable { mutableIntStateOf(0) }
    val pages = TutorialContent.pages
    val page = pages[index]
    val next = TutorialContent.nextIndex(index)
    val previous = TutorialContent.previousIndex(index)

    Surface(modifier = modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .safeDrawingPadding()
                .padding(20.dp),
            verticalArrangement = Arrangement.SpaceBetween
        ) {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                TextButton(onClick = onFinish) { Text("Skip") }
            }

            ElevatedCard(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(20.dp)
            ) {
                Column(modifier = Modifier.padding(24.dp)) {
                    Text(
                        text = page.title,
                        style = MaterialTheme.typography.headlineSmall,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    Text(
                        text = page.body,
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    if (page.examples.isNotEmpty()) {
                        Spacer(modifier = Modifier.height(20.dp))
                        Text(
                            text = "Try saying",
                            style = MaterialTheme.typography.labelLarge,
                            color = MeanwhileDeepTeal,
                            fontWeight = FontWeight.SemiBold
                        )
                        page.examples.forEach { example ->
                            Spacer(modifier = Modifier.height(8.dp))
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clip(RoundedCornerShape(12.dp))
                                    .background(MeanwhileDeepTeal.copy(alpha = 0.12f))
                                    .padding(horizontal = 14.dp, vertical = 10.dp)
                            ) {
                                Text(
                                    text = "“$example”",
                                    style = MaterialTheme.typography.titleMedium,
                                    fontStyle = FontStyle.Italic,
                                    color = MaterialTheme.colorScheme.onSurface
                                )
                            }
                        }
                    }
                }
            }

            Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    pages.indices.forEach { i ->
                        Box(
                            modifier = Modifier
                                .size(8.dp)
                                .clip(CircleShape)
                                .background(
                                    if (i == index) MeanwhileDeepTeal
                                    else MaterialTheme.colorScheme.outline.copy(alpha = 0.4f)
                                )
                        )
                    }
                }
                Spacer(modifier = Modifier.height(16.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    if (previous != null) {
                        TextButton(onClick = { index = previous }) { Text("Back") }
                    } else {
                        Spacer(modifier = Modifier.size(1.dp))
                    }
                    Button(
                        onClick = { if (next != null) index = next else onFinish() },
                        colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal)
                    ) {
                        Text(if (next != null) "Next" else "Got it")
                    }
                }
            }
        }
    }
}
