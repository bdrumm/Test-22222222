#!/usr/bin/env bash
# Archive the iOS app (Release) and upload it to TestFlight, or just export the .ipa.
#   scripts/testflight.sh --upload        # archive, then upload to App Store Connect
#   scripts/testflight.sh                 # archive and export build/WhichWay.ipa (upload it with Transporter)
#   BUILD=42 scripts/testflight.sh ...    # a build number of your own (default: the minute, e.g. 202609261615)
#
# Signing uses the team in ios/WhichWay/Config/Local.xcconfig (DEVELOPMENT_TEAM) through Xcode's automatic
# signing. The upload authenticates with an App Store Connect API key when these are set:
#   ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_PATH (the .p8 file)   -- App Store Connect > Users and Access > Integrations
# else with the Apple ID signed in to Xcode (Xcode > Settings > Accounts).
set -euo pipefail
cd "$(dirname "$0")/../ios/WhichWay"
UPLOAD=0; ORGANIZER=0; for a in "$@"; do [ "$a" = "--upload" ] && UPLOAD=1; [ "$a" = "--organizer" ] && ORGANIZER=1; done
BUILD="${BUILD:-$(date +%Y%m%d%H%M)}"
TEAM="${WHICHWAY_TEAM:-$(sed -n 's/^DEVELOPMENT_TEAM *= *//p' Config/Local.xcconfig 2>/dev/null | tail -1)}"
[ -n "$TEAM" ] || { echo "no team: put DEVELOPMENT_TEAM = <id> in Config/Local.xcconfig or set WHICHWAY_TEAM"; exit 1; }
BUNDLE="$(sed -n 's/^PRODUCT_BUNDLE_IDENTIFIER *= *//p' Config/Local.xcconfig 2>/dev/null | tail -1)"
AUTH=()
if [ -n "${ASC_KEY_ID:-}" ] && [ -n "${ASC_ISSUER_ID:-}" ] && [ -n "${ASC_KEY_PATH:-}" ]; then
  AUTH=(-authenticationKeyID "$ASC_KEY_ID" -authenticationKeyIssuerID "$ASC_ISSUER_ID" -authenticationKeyPath "$ASC_KEY_PATH")
fi
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
if [ ${#AUTH[@]} -eq 0 ]; then
  echo "note: no App Store Connect API key (ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_PATH). The archive will work, but"
  echo "      the export or upload needs the key from the command line, or use Xcode > Window > Organizer > Distribute App."
fi

say "1/3 Archive (Release, build $BUILD, team $TEAM${BUNDLE:+, $BUNDLE})"
rm -rf build/WhichWay.xcarchive
xcodebuild -project WhichWay.xcodeproj -scheme WhichWay -configuration Release -destination 'generic/platform=iOS' \
  -archivePath build/WhichWay.xcarchive CURRENT_PROJECT_VERSION="$BUILD" \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration -skipMacroValidation ${AUTH[@]+"${AUTH[@]}"} archive \
  | grep -E "error:|warning: .*(icon|Icon)|\*\* ARCHIVE" || true
[ -d build/WhichWay.xcarchive ] || { echo "archive failed"; exit 1; }
if [ "$ORGANIZER" = 1 ]; then
  D="$HOME/Library/Developer/Xcode/Archives/$(date +%Y-%m-%d)"; mkdir -p "$D"
  DEST="$D/WhichWay $(date '+%-m-%-d-%y, %-I.%M %p').xcarchive"; cp -R build/WhichWay.xcarchive "$DEST"
  echo "archive in Xcode's Organizer: $DEST"; open -a Xcode; osascript -e 'tell application "Xcode" to activate' >/dev/null 2>&1 || true
  echo "Xcode > Window > Organizer > Archives > Distribute App > TestFlight & App Store"; exit 0
fi

say "2/3 Export options"
DEST=export; [ "$UPLOAD" = 1 ] && DEST=upload
cat > build/ExportOptions.plist <<EOF
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
EOF

say "3/3 $([ "$UPLOAD" = 1 ] && echo "Upload to App Store Connect" || echo "Export the .ipa")"
rm -rf build/export
xcodebuild -exportArchive -archivePath build/WhichWay.xcarchive -exportOptionsPlist build/ExportOptions.plist \
  -exportPath build/export -allowProvisioningUpdates ${AUTH[@]+"${AUTH[@]}"} \
  | grep -E "error:|EXPORT|Upload|upload" || true
if [ ! -d build/export ] || [ -z "$(ls -A build/export 2>/dev/null)" ]; then
  cat <<MSG

The export needs App Store signing, which from a script only works with an App Store Connect API key:
  App Store Connect > Users and Access > Integrations > App Store Connect API > Team Keys > +  (role: App Manager)
  download the .p8 once, then:  ASC_KEY_ID=XXXXXXXXXX ASC_ISSUER_ID=<issuer uuid> ASC_KEY_PATH=~/AuthKey_XXXXXXXXXX.p8 $0 $*
Or distribute this same archive from Xcode: Window > Organizer > Archives > Distribute App (the archive is
also copied there by the ios-organizer target).
MSG
  exit 1
fi
if [ "$UPLOAD" = 1 ]; then
  cat <<MSG

Uploaded build $BUILD. In App Store Connect > TestFlight it appears after processing (a few minutes). Add
testers to a group, or use "Internal Testing" for your own account; testers get it in the TestFlight app.
MSG
else
  ls -la build/export/*.ipa 2>/dev/null && echo "Upload with the Transporter app, or run again with --upload." || { echo "export failed"; exit 1; }
fi
