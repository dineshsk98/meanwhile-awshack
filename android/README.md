# Meanwhile Android app

The Meanwhile phone app (Kotlin, Jetpack Compose): live camera video, the timeline of events, people, alerts and the
Ask sheet. It talks to a running Meanwhile core and voice side (see the [root README](../README.md)).

## Prerequisites
- Android Studio (Ladybug or newer). Its bundled JDK (`jbr`, Java 21) builds the app.
- A phone with Android 8.0 (API 26) or newer, with USB debugging on.
- A Firebase project, for push alerts.

## Set up
1. Open `android/` in Android Studio. It installs the Android SDK and writes `sdk.dir` to `local.properties`.
2. Copy `local.properties.example` to `local.properties` and set `meanwhile.hub`, `meanwhile.corePort`,
   `meanwhile.voicePort` and `meanwhile.wakeWord`. The hub and ports are only the app's first address; pairing
   replaces them.
   - A host name (the cloud box) means `https://<host>` for everything, core and voice, with no port.
   - A LAN IP address or `localhost` (a laptop running the core) means plain HTTP on the two ports. Only debug builds
     allow plain HTTP.
3. In the Firebase console, add an Android app with the package name `com.meanwhile.android`, download its
   `google-services.json` and put it in `app/` (`app/google-services.json`).

`local.properties` and `app/google-services.json` are git-ignored; the build stops with a message if either is missing.

## Build and install
From `android/` in PowerShell, with `JAVA_HOME` set to Android Studio's `jbr`:

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew.bat assembleDebug
.\gradlew.bat installDebug
```

Or press **Run** in Android Studio. The debug APK is in `app/build/outputs/apk/debug/`.

## Pair with a hub
1. On a browser that is already paired with the core, open **Pair a device** (`/debug.html`) to show a one-time QR code.
2. In the app, a short tutorial opens on first run. Then open **Settings → Re-pair** and tap **Scan to Pair**, or
   **Enter Code** to type the code instead.
3. Confirm the hub address and tap **Pair**. The app then uses the hub from the QR code's link (`https://<host>` on
   the cloud box). Home shows the cameras once the app is connected.

To pair another phone, open **Settings → Pair** on a paired phone and scan its QR code.

## Talk to it hands-free
While the app is open, it listens for the wake word (`meanwhile.wakeWord`, e.g. `Alexa`) and shows
"Listening for "Alexa"" in the top bar. Say the request after it: "Alexa, I'm stepping away, watch my bag". Or say
"Alexa", wait for "Yes?", then say the request. The Ask sheet opens with the exchange and the reply is spoken.
Turn it off in **Settings → Voice**. It needs the microphone permission and stops when the app is in the background.

## Run the unit tests
```powershell
.\gradlew.bat testDebugUnitTest
```

The tests need `local.properties` and `app/google-services.json` to exist; they don't contact the hub or Firebase.
Reports are in `app/build/reports/tests/testDebugUnitTest/`.
