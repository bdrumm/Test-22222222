import SwiftUI

struct DiagramTrain: Identifiable {
    var id: String
    var idx: Double
    var state: String        // moving | stopped | holding | stalled | terminal | unknown
    var route: String
    var label: String
    var sub: String = ""
    var emphasis: String? = nil   // "origin" (the train to board) | "connection"
}

struct DiagramLayer: Identifiable {
    var id: String
    var name: String
    var values: [Double?]
    var color: Color
    var format: (Double) -> String
}

/// Horizontal track of one line direction: per-stop data layers above, train markers on the track, stop names
/// below. Marker positions come from the caller (dead-reckoned each second by a TimelineView).
struct TrackDiagramView: View {
    let line: LineTopology
    let route: String
    var fromIdx: Int? = nil
    var toIdx: Int? = nil
    var layers: [DiagramLayer] = []
    var trains: [DiagramTrain] = []
    var colW: CGFloat = 60
    var scrollTo: Int? = nil

    private let left: CGFloat = 44
    private let nameW: CGFloat = 96
    private var layerTop: CGFloat { 8 }
    private var trackY: CGFloat { layerTop + CGFloat(layers.count) * 16 + 60 }
    private var height: CGFloat { trackY + 24 + nameW * 0.87 }
    private var n: Int { line.stops.count }
    private var width: CGFloat { left * 2 + colW * CGFloat(max(1, n - 1)) + 30 }
    private func x(_ i: Double) -> CGFloat { left + CGFloat(i) * colW }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if !layers.isEmpty {
                HStack(spacing: 10) {
                    ForEach(layers) { l in
                        HStack(spacing: 3) {
                            Circle().fill(l.color).frame(width: 6, height: 6)
                            Text(l.name).font(.system(size: 9)).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            ScrollViewReader { proxy in
                ScrollView(.horizontal, showsIndicators: true) {
                    ZStack(alignment: .topLeading) {
                        layerRows
                        track
                        stops
                        trainMarkers
                    }
                    .frame(width: width, height: height, alignment: .topLeading)
                }
                .onAppear { if let s = scrollTo { proxy.scrollTo("stop-\(s)", anchor: .leading) } }
                .onChange(of: scrollTo) { _, s in
                    if let s = s { withAnimation { proxy.scrollTo("stop-\(s)", anchor: .leading) } }
                }
            }
        }
    }

    private var layerRows: some View {
        ForEach(Array(layers.enumerated()), id: \.element.id) { li, layer in
            let y = layerTop + CGFloat(li) * 16 + 8
            ForEach(Array(layer.values.enumerated()), id: \.offset) { i, v in
                if let v = v {
                    Text(layer.format(v))
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(layer.color)
                        .fixedSize()
                        .position(x: x(Double(i)), y: y)
                }
            }
        }
    }

    private var track: some View {
        ZStack(alignment: .topLeading) {
            Path { p in
                p.move(to: CGPoint(x: x(0), y: trackY))
                p.addLine(to: CGPoint(x: x(Double(max(0, n - 1))), y: trackY))
            }
            .stroke(Color.secondary.opacity(0.35), lineWidth: 4)
            if let f = fromIdx, let t = toIdx, t > f {
                Path { p in
                    p.move(to: CGPoint(x: x(Double(f)), y: trackY))
                    p.addLine(to: CGPoint(x: x(Double(t)), y: trackY))
                }
                .stroke(RouteStyle.color(route), lineWidth: 5)
            }
        }
    }

    private var stops: some View {
        ForEach(Array(line.stops.enumerated()), id: \.offset) { i, sid in
            let special = (i == fromIdx || i == toIdx)
            Circle()
                .fill(special ? RouteStyle.color(route) : Color(.systemBackground))
                .overlay(Circle().stroke(special ? Color.primary : Color.secondary, lineWidth: special ? 2 : 1.5))
                .frame(width: special ? 12 : 8, height: special ? 12 : 8)
                .position(x: x(Double(i)), y: trackY)
                .id("stop-\(i)")
            if i == fromIdx {
                Text("▲ board").font(.system(size: 8, weight: .bold)).fixedSize().position(x: x(Double(i)), y: trackY + 13)
            }
            if i == toIdx {
                Text("▼ alight").font(.system(size: 8, weight: .bold)).fixedSize().position(x: x(Double(i)), y: trackY + 13)
            }
            Text(i < line.names.count ? line.names[i] : sid)
                .font(.system(size: 9))
                .foregroundStyle(special ? Color.primary : Color.secondary)
                .lineLimit(1)
                .truncationMode(.head)
                .frame(width: nameW, alignment: .trailing)
                .rotationEffect(.degrees(-60), anchor: .trailing)
                .position(x: x(Double(i)) - nameW / 2, y: trackY + 22)
        }
    }

    private var trainMarkers: some View {
        ForEach(Array(trains.enumerated()), id: \.element.id) { ti, t in
            let cx = x(t.idx)
            let color = RouteStyle.stateColor(t.state, route: t.route)
            let ring: Color = t.emphasis == "origin" ? Color.green : (t.emphasis == "connection" ? Color.purple : Color.clear)
            let my = trackY - 14
            RoundedRectangle(cornerRadius: 3)
                .fill(color)
                .frame(width: 18, height: 10)
                .overlay(RoundedRectangle(cornerRadius: 3).stroke(t.state == "stopped" ? Color.primary : Color.clear, lineWidth: 1))
                .overlay(RoundedRectangle(cornerRadius: 5).stroke(ring, lineWidth: 2).padding(-3))
                .position(x: cx, y: my)
            VStack(spacing: 0) {
                Text(t.label).font(.system(size: 8, weight: .semibold)).foregroundStyle(.primary)
                if !t.sub.isEmpty { Text(t.sub).font(.system(size: 7)).foregroundStyle(.secondary) }
            }
            .fixedSize()
            .position(x: cx, y: my - 18 - CGFloat(ti % 2) * 18)
        }
    }
}
