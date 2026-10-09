// WhichWay for Android. Lives beside the iOS app and never builds or edits anything under ios/; the one link is
// read-only: the core tests check the Kotlin predictor against the iOS package's Python-generated fixture.
pluginManagement {
    repositories { google(); mavenCentral(); gradlePluginPortal() }
}
dependencyResolutionManagement {
    repositories { google(); mavenCentral() }
}
rootProject.name = "WhichWay"

include(":core")

// The app needs the Android SDK; the core is plain Kotlin and builds and tests with only a JDK. Without an SDK
// (no ANDROID_HOME, no sdk.dir in local.properties) the app module is left out instead of failing the build.
val localSdk = file("local.properties").takeIf { it.exists() }?.readLines()
    ?.firstOrNull { it.startsWith("sdk.dir=") }?.substringAfter("=")
val sdk = System.getenv("ANDROID_HOME") ?: System.getenv("ANDROID_SDK_ROOT") ?: localSdk
if (sdk != null && file(sdk).exists()) include(":app")
else logger.lifecycle("WhichWay: no Android SDK found, building :core only (install Android Studio for :app)")
