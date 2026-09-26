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
    @ObservationIgnored private let manager = CLLocationManager()

    override init() {
        status = manager.authorizationStatus
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    var authorized: Bool { status == .authorizedWhenInUse || status == .authorizedAlways }

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
