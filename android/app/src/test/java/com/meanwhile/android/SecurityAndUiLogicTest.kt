// Unit tests of the app's pure logic: pairing link checks, hub URLs, overlay maths, chip labels and times.
package com.meanwhile.android

import com.meanwhile.android.model.Event
import com.meanwhile.android.model.SessionObjectState
import com.meanwhile.android.ui.components.LiveBoxOverlayMath
import com.meanwhile.android.ui.components.StatusChipHelper
import com.meanwhile.android.util.DateTimeUtils
import com.meanwhile.android.util.HubUrls
import com.meanwhile.android.util.QrLinkValidator
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId

/** Tests of QR link checks, hub URL rules, live box letterboxing, status chip labels and time formatting. */
class SecurityAndUiLogicTest {

    /** Private-network hosts and localhost count as private; public ones don't. */
    @Test
    fun testQrLinkValidationPrivateHost() {
        // Valid private IPv4 addresses and localhost
        assertTrue(QrLinkValidator.isPrivateHost("127.0.0.1"))
        assertTrue(QrLinkValidator.isPrivateHost("localhost"))
        assertTrue(QrLinkValidator.isPrivateHost("192.168.0.5"))
        assertTrue(QrLinkValidator.isPrivateHost("192.168.1.100"))
        assertTrue(QrLinkValidator.isPrivateHost("10.0.0.1"))
        assertTrue(QrLinkValidator.isPrivateHost("172.16.0.1"))
        assertTrue(QrLinkValidator.isPrivateHost("172.31.255.255"))

        // Public and invalid addresses
        assertFalse(QrLinkValidator.isPrivateHost("8.8.8.8"))
        assertFalse(QrLinkValidator.isPrivateHost("172.32.0.1"))
        assertFalse(QrLinkValidator.isPrivateHost("172.15.0.1"))
        assertFalse(QrLinkValidator.isPrivateHost("example.com"))
        assertFalse(QrLinkValidator.isPrivateHost("evil.com"))
    }

    /** Pairing links parse, and look-alike or plain-HTTP public links are refused. */
    @Test
    fun testQrLinkParsingAndProtectionAgainstSubstringAttack() {
        // Malicious link containing private IP substring in query parameter
        val malicious = "http://example.com/pair?code=secret123&x=192.168.1.1"
        assertNull("Malicious link with private IP in query must be rejected", QrLinkValidator.parse(malicious))

        // Non-pair path
        assertNull(QrLinkValidator.parse("http://192.168.1.1/other?code=123"))

        // Valid local HTTP link with port
        val validLocal = "http://192.168.0.5:8100/pair?code=token-abc"
        val localInfo = QrLinkValidator.parse(validLocal)
        assertNotNull(localInfo)
        assertEquals("http", localInfo!!.scheme)
        assertEquals("192.168.0.5", localInfo.host)
        assertEquals(8100, localInfo.port)
        assertEquals("token-abc", localInfo.code)
        assertEquals("http://192.168.0.5:8100", localInfo.pairingOrigin)
        assertEquals("192.168.0.5", localInfo.hubToStore)

        // Valid HTTPS link
        val validHttps = "https://hub.example.com/pair?code=token-xyz"
        val httpsInfo = QrLinkValidator.parse(validHttps)
        assertNotNull(httpsInfo)
        assertEquals("https", httpsInfo!!.scheme)
        assertEquals("hub.example.com", httpsInfo.host)
        assertEquals("token-xyz", httpsInfo.code)
        assertEquals("https://hub.example.com", httpsInfo.pairingOrigin)
        assertEquals("https://hub.example.com", httpsInfo.hubToStore)
    }

    /** Hub addresses resolve to the right scheme, host and port. */
    @Test
    fun testPreferencesBaseUrlResolution() {
        // Local LAN HTTP: core on 8100, voice on 8000
        val localHub = "192.168.0.5"
        assertEquals("http://192.168.0.5:8100", HubUrls.resolve(localHub, 8100))
        assertEquals("http://192.168.0.5:8000", HubUrls.resolve(localHub, 8000))

        // Local LAN HTTP even if stored with port: extracts host and maps to correct port
        val hubWithPort = "http://192.168.0.5:8100"
        assertEquals("http://192.168.0.5:8100", HubUrls.resolve(hubWithPort, 8100))
        assertEquals("http://192.168.0.5:8000", HubUrls.resolve(hubWithPort, 8000))

        // Remote HTTPS: single origin for both
        val httpsHub = "https://hub.example.com"
        assertEquals("https://hub.example.com", HubUrls.resolve(httpsHub, 8100))
        assertEquals("https://hub.example.com", HubUrls.resolve(httpsHub, 8000))

        // A host name without a scheme is the remote HTTPS origin
        val namedHub = "meanwhile.example.com"
        assertEquals("https://meanwhile.example.com", HubUrls.resolve(namedHub, 8100))
        assertEquals("https://meanwhile.example.com", HubUrls.resolve(namedHub, 8000))

        // A bare documentation IP (RFC 5737) or localhost stays plain HTTP on the service's port
        assertEquals("http://203.0.113.10:8100", HubUrls.resolve("203.0.113.10", 8100))
        assertEquals("http://203.0.113.10:8000", HubUrls.resolve("203.0.113.10:8100", 8000))
        assertEquals("http://localhost:8000", HubUrls.resolve("localhost", 8000))

        // A host name is always the one HTTPS origin: no port, even when one or http:// was typed
        assertEquals("https://hub.example.com", HubUrls.resolve("http://hub.example.com:8100", 8000))
        assertEquals("https://hub.example.com", HubUrls.resolve("hub.example.com:8100", 8100))
        assertEquals("https://hub.example.com", HubUrls.resolve("https://hub.example.com/", 8000, useHttps = false))

        // An IP given as https:// (or with useHttps) stays HTTPS as typed
        assertEquals("https://192.168.0.5:8443", HubUrls.resolve("https://192.168.0.5:8443", 8100))
        assertEquals("https://192.168.0.5", HubUrls.resolve("192.168.0.5", 8100, useHttps = true))

        // The pairing QR code's hub resolves to the same HTTPS origin for core and voice
        val hub = QrLinkValidator.parse("https://hub.example.com/pair?code=abc")!!.hubToStore
        assertEquals("https://hub.example.com", HubUrls.resolve(hub, 8100))
        assertEquals("https://hub.example.com", HubUrls.resolve(hub, 8000))
    }

    /** Signed media paths are prefixed with the hub and keep their query byte for byte; absolute links are kept. */
    @Test
    fun testMediaLinksKeepTheirSignedQuery() {
        val signed = "/media/clips/desk/evt_1.mp4?exp=1761200000&sig=Ab-_9%2Bx="
        assertEquals("https://hub.example.com$signed", HubUrls.media("https://hub.example.com", signed))
        assertEquals("http://192.168.0.5:8100$signed", HubUrls.media("http://192.168.0.5:8100/", signed))
        val absolute = "https://cdn.example.com/x.jpg?sig=1"
        assertEquals(absolute, HubUrls.media("https://hub.example.com", absolute))
    }

    /** A portrait frame in a landscape view is pillarboxed and the box maps inside it. */
    @Test
    fun testLetterboxMathForPortraitStreamInLandscapeContainer() {
        // Container: 1600 x 900 (16:9 = 1.777f)
        val containerWidth = 1600f
        val containerHeight = 900f

        // Video stream: 9:16 portrait (0.5625f)
        val videoAspect = 9f / 16f

        val bounds = LiveBoxOverlayMath.calculateLetterboxBounds(
            containerWidth = containerWidth,
            containerHeight = containerHeight,
            frameAspectRatio = videoAspect
        )

        // Height matches container height
        assertEquals(900f, bounds.contentHeight, 0.01f)
        // Width is 900 * 9 / 16 = 506.25f
        assertEquals(506.25f, bounds.contentWidth, 0.01f)
        // Y offset is 0
        assertEquals(0f, bounds.offsetY, 0.01f)
        // X offset is (1600 - 506.25) / 2 = 546.875f
        assertEquals(546.875f, bounds.offsetX, 0.01f)

        // Bounding box at center of video frame (x=0.5, y=0.5, w=0.2, h=0.2)
        val rect = LiveBoxOverlayMath.mapNormalizedBox(
            normX = 0.5f,
            normY = 0.5f,
            normW = 0.2f,
            normH = 0.2f,
            bounds = bounds
        )

        // Scaled left X must be offsetX + 0.5f * contentWidth
        assertEquals(800f, rect.left, 0.01f)
        // Scaled top Y must be 0.5 * 900f
        assertEquals(450f, rect.top, 0.01f)
    }

    /** The status chip names who holds the object. */
    @Test
    fun testStatusChipLabelResolution() {
        // With actor name
        val text1 = StatusChipHelper.resolveLabel(SessionObjectState.WITH_PERSON, "Sarah")
        assertEquals("With Sarah", text1)

        // With owner
        val text2 = StatusChipHelper.resolveLabel(SessionObjectState.WITH_PERSON, "You")
        assertEquals("With you", text2)

        // With anonymous person
        val text3 = StatusChipHelper.resolveLabel(SessionObjectState.WITH_PERSON, null)
        assertEquals("With person", text3)

        // Unknown
        val text4 = StatusChipHelper.resolveLabel(SessionObjectState.UNKNOWN, null)
        assertEquals("Unknown", text4)

        // On surface
        val text5 = StatusChipHelper.resolveLabel(SessionObjectState.ON_SURFACE, null)
        assertEquals("On surface", text5)
    }

    /** UTC timestamps format in a given zone; blank or bad input gives "". */
    @Test
    fun testTimestampFormattingWithPinnedUtcTimeZone() {
        val iso = "2026-10-21T09:20:00Z"
        val formattedUtc = DateTimeUtils.formatTime(iso, zoneId = ZoneId.of("UTC"))
        assertEquals("09:20", formattedUtc)

        val empty = ""
        assertEquals("", DateTimeUtils.formatTime(empty))

        val invalid = "not-a-date"
        assertEquals("", DateTimeUtils.formatTime(invalid))
        assertEquals("", DateTimeUtils.formatTime("2026-10-21T09:20:00"))
    }

    /** Event times format in a local zone, with or without seconds. */
    @Test
    fun testEventFormattedTimeInLocalTimeZone() {
        val utcTimestamp = "2026-10-21T09:20:00Z"
        val kolkataZone = ZoneId.of("Asia/Kolkata")
        val formattedTime = DateTimeUtils.formatTime(utcTimestamp, zoneId = kolkataZone)
        // 09:20 UTC + 5h30m -> 14:50 IST
        assertEquals("14:50", formattedTime)

        val formattedSeconds = DateTimeUtils.formatTime(utcTimestamp, zoneId = kolkataZone, pattern = "HH:mm:ss")
        assertEquals("14:50:00", formattedSeconds)

        val event = Event(
            id = "evt_sample",
            happenedAt = utcTimestamp
        )
        assertEquals("14:50", DateTimeUtils.formatTime(event.happenedAt ?: "", zoneId = kolkataZone))
    }
}
