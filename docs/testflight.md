# WhichWay on TestFlight

How a build gets from this Mac to testers' phones, and what App Store Connect needs from you along the way.
The build side is `make ios-testflight` (`scripts/testflight.sh`); everything else happens once in App Store
Connect, in a browser, with the Apple ID of the team in `ios/WhichWay/Config/Local.xcconfig`.

## Once

1. **Apple ID in Xcode.** *Xcode > Settings > Accounts > +*, sign in with the developer account (team
   `8JRNGAPMU7`, "Baron Drumm"). The script needs this to create the Apple Distribution certificate and the
   App Store provisioning profiles for `com.barondrumm.whichway` and `com.barondrumm.whichway.LiveActivity`, and
   to upload. (Alternative, for a Mac without the account: an App Store Connect API key, see the README.)
2. **The app record.** [App Store Connect](https://appstoreconnect.apple.com) > *My Apps > + > New App*:
   platform iOS, name **WhichWay**, primary language English (U.S.), bundle ID `com.barondrumm.whichway` (it is
   in the list because automatic signing registered it; if not, register it first at
   developer.apple.com > Identifiers), SKU `whichway`, full access. The name must be unique on the App Store;
   if "WhichWay" is taken, "WhichWay NYC" or similar, which can change later.
3. **Upload.** From the repository root, `make ios-testflight`. The archive takes about two minutes; the first
   upload also creates the certificate and profiles. The build shows up under the app's *TestFlight* tab after
   a few minutes of processing. (No export-compliance question: the app declares standard HTTPS only.)

## Testers

For internal testers only (the current plan), the three steps above plus the first bullet are the whole job;
Test Information, Beta App Review and the paste-ready texts below only matter for external testers.

- **Internal testing** (up to 100 people who are users of your App Store Connect team): *TestFlight > Internal
  Testing > +*, name the group, enable automatic distribution, add the users. No review; they get every build.
  Start here with yourself.
- **External testing** (up to 10,000 people, by email invite or a public link): *TestFlight > External Testing
  > +*, name the group (say "Riders"), add the build. The first build in an external group goes through Beta
  App Review (usually within a day or two; later builds normally skip it). Before Apple takes it, fill in *Test
  Information* (sidebar, under *Additional*): the texts below. Then either add testers by email, or *Enable
  Public Link* and put the link on trywhichway.com in place of the "Ask for an invite" mailto.
- Testers need the TestFlight app from the App Store and iOS 17 or later; builds expire after 90 days, so
  upload a new one at least that often. Feedback they send from TestFlight (screenshots, crash reports) arrives
  under *TestFlight > Feedback*.

## Paste-ready texts

**Beta app description** (shown to testers in TestFlight):

> WhichWay is a subway assistant for New York. Pick where you're going, or let it start the trip on its own
> when you walk up to your station, and it tells you which train to take and when you'll arrive, with its own
> prediction engine on top of the MTA's live feeds. On the train it notices you're riding, follows the ride and
> keeps the fastest way from the train you're on, including when to change. This beta is about the predictions
> and the ride detection: please ride with it and tell us where it was right and where it was wrong.

**What to test** (per build, optional):

> Open the app near a station and see whether the route starts on its own. Compare the predicted arrival with
> your actual arrival. Watch whether "On the …" appears once the train pulls away, and whether a change is
> suggested at the right stop. If you turn on "Improve the predictions" in Settings, the phone's motion during a
> trip is summarised and sent as one record per trip; nothing identifying and no location is ever sent.

**Feedback email:** info@parametric.space
**Marketing URL:** https://trywhichway.com
**Privacy policy URL:** https://trywhichway.com/privacy.html (the page is in the trywhichway repository)

**Beta App Review information:** contact first name, last name, phone, email (yours). *Sign-in required:* no;
the app has no accounts. *Notes* for the reviewer:

> No sign-in. The app reads the MTA's public GTFS-Realtime feeds and published prediction tables. Location
> (when in use) is used to suggest the nearest station and to notice when the rider reaches a station while a
> route is in progress; it stays on the phone. The "location" background mode keeps a trip in progress updated
> while the phone is in a pocket; the Live Activity shows the countdown on the Lock Screen. Motion data is only
> read if the rider opts in under Settings > Improve the predictions, and only a per-second summary is kept.
> To try a route: Go tab, From "14 St-Union Sq", To "Jay St-MetroTech", or any pair of stations.

## If something fails

- `No Accounts` in `ios/WhichWay/build/export.log`: step 1 above.
- `No suitable application records were found`: step 2 above, and check the bundle id matches `Local.xcconfig`.
- `ITMS-90xxx` emails from Apple after an upload are warnings about the bundle (icon, Info.plist keys,
  privacy manifest); the build still processes unless the mail says it was rejected.
- The archive itself never needs the account: `make ios-organizer` builds it and opens Xcode's Organizer, where
  *Distribute App > TestFlight & App Store* does the same upload with whatever account Xcode has.

## Uploading without an Xcode sign-in (the API key)

Set up on Oct 10 2026 after Xcode's App Store Connect session lapsed and `make ios-testflight` failed with
"Failed to Use Accounts". Three pieces, each made once:

1. **An App Store Connect API key** (Users and Access > Integrations > App Store Connect API > Team Keys, role
   App Manager). The `.p8` lives outside the repository, with its Key ID and the page's Issuer ID exported in
   `~/.zshrc` as `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_PATH`. The upload script uses them when they are set.
2. **An Apple Distribution certificate on this Mac.** The team key cannot use Xcode's cloud-managed certificate
   (Apple's "Access to Cloud Managed Distribution Certificate" is not offered for a Team Key), so the export signs
   with a local one: a CSR made with `openssl req -new -newkey rsa:2048 -nodes -keyout distribution.key -out
   distribution.csr -subj "/emailAddress=…/CN=…/C=US"`, uploaded at developer.apple.com > Certificates > + >
   Apple Distribution, the downloaded `distribution.cer` and the key imported into the login keychain as a legacy
   PKCS#12 (`openssl pkcs12 -export -legacy …` then `security import … -T /usr/bin/codesign`; OpenSSL 3's default
   PKCS#12 format is one the keychain rejects). Certificates last a year; Apple allows three per team.
3. **App Store profiles that name that certificate**, one per bundle id (`WhichWay App Store`,
   `WhichWay LiveActivity App Store`), made through the API by `scripts/asc_profiles.py path/to/distribution.cer
   --install` (also what to run after renewing the certificate). Xcode's own "iOS Team Store Provisioning Profile"s
   only carry the cloud-managed certificate and cannot be regenerated with the key.

With the three in place `make ios-testflight` archives, signs by hand with that certificate and those profiles,
and uploads. Build 202610101120 went up this way.
