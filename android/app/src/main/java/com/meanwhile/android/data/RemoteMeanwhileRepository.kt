// The app's connection to the hub over HTTP: polls the core's API and calls the voice side's /voice/ask.
package com.meanwhile.android.data

import com.meanwhile.android.MeanwhileApplication
import com.meanwhile.android.model.Camera
import com.meanwhile.android.model.ChatMessage
import com.meanwhile.android.model.Event
import com.meanwhile.android.model.MessageSender
import com.meanwhile.android.model.NamePersonResult
import com.meanwhile.android.model.Person
import com.meanwhile.android.model.Session
import com.meanwhile.android.util.HubUrls
import com.meanwhile.android.model.PairingCode
import com.meanwhile.android.model.PairResponse
import com.meanwhile.android.model.PairedDevice
import com.meanwhile.android.model.IceServerConfig
import com.meanwhile.android.model.IceServersResponse
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit

/** [MeanwhileRepository] over OkHttp; sends the stored key or token only to the configured hub. */
class RemoteMeanwhileRepository(
    private val prefs: PreferencesProvider
) : MeanwhileRepository {

    private val _authError = MutableStateFlow(false)
    override val authError: Flow<Boolean> get() = _authError

    private val json = Json { ignoreUnknownKeys = true; isLenient = true }
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)
        .addInterceptor { chain ->
            val request = chain.request()
            val builder = request.newBuilder()

            val existingKey = request.header("X-Meanwhile-Key")
            val requestHost = request.url.host
            val requestPort = request.url.port
            val configuredOrigins = try {
                listOfNotNull(coreBaseUrl.toHttpUrlOrNull(), voiceBaseUrl.toHttpUrlOrNull())
            } catch (e: Exception) { emptyList() }
            val isConfiguredHub = configuredOrigins.any {
                it.host.equals(requestHost, ignoreCase = true) && it.port == requestPort
            }
            // Only the configured hub ever gets the stored key or token. On POST /api/pair it tells that hub which
            // entry this device replaces when pairing again; a QR code for any other host gets nothing.
            if (existingKey.isNullOrBlank() && isConfiguredHub && prefs.apiKey.isNotBlank()) {
                builder.header("X-Meanwhile-Key", prefs.apiKey)
            }

            val response = chain.proceed(builder.build())
            if (response.code == 401 && isConfiguredHub) {
                _authError.value = true
            } else if (response.isSuccessful && isConfiguredHub) {
                _authError.value = false
            }
            response
        }
        .build()

    private val coreBaseUrl: String
        get() = prefs.getCoreBaseUrl()

    private val voiceBaseUrl: String
        get() = prefs.getVoiceBaseUrl()

    /** A media link from the API, made absolute against the core with its signed query untouched. */
    private fun media(url: String): String = HubUrls.media(coreBaseUrl, url)

    /** Polls `GET /api/sessions/latest`. */
    override fun observeActiveSession(): Flow<Session?> = flow {
        while (true) {
            var hasActiveSession = false
            try {
                val request = Request.Builder()
                    .url("$coreBaseUrl/api/sessions/latest")
                    .get()
                    .build()
                client.newCall(request).execute().use { response ->
                    if (response.isSuccessful) {
                        val body = response.body?.string() ?: ""
                        val root = json.parseToJsonElement(body).jsonObject
                        val active = root["active"]?.jsonPrimitive?.content.toBoolean()
                        val sessionElement = root["session"]
                        if (active && sessionElement != null && sessionElement !is kotlinx.serialization.json.JsonNull) {
                            val session = json.decodeFromJsonElement(Session.serializer(), sessionElement)
                            hasActiveSession = true
                            emit(session)
                        } else {
                            emit(null)
                        }
                    }
                }
            } catch (e: Exception) {
                // Keep trying silently
            }
            // 500 ms while a session is active (the live box moves), 2 s when idle
            delay(if (hasActiveSession) 500L else 2000L)
        }
    }.flowOn(Dispatchers.IO)

    /** Polls `GET /api/sessions/{id}/events` every 2 s. */
    override fun observeEvents(sessionId: String): Flow<List<Event>> = flow {
        while (true) {
            if (sessionId.isNotEmpty()) {
                try {
                    val request = Request.Builder()
                        .url("$coreBaseUrl/api/sessions/$sessionId/events")
                        .get()
                        .build()
                    client.newCall(request).execute().use { response ->
                        if (response.isSuccessful) {
                            val body = response.body?.string() ?: ""
                            val root = json.parseToJsonElement(body).jsonObject
                            val eventsArray = root["events"]?.jsonArray
                            if (eventsArray != null) {
                                val list = eventsArray.map {
                                    val ev = json.decodeFromJsonElement(Event.serializer(), it)
                                    // Prepend core base URL if relative path
                                    val resolvedClip = ev.clipUrl?.let { url ->
                                        media(url)
                                    }
                                    val resolvedFrame = ev.frameUrl?.let { url ->
                                        media(url)
                                    }
                                    ev.copy(clipUrl = resolvedClip, frameUrl = resolvedFrame)
                                }
                                emit(list.reversed()) // Newest first
                            }
                        }
                    }
                } catch (e: Exception) {
                    // Ignore transient errors
                }
            }
            delay(2000)
        }
    }.flowOn(Dispatchers.IO)

    /** Polls `GET /api/cameras` every 5 s. */
    override fun observeCameras(): Flow<List<Camera>> = flow {
        while (true) {
            try {
                val request = Request.Builder()
                    .url("$coreBaseUrl/api/cameras")
                    .get()
                    .build()
                client.newCall(request).execute().use { response ->
                    if (response.isSuccessful) {
                        val body = response.body?.string() ?: ""
                        val root = json.parseToJsonElement(body).jsonObject
                        val camerasArray = root["cameras"]?.jsonArray
                        if (camerasArray != null) {
                            val list = camerasArray.map {
                                val cam = json.decodeFromJsonElement(Camera.serializer(), it)
                                val resolvedThumb = cam.thumbUrl?.let { url ->
                                    media(url)
                                }
                                cam.copy(thumbUrl = resolvedThumb)
                            }
                            emit(list)
                        }
                    }
                }
            } catch (e: Exception) {
                // Ignore
            }
            delay(5000)
        }
    }.flowOn(Dispatchers.IO)

    /** Polls `GET /api/people` every 10 s. */
    override fun observePeople(): Flow<List<Person>> = flow {
        while (true) {
            try {
                val request = Request.Builder()
                    .url("$coreBaseUrl/api/people")
                    .get()
                    .build()
                client.newCall(request).execute().use { response ->
                    if (response.isSuccessful) {
                        val body = response.body?.string() ?: ""
                        val root = json.parseToJsonElement(body).jsonObject
                        val peopleArray = root["people"]?.jsonArray
                        if (peopleArray != null) {
                            val list = peopleArray.map { elem ->
                                val p = json.decodeFromJsonElement(Person.serializer(), elem)
                                val resolvedPhoto = p.photoUrl?.let { url ->
                                    media(url)
                                }
                                val resolvedPhotos = p.photoUrls.map { url ->
                                    media(url)
                                }
                                p.copy(
                                    photoUrl = resolvedPhoto,
                                    photoUrls = resolvedPhotos
                                )
                            }
                            emit(list)
                        }
                    }
                }
            } catch (e: Exception) {
                // Ignore
            }
            delay(10000)
        }
    }.flowOn(Dispatchers.IO)

    /** Polls `GET /api/sessions?limit=50` every 5 s. */
    override fun observePastSessions(): Flow<List<Session>> = flow {
        while (true) {
            try {
                val request = Request.Builder()
                    .url("$coreBaseUrl/api/sessions?limit=50")
                    .get()
                    .build()
                client.newCall(request).execute().use { response ->
                    if (response.isSuccessful) {
                        val body = response.body?.string() ?: ""
                        val root = json.parseToJsonElement(body).jsonObject
                        val sessionsArray = root["sessions"]?.jsonArray
                        if (sessionsArray != null) {
                            val list = sessionsArray.map {
                                json.decodeFromJsonElement(Session.serializer(), it)
                            }
                            emit(list)
                        }
                    }
                }
            } catch (e: Exception) {
                // Ignore
            }
            delay(5000)
        }
    }.flowOn(Dispatchers.IO)

    /** `POST /api/sessions/{id}/end`. */
    override suspend fun endSession(sessionId: String): Session = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$coreBaseUrl/api/sessions/$sessionId/end")
            .post("{}".toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(request).execute().use { response ->
            val responseText = response.body?.string() ?: ""
            val root = json.parseToJsonElement(responseText).jsonObject
            val sessionElem = root["session"] ?: throw IllegalStateException("Failed to end session")
            json.decodeFromJsonElement(Session.serializer(), sessionElem)
        }
    }

    /** `POST /voice/ask`; network and parse failures become a reply that says so. */
    override suspend fun processUserUtterance(text: String): ChatMessage = withContext(Dispatchers.IO) {
        try {
            val payload = buildJsonObject {
                put("text", text)
            }.toString()
            val request = Request.Builder()
                .url("$voiceBaseUrl/voice/ask")
                .post(payload.toRequestBody("application/json".toMediaType()))
                .build()

            client.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    val responseText = response.body?.string() ?: ""
                    val root = json.parseToJsonElement(responseText).jsonObject
                    val reply = root["reply"]?.jsonPrimitive?.content
                        ?: root["result"]?.jsonObject?.get("spoken")?.jsonPrimitive?.content
                        ?: "No reply from Meanwhile"
                    val resultObj = root["result"]?.jsonObject
                    val status = resultObj?.get("status")?.jsonPrimitive?.content
                    val question = resultObj?.get("question")?.jsonPrimitive?.content

                    // Tools list cameras as objects ({"camera_id", "label", "online"}) or as plain names
                    val cameraOptions = (resultObj?.get("cameras") as? kotlinx.serialization.json.JsonArray)
                        ?.mapNotNull { element ->
                            when (element) {
                                is JsonObject -> (element["label"] as? kotlinx.serialization.json.JsonPrimitive)?.content
                                is kotlinx.serialization.json.JsonPrimitive -> element.content
                                else -> null
                            }
                        } ?: emptyList()

                    ChatMessage(
                        sender = MessageSender.MEANWHILE,
                        text = reply,
                        status = status,
                        clarificationOptions = if (status == "clarify") cameraOptions else emptyList()
                    )
                } else {
                    ChatMessage(
                        sender = MessageSender.MEANWHILE,
                        text = "I couldn't reach Meanwhile. Please check that the hub is running."
                    )
                }
            }
        } catch (e: java.io.IOException) {
            ChatMessage(
                sender = MessageSender.MEANWHILE,
                text = "Connection error. Please check your network connection."
            )
        } catch (e: Exception) {
            android.util.Log.w("MeanwhileAsk", "Couldn't read the reply from /voice/ask", e)
            ChatMessage(
                sender = MessageSender.MEANWHILE,
                text = "Meanwhile answered, but the app couldn't read the reply."
            )
        }
    }

    /** The photo at [uri] as a JPEG, upright (EXIF) and at most 1920 px on its long side; null when unreadable. */
    private fun compressPhoto(context: android.content.Context, uri: Uri): ByteArray? {
        return try {
            val contentResolver = context.contentResolver

            // 1. Read EXIF orientation
            val orientation = contentResolver.openInputStream(uri)?.use { stream ->
                androidx.exifinterface.media.ExifInterface(stream).getAttributeInt(
                    androidx.exifinterface.media.ExifInterface.TAG_ORIENTATION,
                    androidx.exifinterface.media.ExifInterface.ORIENTATION_NORMAL
                )
            } ?: androidx.exifinterface.media.ExifInterface.ORIENTATION_NORMAL

            // 2. Decode bounds to compute inSampleSize (prevents OutOfMemory on high-res photos)
            val boundsOptions = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            contentResolver.openInputStream(uri)?.use { stream ->
                BitmapFactory.decodeStream(stream, null, boundsOptions)
            }

            var inSampleSize = 1
            val maxSide = 1920
            while (boundsOptions.outWidth / (inSampleSize * 2) >= maxSide &&
                   boundsOptions.outHeight / (inSampleSize * 2) >= maxSide) {
                inSampleSize *= 2
            }

            // 3. Decode sampled bitmap
            val decodeOptions = BitmapFactory.Options().apply { this.inSampleSize = inSampleSize }
            val decodedBitmap = contentResolver.openInputStream(uri)?.use { stream ->
                BitmapFactory.decodeStream(stream, null, decodeOptions)
            } ?: return null

            // 4. Rotate according to EXIF
            val matrix = android.graphics.Matrix()
            when (orientation) {
                androidx.exifinterface.media.ExifInterface.ORIENTATION_ROTATE_90 -> matrix.postRotate(90f)
                androidx.exifinterface.media.ExifInterface.ORIENTATION_ROTATE_180 -> matrix.postRotate(180f)
                androidx.exifinterface.media.ExifInterface.ORIENTATION_ROTATE_270 -> matrix.postRotate(270f)
            }

            val rotatedBitmap = if (!matrix.isIdentity) {
                val rotated = Bitmap.createBitmap(
                    decodedBitmap, 0, 0, decodedBitmap.width, decodedBitmap.height, matrix, true
                )
                if (rotated != decodedBitmap) decodedBitmap.recycle()
                rotated
            } else {
                decodedBitmap
            }

            // 5. Downscale if still larger than maxSide
            val width = rotatedBitmap.width
            val height = rotatedBitmap.height
            val scale = if (kotlin.math.max(width, height) > maxSide) {
                maxSide.toFloat() / kotlin.math.max(width, height)
            } else {
                1.0f
            }

            val finalBitmap = if (scale < 1.0f) {
                val scaled = Bitmap.createScaledBitmap(
                    rotatedBitmap,
                    (width * scale).toInt(),
                    (height * scale).toInt(),
                    true
                )
                if (scaled != rotatedBitmap) rotatedBitmap.recycle()
                scaled
            } else {
                rotatedBitmap
            }

            val outputStream = ByteArrayOutputStream()
            finalBitmap.compress(Bitmap.CompressFormat.JPEG, 85, outputStream)
            finalBitmap.recycle()
            outputStream.toByteArray()
        } catch (e: Exception) {
            null
        }
    }

    /** `POST /api/people`: multipart with the photos, or JSON without any. */
    override suspend fun addPerson(name: String, role: String, photoUris: List<String>, note: String?): Person = withContext(Dispatchers.IO) {
        val requestBody = if (photoUris.isNotEmpty()) {
            val builder = MultipartBody.Builder().setType(MultipartBody.FORM)
                .addFormDataPart("name", name.trim())
                .addFormDataPart("role", role.lowercase().trim())
                .addFormDataPart("note", note?.trim() ?: "")

            val context = MeanwhileApplication.instance
            var photoCount = 0
            photoUris.take(3).forEachIndexed { index, uriStr ->
                try {
                    val uri = Uri.parse(uriStr)
                    val bytes = compressPhoto(context, uri) ?: context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    if (bytes != null && bytes.isNotEmpty()) {
                        val filename = "photo_${index + 1}.jpg"
                        builder.addFormDataPart("photo", filename, bytes.toRequestBody("image/jpeg".toMediaType()))
                        photoCount++
                    }
                } catch (e: Exception) {
                    // Skip unreadable uri
                }
            }
            if (photoCount > 0) {
                builder.build()
            } else {
                buildJsonObject {
                    put("name", name.trim())
                    put("role", role.lowercase().trim())
                    put("note", note?.trim() ?: "")
                }.toString().toRequestBody("application/json".toMediaType())
            }
        } else {
            buildJsonObject {
                put("name", name.trim())
                put("role", role.lowercase().trim())
                put("note", note?.trim() ?: "")
            }.toString().toRequestBody("application/json".toMediaType())
        }

        val request = Request.Builder()
            .url("$coreBaseUrl/api/people")
            .post(requestBody)
            .build()

        client.newCall(request).execute().use { response ->
            val responseText = response.body?.string() ?: ""
            if (!response.isSuccessful) {
                val errorObj = try { json.parseToJsonElement(responseText).jsonObject } catch (e: Exception) { null }
                val errorCode = errorObj?.get("error")?.jsonPrimitive?.content ?: ""
                val errorMessage = errorObj?.get("message")?.jsonPrimitive?.content ?: "Failed to add person"
                when (errorCode) {
                    "no_face" -> throw IllegalArgumentException(errorMessage.ifEmpty { "No face detected in photo. Please retake with face clearly visible." })
                    "bad_photo" -> throw IllegalArgumentException(errorMessage.ifEmpty { "Photo format invalid or corrupt. JPEG or PNG required." })
                    "bad_size" -> throw IllegalArgumentException(errorMessage.ifEmpty { "Photo exceeds 15 MB limit." })
                    "face_id_unavailable" -> throw IllegalStateException(errorMessage.ifEmpty { "Face ID service unavailable. Please retry." })
                    else -> throw RuntimeException(errorMessage)
                }
            }
            val root = json.parseToJsonElement(responseText).jsonObject
            val personElem = root["person"] ?: root
            val person = json.decodeFromJsonElement(Person.serializer(), personElem)
            val resolvedPhoto = person.photoUrl?.let { media(it) }
            val resolvedPhotos = person.photoUrls.map { media(it) }
            person.copy(photoUrl = resolvedPhoto, photoUrls = resolvedPhotos)
        }
    }

    /** `PATCH /api/people/{id}`; 409 means the name is taken. */
    override suspend fun namePerson(id: String, name: String, role: String?): NamePersonResult = withContext(Dispatchers.IO) {
        try {
            val payload = buildJsonObject {
                put("name", name.trim())
                if (!role.isNullOrBlank()) {
                    put("role", role.lowercase().trim())
                }
            }.toString()

            val request = Request.Builder()
                .url("$coreBaseUrl/api/people/$id")
                .patch(payload.toRequestBody("application/json".toMediaType()))
                .build()

            client.newCall(request).execute().use { response ->
                val responseText = response.body?.string() ?: ""
                val root = try { json.parseToJsonElement(responseText).jsonObject } catch (e: Exception) { null }
                if (response.code == 409) {
                    val into = root?.get("into")?.jsonPrimitive?.content ?: ""
                    return@withContext NamePersonResult.NameTaken(existingPersonId = into, proposedName = name.trim())
                }
                if (!response.isSuccessful) {
                    val msg = root?.get("message")?.jsonPrimitive?.content ?: "Failed to rename person (${response.code})"
                    return@withContext NamePersonResult.Failure(msg)
                }
                val personElem = root?.get("person") ?: root
                if (personElem != null) {
                    val person = json.decodeFromJsonElement(Person.serializer(), personElem)
                    val renamedFrom = root?.get("renamed_from")?.jsonPrimitive?.content
                    val resolvedPhoto = person.photoUrl?.let { media(it) }
                    NamePersonResult.Success(person.copy(photoUrl = resolvedPhoto), renamedFrom = renamedFrom)
                } else {
                    NamePersonResult.Failure("Empty response from server")
                }
            }
        } catch (e: Exception) {
            NamePersonResult.Failure(e.message ?: "Network error")
        }
    }

    /** `POST /api/people/{id}/merge`. */
    override suspend fun mergePerson(id: String, intoId: String): Person = withContext(Dispatchers.IO) {
        val payload = buildJsonObject {
            put("into", intoId)
        }.toString()

        val request = Request.Builder()
            .url("$coreBaseUrl/api/people/$id/merge")
            .post(payload.toRequestBody("application/json".toMediaType()))
            .build()

        client.newCall(request).execute().use { response ->
            val responseText = response.body?.string() ?: ""
            if (!response.isSuccessful) {
                val errorObj = try { json.parseToJsonElement(responseText).jsonObject } catch (e: Exception) { null }
                val errorMsg = errorObj?.get("message")?.jsonPrimitive?.content ?: "Failed to merge person (${response.code})"
                throw RuntimeException(errorMsg)
            }
            val root = json.parseToJsonElement(responseText).jsonObject
            val personElem = root["person"] ?: root
            val person = json.decodeFromJsonElement(Person.serializer(), personElem)
            val resolvedPhoto = person.photoUrl?.let { media(it) }
            person.copy(photoUrl = resolvedPhoto)
        }
    }

    /** `DELETE /api/people/unnamed`. */
    override suspend fun deleteAllUnnamed(): List<String> = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$coreBaseUrl/api/people/unnamed")
            .delete()
            .build()

        client.newCall(request).execute().use { response ->
            val responseText = response.body?.string() ?: ""
            if (!response.isSuccessful) {
                val errorObj = try { json.parseToJsonElement(responseText).jsonObject } catch (e: Exception) { null }
                val errorMsg = errorObj?.get("message")?.jsonPrimitive?.content ?: "Failed to delete unnamed people"
                throw RuntimeException(errorMsg)
            }
            val root = json.parseToJsonElement(responseText).jsonObject
            val removedArr = root["removed"]?.jsonArray
            removedArr?.map { it.jsonPrimitive.content } ?: emptyList()
        }
    }

    /** `DELETE /api/people/{id}`. */
    override suspend fun deletePerson(personId: String) = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$coreBaseUrl/api/people/$personId")
            .delete()
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                val responseText = response.body?.string() ?: ""
                val errorObj = try { json.parseToJsonElement(responseText).jsonObject } catch (e: Exception) { null }
                val errorMsg = errorObj?.get("message")?.jsonPrimitive?.content ?: "Failed to delete person (${response.code})"
                throw RuntimeException(errorMsg)
            }
        }
    }

    /** `PATCH /api/cameras/{id}`. */
    override suspend fun renameCamera(cameraId: String, label: String): Camera = withContext(Dispatchers.IO) {
        val payload = buildJsonObject { put("label", label.trim()) }.toString()
        val request = Request.Builder()
            .url("$coreBaseUrl/api/cameras/$cameraId")
            .patch(payload.toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(request).execute().use { response ->
            val text = response.body?.string() ?: ""
            val root = try { json.parseToJsonElement(text).jsonObject } catch (e: Exception) { null }
            if (!response.isSuccessful) {
                throw RuntimeException(root?.get("message")?.jsonPrimitive?.content ?: "Couldn't rename the camera (${response.code})")
            }
            val camera = json.decodeFromJsonElement(Camera.serializer(), root?.get("camera") ?: throw IllegalStateException("No camera in the reply"))
            camera.copy(thumbUrl = camera.thumbUrl?.let { media(it) })
        }
    }

    /** `DELETE /api/cameras/{id}`. */
    override suspend fun removeCamera(cameraId: String) = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$coreBaseUrl/api/cameras/$cameraId")
            .delete()
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                val root = try { json.parseToJsonElement(response.body?.string() ?: "").jsonObject } catch (e: Exception) { null }
                throw RuntimeException(root?.get("message")?.jsonPrimitive?.content ?: "Couldn't remove the camera (${response.code})")
            }
        }
    }

    /** `lag_s` from `GET /api/status`. */
    override suspend fun getLagSeconds(): Int? = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder()
                .url("$coreBaseUrl/api/status")
                .get()
                .build()
            client.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    val body = response.body?.string() ?: ""
                    val root = json.parseToJsonElement(body).jsonObject
                    val lagS = root["lag_s"]?.jsonPrimitive?.content?.toDoubleOrNull()
                    lagS?.toInt()
                } else {
                    null
                }
            }
        } catch (e: Exception) {
            null
        }
    }

    /** `POST /api/devices`. */
    override suspend fun registerDevice(token: String): Boolean = withContext(Dispatchers.IO) {
        if (token.isBlank()) return@withContext false
        try {
            val payload = buildJsonObject {
                put("fcm_token", token)
            }.toString()
            val request = Request.Builder()
                .url("$coreBaseUrl/api/devices")
                .post(payload.toRequestBody("application/json".toMediaType()))
                .build()
            client.newCall(request).execute().use { response ->
                response.isSuccessful
            }
        } catch (e: Exception) {
            false
        }
    }

    /** `POST /api/pairing-codes`. */
    override suspend fun createPairingCode(): PairingCode = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$coreBaseUrl/api/pairing-codes")
            .post("{}".toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(request).execute().use { response ->
            val text = response.body?.string() ?: ""
            if (!response.isSuccessful) {
                val errorObj = try { json.parseToJsonElement(text).jsonObject } catch (e: Exception) { null }
                val msg = errorObj?.get("message")?.jsonPrimitive?.content ?: "Failed to generate pairing code"
                throw RuntimeException(msg)
            }
            json.decodeFromString(PairingCode.serializer(), text)
        }
    }

    /** `POST /api/pair` on the QR code's hub, a typed hub, or the stored one. */
    override suspend fun pairWithCode(targetHub: String?, code: String, deviceName: String): PairResponse = withContext(Dispatchers.IO) {
        val hubUrl = if (!targetHub.isNullOrBlank()) {
            if (targetHub.startsWith("http://") || targetHub.startsWith("https://")) {
                targetHub.removeSuffix("/")
            } else {
                prefs.getCoreBaseUrl(hub = targetHub)
            }
        } else {
            coreBaseUrl
        }
        val payload = buildJsonObject {
            put("code", code.trim())
            put("device_name", deviceName.trim())
        }.toString()

        val request = Request.Builder()
            .url("$hubUrl/api/pair")
            .post(payload.toRequestBody("application/json".toMediaType()))
            .build()

        client.newCall(request).execute().use { response ->
            val text = response.body?.string() ?: ""
            if (!response.isSuccessful) {
                val errorObj = try { json.parseToJsonElement(text).jsonObject } catch (e: Exception) { null }
                val msg = errorObj?.get("message")?.jsonPrimitive?.content ?: "Pairing failed. The code may have expired."
                throw RuntimeException(msg)
            }
            json.decodeFromString(PairResponse.serializer(), text)
        }
    }

    /** Polls `GET /api/paired-devices` every 5 s. */
    override fun observePairedDevices(): Flow<List<PairedDevice>> = flow {
        while (true) {
            try {
                val request = Request.Builder()
                    .url("$coreBaseUrl/api/paired-devices")
                    .get()
                    .build()
                client.newCall(request).execute().use { response ->
                    if (response.isSuccessful) {
                        val text = response.body?.string() ?: ""
                        val root = json.parseToJsonElement(text).jsonObject
                        val devArray = root["devices"]?.jsonArray
                        if (devArray != null) {
                            val list = devArray.map { json.decodeFromJsonElement(PairedDevice.serializer(), it) }
                            emit(list)
                        }
                    }
                }
            } catch (e: Exception) {
                // Ignore and retry
            }
            delay(5000)
        }
    }.flowOn(Dispatchers.IO)

    /** `DELETE /api/paired-devices/{id}`. */
    override suspend fun revokePairedDevice(deviceId: String): Boolean = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder()
                .url("$coreBaseUrl/api/paired-devices/$deviceId")
                .delete()
                .build()
            client.newCall(request).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            false
        }
    }

    /** `GET /api/rtc/ice-servers`; empty when the core gives none or can't be reached. */
    override suspend fun getIceServers(): List<IceServerConfig> = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder()
                .url("$coreBaseUrl/api/rtc/ice-servers")
                .get()
                .build()
            client.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    val text = response.body?.string() ?: ""
                    val parsed = json.decodeFromString(IceServersResponse.serializer(), text)
                    if (parsed.iceServers.isNotEmpty()) {
                        return@withContext parsed.iceServers
                    }
                }
            }
        } catch (e: Exception) {
            // No ICE servers: host candidates only
        }
        emptyList()
    }

    /** `POST /api/rtc/{camera_id}/offers`. */
    override suspend fun createRtcOffer(cameraId: String, sdp: String): String = withContext(Dispatchers.IO) {
        val payload = buildJsonObject {
            put("sdp", sdp)
            put("purpose", "live")
        }.toString()
        val request = Request.Builder()
            .url("$coreBaseUrl/api/rtc/$cameraId/offers")
            .post(payload.toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(request).execute().use { response ->
            val text = response.body?.string() ?: ""
            if (!response.isSuccessful) {
                val errorObj = try { json.parseToJsonElement(text).jsonObject } catch (e: Exception) { null }
                val msg = errorObj?.get("message")?.jsonPrimitive?.content ?: "Failed to post RTC offer"
                throw RuntimeException(msg)
            }
            val root = json.parseToJsonElement(text).jsonObject
            root["offer_id"]?.jsonPrimitive?.content ?: throw IllegalStateException("Missing offer_id")
        }
    }

    /** `GET /api/rtc/offers/{offer_id}`. */
    override suspend fun pollRtcAnswer(offerId: String): String? = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder()
                .url("$coreBaseUrl/api/rtc/offers/$offerId")
                .get()
                .build()
            client.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    val text = response.body?.string() ?: ""
                    val root = json.parseToJsonElement(text).jsonObject
                    root["sdp"]?.jsonPrimitive?.content
                } else {
                    null
                }
            }
        } catch (e: Exception) {
            null
        }
    }
}
