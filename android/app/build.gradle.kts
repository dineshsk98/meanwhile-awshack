// Build script for the Meanwhile Android app: SDK levels, the hub and wake word read from local.properties, dependencies.
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.google.services)
}

import java.util.Properties

val localProperties = Properties().apply {
    val localPropertiesFile = rootProject.file("local.properties")
    if (localPropertiesFile.exists()) {
        localPropertiesFile.inputStream().use { load(it) }
    }
}

val defaultHub = localProperties.getProperty("meanwhile.hub")
    ?: throw GradleException("meanwhile.hub is missing in local.properties; see local.properties.example")

val defaultCorePort = localProperties.getProperty("meanwhile.corePort")?.toIntOrNull()
    ?: throw GradleException("meanwhile.corePort is missing or invalid in local.properties; see local.properties.example")

val defaultVoicePort = localProperties.getProperty("meanwhile.voicePort")?.toIntOrNull()
    ?: throw GradleException("meanwhile.voicePort is missing or invalid in local.properties; see local.properties.example")

val wakeWord = localProperties.getProperty("meanwhile.wakeWord")?.trim()?.takeIf { it.isNotEmpty() && '"' !in it }
    ?: throw GradleException("meanwhile.wakeWord is missing or invalid in local.properties; see local.properties.example")

if (!file("google-services.json").exists()) {
    throw GradleException("app/google-services.json is missing. Copy your Firebase configuration file into the app/ directory.")
}

android {
    namespace = "com.meanwhile.android"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.meanwhile.android"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"

        buildConfigField("String", "DEFAULT_HUB_ADDRESS", "\"$defaultHub\"")
        buildConfigField("int", "DEFAULT_CORE_PORT", "$defaultCorePort")
        buildConfigField("int", "DEFAULT_VOICE_PORT", "$defaultVoicePort")
        buildConfigField("String", "WAKE_WORD", "\"$wakeWord\"")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }
    kotlin {
        jvmToolchain(21)
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }
    testOptions {
        unitTests {
            isReturnDefaultValues = true
        }
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.material3)
    implementation(libs.androidx.material.icons.extended)
    implementation(libs.androidx.navigation.compose)

    // Event clips
    implementation(libs.androidx.media3.exoplayer)
    implementation(libs.androidx.media3.ui)

    // Networking and JSON
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)

    // Camera thumbnails, frames and people photos
    implementation(libs.coil.compose)

    // Push notifications
    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.messaging)

    // Pairing QR codes: generating (ZXing) and scanning (CameraX + ML Kit)
    implementation(libs.zxing.core)
    implementation(libs.androidx.camera.camera2)
    implementation(libs.androidx.camera.lifecycle)
    implementation(libs.androidx.camera.view)
    implementation(libs.mlkit.barcode.scanning)

    // 16 KB page-aligned build of the graphics path library
    implementation(libs.androidx.graphics.path)

    // Live camera video
    implementation(libs.stream.webrtc)

    testImplementation(libs.junit)
    testImplementation(libs.mockwebserver)
}
