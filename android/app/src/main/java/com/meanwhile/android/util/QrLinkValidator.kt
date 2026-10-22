// Reads the pairing link from a scanned QR code and refuses links to hosts the app must not trust.
package com.meanwhile.android.util

import java.net.URI

/** Parses pairing QR links: HTTPS to any host, plain HTTP only to a private-network address. */
object QrLinkValidator {

    /** A parsed pairing link: where to pair and the one-time code. */
    data class QrPairInfo(
        val scheme: String,
        val host: String,
        val port: Int?,
        val code: String
    ) {
        /** The origin to send `POST /api/pair` to. */
        val pairingOrigin: String
            get() = if (port != null && port != -1) "$scheme://$host:$port" else "$scheme://$host"

        /** The hub address to keep after pairing: the HTTPS origin, or the bare LAN host. */
        val hubToStore: String
            get() = if (scheme.equals("https", ignoreCase = true)) {
                if (port != null && port != -1 && port != 443) "https://$host:$port" else "https://$host"
            } else {
                host
            }
    }

    /** True for localhost or an IPv4 address in 10/8, 172.16/12 or 192.168/16. */
    fun isPrivateHost(host: String): Boolean {
        if (host.equals("localhost", ignoreCase = true) || host == "127.0.0.1") return true
        val parts = host.split(".")
        if (parts.size != 4) return false
        val octets = parts.map { it.toIntOrNull() ?: return false }
        if (octets.any { it !in 0..255 }) return false

        // 10.0.0.0/8
        if (octets[0] == 10) return true
        // 172.16.0.0/12 (172.16.0.0 - 172.31.255.255)
        if (octets[0] == 172 && octets[1] in 16..31) return true
        // 192.168.0.0/16
        if (octets[0] == 192 && octets[1] == 168) return true
        return false
    }

    /** The pairing info in a `…/pair?code=…` link, or null when the link is not one the app may follow. */
    fun parse(content: String): QrPairInfo? {
        val trimmed = content.trim()
        val uri = try {
            URI.create(trimmed)
        } catch (e: Exception) {
            return null
        }
        val scheme = uri.scheme?.lowercase() ?: return null
        val host = uri.host ?: return null
        val path = uri.path ?: ""
        if (!path.endsWith("/pair") && path != "/pair") return null

        val query = uri.query ?: ""
        val code = query.split("&")
            .find { it.startsWith("code=") }
            ?.substringAfter("code=")
            ?: return null
        if (code.isBlank()) return null

        if (scheme == "https") {
            val port = if (uri.port != -1 && uri.port != 443) uri.port else null
            return QrPairInfo(scheme, host, port, code)
        } else if (scheme == "http") {
            if (isPrivateHost(host)) {
                val port = if (uri.port != -1 && uri.port != 80) uri.port else null
                return QrPairInfo(scheme, host, port, code)
            }
        }
        return null
    }
}
