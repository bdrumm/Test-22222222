// The non-UI port of ios/WhichWay/WhichWay/{Models,Core}: plain Kotlin on the JVM, so it is tested with a JDK
// alone and the app depends on it as an ordinary library.
plugins {
    kotlin("jvm")
    kotlin("plugin.serialization")
}

// bytecode for Java 17 (the app's level), built with whatever JDK runs Gradle (Android Studio's own is fine)
java { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
kotlin { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }

dependencies {
    // api: the app decodes and stores with the same Json instance (WWJson) and serializers
    api("org.jetbrains.kotlinx:kotlinx-serialization-json:1.11.0")
    testImplementation(kotlin("test"))
    testImplementation("junit:junit:4.13.2")
}

tasks.test {
    // the predictor fixture is the iOS package's (regenerated there by `make ios-fixtures`), read in place
    systemProperty("whichway.fixture", rootDir.resolve("../ios/WhichWayCore/Tests/WhichWayCoreTests/Fixtures/predictor_fixture.json").absolutePath)
    // the real published schedule, when a local site build exists (optional; the test skips without it)
    systemProperty("whichway.site", rootDir.resolve("../_site/data").absolutePath)
    testLogging { events("failed"); exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL }
}
