// Images from the hub's signed media links (thumbnails, frames, people photos).
package com.meanwhile.android.ui.components

import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import coil.compose.AsyncImage
import coil.compose.AsyncImagePainter
import coil.memory.MemoryCache
import coil.request.ImageRequest

/**
 * Loads [url] exactly as the API gave it; the cache key is the whole link, signature included, so an expired link is
 * never reused once a list refetch brings a fresh one. While a re-signed link loads, the image this view showed last
 * stays on screen instead of going blank.
 */
@Composable
fun MediaImage(
    url: String?,
    contentDescription: String?,
    modifier: Modifier = Modifier,
    contentScale: ContentScale = ContentScale.Fit,
    alpha: Float = 1f,
    onSuccess: ((AsyncImagePainter.State.Success) -> Unit)? = null
) {
    val context = LocalContext.current
    var shownKey by remember { mutableStateOf<MemoryCache.Key?>(null) }
    val request = remember(url) {
        ImageRequest.Builder(context)
            .data(url)
            .placeholderMemoryCacheKey(shownKey)
            .build()
    }
    AsyncImage(
        model = request,
        contentDescription = contentDescription,
        modifier = modifier,
        contentScale = contentScale,
        alpha = alpha,
        onSuccess = { state ->
            shownKey = state.result.memoryCacheKey
            onSuccess?.invoke(state)
        }
    )
}
