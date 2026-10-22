// WebRTC signalling models: the ICE servers the core hands out for live video.
package com.meanwhile.android.model

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.descriptors.buildClassSerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** One ICE server from `GET /api/rtc/ice-servers`. */
@Serializable(with = IceServerConfigSerializer::class)
data class IceServerConfig(
    val urls: List<String> = emptyList(),
    val username: String? = null,
    val credential: String? = null
)

/** Reads an ICE server whose `urls` is either one string or a list of strings. */
object IceServerConfigSerializer : KSerializer<IceServerConfig> {
    override val descriptor: SerialDescriptor = buildClassSerialDescriptor("IceServerConfig")

    /** Decodes one ICE server object. */
    override fun deserialize(decoder: Decoder): IceServerConfig {
        val jsonDecoder = decoder as? JsonDecoder ?: throw IllegalStateException("Json expected")
        val element = jsonDecoder.decodeJsonElement()
        val obj = element.jsonObject

        val urls = when (val u = obj["urls"]) {
            is JsonArray -> u.mapNotNull { (it as? JsonPrimitive)?.content }
            is JsonPrimitive -> listOf(u.content)
            else -> emptyList()
        }
        val username = obj["username"]?.jsonPrimitive?.content
        val credential = obj["credential"]?.jsonPrimitive?.content

        return IceServerConfig(urls = urls, username = username, credential = credential)
    }

    /** The app never sends ICE servers, so this writes nothing. */
    override fun serialize(encoder: Encoder, value: IceServerConfig) = Unit
}

/** The body of `GET /api/rtc/ice-servers`. */
@Serializable
data class IceServersResponse(
    @SerialName("ice_servers") val iceServers: List<IceServerConfig> = emptyList()
)
