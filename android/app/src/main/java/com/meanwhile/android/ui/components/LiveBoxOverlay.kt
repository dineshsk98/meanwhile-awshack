// The live box drawn over a camera view to show where the watched object is.
package com.meanwhile.android.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import com.meanwhile.android.model.LiveBox
import com.meanwhile.android.ui.theme.MeanwhileAlertColors

/** Maps a box given as fractions of the video frame onto a view that letterboxes the frame. */
object LiveBoxOverlayMath {
    /** Where the frame sits inside the view: its drawn size and offset. */
    data class LetterboxBounds(
        val contentWidth: Float,
        val contentHeight: Float,
        val offsetX: Float,
        val offsetY: Float
    )

    /** A box in view pixels. */
    data class MappedRect(
        val left: Float,
        val top: Float,
        val width: Float,
        val height: Float,
        val centerX: Float = left + width / 2f,
        val centerY: Float = top + height / 2f
    )

    /** The frame's drawn area in a container, fitting [frameAspectRatio] (or filling it when that is null). */
    fun calculateLetterboxBounds(
        containerWidth: Float,
        containerHeight: Float,
        frameAspectRatio: Float?
    ): LetterboxBounds {
        val safeContainerHeight = containerHeight.coerceAtLeast(1f)
        val canvasAspect = containerWidth / safeContainerHeight
        val targetAspect = frameAspectRatio ?: canvasAspect

        return if (targetAspect > canvasAspect) {
            val h = containerWidth / targetAspect
            val yOffset = (containerHeight - h) / 2f
            LetterboxBounds(contentWidth = containerWidth, contentHeight = h, offsetX = 0f, offsetY = yOffset)
        } else {
            val w = containerHeight * targetAspect
            val xOffset = (containerWidth - w) / 2f
            LetterboxBounds(contentWidth = w, contentHeight = containerHeight, offsetX = xOffset, offsetY = 0f)
        }
    }

    /** A box at fractions ([normX], [normY], [normW], [normH]) of the frame, in view pixels. */
    fun mapNormalizedBox(
        normX: Float,
        normY: Float,
        normW: Float,
        normH: Float,
        bounds: LetterboxBounds
    ): MappedRect {
        val width = normW.coerceAtLeast(0f) * bounds.contentWidth
        val height = normH.coerceAtLeast(0f) * bounds.contentHeight
        val left = bounds.offsetX + (normX * bounds.contentWidth)
        val top = bounds.offsetY + (normY * bounds.contentHeight)
        return MappedRect(left = left, top = top, width = width, height = height)
    }
}

/** Draws the watched object's live box: green when the detector sees it, amber dashed from the vision model, white dashed when held, red dashed where it was last seen. */
@Composable
fun LiveBoxOverlay(
    liveBox: LiveBox?,
    modifier: Modifier = Modifier,
    frameAspectRatio: Float? = null
) {
    if (liveBox == null || !liveBox.isValid) return

    val box = liveBox.box
    val x0 = box.getOrNull(0) ?: return
    val y0 = box.getOrNull(1) ?: return
    val x1 = box.getOrNull(2) ?: return
    val y1 = box.getOrNull(3) ?: return

    val isMissing = liveBox.missing
    val isHeld = liveBox.held
    val source = liveBox.source?.lowercase()

    val boxColor = when {
        isMissing -> MeanwhileAlertColors.AlertRed
        isHeld -> Color.White
        source == "vision" || source == "step_away" -> MeanwhileAlertColors.HeadsUpAmber
        else -> MeanwhileAlertColors.VerdictGreen
    }

    val pathEffect = when {
        isMissing -> PathEffect.dashPathEffect(floatArrayOf(8f, 6f), 0f)
        isHeld -> PathEffect.dashPathEffect(floatArrayOf(16f, 10f), 0f)
        source == "vision" || source == "step_away" -> PathEffect.dashPathEffect(floatArrayOf(14f, 8f), 0f)
        else -> null
    }

    Canvas(modifier = modifier.fillMaxSize()) {
        val bounds = LiveBoxOverlayMath.calculateLetterboxBounds(
            containerWidth = size.width,
            containerHeight = size.height,
            frameAspectRatio = frameAspectRatio
        )

        val mapped = LiveBoxOverlayMath.mapNormalizedBox(
            normX = x0,
            normY = y0,
            normW = x1 - x0,
            normH = y1 - y0,
            bounds = bounds
        )

        // Bounding box rectangle
        drawRect(
            color = boxColor,
            topLeft = Offset(mapped.left, mapped.top),
            size = Size(mapped.width, mapped.height),
            style = Stroke(
                width = 3.dp.toPx(),
                pathEffect = pathEffect
            )
        )

        // Subtle shaded translucent background for the tracked object
        drawRect(
            color = boxColor.copy(alpha = if (isMissing) 0.15f else 0.08f),
            topLeft = Offset(mapped.left, mapped.top),
            size = Size(mapped.width, mapped.height)
        )
    }
}
