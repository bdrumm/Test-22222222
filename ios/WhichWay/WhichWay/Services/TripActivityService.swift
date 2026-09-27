import ActivityKit
import Foundation

/// Starts, updates and ends the Live Activity for the route in progress.
@MainActor
final class TripActivityService {
    static let shared = TripActivityService()
    private var activity: Activity<TripActivityAttributes>?
    /// The route the running activity describes (its attributes are fixed for its life).
    private(set) var routeId: String?

    var isRunning: Bool { activity != nil }

    private func content(_ state: TripActivityAttributes.ContentState) -> ActivityContent<TripActivityAttributes.ContentState> {
        ActivityContent(state: state, staleDate: Date().addingTimeInterval(180))
    }

    func start(routeId: String, attributes: TripActivityAttributes, state: TripActivityAttributes.ContentState) {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { return }
        endAll()
        do {
            activity = try Activity.request(attributes: attributes, content: content(state), pushType: nil)
            self.routeId = routeId
        } catch {
            activity = nil
            self.routeId = nil
        }
    }

    func update(_ state: TripActivityAttributes.ContentState) {
        guard let a = activity else { return }
        let c = content(state)
        Task { await a.update(c) }
    }

    /// Ends the activity; with a final state it lingers a minute on the lock screen, else it goes at once.
    func end(final state: TripActivityAttributes.ContentState? = nil) {
        guard let a = activity else { return }
        activity = nil
        routeId = nil
        let c = state.map { ActivityContent(state: $0, staleDate: nil) }
        Task { await a.end(c, dismissalPolicy: state == nil ? .immediate : .after(Date().addingTimeInterval(60))) }
    }

    /// Any activity left from an earlier run of the app.
    func endAll() {
        for a in Activity<TripActivityAttributes>.activities { Task { await a.end(nil, dismissalPolicy: .immediate) } }
        activity = nil
        routeId = nil
    }
}
