// What the app needs from the Meanwhile hub: polled flows of sessions, events, cameras and people, plus the actions.
package com.meanwhile.android.data

import com.meanwhile.android.model.Camera
import com.meanwhile.android.model.ChatMessage
import com.meanwhile.android.model.Event
import com.meanwhile.android.model.IceServerConfig
import com.meanwhile.android.model.NamePersonResult
import com.meanwhile.android.model.PairResponse
import com.meanwhile.android.model.PairedDevice
import com.meanwhile.android.model.PairingCode
import com.meanwhile.android.model.Person
import com.meanwhile.android.model.Session
import kotlinx.coroutines.flow.Flow

/** The hub's API as the app uses it (see CONTRACT.md); flows poll until they are no longer collected. */
interface MeanwhileRepository {
    /** The active session, or null when nothing is being watched. */
    fun observeActiveSession(): Flow<Session?>
    /** The events of session [sessionId], newest first. */
    fun observeEvents(sessionId: String): Flow<List<Event>>
    /** Every camera the core knows. */
    fun observeCameras(): Flow<List<Camera>>
    /** Named and unnamed people. */
    fun observePeople(): Flow<List<Person>>
    /** Recent sessions, newest first. */
    fun observePastSessions(): Flow<List<Session>>

    /** True while the hub answers 401 (the key or token is wrong or revoked). */
    val authError: Flow<Boolean>

    /** Ends session [sessionId] ("I'm back") and returns it. */
    suspend fun endSession(sessionId: String): Session
    /** Sends what the user said or typed to the voice side and returns its reply. */
    suspend fun processUserUtterance(text: String): ChatMessage

    /** Adds a named person with up to three face photos (content URIs); throws with the core's message on failure. */
    suspend fun addPerson(name: String, role: String, photoUris: List<String> = emptyList(), note: String? = null): Person
    /** Names (or renames) person [id]. */
    suspend fun namePerson(id: String, name: String, role: String? = null): NamePersonResult
    /** Merges person [id] into [intoId] and returns the merged person. */
    suspend fun mergePerson(id: String, intoId: String): Person
    /** Deletes every unnamed person and returns their ids. */
    suspend fun deleteAllUnnamed(): List<String>
    /** Deletes a person and their faces; throws with the core's message on failure. */
    suspend fun deletePerson(personId: String)

    /** The core's lag behind live in seconds, or null when unknown. */
    suspend fun getLagSeconds(): Int?
    /** Registers this phone's FCM token for push alerts. */
    suspend fun registerDevice(token: String): Boolean

    /** Gives a camera a new label (`PATCH /api/cameras/{id}`); throws with the core's message when refused. */
    suspend fun renameCamera(cameraId: String, label: String): Camera
    /** Removes a camera (`DELETE /api/cameras/{id}`); throws with the core's message when refused. */
    suspend fun removeCamera(cameraId: String)

    /** Creates a one-time code that pairs another device. */
    suspend fun createPairingCode(): PairingCode
    /** Pairs this phone with [targetHub] (or the stored hub) using [code]. */
    suspend fun pairWithCode(targetHub: String?, code: String, deviceName: String): PairResponse
    /** The paired devices. */
    fun observePairedDevices(): Flow<List<PairedDevice>>
    /** Revokes a paired device's token. */
    suspend fun revokePairedDevice(deviceId: String): Boolean

    /** The ICE servers for live video. */
    suspend fun getIceServers(): List<IceServerConfig>
    /** Posts a WebRTC offer for [cameraId] and returns its offer id. */
    suspend fun createRtcOffer(cameraId: String, sdp: String): String
    /** The answer SDP for [offerId], or null while the camera has not answered. */
    suspend fun pollRtcAnswer(offerId: String): String?
}
