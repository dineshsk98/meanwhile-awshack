// The app's colours: the teal brand palette, neutral surfaces and the fixed alert colours.
package com.meanwhile.android.ui.theme

import androidx.compose.ui.graphics.Color

// Brand teal
val MeanwhileDeepTeal = Color(0xFF006874)
val MeanwhileOnTeal = Color(0xFFFFFFFF)
val MeanwhileTealContainer = Color(0xFF97F0FF)
val MeanwhileOnTealContainer = Color(0xFF001F24)

// Neutral surfaces, light and dark
val MeanwhileBackgroundLight = Color(0xFFFBFDFD)
val MeanwhileOnBackgroundLight = Color(0xFF191C1D)
val MeanwhileSurfaceLight = Color(0xFFFBFDFD)
val MeanwhileOnSurfaceLight = Color(0xFF191C1D)
val MeanwhileSurfaceVariantLight = Color(0xFFDAE4E5)
val MeanwhileOnSurfaceVariantLight = Color(0xFF3F484A)
val MeanwhileOutlineLight = Color(0xFF6F797A)

val MeanwhileBackgroundDark = Color(0xFF0E1415)
val MeanwhileOnBackgroundDark = Color(0xFFDEE3E5)
val MeanwhileSurfaceDark = Color(0xFF111819)
val MeanwhileOnSurfaceDark = Color(0xFFDEE3E5)
val MeanwhileSurfaceVariantDark = Color(0xFF3F484A)
val MeanwhileOnSurfaceVariantDark = Color(0xFFBFC8CA)
val MeanwhileOutlineDark = Color(0xFF899294)

/** Alert colours, the same in light and dark: amber heads-up, green verdict, red alert, grey note. */
object MeanwhileAlertColors {
    // Amber: Heads-up
    val HeadsUpAmber = Color(0xFFB26B00)
    val HeadsUpAmberContainer = Color(0xFFFFE0B2)
    val HeadsUpOnAmberContainer = Color(0xFF4E2C00)

    // Green: Verdict / All Fine
    val VerdictGreen = Color(0xFF1B6B44)
    val VerdictGreenContainer = Color(0xFFC7EED6)
    val VerdictOnGreenContainer = Color(0xFF003820)

    // Red: Immediate Alert
    val AlertRed = Color(0xFFBA1A1A)
    val AlertRedContainer = Color(0xFFFFDAD6)
    val AlertOnRedContainer = Color(0xFF410002)

    // Grey: Quiet note
    val NoteNeutral = Color(0xFF5F6368)
    val NoteNeutralContainer = Color(0xFFE8EAED)
    val NoteOnNeutralContainer = Color(0xFF1F1F1F)
}
