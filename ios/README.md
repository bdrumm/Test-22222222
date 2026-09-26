# WhichWay — iOS travel assistant

A SwiftUI app (iOS 17+) that turns the delay analysis into a realtime travel assistant on the phone. It reads the
MTA GTFS-Realtime feeds directly, every 30 s, and layers the published data from this repository's site on top.

## What it does

- **Go** tab: pick where you are and where you're going (searchable station pickers; destinations are limited to
  stations reachable direct or with one change, each showing how). Every viable path is listed and ranked, first by
  expected time (half the scheduled headway as the wait, the scheduled ride, the time trains typically lose on those
  stretches at this hour from the deviation grids, the hold risk from the hold log, the walk at the change), then by
  the arrival of the next live itinerary once the feeds are in. The selected path shows each leg on a horizontal
  track diagram with the trains moving on it (dead-reckoned every second between polls, held and overdue trains
  coloured, the train you'd board ringed), per-stop layers (typical seconds lost at this hour, holds per day, km/h
  measured where known or scheduled), the next itineraries (board and arrive times, ride versus schedule,
  connection margin and what happens if you miss it, where each train is and its last measured speed) and insights
  (worst stop on the stretch, slowest measured segment, active alerts on the lines, trains held or overdue now).
- **Line** tab: one line direction with every started train on the track and a list with lateness against the
  timetable (`~` marks a nearest-trip match), position and time there, holds, overdue trains, feed-optimistic ETAs,
  track changes and the last measured segment speed.
- **Settings**: base URL of the published data (defaults to the GitHub Pages site), poll interval, data status.

## Layout

```
ios/WhichWay/
  WhichWay.xcodeproj/          Xcode 16 project (synchronized folder, one app target)
  WhichWay/
    WhichWayApp.swift          entry point
    Models/Schedule.swift      client_schedule.json, client_lines.json, holds.json, segments.json, lines/<key>.json
    Models/GTFSRealtime.swift  protobuf wire decoder for the feeds (trip updates, vehicle positions, NYCT extension)
    Models/Alerts.swift        Mercury service alerts (JSON)
    Core/StationGraph.swift    station complexes, reachable destinations, paths with up to one change
    Core/LineBoard.swift       live board of one line: lateness, position fusion, holds/stalls, segment speeds
    Core/Planner.swift         trip candidates, itineraries with connections, headways, expected time
    Core/Format.swift          time and number formatting (New York local time)
    Services/DataService.swift loads the published data, polls the feeds the visible screens need
    Views/                     ContentView (tabs), PlannerView, LineBoardView, TrackDiagramView, StationPicker, SettingsView, RouteBullet
```

The Swift code ports `site/rt-client.js` (feed decoding, board, station graph, planner) so the phone and the site
compute the same thing from the same inputs; see METHODOLOGY.md §9j–9m for the methods.

## Running it

1. Open `ios/WhichWay/WhichWay.xcodeproj` in Xcode 16 or later.
2. In the target's *Signing & Capabilities*, pick your team (bundle id `com.whichway.app`; change it if it clashes).
3. Run on a simulator or a device. The app loads `client_schedule.json` and the other files from
   `https://bdrumm.github.io/Test-22222222/data/` and then polls the MTA feeds the selected paths need.

To run against a local site build (`python -m pipeline.build_site` output served with `python -m http.server`),
set the base URL in Settings to that server's `data/` folder. The synthetic preview's `demo_now` is honoured, so the
recorded feeds it ships line up with its clock.

## Existing "whichway" project

If you already have a WhichWay Xcode project, drop the `WhichWay/` source folder (or just `Models/`, `Core/` and
`Services/`) into it: there are no dependencies beyond Foundation and SwiftUI. This project was written without
access to that repository or to a Swift toolchain, so the first build in Xcode is the first compile; expect to fix
small type or availability issues rather than design ones.

## Notes

- The MTA feeds carry no GPS or speed: a train's position is the stop it is at or heading to and when that state
  began. Positions between stops are dead-reckoned along the scheduled running time; speeds are measured from
  stop-to-stop timing (`Core/LineBoard.swift`, `VehicleHistory`) and from the published segment statistics.
- Trains are matched to the timetable extract by trip stem, then by the nearest scheduled trip within 15 minutes.
- Feeds are polled only for the lines the visible screens show; polling pauses in the background.
