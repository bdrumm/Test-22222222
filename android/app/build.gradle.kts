plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

import java.util.Properties

val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "com.whichway.app"
    compileSdk = 37
    defaultConfig {
        applicationId = "com.whichway.app"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "0.1"
        // where the published data lives (Settings can change the source at run time)
        buildConfigField("String", "PUBLISHED_URL", "\"https://bdrumm.github.io/whichway/data/\"")
        buildConfigField("String", "DEFAULT_URL", "\"https://bdrumm.github.io/whichway/data/\"")
    }
    buildTypes {
        debug {
            // Like Local.xcconfig on iOS: a local server from `make serve` for the Debug build only, set in the
            // git-ignored local.properties as whichway.baseUrl (the emulator reaches the Mac at
            // http://10.0.2.2:8000/data/; a phone needs the Mac's address on the local network).
            val local = localProps.getProperty("whichway.baseUrl")?.trim().orEmpty()
            if (local.startsWith("http")) buildConfigField("String", "DEFAULT_URL", "\"$local\"")
        }
    }
    buildFeatures { compose = true; buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation(project(":core"))
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    implementation(platform("androidx.compose:compose-bom:2026.09.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.11.0")
}
