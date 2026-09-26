# WhichWay — iOS travel assistant

A SwiftUI app (iOS 17+) that turns the delay analysis into a realtime travel assistant on the phone. It reads
the MTA GTFS-Realtime feeds directly, aligned to their 30-second publication, runs the prediction engine on
every train, and layers the published data from this repository's site on top.

## What it does

- **Go** tab. Pick where you are and where you're going (searchable station pickers; destinations are limited
  to stations reachable direct or with one change, each showing how). The location button next to *From* lists
  the stations nearest to you with the walk to each. **Commutes** are saved trips with a daily window: the first
  one whose window covers the current time is applied on its own when the tab opens or the app comes back to
  the foreground (once per day per window; stations picked by hand keep for the rest of the day), and a
  commute can start from the nearest station instead of a fixed one, choosing the nearest from which the
  destination is reachable with at most one change. Save the current trip from the chip row; manage commutes
  in Settings. The **Now card** says which train to
  take, counts down to boarding, and gives the predicted arrival with its 80% window and the feed's own time.
  Every viable path is ranked, first by expected time (half the scheduled headway as the wait, the scheduled
  ride, the time trains typically lose on those stretches at this hour, the hold risk from the hold log, the
  walk at the change), then by the arrival of the next live itinerary once the feeds are in. When a train on
  the path's lines is held, a **scenario switch** appears: the hold ends as holds here usually do, drags on
  (90th percentile), or clears now. The selected path has five views:
  - **Track** — a horizontal diagram per leg with the trains gliding on it (dead-reckoned every second between
    polls, held and overdue trains coloured, the train you'd board ringed) and per-stop layers (typical seconds
    lost at this hour, holds per day, km/h measured where known or scheduled).
  - **Time** — a Marey chart of the next 45 minutes: the feed's projections dashed, the engine's dotted (red when
    held or held back), the recommended itinerary in red.
  - **Board** — a departure board: countdowns to boarding, each train's position and flags, the predicted arrival
    with its window, the connection margin.
  - **Map** — the line's track and your stretch, the stops, and the trains moving on the map.
  - **Hours** — the time trains typically lose on the path's stretches by hour of day, with the best hour to
    leave in the next six.
  Below the views: the next itineraries (board and arrive times, ride versus schedule, connection margin and
  what happens if you miss it, where each train is and its last measured speed) and insights (worst stop on
  the stretch, slowest measured segment, active alerts, trains held or overdue now, what the engine is
  calibrated on).
- **Line** tab. One line direction with every started train on the track, the engine's projection for the next
  hour (largest gap, trains held back by the train ahead, the scenario switch when a train is held) and a list
  with each train's feed and engine ETA, lateness against the timetable (`~` marks a nearest-trip match),
  position and time there, holds, overdue trains, feed-optimistic ETAs, track changes and the last measured
  segment speed.
- **Settings**. Commutes (add, edit, reorder, delete), base URL of the published data (defaults to the GitHub
  Pages site), poll interval, data status.

## The prediction engine

`Core/Predictor.swift` is a port of `mta_delay_insights/realtime/client_model.py` and reads
`data/client_model.json`: the feed's ETA error by line and forecast horizon (bias and 80% window), the
remaining hold given the time a train has already been held, and how lateness carries from a train's current
stop to each stop ahead. After every poll the data service projects each board under the scenarios and the
planner, itineraries and views use those ETAs. `ios/WhichWayCore/Tests` reproduces the Python reference on a
fixture to the microsecond (`Tests/make_fixtures.py` regenerates it from the repository root).

## Layout

```
ios/WhichWay/
  WhichWay.xcodeproj/          Xcode 16 project (synchronized folder, one app target)
  WhichWay/
    WhichWayApp.swift          entry point
    Models/Schedule.swift      client_schedule.json, client_lines.json, holds.json, segments.json, lines/<key>.json
    Models/GTFSRealtime.swift  protobuf wire decoder for the feeds (trip updates, vehicle positions, NYCT extension)
    Models/Alerts.swift        Mercury service alerts (JSON)
    Models/Geometry.swift      client_geometry.json (stop coordinates, simplified tracks)
    Core/StationGraph.swift    station complexes, reachable destinations, paths with up to one change
    Core/LineBoard.swift       live board of one line: lateness, position fusion, holds/stalls, segment speeds
    Core/Planner.swift         trip candidates, itineraries with connections, headways, expected time
    Core/Predictor.swift       the prediction engine (port of client_model.py)
    Core/Presets.swift         commute presets (time windows, nearest-origin) and their store
    Core/Format.swift          time and number formatting (New York local time)
    Services/DataService.swift loads the published data, polls the feeds the visible screens need, runs the engine
    Services/LocationService.swift one-shot location for the nearest station (when-in-use permission)
    Views/                     ContentView (tabs), PlannerView, CommuteViews (chips, editor, nearby stations, settings section),
                               PathViews (Now card, scenario switch, view switcher,
                               departure board, hours), TrackDiagramView, MareyChartView, RouteMapView,
                               LineBoardView, StationPicker, SettingsView, RouteBullet
ios/WhichWayCore/              SwiftPM package over Models/ and Core/ (symlinks) with the predictor tests
```

## Local development (Mac + Xcode)

Everything the app needs runs locally: the Python pipeline builds the site data (the timetable extract, the
prediction engine's tables, geometry, hold log, …) and `mta-insights serve` serves it with `live.json` refreshed
from the MTA feeds every 30 seconds. One command does all of it (`make local`, or
`scripts/local_setup.sh --real` for the full build from the collected history): the Python environment, the
data, `Config/Local.xcconfig` with your team id and a bundle id, the local server in the background (log and
pid under `.local/`), and Xcode opened on the project. Step by step, from the repository root:

```bash
make venv                 # Python environment with the package and dev tools (once)
make site-synthetic       # offline preview: synthetic history + recorded feeds, builds _site in ~1 min
#   or, for real data:
make site                 # checks out the collected history (data branch), downloads the static GTFS,
                          # trains the models and fits the engine tables into _site (several minutes)
make serve                # http://localhost:8000 — the site, /data/*.json and /api/*, live data every 30 s
make ios                  # opens WhichWay.xcodeproj in Xcode
```

Then in Xcode:

1. `cp ios/WhichWay/Config/Local.xcconfig.example ios/WhichWay/Config/Local.xcconfig` and fill in your team id and
   a bundle id of your own (the file is git-ignored; the Debug configuration includes it). Setting the team in
   *Signing & Capabilities* works too.
2. With `WHICHWAY_BASE_URL = http:/$()/localhost:8000/data/` in that file the Debug build starts against the
   local server; otherwise it starts against the published site. Either can be changed at run time in Settings
   ("Use the local server", "Use the published site"). On a physical device use your Mac's address on the local
   network instead of localhost.
3. Pick a Simulator and run. The shared scheme simulates the phone at 14 St-Union Sq
   (`Simulation/UnionSquare.gpx`), so the location button and nearest-station commutes work in the Simulator;
   change it under *Debug > Simulate Location*, or use a real device.
4. `make ios-test` runs the core package tests from the command line (`swift test`); `make ios-build` compiles
   the app for the Simulator the way CI does.

The Simulator reaches the MTA feeds directly (internet needed); the synthetic preview instead ships recorded
feeds and pins the clock (`demo_now`), so the app replays that moment offline. Plain HTTP to localhost and local
network addresses is allowed by App Transport Security, so no exception is needed for the local server.

`.github/workflows/ios.yml` builds the app for the Simulator and runs the package tests on a macOS runner on
every push that touches `ios/`, so the sources stay compiler-checked from any machine.

## Existing "whichway" project

If you already have a WhichWay Xcode project, drop the `WhichWay/` source folder (or just `Models/`, `Core/` and
`Services/`) into it: there are no dependencies beyond Foundation, SwiftUI and MapKit.

## Notes

- The MTA feeds carry no GPS or speed: a train's position is the stop it is at or heading to and when that state
  began. Positions between stops are dead-reckoned along the scheduled running time; speeds are measured from
  stop-to-stop timing (`Core/LineBoard.swift`, `VehicleHistory`) and from the published segment statistics.
- Trains are matched to the timetable extract by trip stem, then by the nearest scheduled trip within 15 minutes.
- Feeds are polled only for the lines the visible screens show, just after each expected publication; polling
  pauses in the background and resumes with an immediate refresh in the foreground.
- Location is requested only when you tap the location button or a commute starts from the nearest station
  (when-in-use permission, a single fix); station coordinates come from `data/client_geometry.json`.
