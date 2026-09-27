import ActivityKit
import SwiftUI
import WidgetKit

/// The route in progress: the train to take with a live countdown, the arrival, the standing against the
/// timetable and the train's state; compact in the Dynamic Island, fuller on the lock screen and when expanded.
struct TripLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: TripActivityAttributes.self) { context in
            LockScreenView(attributes: context.attributes, state: context.state)
                .activityBackgroundTint(Color.black.opacity(0.55))
                .activitySystemActionForegroundColor(Color.white)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    HStack(spacing: 6) {
                        RouteBullet(route: context.state.route, size: 26)
                        VStack(alignment: .leading, spacing: 1) {
                            Text("at \(Fmt.hhmm(context.state.boardTs))").font(.headline)
                            Text(context.attributes.originName).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                        }
                    }
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Countdown(boardTs: context.state.boardTs).font(.system(size: 26, weight: .bold, design: .rounded))
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text("Arrive \(context.attributes.destName) \(Fmt.hhmm(context.state.arriveTs))").font(.subheadline.weight(.semibold))
                            Spacer()
                            ExtraBadge(state: context.state)
                        }
                        Text(subline(context.state)).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                    }
                    .padding(.horizontal, 6)
                }
            } compactLeading: {
                RouteBullet(route: context.state.route, size: 20)
            } compactTrailing: {
                Countdown(boardTs: context.state.boardTs).font(.system(size: 14, weight: .semibold, design: .rounded)).frame(maxWidth: 52)
            } minimal: {
                RouteBullet(route: context.state.route, size: 20)
            }
        }
    }

    private func subline(_ s: TripActivityAttributes.ContentState) -> String {
        var bits: [String] = []
        if !s.status.isEmpty { bits.append(s.status) }
        if let n = s.nextBoardTs { bits.append("next at \(Fmt.hhmm(n))") }
        if s.offline { bits.append("offline") }
        return bits.joined(separator: " · ")
    }
}

/// Counts down to the boarding time on its own, no updates needed; sits at 0:00 once it has passed.
struct Countdown: View {
    let boardTs: Double

    var body: some View {
        let board = Date(timeIntervalSince1970: boardTs)
        if board > Date() {
            Text(timerInterval: Date()...board, countsDown: true, showsHours: false).monospacedDigit().multilineTextAlignment(.trailing)
        } else {
            Text("0:00").monospacedDigit()
        }
    }
}

struct ExtraBadge: View {
    let state: TripActivityAttributes.ContentState

    var body: some View {
        if state.extraMin > 0 {
            Text("+\(state.extraMin) min")
                .font(.caption.weight(.semibold))
                .foregroundStyle(state.level >= 2 ? Color.red : (state.level == 1 ? Color.yellow : Color.secondary))
        }
    }
}

struct LockScreenView: View {
    let attributes: TripActivityAttributes
    let state: TripActivityAttributes.ContentState

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .center, spacing: 8) {
                Text("Take the").font(.subheadline).foregroundStyle(.secondary)
                RouteBullet(route: state.route, size: 26)
                Text("at \(Fmt.hhmm(state.boardTs))").font(.title3.bold())
                Spacer()
                Countdown(boardTs: state.boardTs).font(.system(size: 30, weight: .bold, design: .rounded))
            }
            if let n = state.nextBoardTs {
                HStack(spacing: 6) {
                    if let r = state.nextRoute, r != state.route { RouteBullet(route: r, size: 14) }
                    Text("Next at \(Fmt.hhmm(n))").font(.caption).foregroundStyle(.tertiary)
                }
            }
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text("Arrive \(attributes.destName) \(Fmt.hhmm(state.arriveTs))").font(.headline)
                Spacer()
                ExtraBadge(state: state)
            }
            Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
        }
        .padding(14)
    }

    private var detail: String {
        var bits: [String] = []
        if !state.trainLabel.isEmpty { bits.append(state.trainLabel) }
        if !state.status.isEmpty { bits.append(state.status) }
        if let c = state.changeAt, let r = state.changeRoutes { bits.append("change to the \(r) at \(c)") }
        if state.offline { bits.append("offline, times from saved data") }
        return bits.isEmpty ? attributes.routeLabel : bits.joined(separator: " · ")
    }
}
