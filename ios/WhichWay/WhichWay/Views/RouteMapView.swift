import SwiftUI
import MapKit

/// The path on a map: each leg's track (the simplified shape, else straight lines between stops), the stops with
/// board and alight marked, and the trains at their dead-reckoned positions, refreshed every second.
struct RouteMapView: View {
    @Environment(DataService.self) private var data
    let option: PathOption
    let schedule: ClientSchedule
    @State private var camera: MapCameraPosition = .automatic

    private struct StopPin: Identifiable { var id: String; var name: String; var coord: CLLocationCoordinate2D; var special: Bool; var route: String }
    private struct TrainPin: Identifiable { var id: String; var coord: CLLocationCoordinate2D; var color: Color; var label: String; var mine: Bool }
    private struct LegShape: Identifiable { var id: String; var route: String; var line: [CLLocationCoordinate2D]; var span: [CLLocationCoordinate2D] }

    var body: some View {
        Group {
            if let geo = data.geometry {
                TimelineView(.periodic(from: .now, by: 1)) { _ in
                    let now = data.now
                    let shapes = legShapes(geo)
                    let stops = stopPins(geo)
                    let trains = trainPins(geo, now: now)
                    Map(position: $camera) {
                        ForEach(shapes) { s in
                            if s.line.count >= 2 { MapPolyline(coordinates: s.line).stroke(RouteStyle.color(s.route).opacity(0.45), lineWidth: 3) }
                            if s.span.count >= 2 { MapPolyline(coordinates: s.span).stroke(RouteStyle.color(s.route), lineWidth: 5) }
                        }
                        ForEach(stops) { st in
                            Annotation(st.special ? st.name : "", coordinate: st.coord, anchor: .center) {
                                Circle().fill(st.special ? RouteStyle.color(st.route) : Color(.systemBackground))
                                    .overlay(Circle().stroke(Color.primary.opacity(0.7), lineWidth: st.special ? 2 : 1))
                                    .frame(width: st.special ? 12 : 7, height: st.special ? 12 : 7)
                            }
                        }
                        ForEach(trains) { t in
                            Annotation(t.label, coordinate: t.coord, anchor: .center) {
                                RoundedRectangle(cornerRadius: 3).fill(t.color)
                                    .overlay(RoundedRectangle(cornerRadius: 4).stroke(t.mine ? Color.green : Color.clear, lineWidth: 2).padding(-2))
                                    .frame(width: 16, height: 10)
                            }
                        }
                    }
                    .mapStyle(.standard(elevation: .flat, pointsOfInterest: .excludingAll))
                    .frame(height: 420)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .onAppear { camera = .region(region(stops)) }
                }
                Text("Solid: your stretch; faint: the rest of the line. Trains glide between polls along the scheduled running time; the ringed one is yours.").font(.caption2).foregroundStyle(.secondary)
            } else if let err = data.lastError, data.geometry == nil, err.contains("geometry") {
                Text(err).font(.caption).foregroundStyle(Color.red)
            } else {
                ProgressView("Loading the map…").frame(maxWidth: .infinity).frame(height: 200).onAppear { data.requestGeometry() }
            }
        }
    }

    private func coords(_ g: LineGeometry, _ range: ClosedRange<Int>) -> [CLLocationCoordinate2D] {
        range.compactMap { i in g.coord(i).map { CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon) } }
    }

    private func legShapes(_ geo: ClientGeometry) -> [LegShape] {
        option.legs.compactMap { leg in
            guard let g = geo.lines[leg.primaryKey], let ix = leg.idx[leg.primaryKey] else { return nil }
            let full = g.shape.count >= 2 ? g.shape.compactMap { $0.count >= 2 ? CLLocationCoordinate2D(latitude: $0[0], longitude: $0[1]) : nil } : coords(g, 0...(g.coords.count - 1))
            return LegShape(id: leg.primaryKey, route: leg.primaryRoute, line: full, span: coords(g, ix.from...ix.to))
        }
    }

    private func stopPins(_ geo: ClientGeometry) -> [StopPin] {
        var out: [StopPin] = []
        for leg in option.legs {
            guard let g = geo.lines[leg.primaryKey], let line = schedule.lines[leg.primaryKey], let ix = leg.idx[leg.primaryKey] else { continue }
            for i in ix.from...ix.to {
                guard let c = g.coord(i) else { continue }
                out.append(StopPin(id: "\(leg.primaryKey)|\(i)", name: i < line.names.count ? line.names[i] : line.stops[i], coord: CLLocationCoordinate2D(latitude: c.lat, longitude: c.lon), special: i == ix.from || i == ix.to, route: leg.primaryRoute))
            }
        }
        return out
    }

    private func trainPins(_ geo: ClientGeometry, now: Double) -> [TrainPin] {
        var out: [TrainPin] = []
        let mine = Set((option.live?.legs ?? []).map { $0.train.id })
        for leg in option.legs {
            for k in leg.keys {
                guard let b = data.predictedBoards[k], let bl = schedule.lines[k], let g = geo.lines[k] else { continue }
                let age = now - b.now
                for t in b.trains {
                    let p = trainProgress(t, age: age, line: bl)
                    let j = Int(p.idx.rounded(.down))
                    guard let a = g.coord(j) else { continue }
                    let f = p.idx - Double(j)
                    let bb = g.coord(min(bl.stops.count - 1, j + 1)) ?? a
                    let coord = CLLocationCoordinate2D(latitude: a.lat + (bb.lat - a.lat) * f, longitude: a.lon + (bb.lon - a.lon) * f)
                    out.append(TrainPin(id: t.id, coord: coord, color: RouteStyle.stateColor(p.state, route: t.route), label: shortLabel(t), mine: mine.contains(t.id)))
                }
            }
        }
        return out
    }

    private func region(_ stops: [StopPin]) -> MKCoordinateRegion {
        let lats = stops.map { $0.coord.latitude }, lons = stops.map { $0.coord.longitude }
        guard let minLat = lats.min(), let maxLat = lats.max(), let minLon = lons.min(), let maxLon = lons.max() else {
            return MKCoordinateRegion(center: CLLocationCoordinate2D(latitude: 40.73, longitude: -73.95), span: MKCoordinateSpan(latitudeDelta: 0.25, longitudeDelta: 0.25))
        }
        return MKCoordinateRegion(center: CLLocationCoordinate2D(latitude: (minLat + maxLat) / 2, longitude: (minLon + maxLon) / 2),
                                  span: MKCoordinateSpan(latitudeDelta: max(0.02, (maxLat - minLat) * 1.4), longitudeDelta: max(0.02, (maxLon - minLon) * 1.4)))
    }
}
