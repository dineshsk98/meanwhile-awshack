// Unit tests for the HTTP repository: which hosts get the key, pairing, auth errors and the core's error replies.
package com.meanwhile.android

import com.meanwhile.android.data.PreferencesProvider
import com.meanwhile.android.data.RemoteMeanwhileRepository
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/** Tests of RemoteMeanwhileRepository against MockWebServer: key scoping, pairing, 401 handling and error replies. */
class RemoteMeanwhileRepositoryTest {

    private lateinit var server: MockWebServer
    private lateinit var fakePrefs: TestPreferencesProvider
    private lateinit var repo: RemoteMeanwhileRepository

    /** Connection settings pointing at a mock server, with a fake key. */
    class TestPreferencesProvider(
        override var hubAddress: String,
        override var corePort: Int,
        override var voicePort: Int = 8000,
        override var apiKey: String = "test-key-not-real",
        override var useHttps: Boolean = false
    ) : PreferencesProvider {
        /** `http://hub:port`, or the hub itself when it has a scheme. */
        override fun getCoreBaseUrl(hub: String, port: Int): String {
            return if (hub.startsWith("http://") || hub.startsWith("https://")) {
                hub.removeSuffix("/")
            } else {
                "http://$hub:$port"
            }
        }

        /** Same rule as the core's URL. */
        override fun getVoiceBaseUrl(hub: String, port: Int): String {
            return if (hub.startsWith("http://") || hub.startsWith("https://")) {
                hub.removeSuffix("/")
            } else {
                "http://$hub:$port"
            }
        }
    }

    /** Starts the mock core and a repository pointed at it. */
    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        fakePrefs = TestPreferencesProvider(
            hubAddress = server.hostName,
            corePort = server.port,
            apiKey = "test-key-not-real"
        )
        repo = RemoteMeanwhileRepository(fakePrefs)
    }

    /** Stops the mock core. */
    @After
    fun tearDown() {
        server.shutdown()
    }

    /** The stored key goes to the configured hub. */
    @Test
    fun testKeyHeaderSentToConfiguredHub() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"lag_s": 4.5}"""))

        val lag = repo.getLagSeconds()
        assertEquals(4, lag)

        val recorded = server.takeRequest()
        assertEquals("test-key-not-real", recorded.getHeader("X-Meanwhile-Key"))
    }

    /** Pairing again with the same hub sends the old token, so the hub replaces that entry. */
    @Test
    fun testPairingAgainWithTheSameHubSendsTheOldTokenSoItIsReplaced() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"device_token": "paired-token-xyz"}"""))

        val response = repo.pairWithCode(server.url("/").toString(), "code-12345", "TestDevice")
        assertEquals("paired-token-xyz", response.deviceToken)

        val recorded = server.takeRequest()
        assertEquals("/api/pair", recorded.path)
        assertEquals("test-key-not-real", recorded.getHeader("X-Meanwhile-Key"))
    }

    /** Pairing with a QR code for another host never sends it the stored key. */
    @Test
    fun testKeyHeaderNeverSentToForeignHost() = runBlocking {
        val foreignServer = MockWebServer()
        foreignServer.start()
        try {
            foreignServer.enqueue(MockResponse().setResponseCode(200).setBody("""{"device_token": "paired-token-foreign"}"""))
            val pairResponse = repo.pairWithCode(foreignServer.url("/").toString(), "code-foreign-999", "TestPhone")
            assertEquals("paired-token-foreign", pairResponse.deviceToken)

            val recordedPair = foreignServer.takeRequest()
            assertEquals("/api/pair", recordedPair.path)
            assertNull("X-Meanwhile-Key must NOT be sent to a foreign host during pairing", recordedPair.getHeader("X-Meanwhile-Key"))
        } finally {
            foreignServer.shutdown()
        }
    }

    /** The voice side on its own port also gets the key. */
    @Test
    fun testKeyHeaderSentToVoiceServerOnItsOwnPort() = runBlocking {
        val voiceServer = MockWebServer()
        voiceServer.start()
        try {
            val prefs = TestPreferencesProvider(
                hubAddress = server.hostName,
                corePort = server.port,
                voicePort = voiceServer.port
            )
            val voiceRepo = RemoteMeanwhileRepository(prefs)
            voiceServer.enqueue(MockResponse().setResponseCode(200).setBody("""{"reply": "Okay."}"""))

            val reply = voiceRepo.processUserUtterance("what did I miss")
            assertEquals("Okay.", reply.text)

            val recorded = voiceServer.takeRequest()
            assertEquals("/voice/ask", recorded.path)
            assertEquals("test-key-not-real", recorded.getHeader("X-Meanwhile-Key"))
        } finally {
            voiceServer.shutdown()
        }
    }

    /** Ask replies listing cameras as objects or names give the camera chips. */
    @Test
    fun testAskReplyWithCameraObjectsIsRead() = runBlocking {
        val voiceServer = MockWebServer()
        voiceServer.start()
        try {
            val voiceRepo = RemoteMeanwhileRepository(
                TestPreferencesProvider(hubAddress = server.hostName, corePort = server.port, voicePort = voiceServer.port)
            )
            voiceServer.enqueue(MockResponse().setResponseCode(200).setBody(
                """{"reply": "I'm not watching anything right now.", "result": {"spoken": "x", "cameras": [{"camera_id": "desk", "label": "Desk", "online": false}]}}"""
            ))
            assertEquals("I'm not watching anything right now.", voiceRepo.processUserUtterance("what are you watching?").text)

            voiceServer.enqueue(MockResponse().setResponseCode(200).setBody(
                """{"reply": "Which camera?", "result": {"status": "clarify", "question": "Which camera?", "cameras": [{"camera_id": "desk", "label": "Desk"}, "Room"]}}"""
            ))
            assertEquals(listOf("Desk", "Room"), voiceRepo.processUserUtterance("watch my keys").clarificationOptions)
        } finally {
            voiceServer.shutdown()
        }
    }

    /** Rename and remove call the core and surface its refusal message. */
    @Test
    fun testRenameAndRemoveCameraUseTheCoreAndSurfaceItsRefusal() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"camera": {"camera_id": "cam-room", "id": "cam-room", "label": "Room", "online": true}}"""))
        val renamed = repo.renameCamera("cam-room", " Room ")
        assertEquals("Room", renamed.label)
        val patch = server.takeRequest()
        assertEquals("PATCH", patch.method)
        assertEquals("/api/cameras/cam-room", patch.path)
        assertEquals("""{"label":"Room"}""", patch.body.readUtf8())

        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"error": "camera_in_use", "message": "This camera is watching the current session. End the session first."}"""))
        try {
            repo.removeCamera("cam-room")
            fail("removeCamera must throw when the core refuses")
        } catch (e: RuntimeException) {
            assertTrue(e.message!!.startsWith("This camera is watching"))
        }
        assertEquals("DELETE", server.takeRequest().method)
    }

    /** A pairing code with a fractional `expires_in_s` parses. */
    @Test
    fun testPairingCodeAcceptsFractionalExpiry() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(201).setBody(
                """{"code": "c1", "pair_url": "https://hub.example.test/pair?code=c1", "expires_at": "2026-10-21T13:17:58.624Z", "expires_in_s": 120.0, "qr_svg": "<svg/>"}"""
            )
        )
        val code = repo.createPairingCode()
        assertEquals("c1", code.code)
        assertEquals(120.0, code.expiresInS!!, 0.0)
    }

    /** A 401 sets authError and the next success clears it. */
    @Test
    fun test401SetsAuthErrorAnd200ClearsIt() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail": "Unauthorized"}"""))

        // getLagSeconds returns null on error
        val lag = repo.getLagSeconds()
        assertNull(lag)

        assertTrue("401 response must set authError to true", repo.authError.first())

        // Next request succeeds with 200
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"lag_s": 10.0}"""))
        val lag2 = repo.getLagSeconds()
        assertEquals(10, lag2)

        assertFalse("Successful 200 response must clear authError", repo.authError.first())
    }

    /** A status without `lag_s` gives null, never an invented number. */
    @Test
    fun testMissingLagGivesNullNeverAnInventedValue() = runBlocking {
        // Empty status: no lag, not a made-up number
        server.enqueue(MockResponse().setResponseCode(200).setBody("{}"))
        val lag = repo.getLagSeconds()
        assertNull("Missing lag_s must produce null", lag)
    }

    /** Media links in lists come back as hub + the API's path with the signed query untouched. */
    @Test
    fun testSignedMediaLinksAreUsedAsGiven() = runBlocking {
        val clip = "/media/clips/desk/evt_1.mp4?exp=1761200000&sig=Ab-_9%2Bx="
        val frame = "/media/frames/desk/evt_1.jpg?exp=1761200000&sig=Zz9"
        server.enqueue(MockResponse().setResponseCode(200).setBody(
            """{"events": [{"event_id": "evt_1", "id": "evt_1", "summary": "s", "clip_url": "$clip", "frame_url": "$frame"}]}"""
        ))
        val event = repo.observeEvents("ses_1").first().single()
        val origin = server.url("/").toString().removeSuffix("/")
        assertEquals(origin + clip, event.clipUrl)
        assertEquals(origin + frame, event.frameUrl)

        server.enqueue(MockResponse().setResponseCode(200).setBody(
            """{"cameras": [{"camera_id": "desk", "id": "desk", "label": "Desk", "thumb_url": "/media/thumbs/desk/1.jpg?exp=1&sig=q"}]}"""
        ))
        server.takeRequest()
        assertEquals("$origin/media/thumbs/desk/1.jpg?exp=1&sig=q", repo.observeCameras().first().single().thumbUrl)
    }

    /** deletePerson throws the core's message on an error status. */
    @Test
    fun testDeletePersonThrowsOnNon2xx() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"message": "Person not found"}"""))

        try {
            repo.deletePerson("person-not-exist")
            fail("deletePerson must throw on non-2xx status code")
        } catch (e: RuntimeException) {
            assertTrue(e.message?.contains("Person not found") == true)
        }
    }
}
