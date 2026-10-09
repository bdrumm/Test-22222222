// Android Gradle Plugin 9 compiles Kotlin itself (no org.jetbrains.kotlin.android); Gradle 9.7 runs on the JDK 25
// that Android Studio bundles.
plugins {
    kotlin("jvm") version "2.4.20" apply false
    kotlin("plugin.serialization") version "2.4.20" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.20" apply false
    id("com.android.application") version "9.4.1" apply false
}
