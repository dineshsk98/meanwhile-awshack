// The cards of the first-run tutorial and the rule for when it opens by itself.
package com.meanwhile.android.ui.onboarding

/** One tutorial card: a title, an explanation and optional example phrases to say. */
data class TutorialPage(
    val title: String,
    val body: String,
    val examples: List<String> = emptyList()
)

/** The fixed list of tutorial cards plus the show-once decision. */
object TutorialContent {
    val pages: List<TutorialPage> = listOf(
        TutorialPage(
            title = "Meanwhile watches while you're away",
            body = "Tell Alexa what to care about. Meanwhile follows your home cameras live, gives you a heads-up when something matters, then a verdict once it has seen the whole story."
        ),
        TutorialPage(
            title = "Watch an object",
            body = "Name the thing you want kept an eye on. Meanwhile follows it across every camera.",
            examples = listOf("Keep an eye on my lamp", "I'm stepping away, watch my bag")
        ),
        TutorialPage(
            title = "Take notes of who comes in",
            body = "Ask for a record of people instead of an object. Known people are named, anyone else shows up as an unnamed person you can name later.",
            examples = listOf("Take notes of who comes in")
        ),
        TutorialPage(
            title = "Catch up when you're back",
            body = "Ask what happened, or tell Meanwhile you're back and it ends the watch.",
            examples = listOf("What did I miss?", "I'm back")
        ),
        TutorialPage(
            title = "Add a camera",
            body = "Open the Meanwhile camera page on any spare phone or laptop and it appears under Cameras. To use this app on another phone, open Settings and tap Pair."
        )
    )

    /** True when the tutorial should open by itself, i.e. it has not been seen yet. */
    fun shouldAutoShow(seen: Boolean): Boolean = !seen

    /** Index of the card after [index], or null when [index] is the last one. */
    fun nextIndex(index: Int): Int? = if (index + 1 < pages.size) index + 1 else null

    /** Index of the card before [index], or null when [index] is the first one. */
    fun previousIndex(index: Int): Int? = if (index > 0) index - 1 else null
}
