# WhichWay for Android

A periodic port of the iOS app (`ios/WhichWay`). **The iOS app leads**: features land there first, and Android
catches up in occasional port passes. Everything Android lives under `android/` and builds on its own; nothing
here builds, edits or depends on Xcode, and the iOS workflow (`make ios*`, `.github/workflows/ios.yml`) is
unchanged by it.

## Layout

```
android/
  core/      plain Kotlin (JVM): the published data, the GTFS-Realtime decoder, the prediction engine, the line
             board, the station graph and the planner. Tested with a JDK alone.
  app/       the Compose app: Go, Line and Settings tabs over the core (needs the Android SDK).
  PORTING.md which Kotlin file mirrors which Swift file, what is not ported yet, and the iOS commit of the last pass.
  scripts/ios-drift.sh   what changed on iOS since that pass.
```

The one link to `ios/` is read-only: the core tests load the iOS package's predictor fixture
(`ios/WhichWayCore/Tests/WhichWayCoreTests/Fixtures/predictor_fixture.json`, written from the Python reference
by `make ios-fixtures`), so the Kotlin engine is held to the same numbers as the Swift one.

## Build

The core needs only JDK 17:

```bash
cd android && ./gradlew :core:test
```

The app needs the Android SDK. Install Android Studio (it brings the SDK and asks you to accept its licences),
then open the `android/` folder in it, or from the command line with Studio's own JDK:

```bash
cd android && JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" ./gradlew :app:assembleDebug
```

The build runs on Gradle 9.7 with the Android Gradle Plugin 9.4 (which compiles Kotlin itself) and Kotlin 2.4,
targeting API 37; Gradle 9.7 runs on the JDK 25 that Android Studio bundles. To run it on the emulator:

```bash
cd android && ./gradlew :app:installDebug && ~/Library/Android/sdk/platform-tools/adb shell am start -n com.whichway.app/.MainActivity
```

Without an SDK the build includes only `:core`, so the core tests still run. `local.properties` (written by
Android Studio, git-ignored) or `ANDROID_HOME` tells Gradle where the SDK is.

The app starts on the published site. For the local server (`make serve` on the Mac), put
`whichway.baseUrl=http://localhost:8000/data/` in `android/local.properties` (git-ignored), as `Local.xcconfig`
does for the iOS Debug build, and run the app with `scripts/emulator.sh`: it builds, installs, tunnels the
device's `localhost:8000` to the Mac's with `adb reverse` (the Mac's firewall blocks the emulator's own route to
the host) and launches. Settings › Developer can switch the source at run time.

`.github/workflows/android.yml` runs the core tests and builds the app on every push that touches `android/` or
the shared fixture, and only then.

## A port pass

See [PORTING.md](PORTING.md). In short: `scripts/ios-drift.sh`, port what moved, run the tests, bump the
recorded iOS commit, and commit only `android/`.
