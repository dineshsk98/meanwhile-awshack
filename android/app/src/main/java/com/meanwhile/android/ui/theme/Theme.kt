// The Meanwhile Material theme for light and dark mode.
package com.meanwhile.android.ui.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

private val LightColorScheme = lightColorScheme(
    primary = MeanwhileDeepTeal,
    onPrimary = MeanwhileOnTeal,
    primaryContainer = MeanwhileTealContainer,
    onPrimaryContainer = MeanwhileOnTealContainer,
    secondary = MeanwhileDeepTeal,
    onSecondary = MeanwhileOnTeal,
    secondaryContainer = MeanwhileTealContainer,
    onSecondaryContainer = MeanwhileOnTealContainer,
    tertiary = MeanwhileAlertColors.HeadsUpAmber,
    onTertiary = MeanwhileOnTeal,
    background = MeanwhileBackgroundLight,
    onBackground = MeanwhileOnBackgroundLight,
    surface = MeanwhileSurfaceLight,
    onSurface = MeanwhileOnSurfaceLight,
    surfaceVariant = MeanwhileSurfaceVariantLight,
    onSurfaceVariant = MeanwhileOnSurfaceVariantLight,
    outline = MeanwhileOutlineLight
)

private val DarkColorScheme = darkColorScheme(
    primary = MeanwhileTealContainer,
    onPrimary = MeanwhileOnTealContainer,
    primaryContainer = MeanwhileDeepTeal,
    onPrimaryContainer = MeanwhileTealContainer,
    secondary = MeanwhileTealContainer,
    onSecondary = MeanwhileOnTealContainer,
    secondaryContainer = MeanwhileDeepTeal,
    onSecondaryContainer = MeanwhileTealContainer,
    tertiary = MeanwhileAlertColors.HeadsUpAmberContainer,
    onTertiary = MeanwhileAlertColors.HeadsUpOnAmberContainer,
    background = MeanwhileBackgroundDark,
    onBackground = MeanwhileOnBackgroundDark,
    surface = MeanwhileSurfaceDark,
    onSurface = MeanwhileOnSurfaceDark,
    surfaceVariant = MeanwhileSurfaceVariantDark,
    onSurfaceVariant = MeanwhileOnSurfaceVariantDark,
    outline = MeanwhileOutlineDark
)

/** Material 3 theme with the Meanwhile colours (light or dark) and type scale; tints the status bar to match. */
@Composable
fun MeanwhileTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit
) {
    val colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as? Activity)?.window ?: return@SideEffect
            window.statusBarColor = colorScheme.surface.toArgb()
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !darkTheme
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = MeanwhileTypography,
        content = content
    )
}
