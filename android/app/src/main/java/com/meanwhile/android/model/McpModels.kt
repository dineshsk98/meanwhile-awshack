// Messages in the Ask conversation: who sent each one, its text, and the camera choices a clarifying question offers.
package com.meanwhile.android.model

/** Who wrote a message in the Ask conversation. */
enum class MessageSender {
    USER,
    MEANWHILE
}

/** One message in the Ask sheet; [status] is the voice side's tool status, e.g. "clarify". */
data class ChatMessage(
    val id: String = java.util.UUID.randomUUID().toString(),
    val sender: MessageSender,
    val text: String,
    val status: String? = null,
    val clarificationOptions: List<String> = emptyList()
)
