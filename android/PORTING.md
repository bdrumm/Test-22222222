# Porting ledger: iOS → Android

The iOS app leads. Android catches up in occasional, isolated port passes; nothing here is ever a reason to
change `ios/`. This file records what each Kotlin file mirrors and the iOS commit it was last brought level with,
so the next pass knows exactly what moved.

**Last port pass:** iOS at `21bc708` (2026-10-09).

`scripts/ios-drift.sh` lists the iOS files changed since that commit, with the Kotlin file each maps to.

## Ported

| iOS source | Kotlin (`android/…`) | Notes |
|---|---|---|
| `Models/Schedule.swift`, `Models/Geometry.swift` | `core/…/Schedule.kt` | `client_lines.json` parsed by hand (array rows) |
| `Models/GTFSRealtime.swift` | `core/…/GtfsRealtime.kt` | same wire decoder, no protobuf dependency |
| `Models/Alerts.swift` | `core/…/Alerts.kt` | |
| `Core/Predictor.swift` | `core/…/Predictor.kt` | checked against the iOS fixture to 1e-6 |
| `Core/LineBoard.swift` | `core/…/LineBoard.kt` | `VehicleHistory` is an instance, not a singleton |
| `Core/StationGraph.swift` | `core/…/StationGraph.kt` | complex names tie-break alphabetically (Swift: dictionary order) |
| `Core/Planner.swift` (itineraries, headways, `evaluate`) | `core/…/Planner.kt` | |
| `Core/PlatformTiming.swift` | `core/…/PlatformTiming.kt` | |
| `Core/Presets.swift` | `core/…/Presets.kt`, `app/…/store/Stores.kt` | model and store |
| `Core/Places.swift` | `core/…/Places.kt`, `app/…/store/Stores.kt` | |
| `Core/Habits.swift`, `Services/HabitStore.swift` | `core/…/Habits.kt`, `app/…/store/Stores.kt` | with the Swift tests |
| `Core/PersonalModel.swift`, `Services/PersonalModelStore.swift` | `core/…/PersonalModel.kt`, `app/…/store/Stores.kt` | read side only; `learn()` comes with the trip tracker |
| `Services/LocationService.swift` | `app/…/store/LocationService.kt` | platform LocationManager, fixes while the app is in front; background fixes come with the trip tracker |
| `Views/CommuteViews.swift` | `app/…/ui/CommuteViews.kt` | chip, setup prompt, editor, nearby sheet, Settings section |
| `Views/PlacesViews.swift` | `app/…/ui/PlacesViews.kt` | places, editor with the pin, pace section |
| `WhichWayShared/Format.swift`, `RouteStyle.swift` | `core/…/Format.kt` | colours as ints; Compose bullet in `app/…/ui/Common.kt` |
| `Services/DataService.swift`, `DiskCache.swift` | `app/…/store/AppData.kt` | a 404 from the published site retries the other known bases, including the raw `gh-pages` branch (Pages was publishing the source branch on 2026-10-09, so `/data/` was a 404) |
| `Views/PlannerView.swift`, `PathViews.swift` (Now card) | `app/…/ui/GoScreen.kt` | pickers, commutes (auto-apply, nearest origin), habits, walk line, ranked routes, Now card, itineraries; not the route in progress |
| `Views/LineBoardView.swift` | `app/…/ui/LineScreen.kt` | board, engine summary, alerts, train rows |
| `Views/SettingsView.swift` | `app/…/ui/SettingsScreen.kt` | the rider page and the Developer page (data source, status, build); commutes, places, pace and sharing sections not yet |
| `Views/WelcomeView.swift`, `ContentView.swift` (first-launch sheet) | `app/…/ui/WelcomeView.kt`, `MainActivity.kt` | no sharing switch: Android records no trips yet, so the sheet says that instead |

## Not ported yet (in rough order of value)

- **Route in progress**: `TripTracker`, `TripRecorder`, `BoardingDetector`, `LineInference`, `OnTrain`, the ride half of `Planner.swift` (`ridingItinerary`, `plannedCandidate`, `connectionItinerary`), `MotionSampler`/`MotionTrace`. Android needs a foreground service for this, where iOS uses the location background mode.
- **Views**: track diagram, Marey chart, route map, hours profile, route chart, insights, route and line health, hold outlook and the scenario switch.
- **Telemetry and trip upload**: `Telemetry`, `GitHubUploader`, `TripRelay` (sharing on by default since `21bc708`; port it with ride tracking).
- **Live Activity**: the Android counterpart would be an ongoing notification.

## How a port pass goes

1. `scripts/ios-drift.sh` to see what changed on iOS since the last pass.
2. Port those changes into the mapped Kotlin files (or move a row up from "Not ported yet").
3. `./gradlew :core:test` (and `:app:assembleDebug` with the SDK installed).
4. Update **Last port pass** above to the iOS commit you ported from, and commit only `android/` (plus `.github/workflows/android.yml` if it changed).
