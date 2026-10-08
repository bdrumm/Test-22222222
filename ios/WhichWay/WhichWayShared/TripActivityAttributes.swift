import ActivityKit
import Foundation

/// The route in progress, on the lock screen and in the Dynamic Island. Shared by the app (which starts and
/// updates it) and the WhichWayLiveActivity extension (which draws it).
struct TripActivityAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var route: String            // the line to board now, "F"
        var trainLabel: String       // "1F 1248", empty without a train in the feeds
        var boardTs: Double          // when it boards at the origin
        var arriveTs: Double         // when it gets you there
        var nextBoardTs: Double?     // the following train on this route
        var nextRoute: String?
        var changeAt: String?        // "W 4 St-Wash Sq"
        var changeRoutes: String?    // "A/C/E"
        var extraMin: Int            // minutes behind the timetable (0 under a minute and a half)
        var level: Int               // 0 quiet, 1 yellow, 2 red
        var status: String           // "train now at 7 Av · 15 min late"
        var offline: Bool
        /// The route as it stands now ("G → C at Hoyt-Schermerhorn"): it can change under the rider, while the
        /// attributes cannot, and an activity cannot be started afresh from the background.
        var routeLabel: String? = nil
    }
    var originName: String
    var destName: String
    var routeLabel: String           // "F → A/C/E at W 4 St-Wash Sq"
}
