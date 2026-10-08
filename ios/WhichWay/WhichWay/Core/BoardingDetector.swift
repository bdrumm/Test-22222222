import Foundation

/// One second of the phone's motion, summarised on the phone. Raw sensor samples never leave the sampler.
struct MotionSecond: Equatable {
    var ts: Double
    /// Variance of the vertical user acceleration (g²): walking is a steady step rhythm, about 0.02 and up.
    var stepEnergy: Double
    /// Magnitude of the mean horizontal user acceleration (g): a train pulling away is a sustained push of
    /// 0.05 to 0.12 g for several seconds; vibration averages out of this.
    var pushG: Double
    /// RMS of the horizontal user acceleration about its mean (g): rolling stock shakes, 0.02 to 0.06.
    var shakeG: Double
}

enum MotionState: String, Codable { case unknown, walking, still, riding }

struct MotionEvent: Equatable, Codable {
    enum Kind: String, Codable { case departed, alighted }
    var kind: Kind
    var ts: Double
}

/// Finds the moment a rider's train pulls away and the moment they walk off it, from one MotionSecond per
/// second. A ride starts with a sustained push, or with sustained vibration, while no steps are taken; it
/// survives the train's stops (quiet seconds) and steps inside the car; it ends once the rider has been walking
/// for a while after the train stood still (or for a long while regardless). Standing on a platform while another
/// train passes does not start a ride.
struct BoardingDetector {
    var stepThreshold = 0.012
    var pushThreshold = 0.05
    var shakeThreshold = 0.02
    var pushSeconds = 3
    var shakeSeconds = 8
    var alightWalkSeconds = 8
    var minRideSeconds = 20
    var stoppedBeforeWalkSeconds = 5       // the train must have stood this long before the steps: nobody steps off a moving train
    var longWalkSeconds = 30               // unless the walking goes on this long (the stop went unfelt)

    private(set) var state: MotionState = .unknown
    private(set) var seconds = 0
    private var walkRun = 0, pushRun = 0, shakeRun = 0, walkAfterRide = 0, stoppedRun = 0
    private var walkFromStop = false
    private var rideStart: Double?

    mutating func feed(_ m: MotionSecond) -> MotionEvent? {
        seconds += 1
        let walking = m.stepEnergy > stepThreshold
        if walking {
            if walkRun == 0 { walkFromStop = stoppedRun >= stoppedBeforeWalkSeconds }
            walkRun += 1; pushRun = 0; shakeRun = 0; stoppedRun = 0
            if state == .riding {
                walkAfterRide += 1
                if walkAfterRide >= alightWalkSeconds, walkFromStop || walkAfterRide >= longWalkSeconds,
                   let start = rideStart, m.ts - start >= Double(minRideSeconds) {
                    state = .walking; rideStart = nil; walkAfterRide = 0
                    return MotionEvent(kind: .alighted, ts: m.ts - Double(alightWalkSeconds - 1))
                }
                return nil
            }
            if walkRun >= 2 { state = .walking }
            return nil
        }
        walkRun = 0; walkAfterRide = 0
        pushRun = m.pushG >= pushThreshold ? pushRun + 1 : 0
        shakeRun = m.shakeG >= shakeThreshold ? shakeRun + 1 : 0
        stoppedRun = (m.shakeG < shakeThreshold && m.pushG < pushThreshold) ? stoppedRun + 1 : 0
        if state == .riding { return nil }
        if pushRun >= pushSeconds || shakeRun >= shakeSeconds {
            let run = pushRun >= pushSeconds ? pushRun : shakeRun
            let start = m.ts - Double(run - 1)
            state = .riding; rideStart = start
            return MotionEvent(kind: .departed, ts: start)
        }
        state = .still
        return nil
    }

    /// The rider says they are on a train already (a route started, or put right, from the train): the ride counts
    /// from `ts`, and the next sustained walk is the alighting, as after a felt departure.
    mutating func assumeRiding(since ts: Double) {
        state = .riding; rideStart = ts; walkAfterRide = 0; pushRun = 0; shakeRun = 0; stoppedRun = 0; walkFromStop = false
    }

    /// The rider stepped off without walking (a same-platform change, told by the feed): the ride is over and the
    /// next sustained push or vibration is a new departure.
    mutating func standDown() {
        state = .still; rideStart = nil; walkAfterRide = 0; pushRun = 0; shakeRun = 0; stoppedRun = 0; walkFromStop = false
    }
}
