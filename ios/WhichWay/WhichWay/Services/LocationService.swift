import Foundation
import CoreLocation
import Observation

/// One-shot "where am I" for picking the nearest station: asks for when-in-use permission the first time, then
/// requests a single fix. Errors and denial are reported as text for the UI.
@MainActor
@Observable
final class LocationService: NSObject, CLLocationManagerDelegate {
    private(set) var status: CLAuthorizationStatus
    private(set) var location: CLLocation?
    private(set) var error: String?
    private(set) var locating = false
    /// Continuous fixes are on: a route is in progress.
    private(set) var tracking = false
    @ObservationIgnored private let manager = CLLocationManager()

    override init() {
        status = manager.authorizationStatus
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    var authorized: Bool { status == .authorizedWhenInUse || status == .authorizedAlways }

    /// The last fix if it is fresh and sure enough to act on: no older than `maxAgeSec` and no less accurate than
    /// `maxAccuracyM`. When the app comes back, the first fix Core Location hands over is often an old one from
    /// wherever it was last used, or a coarse one from cell towers; a route must not start from either.
    func fix(maxAgeSec: Double = 30, maxAccuracyM: Double = 100) -> CLLocation? {
        guard let l = location, Date().timeIntervalSince(l.timestamp) <= maxAgeSec,
              l.horizontalAccuracy >= 0, l.horizontalAccuracy <= maxAccuracyM else { return nil }
        return l
    }

    /// Ask for a fix (and for permission first when it was never asked). A recent fix is kept.
    func request() {
        error = nil
        switch manager.authorizationStatus {
        case .notDetermined:
            manager.requestWhenInUseAuthorization()
        case .denied, .restricted:
            error = "Location access is off for WhichWay. Allow it in Settings to pick the nearest station."
        default:
            if let l = location, Date().timeIntervalSince(l.timestamp) < 60 { return }
            locating = true
            manager.requestLocation()
        }
    }

    /// Continuous fixes for a route in progress: ten-metre accuracy, a fix every ten metres moved. Stops with
    /// the route; the one-shot `request()` is untouched.
    func startTracking() {
        guard authorized else { request(); return }
        guard !tracking else { return }
        manager.desiredAccuracy = kCLLocationAccuracyNearestTenMeters
        manager.distanceFilter = 10
        manager.activityType = .fitness
        // a route runs with the screen off and the phone in a pocket: background updates keep the app alive (and with it
        // the motion sampler and the feed polls) for the length of the route; the blue indicator shows the rider
        manager.pausesLocationUpdatesAutomatically = false
        manager.allowsBackgroundLocationUpdates = true
        manager.showsBackgroundLocationIndicator = true
        manager.startUpdatingLocation()
        tracking = true
    }

    func stopTracking() {
        guard tracking else { return }
        manager.stopUpdatingLocation()
        manager.allowsBackgroundLocationUpdates = false
        manager.pausesLocationUpdatesAutomatically = true
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        manager.distanceFilter = kCLDistanceFilterNone
        tracking = false
    }

    nonisolated func locationManagerDidChangeAuthorization(_ m: CLLocationManager) {
        let st = m.authorizationStatus
        Task { @MainActor in
            self.status = st
            if st == .authorizedWhenInUse || st == .authorizedAlways {
                self.locating = true
                self.manager.requestLocation()
            } else if st == .denied || st == .restricted {
                self.error = "Location access is off for WhichWay. Allow it in Settings to pick the nearest station."
                self.locating = false
            }
        }
    }

    nonisolated func locationManager(_ m: CLLocationManager, didUpdateLocations locs: [CLLocation]) {
        let last = locs.last
        Task { @MainActor in
            if let l = last { self.location = l }
            self.locating = false
        }
    }

    nonisolated func locationManager(_ m: CLLocationManager, didFailWithError e: Error) {
        let text = e.localizedDescription
        Task { @MainActor in
            self.error = "Could not get your location (\(text))."
            self.locating = false
        }
    }
}
