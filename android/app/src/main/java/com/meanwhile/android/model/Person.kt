// A person the core knows (named, or an unnamed face) and the result of naming one.
package com.meanwhile.android.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** One person from `GET /api/people`: named, or an unnamed face the core saw ("Person N"). */
@Serializable
data class Person(
    val id: String = "",
    @SerialName("person_id")
    val personId: String? = null,
    val name: String = "",
    val role: String = "family",
    @SerialName("photo_url")
    val photoUrl: String? = null,
    @SerialName("photo_urls")
    val photoUrls: List<String> = emptyList(),
    val faces: Int = 0,
    val unnamed: Boolean = false,
    val number: Int? = null,
    val note: String? = null,
    @SerialName("first_seen")
    val firstSeen: String? = null,
    @SerialName("last_seen")
    val lastSeen: String? = null,
    @SerialName("camera_id")
    val cameraId: String? = null
) {
    /** The person's id, from `id` or `person_id`. */
    val displayId: String
        get() = id.ifEmpty { personId ?: "" }

    /** True for the home's owner. */
    val isOwner: Boolean
        get() = role.equals("owner", ignoreCase = true)
}

/** The outcome of naming a person: done, the name belongs to someone else (offer a merge), or an error. */
sealed class NamePersonResult {
    /** Named; [renamedFrom] is the old name, if any. */
    data class Success(val person: Person, val renamedFrom: String? = null) : NamePersonResult()
    /** Someone else already has the name. */
    data class NameTaken(val existingPersonId: String, val proposedName: String) : NamePersonResult()
    /** The core refused or could not be reached. */
    data class Failure(val message: String) : NamePersonResult()
}
