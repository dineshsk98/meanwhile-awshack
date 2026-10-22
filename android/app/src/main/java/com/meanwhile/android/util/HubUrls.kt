// Resolves the hub address and a port to a service's base URL, and media links against the hub's origin.
package com.meanwhile.android.util

/** Builds the core's and voice side's base URLs: one HTTPS origin for a host name, LAN HTTP on ports for an IP. */
object HubUrls {

    private val ipv4 = Regex("""^\d{1,3}(\.\d{1,3}){3}$""")
    private val scheme = Regex("^https?://", RegexOption.IGNORE_CASE)

    /**
     * Base URL for a service on [port]. A host name is always `https://<host>` (core and voice share that origin, no
     * port). An IP address or localhost is `http://<host>:<port>` for local development, or HTTPS when the hub was given
     * as `https://…` (kept as typed) or [useHttps] is set.
     */
    fun resolve(hub: String, port: Int, useHttps: Boolean = false): String {
        val trimmed = hub.trim().removeSuffix("/")
        val withoutScheme = trimmed.replaceFirst(scheme, "")
        val host = withoutScheme.substringBefore(":")
        return when {
            !isLocalAddress(host) -> "https://$host"
            trimmed.startsWith("https://", ignoreCase = true) || useHttps -> "https://$withoutScheme"
            else -> "http://$host:$port"
        }
    }

    /**
     * A media link from the API made absolute against the hub origin [baseUrl]: a path such as
     * `/media/…?exp=…&sig=…` is appended byte for byte (query intact); an absolute link is returned unchanged.
     */
    fun media(baseUrl: String, url: String): String =
        if (url.startsWith("/")) baseUrl.removeSuffix("/") + url else url

    /** True for an IPv4 address or localhost, which the hub serves over plain HTTP on its LAN ports. */
    private fun isLocalAddress(host: String): Boolean =
        host.equals("localhost", ignoreCase = true) || ipv4.matches(host)
}
