#!/usr/bin/env bash
# Build the Debug app, put it on the running emulator (or the connected phone) and launch it. `localhost:8000`
# inside the device is tunnelled to the Mac's local server with `adb reverse`, so the Debug build reaches
# `make serve` the way the iOS Simulator does, with no firewall change (the Mac's firewall blocks the emulator's
# 10.0.2.2 route). Needs Android Studio (its JDK and SDK); start an emulator from Studio or with
#   ~/Library/Android/sdk/emulator/emulator -avd <name>
set -euo pipefail
cd "$(dirname "$0")/.."
SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
ADB="$SDK/platform-tools/adb"
export JAVA_HOME="${JAVA_HOME:-/Applications/Android Studio.app/Contents/jbr/Contents/Home}"
[ -x "$ADB" ] || { echo "no adb at $ADB (install Android Studio)"; exit 1; }
"$ADB" get-state >/dev/null 2>&1 || { echo "no device: start an emulator in Android Studio first"; exit 1; }
# the app must not be in front while it is replaced: Android 17 otherwise leaves a system "Updating…" screen
# on top that only a Back, Home and a fresh start clear
"$ADB" shell am force-stop com.whichway.app >/dev/null 2>&1 || true
"$ADB" shell input keyevent KEYCODE_HOME >/dev/null 2>&1 || true
./gradlew :app:installDebug --console=plain -q
"$ADB" reverse tcp:8000 tcp:8000 >/dev/null
"$ADB" shell input keyevent KEYCODE_BACK >/dev/null 2>&1 || true
"$ADB" shell am start -n com.whichway.app/.MainActivity >/dev/null
echo "WhichWay launched; localhost:8000 on the device is this Mac's port 8000"
