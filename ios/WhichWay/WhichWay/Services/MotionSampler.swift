import CoreMotion
import Foundation

/// Reads the motion sensors while a route is in progress and hands one summarised second at a time to the
/// boarding detector. The raw samples are reduced here and never stored or sent.
final class MotionSampler {
    private let manager = CMMotionManager()
    private let queue: OperationQueue = {
        let q = OperationQueue(); q.maxConcurrentOperationCount = 1; q.name = "whichway.motion"; return q
    }()
    private final class Window: @unchecked Sendable {
        var start = 0.0
        var vert: [Double] = [], hx: [Double] = [], hy: [Double] = [], hz: [Double] = []
        func reset(_ now: Double) { start = now; vert = []; hx = []; hy = []; hz = [] }
    }
    private let w = Window()

    static var isAvailable: Bool { CMMotionManager().isDeviceMotionAvailable }
    var isRunning: Bool { manager.isDeviceMotionActive }

    func start(_ onSecond: @escaping @Sendable (MotionSecond) -> Void) {
        guard manager.isDeviceMotionAvailable, !manager.isDeviceMotionActive else { return }
        manager.deviceMotionUpdateInterval = 1.0 / 25.0
        let w = self.w
        w.reset(0)
        manager.startDeviceMotionUpdates(to: queue) { motion, _ in
            guard let m = motion else { return }
            let now = Date().timeIntervalSince1970
            let g = m.gravity, u = m.userAcceleration
            let gn = (g.x * g.x + g.y * g.y + g.z * g.z).squareRoot()
            guard gn > 0 else { return }
            let gx = g.x / gn, gy = g.y / gn, gz = g.z / gn
            let v = u.x * gx + u.y * gy + u.z * gz                  // along gravity
            w.vert.append(v); w.hx.append(u.x - v * gx); w.hy.append(u.y - v * gy); w.hz.append(u.z - v * gz)
            if w.start == 0 { w.start = now }
            guard now - w.start >= 1.0, w.vert.count >= 5 else { return }
            let n = Double(w.vert.count)
            let mv = w.vert.reduce(0, +) / n
            let stepEnergy = w.vert.reduce(0) { $0 + ($1 - mv) * ($1 - mv) } / n
            let mx = w.hx.reduce(0, +) / n, my = w.hy.reduce(0, +) / n, mz = w.hz.reduce(0, +) / n
            let push = (mx * mx + my * my + mz * mz).squareRoot()
            var dev = 0.0
            for i in 0..<w.hx.count {
                let dx = w.hx[i] - mx, dy = w.hy[i] - my, dz = w.hz[i] - mz
                dev += dx * dx + dy * dy + dz * dz
            }
            let second = MotionSecond(ts: w.start, stepEnergy: stepEnergy, pushG: push, shakeG: (dev / n).squareRoot())
            w.reset(now)
            onSecond(second)
        }
    }

    func stop() { manager.stopDeviceMotionUpdates() }
}
