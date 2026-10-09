#!/usr/bin/env bash
# Archive the iOS app (Release) and upload it to TestFlight, or just export the .ipa.
#   scripts/testflight.sh --upload        # archive, then upload to App Store Connect (TestFlight)
#   scripts/testflight.sh                 # archive and export build/WhichWay.ipa (upload it with Transporter)
#   scripts/testflight.sh --organizer     # archive and hand it to Xcode's Organizer (Distribute App from there)
#   BUILD=42 scripts/testflight.sh ...    # a build number of your own (default: the minute, e.g. 202609261615)
#
# Signing uses the team in ios/WhichWay/Config/Local.xcconfig (DEVELOPMENT_TEAM) through Xcode's automatic
# signing. Talking to Apple (the distribution certificate, the App Store profiles, the upload) needs one of:
#   - the Apple ID of that team signed in to Xcode (Xcode > Settings > Accounts > +), or
#   - an App Store Connect API key: ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_PATH (the .p8 file), from
#     App Store Connect > Users and Access > Integrations > App Store Connect API > Team Keys (role App Manager).
# The upload also needs the app record in App Store Connect (My Apps > + > New App, with the bundle id).
# Full xcodebuild output goes to ios/WhichWay/build/archive.log and build/export.log.
set -euo pipefail
cd "$(dirname "$0")/../ios/WhichWay"
UPLOAD=0; ORGANIZER=0; for a in "$@"; do [ "$a" = "--upload" ] && UPLOAD=1; [ "$a" = "--organizer" ] && ORGANIZER=1; done
BUILD="${BUILD:-$(date +%Y%m%d%H%M)}"
TEAM="${WHICHWAY_TEAM:-$(sed -n 's/^DEVELOPMENT_TEAM *= *//p' Config/Local.xcconfig 2>/dev/null | tail -1)}"
[ -n "$TEAM" ] || { echo "no team: put DEVELOPMENT_TEAM = <id> in Config/Local.xcconfig or set WHICHWAY_TEAM"; exit 1; }
BUNDLE="$(sed -n 's/^PRODUCT_BUNDLE_IDENTIFIER *= *//p' Config/Local.xcconfig 2>/dev/null | tail -1)"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
errors() { grep -E -A2 "^error:|error: " "$1" | grep -vE "^--$" | sed 's/^/  /' | head -40; }

AUTH=()
if [ -n "${ASC_KEY_ID:-}" ] && [ -n "${ASC_ISSUER_ID:-}" ] && [ -n "${ASC_KEY_PATH:-}" ]; then
  AUTH=(-authenticationKeyID "$ASC_KEY_ID" -authenticationKeyIssuerID "$ASC_ISSUER_ID" -authenticationKeyPath "$ASC_KEY_PATH")
  echo "Apple: App Store Connect API key $ASC_KEY_ID"
else
  # Xcode keeps its signed-in Apple IDs here (the Prod list); empty means Xcode > Settings > Accounts has none.
  ACCOUNTS="$(defaults read com.apple.dt.Xcode DVTDeveloperAccountManagerAppleIDLists 2>/dev/null | sed -n '/IDE.Identifiers.Prod/,/);/p' | grep -cE 'identifier = |^[[:space:]]+"[^"]+",?$' || true)"
  if [ "${ACCOUNTS:-0}" = 0 ] && [ "$ORGANIZER" = 0 ] && [ -z "${WHICHWAY_SKIP_CHECK:-}" ]; then
    cat <<MSG
No Apple ID is signed in to Xcode and no App Store Connect API key is set, so the export or upload would fail
with "No Accounts" after the archive. Either:
  - Xcode > Settings > Accounts > + > Apple ID: sign in with the account of team $TEAM, then run this again, or
  - export ASC_KEY_ID=XXXXXXXXXX ASC_ISSUER_ID=<issuer uuid> ASC_KEY_PATH=~/AuthKey_XXXXXXXXXX.p8  (see the header)
(WHICHWAY_SKIP_CHECK=1 runs anyway; --organizer archives without talking to Apple.)
MSG
    exit 1
  fi
  [ "${ACCOUNTS:-0}" = 0 ] || echo "Apple: the Apple ID signed in to Xcode"
fi

say "1/3 Archive (Release, build $BUILD, team $TEAM${BUNDLE:+, $BUNDLE})"
mkdir -p build; rm -rf build/WhichWay.xcarchive
xcodebuild -project WhichWay.xcodeproj -scheme WhichWay -configuration Release -destination 'generic/platform=iOS' \
  -archivePath build/WhichWay.xcarchive CURRENT_PROJECT_VERSION="$BUILD" \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration -skipMacroValidation ${AUTH[@]+"${AUTH[@]}"} archive \
  > build/archive.log 2>&1 || true
grep -E "warning: .*(icon|Icon)|\*\* ARCHIVE" build/archive.log || true
[ -d build/WhichWay.xcarchive ] || { echo "archive failed (build/archive.log):"; errors build/archive.log; exit 1; }
if [ "$ORGANIZER" = 1 ]; then
  D="$HOME/Library/Developer/Xcode/Archives/$(date +%Y-%m-%d)"; mkdir -p "$D"
  DEST="$D/WhichWay $(date '+%-m-%-d-%y, %-I.%M %p').xcarchive"; cp -R build/WhichWay.xcarchive "$DEST"
  echo "archive in Xcode's Organizer: $DEST"; open -a Xcode; osascript -e 'tell application "Xcode" to activate' >/dev/null 2>&1 || true
  echo "Xcode > Window > Organizer > Archives > Distribute App > TestFlight & App Store"; exit 0
fi

say "2/3 Export options"
DEST=export; [ "$UPLOAD" = 1 ] && DEST=upload
cat > build/ExportOptions.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>method</key><string>app-store-connect</string>
	<key>destination</key><string>$DEST</string>
	<key>teamID</key><string>$TEAM</string>
	<key>signingStyle</key><string>automatic</string>
	<key>uploadSymbols</key><true/>
	<key>manageAppVersionAndBuildNumber</key><false/>
</dict>
</plist>
PLIST

say "3/3 $([ "$UPLOAD" = 1 ] && echo "Upload to App Store Connect" || echo "Export the .ipa")"
rm -rf build/export
xcodebuild -exportArchive -archivePath build/WhichWay.xcarchive -exportOptionsPlist build/ExportOptions.plist \
  -exportPath build/export -allowProvisioningUpdates ${AUTH[@]+"${AUTH[@]}"} > build/export.log 2>&1 || true
grep -E "EXPORT|Upload succeeded|Uploaded" build/export.log || true
if grep -q "EXPORT FAILED" build/export.log || ! grep -q "EXPORT SUCCEEDED" build/export.log; then
  echo "export failed (build/export.log):"; errors build/export.log
  cat <<MSG

"No Accounts": sign in to Xcode (Settings > Accounts) or set the API key (see the header).
"No suitable application records": create the app in App Store Connect (My Apps > + > New App, bundle id $BUNDLE).
The archive is still usable from Xcode: Window > Organizer > Archives > Distribute App (make ios-organizer copies it there).
MSG
  exit 1
fi
if [ "$UPLOAD" = 1 ]; then
  cat <<MSG

Uploaded build $BUILD. In App Store Connect > TestFlight it appears after processing (a few minutes). Add it to
an internal group (your own App Store Connect users, no review) or an external group (anyone with the invite
or the public link, after Beta App Review of the first build); testers install through the TestFlight app.
MSG
else
  ls -la build/export/*.ipa 2>/dev/null && echo "Upload with the Transporter app, or run again with --upload." || { echo "export failed"; exit 1; }
fi
